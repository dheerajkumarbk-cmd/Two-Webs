"""Layer 1: storage. One SQLite file holds every stage, so each later layer
can be re-run without re-crawling."""
import hashlib
import json
import lzma
import os
import sqlite3
import zlib

SCHEMA = """
CREATE TABLE IF NOT EXISTS sites(
  site_id INTEGER PRIMARY KEY, domain TEXT, sample TEXT, rank INTEGER,
  crawled INTEGER DEFAULT 0, note TEXT, UNIQUE(domain, sample));
CREATE TABLE IF NOT EXISTS pages(
  page_id INTEGER PRIMARY KEY, site_id INTEGER, url TEXT, kind TEXT,
  UNIQUE(site_id, url));
CREATE TABLE IF NOT EXISTS fetches(
  fetch_id INTEGER PRIMARY KEY, page_id INTEGER, identity TEXT, rep INTEGER,
  ts REAL, status INTEGER, final_url TEXT, content_type TEXT, n_bytes INTEGER,
  truncated INTEGER, error TEXT, headers TEXT, body BLOB);
CREATE INDEX IF NOT EXISTS fetches_page ON fetches(page_id);
CREATE TABLE IF NOT EXISTS extracts(
  fetch_id INTEGER PRIMARY KEY, outcome TEXT, n_words INTEGER, text TEXT);
CREATE TABLE IF NOT EXISTS comparisons(
  page_id INTEGER, identity TEXT, comparable INTEGER, reason TEXT,
  noise_sim REAL, cross_sim REAL, n_ai_only INTEGER, n_browser_only INTEGER,
  ai_only_text TEXT, browser_only_text TEXT, ai_only_facts TEXT,
  auto_label TEXT, flagged INTEGER, PRIMARY KEY(page_id, identity));
CREATE TABLE IF NOT EXISTS labels(
  page_id INTEGER, identity TEXT, labeller TEXT, label TEXT, note TEXT,
  PRIMARY KEY(page_id, identity, labeller));
CREATE TABLE IF NOT EXISTS confirmations(
  page_id INTEGER, identity TEXT, assistant TEXT, repeated TEXT, note TEXT,
  PRIMARY KEY(page_id, identity, assistant));
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS bodies(sha TEXT PRIMARY KEY, data BLOB);
"""

# Response bodies are stored once per distinct content (most sites send every
# identity the same bytes) in `bodies`, LZMA-compressed; `fetches.body_sha`
# points at them. `fetches.body` (zlib) is the older per-fetch format and is
# still read; `compact` converts it.
LZMA_PRESET = 1


def connect(path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    db.executescript(SCHEMA)
    cols = [r[1] for r in db.execute("PRAGMA table_info(fetches)").fetchall()]
    if "body_sha" not in cols:
        db.execute("ALTER TABLE fetches ADD COLUMN body_sha TEXT")
    cols = [r[1] for r in db.execute("PRAGMA table_info(comparisons)").fetchall()]
    for name, kind in (("ai_markdown", "INTEGER"), ("n_facts_browser", "INTEGER"),
                       ("n_facts_lost", "INTEGER"), ("n_facts_added", "INTEGER"),
                       ("facts_lost", "TEXT"), ("facts_added", "TEXT")):
        if name not in cols:
            db.execute("ALTER TABLE comparisons ADD COLUMN %s %s" % (name, kind))
    db.commit()
    return db


def _put_body(db, body):
    """Store a body once and return its key (None for an empty body)."""
    if not body:
        return None
    sha = hashlib.sha256(body).hexdigest()
    if db.execute("SELECT 1 FROM bodies WHERE sha=?", (sha,)).fetchone() is None:
        db.execute("INSERT INTO bodies(sha, data) VALUES(?,?)",
                   (sha, lzma.compress(body, preset=LZMA_PRESET)))
    return sha


def compact(db):
    """Convert old per-fetch zlib bodies to the shared format and shrink the
    file. Lossless; safe to run more than once."""
    ids = [r[0] for r in db.execute(
        "SELECT fetch_id FROM fetches WHERE body IS NOT NULL").fetchall()]
    try:
        for n, fid in enumerate(ids, 1):
            blob = db.execute("SELECT body FROM fetches WHERE fetch_id=?", (fid,)).fetchone()[0]
            body = zlib.decompress(blob) if blob else b""
            db.execute("UPDATE fetches SET body=NULL, body_sha=? WHERE fetch_id=?",
                       (_put_body(db, body), fid))
            if n % 500 == 0:
                db.commit()
        db.commit()
    except BaseException:
        db.rollback()
        raise
    db.execute("VACUUM")
    return len(ids)


def add_sites(db, sample, domains):
    """domains: iterable of (rank, domain)."""
    n = 0
    for rank, domain in domains:
        cur = db.execute(
            "INSERT OR IGNORE INTO sites(domain, sample, rank) VALUES(?,?,?)",
            (domain, sample, rank))
        n += cur.rowcount
    db.commit()
    return n


def pending_sites(db, sample=None, limit=None):
    q = "SELECT * FROM sites WHERE crawled=0"
    args = []
    if sample:
        q += " AND sample=?"
        args.append(sample)
    q += " ORDER BY sample, rank"
    if limit:
        q += " LIMIT ?"
        args.append(limit)
    return db.execute(q, args).fetchall()


def save_site(db, site_id, pages, note=None):
    """pages: list of {url, kind, fetches:[fetch dict]}. All or nothing: on any
    failure the site's partial rows are rolled back, so a later commit cannot
    persist them while the site stays uncrawled."""
    try:
        for p in pages:
            db.execute("INSERT OR IGNORE INTO pages(site_id, url, kind) VALUES(?,?,?)",
                       (site_id, p["url"], p["kind"]))
            page_id = db.execute("SELECT page_id FROM pages WHERE site_id=? AND url=?",
                                 (site_id, p["url"])).fetchone()[0]
            for f in p["fetches"]:
                db.execute(
                    "INSERT INTO fetches(page_id, identity, rep, ts, status, final_url,"
                    " content_type, n_bytes, truncated, error, headers, body_sha)"
                    " VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                    (page_id, f["identity"], f["rep"], f["ts"], f["status"],
                     f["final_url"], f["content_type"], len(f["body"]), f["truncated"],
                     f["error"], json.dumps(f["headers"]), _put_body(db, f["body"])))
        db.execute("UPDATE sites SET crawled=1, note=? WHERE site_id=?", (note, site_id))
        db.commit()
    except BaseException:
        db.rollback()
        raise


def page_fetches(db, page_id):
    rows = db.execute("SELECT * FROM fetches WHERE page_id=? ORDER BY fetch_id",
                      (page_id,)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        sha = d.pop("body_sha", None)
        if sha:
            blob = db.execute("SELECT data FROM bodies WHERE sha=?", (sha,)).fetchone()[0]
            d["body"] = lzma.decompress(blob)
        else:
            d["body"] = zlib.decompress(d["body"]) if d["body"] else b""
        d["headers"] = json.loads(d["headers"] or "{}")
        out.append(d)
    return out


def set_meta(db, key, value):
    db.execute("INSERT OR REPLACE INTO meta(key, value) VALUES(?,?)",
               (key, json.dumps(value)))
    db.commit()
