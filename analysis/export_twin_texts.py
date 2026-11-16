"""Export the extracted text of every Markdown twin and of the human page it
was compared with, so the fact comparison can be re-run without the 2 GB crawl
database.  python analysis/export_twin_texts.py  ->  release/twin_texts.json.gz

Each record: site rank, page kind, and the extracted text of each fetch under
the browser and under the browser + `Accept: text/markdown` identity.
Re-check with:  python analysis/export_twin_texts.py --check
"""
import gzip
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from twowebs import config as C, diff, extract, store  # noqa: E402

OUT = os.path.join(C.ROOT, "release", "twin_texts.json.gz")
UNLISTED = re.compile(r"xham|sex|porn|fap|redwap|simpcity|kir2kos|xhlink|theyarehuge", re.I)
HARD = tuple(t + ":" for t in extract.HARD_TYPES)


def hard(text):
    return set(x for x in diff._facts(text) if x.startswith(HARD))


def export():
    cfg = C.load()
    db = store.connect(cfg["db_path"])
    rows = db.execute(
        "SELECT s.domain, s.rank, p.page_id, p.kind, p.url FROM comparisons c"
        " JOIN pages p ON p.page_id=c.page_id JOIN sites s ON s.site_id=p.site_id"
        " WHERE s.sample='trigger' AND c.identity='browser-md' AND c.comparable=1"
        " AND c.ai_markdown=1 ORDER BY s.rank").fetchall()
    out = []
    for r in rows:
        if UNLISTED.search(r["domain"]):
            continue
        rec = {"domain": r["domain"], "rank": r["rank"], "page": r["kind"], "url": r["url"]}
        for key, ident in (("browser", "browser"), ("markdown", "browser-md")):
            rec[key] = [t[0] or "" for t in db.execute(
                "SELECT e.text FROM extracts e JOIN fetches f ON f.fetch_id=e.fetch_id"
                " WHERE f.page_id=? AND f.identity=? AND e.outcome='ok' ORDER BY f.rep",
                (r["page_id"], ident))]
        out.append(rec)
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with gzip.open(OUT, "wt", encoding="utf-8") as f:
        json.dump(out, f)
    print(len(out), "twin pages written to", OUT, "(%.1f MB)" % (os.path.getsize(OUT) / 1e6))


def check():
    """Recompute fact loss per page from the released text alone."""
    with gzip.open(OUT, "rt", encoding="utf-8") as f:
        recs = json.load(f)
    for r in recs:
        b, a = [hard(t) for t in r["browser"]], [hard(t) for t in r["markdown"]]
        if len(b) < 2 or not a:
            continue
        stable = set.intersection(*b)
        lost = stable - set.union(*a)
        print("%-28s %-5s facts=%4d lost=%4d" % (r["domain"], r["page"], len(stable), len(lost)))


if __name__ == "__main__":
    check() if "--check" in sys.argv else export()
