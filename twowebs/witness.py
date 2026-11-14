"""Layer 7: witness. Check one live URL on demand and print a report."""
import json
import sys

from . import config, fetch


def _say(s=""):
    """print() that cannot fail on a console with a narrow code page."""
    enc = getattr(sys.stdout, "encoding", None) or "utf-8"
    print(str(s).encode(enc, "replace").decode(enc, "replace"))


async def witness(url, cfg, only=None):
    """Fetch url as the browser and as each AI identity (all, or those named in
    `only`), 2 reps each, then compare. Returns {identity name: compare result}."""
    from . import diff
    browser = config.browser(cfg)
    ai = config.ai_identities(cfg)
    if only:
        known = [i["name"] for i in ai]
        unknown = [n for n in only if n not in known]
        if unknown:
            raise ValueError("unknown identity: %s (known: %s)" % (
                ", ".join(unknown), ", ".join(known)))
        ai = [i for i in ai if i["name"] in only]
    ids = []
    for i in [browser] + ai:
        j = dict(i)
        j["reps"] = 2
        ids.append(j)
    async with fetch.make_client(cfg) as client:
        fetches = await fetch.fetch_page(client, url, cfg, robots=None, identities=ids)
    fetches = [diff.analyse_fetch(f, cfg) for f in fetches]
    b = [f for f in fetches if f["identity"] == browser["name"]]
    results = {}
    for i in ai:
        a = [f for f in fetches if f["identity"] == i["name"]]
        results[i["name"]] = diff.compare(b, a, cfg)
    return results


def _as_list(v):
    if v is None:
        return []
    if isinstance(v, (list, tuple)):
        return list(v)
    if isinstance(v, str):
        v = v.strip()
        if not v:
            return []
        try:
            parsed = json.loads(v)
            if isinstance(parsed, list):
                return parsed
        except ValueError:
            pass
        return [ln for ln in v.split("\n") if ln.strip()]
    return [v]


def report(url, results):
    """Print the report. Returns True if any identity is flagged."""
    _say("Witness report for %s" % url)
    any_flagged = False
    for name, r in results.items():
        flagged = bool(r.get("flagged"))
        any_flagged = any_flagged or flagged
        if r.get("comparable"):
            line = "%-16s %-22s ai-only=%s browser-only=%s%s" % (
                name, r.get("auto_label"), r.get("n_ai_only"), r.get("n_browser_only"),
                "  FLAGGED" if flagged else "")
        else:
            line = "%-16s not comparable (%s)%s" % (
                name, r.get("reason"), "  FLAGGED" if flagged else "")
        _say(line)
        if flagged:
            lines = _as_list(r.get("ai_only_text"))
            for ln in lines[:5]:
                _say("    AI-only: %s" % str(ln)[:300])
            if len(lines) > 5:
                _say("    ... %d more AI-only line(s)" % (len(lines) - 5))
            facts = _as_list(r.get("ai_only_facts"))
            if facts:
                _say("    AI-only facts: %s" % ", ".join(str(x) for x in facts))
    _say("FLAGGED: AI fetchers were served different content." if any_flagged
         else "Not flagged.")
    return any_flagged
