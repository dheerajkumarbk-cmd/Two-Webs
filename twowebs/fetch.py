"""Layer 2: fetching. Stores raw responses and makes no judgement.

Method rules implemented here (see README):
  1. each page is fetched twice per identity, interleaved in time
  2. all identities send identical headers except User-Agent
  3. cookies are cleared after every request
  6. robots handling
"""
import asyncio
import random
import sys
import time
from urllib.parse import urljoin, urlsplit
from urllib.robotparser import RobotFileParser

import httpx

from . import config, extract, store

HEADER_CAP = 300
ERROR_CAP = 300


def headers_for(identity, cfg):
    """Common headers plus the identity's User-Agent. Nothing else varies."""
    h = dict(cfg.get("common_headers", {}))
    h["User-Agent"] = identity["user_agent"]
    return h


def make_client(cfg):
    c = cfg["crawl"]
    return httpx.AsyncClient(
        follow_redirects=True,
        max_redirects=5,
        timeout=httpx.Timeout(float(c["timeout_s"])),
        limits=httpx.Limits(max_connections=100, max_keepalive_connections=20),
    )


def _blank(identity, rep, url):
    return {
        "identity": identity["name"], "rep": rep, "url": url, "ts": time.time(),
        "status": None, "final_url": None, "content_type": "", "headers": {},
        "body": b"", "truncated": 0, "error": None,
    }


def _robots_skip(identity, rep, url):
    rec = _blank(identity, rep, url)
    rec["error"] = "robots_skip"
    return rec


async def _read_response(client, url, headers, max_bytes, rec, buf):
    async with client.stream("GET", url, headers=headers) as resp:
        rec["status"] = resp.status_code
        rec["final_url"] = str(resp.url)
        rec["content_type"] = resp.headers.get("content-type", "")
        rec["headers"] = dict(
            (k.lower(), v[:HEADER_CAP]) for k, v in resp.headers.items())
        async for chunk in resp.aiter_bytes():
            buf.extend(chunk)
            if len(buf) > max_bytes:
                rec["truncated"] = 1
                break


async def fetch_one(client, url, identity, cfg, rep=1):
    """GET one URL as one identity. Never raises; failures go in rec['error']."""
    rec = _blank(identity, rep, url)
    max_bytes = int(cfg["crawl"]["max_body_bytes"])
    total = float(cfg["crawl"]["timeout_s"]) * 3
    buf = bytearray()
    try:
        await asyncio.wait_for(
            _read_response(client, url, headers_for(identity, cfg),
                           max_bytes, rec, buf),
            timeout=total)
    except asyncio.CancelledError:
        raise
    except asyncio.TimeoutError:
        rec["error"] = "TimeoutError: total time limit %ds exceeded" % total
    except Exception as e:
        msg = "%s: %s" % (type(e).__name__, e)
        rec["error"] = msg[:ERROR_CAP]
    finally:
        try:
            client.cookies.clear()
        except Exception:
            pass
        rec["body"] = bytes(buf[:max_bytes])
        if len(buf) > max_bytes:
            rec["truncated"] = 1
    return rec


async def load_robots(client, base_url, cfg):
    """Fetch and parse /robots.txt. A missing, unreadable or erroring robots
    file means everything is allowed. The parser gets a `tw_status` attribute
    holding the HTTP status of the robots request (None on network error)."""
    robots_url = urljoin(base_url, "/robots.txt")
    rp = RobotFileParser()
    rp.set_url(robots_url)
    f = await fetch_one(client, robots_url, config.browser(cfg), cfg, rep=0)
    rp.tw_status = f["status"]
    if f["status"] is not None and 200 <= f["status"] < 300 and not f["error"]:
        text = f["body"].decode("utf-8", "replace")
        rp.parse(text.splitlines())
    else:
        rp.parse([])
    rp.modified()  # marks the file as read so can_fetch() answers from rules
    return rp


async def _pause(cfg):
    d = float(cfg["crawl"]["delay_s"])
    if d > 0:
        await asyncio.sleep(d * (0.5 + random.random()))


async def fetch_page(client, url, cfg, robots=None, prefetched=None, identities=None):
    """Rule 1: round 1 is browser then each AI identity in config order; round 2
    repeats. An identity is skipped in a round when rep > its `reps`."""
    ids = identities if identities is not None else cfg["identities"]
    respect = bool(cfg["crawl"].get("respect_robots")) and robots is not None
    n_rounds = max([i.get("reps", 2) for i in ids] or [0])
    out = []
    made_request = False
    for rep in range(1, n_rounds + 1):
        for ident in ids:
            if rep > ident.get("reps", 2):
                continue
            if rep == 1 and prefetched is not None and ident is ids[0]:
                out.append(prefetched)
                made_request = True
                continue
            if respect and not robots.can_fetch(ident["robots_token"], url):
                out.append(_robots_skip(ident, rep, url))
                continue
            if made_request:
                await _pause(cfg)
            out.append(await fetch_one(client, url, ident, cfg, rep))
            made_request = True
    return out


def _reachable(f):
    return f["error"] is None and f["status"] is not None


async def crawl_site(domain, cfg):
    """Fetch the homepage and one inner page. Returns (pages, note)."""
    browser = config.browser(cfg)
    respect = bool(cfg["crawl"].get("respect_robots"))
    bases = ["https://%s/" % domain, "https://www.%s/" % domain]
    if domain.startswith("www."):
        bases = ["https://%s/" % domain]
    async with make_client(cfg) as client:
        first = None
        robots = None
        base = bases[0]
        failed = None
        for base in bases:
            robots = await load_robots(client, base, cfg) if respect else None
            if robots is not None and not robots.can_fetch(browser["robots_token"], base):
                return [], "robots_disallow"
            first = await fetch_one(client, base, browser, cfg, 1)
            if _reachable(first):
                break
            if failed is None:
                failed = first
            first = None
        if first is None:
            return [{"url": bases[0], "kind": "home", "fetches": [failed]}], "unreachable"

        home = await fetch_page(client, base, cfg, robots=robots, prefetched=first)
        pages = [{"url": base, "kind": "home", "fetches": home}]

        if cfg["crawl"].get("inner_page"):
            try:
                _, links, _ = extract.text_lines(first["body"], first["content_type"])
                inner = extract.pick_inner_link(links, first["final_url"] or base)
            except Exception:
                inner = None
            if inner and inner != base:
                if robots is not None and not robots.can_fetch(browser["robots_token"], inner):
                    inner = None
            if inner and inner != base:
                await _pause(cfg)
                fetches = await fetch_page(client, inner, cfg, robots=robots)
                pages.append({"url": inner, "kind": "inner", "fetches": fetches})
        return pages, None


def _say(s):
    """print() that cannot fail on a console or redirected stream with a narrow
    code page (one odd domain must not abort the whole crawl)."""
    enc = getattr(sys.stdout, "encoding", None) or "utf-8"
    print(s.encode(enc, "replace").decode(enc, "replace"))


async def crawl(db, cfg, sample=None, limit=None):
    sites = store.pending_sites(db, sample, limit)
    total = len(sites)
    print("crawl: %d site(s) pending" % total)
    if not total:
        return
    store.set_meta(db, "crawl_settings", {
        "ts": time.time(), "identities": cfg["identities"],
        "common_headers": cfg.get("common_headers"), "crawl": cfg["crawl"]})
    sem = asyncio.Semaphore(int(cfg["crawl"]["concurrency"]))
    done = [0]

    async def one(site):
        async with sem:
            note = None
            try:
                pages, note = await crawl_site(site["domain"], cfg)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                pages, note = [], ("crash: %s: %s" % (type(e).__name__, e))[:ERROR_CAP]
            try:
                store.save_site(db, site["site_id"], pages, note)
            except Exception as e:
                _say("  could not save %s: %s: %s" % (site["domain"], type(e).__name__, e))
                return
            done[0] += 1
            _say("[%d/%d] %s (%s): %d page(s)%s" % (
                done[0], total, site["domain"], site["sample"], len(pages),
                " [" + note + "]" if note else ""))

    await asyncio.gather(*[one(s) for s in sites])
    print("crawl: done")
