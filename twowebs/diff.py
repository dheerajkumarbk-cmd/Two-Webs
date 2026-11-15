"""Layer 4: comparison. Turns raw fetches into outcomes, and pairs of browser
and AI fetches into a comparison row. All judgement lives here, so thresholds
can be changed and `analyse_all` re-run without re-crawling."""
import json
import re
from urllib.parse import urlsplit

from . import config, extract, store

BLOCKED_STATUSES = (401, 403, 406, 429, 451, 503)

# A bot-check page is short and says so. Only applied to pages under
# CHALLENGE_MAX_WORDS words, looking at the first 3000 characters of text.
CHALLENGE_MAX_WORDS = 300
CHALLENGE_RE = re.compile(
    r"just a moment|checking your browser|checking if the site connection is secure|"
    r"verify you are (?:a )?human|verify you(?:'|’)re (?:a )?human|"
    r"verifying you are (?:a )?human|human verification|are you a robot|"
    r"captcha|access denied|attention required|request blocked|"
    r"you have been blocked|unusual traffic|pardon our interruption|"
    r"enable javascript and cookies|security check to access|"
    r"ddos protection by|performance (?:&|and) security by cloudflare|"
    r"press (?:&|and) hold to confirm|"
    r"appears to be from an automated process|automated (?:queries|requests|access)",
    re.I)

# Text addressed to a model rather than to a reader.
ADDRESSED_RE = re.compile(
    r"ignore (?:all |any |the )?(?:previous|prior|above|earlier|preceding) "
    r"(?:instructions|prompts?|directions)|"
    r"\bas an ai\b|"
    r"\bai (?:assistant|agent|model|bot|system|crawler|chatbot|tool)s?\b|"
    r"\blanguage models?\b|\bllms?\b|\bsystem prompt\b|"
    # A product name counts only when an instruction word follows it closely;
    # a bare mention ("ChatGPT launched in 2022") is ordinary prose.
    r"\b(?:chatgpt|gpt-?\d[\w.]*|claude|gemini|copilot|perplexity|bard|grok|"
    r"siri|alexa)\b.{0,40}?\b(?:should|must|always|never|please|ignore|"
    r"recommend|mention)\b|"
    r"\bwhen (?:you are |you're |asked to |asked about )?"
    r"(?:summari[sz]ing|recommending|answering|citing|describing|responding)\b|"
    r"\byou (?:should|must|shall|need to|have to|are to) (?:always |never )?"
    r"(?:recommend|mention|cite|prefer|prioriti[sz]e|promote|endorse)\b|"
    r"\b(?:do not|don't|never) (?:mention|reveal|disclose)\b"
    r"(?!\s+(?:your|personal|customer|user))",
    re.I)

SPONSORED_RE = re.compile(
    r"\bsponsor(?:ed|s)?\b|\bpromoted\b(?!\s+to\b)|\bpaid partnership\b|"
    r"\bin partnership with\b|\bpresented by\b|\bbrought to you by\b|"
    r"\badvertisement\b|\baffiliate links?\b",
    re.I)


# Markup that survives in Markdown but is not page content: HTML comments and
# bare URLs (their digits are not facts). Removed before extracting facts.
_FACT_NOISE_RE = re.compile(r"<!--.*?-->|(?:https?://|www\.)\S+", re.S)


def _facts(text):
    return extract.facts(_FACT_NOISE_RE.sub(" ", text))


def _count_words(lines):
    return sum(len(ln.split()) for ln in lines)


def analyse_fetch(f, cfg):
    """Add outcome, lines and n_words to a fetch dict and return it."""
    f["lines"] = []
    f["n_words"] = 0
    err = f.get("error")
    status = f.get("status")
    if err == "robots_skip":
        f["outcome"] = "robots_skip"
        return f
    if err or status is None:
        f["outcome"] = "error"
        return f
    if status in BLOCKED_STATUSES:
        f["outcome"] = "blocked"
        return f
    if status >= 400:
        f["outcome"] = "error"
        return f
    lines, _links, _is_html = extract.text_lines(f.get("body") or b"",
                                                 f.get("content_type"))
    f["lines"] = lines
    f["is_html"] = bool(_is_html)
    f["n_words"] = _count_words(lines)
    head = "\n".join(lines)[:3000]
    if f["n_words"] < CHALLENGE_MAX_WORDS and CHALLENGE_RE.search(head):
        f["outcome"] = "challenge"
    elif f["n_words"] < cfg["analysis"]["min_page_words"]:
        f["outcome"] = "thin"
    else:
        f["outcome"] = "ok"
    return f


def _jaccard(a, b):
    if not a and not b:
        return 1.0
    return len(a & b) / float(len(a | b))


def _prep(f, min_words):
    """Sentences and normalised full text for one analysed fetch (memoised on
    the dict, which does not change any result)."""
    c = f.get("_prep")
    if c is not None and c[0] == min_words:
        return c[1]
    lines = f.get("lines") or []
    sents = extract.sentences(lines, min_words)
    d = {
        "sents": sents,
        "set": set(n for n, _o in sents),
        "full": " " + extract.norm(" ".join(lines)) + " ",
        "text": "\n".join(lines),
    }
    f["_prep"] = (min_words, d)
    return d


def _not_comparable(reason, auto_label=None):
    return {
        "comparable": 0, "reason": reason, "noise_sim": None, "cross_sim": None,
        "n_ai_only": None, "n_browser_only": None, "ai_only_text": "",
        "browser_only_text": "", "ai_only_facts": [], "auto_label": auto_label,
        "flagged": 0, "ai_markdown": None, "n_facts_browser": None,
        "n_facts_lost": None, "n_facts_added": None, "facts_lost": [], "facts_added": [],
    }


def _contains(prep, n):
    """True if normalised sentence n occurs in this fetch's normalised text.
    An exact sentence match is the fast path; otherwise substring search."""
    return n in prep["set"] or (" " + n + " ") in prep["full"]


def _exclusive(first, others, absent_from, min_words):
    """Sentences of `first` found in every one of `others` and in none of
    `absent_from` (substring match on normalised full text)."""
    others = [_prep(o, min_words) for o in others]
    absent = [_prep(a, min_words) for a in absent_from]
    out = []
    for n, orig in _prep(first, min_words)["sents"]:
        if not all(_contains(o, n) for o in others):
            continue
        if any(_contains(a, n) for a in absent):
            continue
        out.append(orig)
    return out


def _url_key(f):
    """Host (without www.) and path of the final URL; query string ignored."""
    u = urlsplit(f.get("final_url") or f.get("url") or "")
    host = (u.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    return host + (u.path or "/").rstrip("/")


def compare(browser_fetches, ai_fetches, cfg):
    """Compare analysed browser fetches with analysed fetches of one AI
    identity. Pure: the result depends only on the inputs."""
    an = cfg["analysis"]
    min_words = an["min_sentence_words"]
    if len(browser_fetches) < 2:
        return _not_comparable("browser_fetches_missing")
    if not ai_fetches:
        return _not_comparable("ai_fetches_missing")
    bad = [f for f in browser_fetches if f["outcome"] != "ok"]
    if bad:
        return _not_comparable("browser_" + bad[0]["outcome"])
    bad = [f for f in ai_fetches if f["outcome"] != "ok"]
    if bad:
        # If any fetch was blocked or challenged, that is the reason reported.
        pref = [f for f in bad if f["outcome"] in ("blocked", "challenge")]
        pick = (pref or bad)[0]
        label = "blocked_or_challenged" if pref else None
        return _not_comparable("ai_" + pick["outcome"], label)

    b_prep = [_prep(f, min_words) for f in browser_fetches]
    a_prep = [_prep(f, min_words) for f in ai_fetches]

    ai_only = _exclusive(ai_fetches[0], ai_fetches[1:], browser_fetches, min_words)
    br_only = _exclusive(browser_fetches[0], browser_fetches[1:], ai_fetches, min_words)

    noise_sim = _jaccard(b_prep[0]["set"], b_prep[1]["set"])
    ai_core = set.intersection(*[p["set"] for p in a_prep])
    br_core = set.intersection(*[p["set"] for p in b_prep])
    cross_sim = _jaccard(ai_core, br_core)

    fa = _fact_account(b_prep, a_prep)
    snippet = an["snippet_chars"]
    ai_text = "\n".join(ai_only)
    br_text = "\n".join(br_only)

    browser_facts = set()
    for p in b_prep:
        browser_facts |= _facts(p["text"])
    ai_facts = sorted(_facts(ai_text) - browser_facts) if ai_only else []

    n_ai, n_br = len(ai_only), len(br_only)
    k = an["min_sentences_for_more_or_less"]
    hard = tuple(t + ":" for t in extract.HARD_TYPES)
    ai_hard = set(x.split(":", 1)[0] for x in ai_facts if x.startswith(hard))
    # A fact "differs" only when each side states a hard fact of the same type
    # that the other side lacks (e.g. two different prices). Extra AI-only text
    # that merely contains a number is "AI sees more".
    br_hard = set()
    if br_only and ai_hard:
        ai_all = set()
        for p in a_prep:
            ai_all |= _facts(p["text"])
        br_hard = set(x.split(":", 1)[0] for x in _facts(br_text) - ai_all
                      if x.startswith(hard))
    if ai_only and ADDRESSED_RE.search(ai_text):
        label = "addressed_to_ai"
    elif ai_only and SPONSORED_RE.search(ai_text):
        label = "sponsored_ai_only"
    elif (n_ai or n_br) and _url_key(browser_fetches[0]) != _url_key(ai_fetches[0]):
        # The two identities ended on different addresses (country or device
        # redirect). Different content is then expected; not a headline case.
        label = "different_url"
    elif ai_hard & br_hard:
        label = "facts_differ"
    elif ai_hard or n_ai >= k:
        label = "ai_sees_more"
    elif n_br >= k:
        label = "ai_sees_less"
    elif n_ai or n_br:
        label = "cosmetic"
    else:
        label = "same"

    return {
        "comparable": 1, "reason": None,
        "noise_sim": noise_sim, "cross_sim": cross_sim,
        "n_ai_only": n_ai, "n_browser_only": n_br,
        "ai_only_text": ai_text[:snippet], "browser_only_text": br_text[:snippet],
        "ai_only_facts": ai_facts,
        "auto_label": label,
        "flagged": 1 if label in cfg["labels"]["headline"] else 0,
        "ai_markdown": 1 if all(f.get("is_html") is False for f in ai_fetches) else 0,
        "n_facts_browser": len(fa["stable_b"]),
        "n_facts_lost": len(fa["lost"]), "n_facts_added": len(fa["added"]),
        "facts_lost": sorted(fa["lost"])[:40], "facts_added": sorted(fa["added"])[:40],
    }


def _fact_account(b_prep, a_prep):
    """Hard facts (prices, percents, dates, numbers) in both directions.
    lost: stated in every browser fetch and in no AI fetch.
    added: stated in every AI fetch and in no browser fetch."""
    hard = tuple(t + ":" for t in extract.HARD_TYPES)
    b = [set(x for x in _facts(p["text"]) if x.startswith(hard)) for p in b_prep]
    a = [set(x for x in _facts(p["text"]) if x.startswith(hard)) for p in a_prep]
    stable_b, stable_a = set.intersection(*b), set.intersection(*a)
    return {"stable_b": stable_b, "lost": stable_b - set.union(*a),
            "added": stable_a - set.union(*b)}


def _by_rep(fetches):
    return sorted(fetches, key=lambda f: (f.get("rep") or 0, f.get("fetch_id") or 0))


def analyse_all(db, cfg):
    """Clear and rebuild `extracts` and `comparisons` from the raw fetches."""
    db.execute("DELETE FROM extracts")
    db.execute("DELETE FROM comparisons")
    db.commit()
    browser_name = config.browser(cfg)["name"]
    ai_names = [i["name"] for i in config.ai_identities(cfg)]
    page_ids = [r[0] for r in db.execute("SELECT page_id FROM pages ORDER BY page_id")]
    total = len(page_ids)
    n_cmp = 0
    for i, page_id in enumerate(page_ids, 1):
        fetches = store.page_fetches(db, page_id)
        by_id = {}
        for f in fetches:
            analyse_fetch(f, cfg)
            db.execute(
                "INSERT OR REPLACE INTO extracts(fetch_id, outcome, n_words, text)"
                " VALUES(?,?,?,?)",
                (f["fetch_id"], f["outcome"], f["n_words"], "\n".join(f["lines"])))
            by_id.setdefault(f["identity"], []).append(f)
        browser = _by_rep(by_id.get(browser_name, []))
        for name in ai_names:
            ai = _by_rep(by_id.get(name, []))
            if not ai:
                continue
            r = compare(browser, ai, cfg)
            db.execute(
                "INSERT OR REPLACE INTO comparisons(page_id, identity, comparable,"
                " reason, noise_sim, cross_sim, n_ai_only, n_browser_only,"
                " ai_only_text, browser_only_text, ai_only_facts, auto_label, flagged,"
                " ai_markdown, n_facts_browser, n_facts_lost, n_facts_added,"
                " facts_lost, facts_added)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (page_id, name, r["comparable"], r["reason"], r["noise_sim"],
                 r["cross_sim"], r["n_ai_only"], r["n_browser_only"],
                 r["ai_only_text"], r["browser_only_text"],
                 json.dumps(r["ai_only_facts"]), r["auto_label"], r["flagged"],
                 r["ai_markdown"], r["n_facts_browser"], r["n_facts_lost"],
                 r["n_facts_added"], json.dumps(r["facts_lost"]),
                 json.dumps(r["facts_added"])))
            n_cmp += 1
        if i % 200 == 0:
            db.commit()
            print("analyse: %d/%d pages" % (i, total))
    db.commit()
    print("analyse: %d pages, %d comparisons" % (total, n_cmp))
    return n_cmp
