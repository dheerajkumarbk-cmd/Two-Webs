"""Derive every number the website and the report use from the crawl database.

Run from the project root:  python analysis/build_site_data.py
Writes docs/data.json and docs/data.js. CPU only; a few minutes.

Adds four robustness analyses to the trigger summary:
  1. twin rate by popularity-rank bucket
  2. fact loss by fact type, recomputed from stored page text (no cap)
  3. a site-clustered bootstrap interval for pooled fact loss (facts on one
     page are not independent, so a binomial interval is too narrow)
  4. sensitivity of fact loss to the minimum-facts threshold
and a census of response headers on Markdown twins (how the twin is produced).
"""
import collections
import json
import os
import random
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from twowebs import config as C, diff, extract, stats, store  # noqa: E402

SAMPLE = "trigger"
ACCEPT, UA, BOTH, CONTROL = "browser-md", "Claude-User", "Claude-User-md", "browser-control"
HARD = tuple(t + ":" for t in extract.HARD_TYPES)
META = ("title:", "description:", "image:", "url:", "og:", '"og:', "twitter:", '"twitter:',
        "<!--", "published:", "{", "[](", "canonical", "lang:")
# Sites left out of the public listing (adult content); they stay in every count.
UNLISTED = re.compile(r"xham|sex|porn|fap|redwap|simpcity|kir2kos|xhlink|theyarehuge", re.I)


def clean(text, n, width):
    keep = [l.strip() for l in (text or "").split("\n")
            if l.strip() and not l.strip().lower().startswith(META)]
    return [re.sub(r"\s+", " ", l)[:width] for l in keep[:n]]


def hard_facts(text):
    return set(x for x in diff._facts(text) if x.startswith(HARD))


def rate(k, n):
    r, lo, hi = stats.wilson(k, n)
    return {"k": k, "n": n, "rate": r, "low": lo, "high": hi}


def main():
    cfg = C.load()
    db = store.connect(cfg["db_path"])
    out = {"generated": "2026-11-16", "sample": SAMPLE}
    out["triggers"] = stats.triggers(db, cfg, SAMPLE)

    rows = db.execute(
        "SELECT s.site_id, s.domain, s.rank, p.page_id, p.kind, c.* FROM comparisons c"
        " JOIN pages p ON p.page_id=c.page_id JOIN sites s ON s.site_id=p.site_id"
        " WHERE s.sample=?", (SAMPLE,)).fetchall()
    per = collections.defaultdict(dict)          # (domain, kind) -> identity -> row
    for r in rows:
        if r["comparable"]:
            per[(r["domain"], r["kind"])][r["identity"]] = r

    def twin_sites(ident):
        return set(d for (d, _k), m in per.items() if m.get(ident) and m[ident]["ai_markdown"])
    ua_t, ac_t, bo_t = twin_sites(UA), twin_sites(ACCEPT), twin_sites(BOTH)
    out["venn"] = {"user_agent": len(ua_t), "accept": len(ac_t), "both_headers": len(bo_t),
                   "accept_missed_by_user_agent": len(ac_t - ua_t), "any": len(ua_t | ac_t | bo_t)}

    # ---- per-twin detail, facts recomputed from stored text (no 40-item cap) ----
    def page_texts(page_id, ident):
        return [r[0] or "" for r in db.execute(
            "SELECT e.text FROM extracts e JOIN fetches f ON f.fetch_id=e.fetch_id"
            " WHERE f.page_id=? AND f.identity=? AND e.outcome='ok' ORDER BY f.rep", (page_id, ident))]

    def header_of(page_id, ident):
        h = db.execute("SELECT headers, content_type, n_bytes FROM fetches WHERE page_id=? AND identity=?"
                       " AND status=200 ORDER BY rep LIMIT 1", (page_id, ident)).fetchone()
        return (json.loads(h[0] or "{}"), h[1] or "", h[2]) if h else ({}, "", None)

    twins, hdr_census = {}, collections.Counter()
    for (dom, kind), m in per.items():
        r = m.get(ACCEPT)
        if not r or not r["ai_markdown"]:
            continue
        b = [hard_facts(t) for t in page_texts(r["page_id"], "browser")]
        a = [hard_facts(t) for t in page_texts(r["page_id"], ACCEPT)]
        if len(b) < 2 or not a:
            continue
        stable_b, any_a = set.intersection(*b), set.union(*a)
        lost = stable_b - any_a
        hdr, ctype, md_bytes = header_of(r["page_id"], ACCEPT)
        _bh, _bc, html_bytes = header_of(r["page_id"], "browser")
        for k in hdr:
            if k.startswith("x-") or k in ("server", "content-signal", "vary", "link"):
                hdr_census[k] += 1
        by_type = collections.Counter(x.split(":", 1)[0] for x in stable_b)
        lost_type = collections.Counter(x.split(":", 1)[0] for x in lost)
        if "x-markdown-tokens" in hdr:
            producer = "CDN conversion (x-markdown-tokens header)"
        elif "markdown" in ctype:
            producer = "Site-served text/markdown"
        else:
            producer = "Other non-HTML"
        rec = {"domain": dom, "rank": r["rank"], "page": kind, "label": r["auto_label"],
               "facts": len(stable_b), "lost": len(lost),
               "by_type": dict(by_type), "lost_by_type": dict(lost_type),
               "lost_examples": sorted(x.split(":", 1)[1] for x in lost)[:14],
               "kept_examples": sorted(x.split(":", 1)[1] for x in (stable_b - lost))[:8],
               "machine_only": clean(r["ai_only_text"], 5, 240),
               "human_only": clean(r["browser_only_text"], 4, 240),
               "n_machine_only": r["n_ai_only"], "n_human_only": r["n_browser_only"],
               "html_bytes": html_bytes, "md_bytes": md_bytes,
               "producer": producer, "server": (hdr.get("server") or "").split("/")[0][:24],
               "found_by_user_agent": dom in ua_t,
               "listed": not UNLISTED.search(dom)}
        cur = twins.get(dom)                      # one page per site: most facts
        if cur is None or rec["facts"] > cur["facts"]:
            twins[dom] = rec
    tw = sorted(twins.values(), key=lambda t: t["rank"])
    out["twins"] = [t for t in tw if t["listed"]]
    out["twins_unlisted"] = sum(1 for t in tw if not t["listed"])
    out["header_census"] = hdr_census.most_common(25)
    out["producers"] = dict(collections.Counter(t["producer"] for t in tw))

    # ---- 1. rank buckets ----
    comp_sites = {}
    for (dom, _k), m in per.items():
        if ACCEPT in m:
            comp_sites[dom] = m[ACCEPT]["rank"]
    buckets = [(1, 500), (501, 1000), (1001, 2000), (2001, 3300)]
    out["rank_buckets"] = [dict(rate(sum(1 for d, rk in comp_sites.items() if lo <= rk <= hi and d in ac_t),
                                     sum(1 for d, rk in comp_sites.items() if lo <= rk <= hi)),
                                label="%d-%d" % (lo, hi)) for lo, hi in buckets]

    # ---- 2. loss by fact type ----
    tot, lost = collections.Counter(), collections.Counter()
    for t in tw:
        tot.update(t["by_type"]); lost.update(t["lost_by_type"])
    out["by_type"] = [dict(rate(lost[k], tot[k]), type=k) for k in extract.HARD_TYPES if tot[k]]

    # ---- 3 + 4. clustered bootstrap and threshold sensitivity ----
    rng = random.Random(20261116)

    def pooled(ts):
        n = sum(t["facts"] for t in ts)
        return (sum(t["lost"] for t in ts) / float(n)) if n else None

    sens = []
    for mf in (1, 3, 5, 10, 20):
        ts = [t for t in tw if t["facts"] >= mf]
        boots = []
        for _ in range(5000):
            v = pooled([ts[rng.randrange(len(ts))] for _ in ts])
            if v is not None:
                boots.append(v)
        boots.sort()
        sens.append({"min_facts": mf, "twins": len(ts), "facts": sum(t["facts"] for t in ts),
                     "pooled_loss": pooled(ts),
                     "boot_low": boots[int(0.025 * len(boots))], "boot_high": boots[int(0.975 * len(boots))],
                     "median_site_loss": stats.quantile([t["lost"] / float(t["facts"]) for t in ts], 0.5),
                     "lose_half": rate(sum(1 for t in ts if t["lost"] >= 0.5 * t["facts"]), len(ts)),
                     "lose_none": rate(sum(1 for t in ts if t["lost"] == 0), len(ts))})
    out["sensitivity"] = sens

    # control: identical requests, same accounting
    ctl = [r for r in rows if r["comparable"] and r["identity"] == CONTROL]
    c_facts = c_lost = 0
    for r in ctl:
        b = [hard_facts(t) for t in page_texts(r["page_id"], "browser")]
        a = [hard_facts(t) for t in page_texts(r["page_id"], CONTROL)]
        if len(b) < 2 or not a:
            continue
        sb = set.intersection(*b)
        c_facts += len(sb)
        c_lost += len(sb - set.union(*a))
    out["control"] = {"pages": len(ctl), "facts": c_facts, "facts_lost": c_lost,
                      "pages_flagged": sum(1 for r in ctl if r["flagged"])}

    summ = stats.summary(db, cfg)["samples"]
    out["samples"] = {k: {x: summ[k][x] for x in ("sites_loaded", "sites_crawled", "sites_comparable",
                                                  "divergent_auto")} for k in summ}
    out["counts"] = {"requests": db.execute("SELECT COUNT(*) FROM fetches WHERE error IS NULL").fetchone()[0],
                     "pages": db.execute("SELECT COUNT(*) FROM pages").fetchone()[0],
                     "sites": db.execute("SELECT COUNT(DISTINCT domain) FROM sites").fetchone()[0]}
    g = db.execute(
        "SELECT c.ai_only_text, c.browser_only_text FROM comparisons c JOIN pages p ON p.page_id=c.page_id"
        " JOIN sites s ON s.site_id=p.site_id WHERE s.sample='general' AND s.domain='alibabacloud.com'"
        " AND c.flagged=1 LIMIT 1").fetchone()
    out["ua_case"] = {"machine": clean(g[0], 1, 260), "human": clean(g[1], 1, 260)} if g else None

    docs = os.path.join(C.ROOT, "docs")
    os.makedirs(docs, exist_ok=True)
    text = json.dumps(out, indent=1, default=str)
    with open(os.path.join(docs, "data.json"), "w", encoding="utf-8") as f:
        f.write(text)
    with open(os.path.join(docs, "data.js"), "w", encoding="utf-8") as f:
        f.write("window.DATA = " + text + ";\n")
    for k in ("venn", "producers", "header_census", "rank_buckets", "by_type", "sensitivity", "control", "counts"):
        print(k, json.dumps(out[k], default=str)[:900])
    print("twins listed:", len(out["twins"]), "unlisted:", out["twins_unlisted"])


if __name__ == "__main__":
    main()
