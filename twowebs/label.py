"""Layer 5: human labelling workflow. Cases go out as a CSV with the automatic
label hidden, come back labelled, and feed the statistics."""
import csv
import hashlib
import json
import random


LABEL_COLUMNS = ["page_id", "identities", "sample", "url", "ai_only_text",
                 "ai_only_facts", "browser_only_text", "label", "note"]
CONFIRM_COLUMNS = ["page_id", "identity", "url", "ai_only_text", "prompt",
                   "assistant", "repeated", "note"]
REPEATED_VALUES = ("yes", "no", "blocked")
CUT = 800


def _sha1(text):
    return hashlib.sha1((text or "").encode("utf-8")).hexdigest()


def _write_csv(path, columns, rows):
    # utf-8-sig and newline="" so Excel opens the file cleanly.
    with open(path, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=columns, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)


def _read_csv(path):
    """Read a CSV saved by Excel or by us. Tolerates utf-8 with or without BOM,
    Windows-1252, and ';' as delimiter (some Excel locales)."""
    with open(path, "rb") as fh:
        raw = fh.read()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("cp1252", "replace")
    first = text.split("\n", 1)[0]
    delim = ";" if first.count(";") > first.count(",") else ","
    rows = list(csv.DictReader(text.splitlines(True), delimiter=delim))
    return [{(k or "").strip().lower(): (v or "") for k, v in r.items()} for r in rows]


def _facts_text(raw):
    try:
        return "; ".join(json.loads(raw or "[]"))
    except ValueError:
        return raw or ""


def export_labels(db, cfg, path, include=None, max_cases=None, seed=0):
    """One CSV row per (page, distinct AI-only text). Identities that saw the
    same text on the same page are joined with '|'. The automatic label is not
    written, and rows are shuffled so order carries no information."""
    include = list(include) if include else list(cfg["labels"]["headline"])
    marks = ",".join("?" * len(include))
    rows = db.execute(
        "SELECT c.page_id, c.identity, c.ai_only_text, c.ai_only_facts,"
        " c.browser_only_text, p.url, s.sample"
        " FROM comparisons c JOIN pages p ON p.page_id=c.page_id"
        " JOIN sites s ON s.site_id=p.site_id"
        " WHERE c.comparable=1 AND c.auto_label IN (%s)"
        " ORDER BY c.page_id, c.identity" % marks, include).fetchall()
    cases = {}
    order = []
    for r in rows:
        key = (r["page_id"], _sha1(r["ai_only_text"]))
        if key not in cases:
            cases[key] = {
                "page_id": r["page_id"], "identities": [], "sample": r["sample"],
                "url": r["url"], "ai_only_text": r["ai_only_text"] or "",
                "ai_only_facts": _facts_text(r["ai_only_facts"]),
                "browser_only_text": (r["browser_only_text"] or "")[:CUT],
                "label": "", "note": "",
            }
            order.append(key)
        cases[key]["identities"].append(r["identity"])
    out = []
    for key in order:
        c = cases[key]
        c["identities"] = "|".join(c["identities"])
        out.append(c)
    random.Random(seed).shuffle(out)
    if max_cases is not None:
        out = out[:max_cases]
    _write_csv(path, LABEL_COLUMNS, out)
    return len(out)


def import_labels(db, cfg, path, labeller):
    """Store labels from a filled CSV. Blank labels are skipped. A label that is
    not in labels.all aborts the whole import (SystemExit) before anything is
    written."""
    labeller = (labeller or "").strip()
    if not labeller:
        raise SystemExit("import-labels: a labeller name is required")
    allowed = list(cfg["labels"]["all"])
    todo = []
    errors = []
    for i, r in enumerate(_read_csv(path), 2):  # line 1 is the header
        lab = r.get("label", "").strip().lower()
        if not lab:
            continue
        if lab not in allowed:
            errors.append("line %d: unknown label %r" % (i, r.get("label", "").strip()))
            continue
        try:
            page_id = int(r.get("page_id", "").strip())
        except ValueError:
            errors.append("line %d: bad page_id %r" % (i, r.get("page_id")))
            continue
        idents = [x.strip() for x in r.get("identities", "").split("|") if x.strip()]
        if not idents:
            errors.append("line %d: no identities" % i)
            continue
        for ident in idents:
            todo.append((page_id, ident, labeller, lab, r.get("note", "").strip()))
    if errors:
        raise SystemExit("import-labels: %s\nAllowed labels: %s\nNothing was imported."
                         % ("; ".join(errors[:10]), ", ".join(allowed)))
    for t in todo:
        db.execute("INSERT OR REPLACE INTO labels(page_id, identity, labeller, label,"
                   " note) VALUES(?,?,?,?,?)", t)
    db.commit()
    return len(todo)


def kappa(db):
    """Cohen's kappa between the first two labellers (alphabetical), over the
    cases both labelled. A case is a (page, AI-only text) pair, i.e. what the
    labeller saw as one CSV row, so identities that share a text count once.
    Returns {"labellers", "n", "agreement", "kappa"} or None. kappa is None
    inside the dict when chance agreement is 1 (it is undefined then)."""
    names = [r[0] for r in db.execute(
        "SELECT DISTINCT labeller FROM labels ORDER BY labeller")]
    if len(names) < 2:
        return None
    a_name, b_name = names[0], names[1]
    texts = {}
    for r in db.execute("SELECT page_id, identity, ai_only_text FROM comparisons"):
        texts[(r[0], r[1])] = _sha1(r[2])
    got = {a_name: {}, b_name: {}}
    for r in db.execute(
            "SELECT page_id, identity, labeller, label FROM labels"
            " WHERE labeller IN (?,?) ORDER BY page_id, identity",
            (a_name, b_name)):
        case = (r[0], texts.get((r[0], r[1]), r[1]))
        got[r[2]].setdefault(case, r[3])
    both = sorted(set(got[a_name]) & set(got[b_name]), key=str)
    n = len(both)
    if n == 0:
        return None
    pairs = [(got[a_name][c], got[b_name][c]) for c in both]
    po = sum(1 for x, y in pairs if x == y) / float(n)
    pe = 0.0
    for lab in set(x for x, _ in pairs) | set(y for _, y in pairs):
        pa = sum(1 for x, _ in pairs if x == lab) / float(n)
        pb = sum(1 for _, y in pairs if y == lab) / float(n)
        pe += pa * pb
    k = None if abs(1.0 - pe) < 1e-12 else (po - pe) / (1.0 - pe)
    return {"labellers": [a_name, b_name], "n": n, "agreement": po, "kappa": k}


def export_confirm(db, cfg, path, max_cases=30):
    """Cases for the end-to-end check with real assistants. Pages where at least
    two labellers gave a headline label; if there are none, the flagged
    comparisons instead. One row per page, sampled with a fixed seed."""
    headline = list(cfg["labels"]["headline"])
    marks = ",".join("?" * len(headline))
    ident_order = {i["name"]: n for n, i in enumerate(cfg["identities"])}
    sql = ("SELECT c.page_id, c.identity, c.ai_only_text, p.url"
           " FROM comparisons c JOIN pages p ON p.page_id=c.page_id")
    confirmed = db.execute(
        sql + " WHERE c.comparable=1 AND (SELECT COUNT(DISTINCT l.labeller) FROM labels l"
        " WHERE l.page_id=c.page_id AND l.identity=c.identity AND l.label IN (%s)) >= 2"
        % marks, headline).fetchall()
    chosen = confirmed or db.execute(
        sql + " WHERE c.comparable=1 AND c.flagged=1").fetchall()
    per_page = {}
    for r in chosen:
        cur = per_page.get(r["page_id"])
        if cur is None or (ident_order.get(r["identity"], 99)
                           < ident_order.get(cur["identity"], 99)):
            per_page[r["page_id"]] = r
    pages = sorted(per_page)
    random.Random(0).shuffle(pages)
    if max_cases is not None:
        pages = pages[:max_cases]
    out = []
    for pid in pages:
        r = per_page[pid]
        out.append({"page_id": pid, "identity": r["identity"], "url": r["url"],
                    "ai_only_text": (r["ai_only_text"] or "")[:CUT],
                    "prompt": "", "assistant": "", "repeated": "", "note": ""})
    _write_csv(path, CONFIRM_COLUMNS, out)
    return len(out)


def import_confirm(db, path):
    """Read back a filled confirmation CSV. Rows with a blank `repeated` are
    skipped; `repeated` must be yes, no or blocked and `assistant` is needed."""
    todo = []
    errors = []
    for i, r in enumerate(_read_csv(path), 2):
        rep = r.get("repeated", "").strip().lower()
        if not rep:
            continue
        if rep not in REPEATED_VALUES:
            errors.append("line %d: repeated must be yes, no or blocked, got %r"
                          % (i, r.get("repeated", "").strip()))
            continue
        assistant = r.get("assistant", "").strip()
        if not assistant:
            errors.append("line %d: assistant is missing" % i)
            continue
        try:
            page_id = int(r.get("page_id", "").strip())
        except ValueError:
            errors.append("line %d: bad page_id %r" % (i, r.get("page_id")))
            continue
        note = r.get("note", "").strip()
        prompt = r.get("prompt", "").strip()
        if prompt:
            note = ("prompt: %s | %s" % (prompt, note)) if note else "prompt: " + prompt
        todo.append((page_id, r.get("identity", "").strip(), assistant, rep, note))
    if errors:
        raise SystemExit("import-confirm: %s\nNothing was imported." % "; ".join(errors[:10]))
    for t in todo:
        db.execute("INSERT OR REPLACE INTO confirmations(page_id, identity, assistant,"
                   " repeated, note) VALUES(?,?,?,?,?)", t)
    db.commit()
    return len(todo)
