"""Layer 3: extraction. Turns a raw response body into text lines, sentences
and mechanically extracted facts. Standard library only, no model involved."""
import html
import re
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

SKIP = {"script", "style", "svg", "template"}
BLOCK = {
    "p", "div", "li", "br", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "td", "th",
    "section", "article", "header", "footer", "nav", "aside", "ul", "ol", "table",
    "blockquote", "pre", "title", "figcaption", "dd", "dt", "main", "form",
    "button", "label", "summary", "details", "hr",
}


class _Parser(HTMLParser):
    def __init__(self):
        HTMLParser.__init__(self, convert_charrefs=True)
        self.out = []
        self.skip = 0
        self.links = []

    def handle_starttag(self, tag, attrs):
        if tag in SKIP:
            self.skip += 1
            return
        if tag == "meta":
            d = dict(attrs)
            name = (d.get("name") or d.get("property") or "").lower()
            if name in ("description", "og:description") and d.get("content"):
                self.out.append("\n" + d["content"] + "\n")
        elif tag == "a":
            href = dict(attrs).get("href")
            if href:
                self.links.append(href)
        if tag in BLOCK:
            self.out.append("\n")

    def handle_endtag(self, tag):
        if tag in SKIP:
            self.skip = max(0, self.skip - 1)
            return
        if tag in BLOCK:
            self.out.append("\n")

    def handle_data(self, data):
        if not self.skip:
            self.out.append(data)


def decode(body, content_type):
    m = re.search(r"charset=([\w-]+)", content_type or "", re.I)
    if not m:
        m = re.search(rb"charset=[\"']?([\w-]+)", body[:4096], re.I)
    enc = m.group(1) if m else "utf-8"
    if isinstance(enc, bytes):
        enc = enc.decode("ascii", "replace")
    try:
        return body.decode(enc, "replace")
    except LookupError:
        return body.decode("utf-8", "replace")


def looks_like_html(raw, content_type):
    if "html" in (content_type or "").lower():
        return True
    head = raw[:2000].lower()
    return "<html" in head or "<!doctype html" in head or "<body" in head


def _markdown_to_text(raw):
    raw = html.unescape(raw)
    raw = re.sub(r"(?m)^\s{0,3}\[[^\]]+\]:\s+\S+.*$", "", raw)  # reference definitions
    raw = re.sub(r"!\[([^\]]*)\]\([^)]*\)", r"\1", raw)
    raw = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", raw)
    raw = re.sub(r"\[([^\]]+)\]\[[^\]]*\]", r"\1", raw)  # reference-style links
    raw = re.sub(r"(?m)^\s{0,3}(#{1,6}|>|[-*+]|\d+\.)\s+", "", raw)
    # Markdown backslash escapes (12\.3% or \$5) would hide a fact from the patterns.
    raw = re.sub(r"\\([\\`*_{}\[\]()#+\-.!|>~$%&<=:])", r"\1", raw)
    raw = re.sub(r"[*_`|]+", " ", raw)
    return raw


def _clean_lines(text):
    lines = []
    for ln in text.split("\n"):
        ln = re.sub(r"\s+", " ", ln).strip()
        if ln:
            lines.append(ln)
    return lines


def text_lines(body, content_type):
    """Return (lines, links, is_html) for one response body."""
    raw = decode(body, content_type)
    if looks_like_html(raw, content_type):
        p = _Parser()
        try:
            p.feed(raw)
            p.close()
        except Exception:
            pass
        return _clean_lines("".join(p.out)), p.links, True
    return _clean_lines(_markdown_to_text(raw)), [], False


def norm(s):
    """Normalise text so that HTML and Markdown renderings of the same
    sentence compare equal."""
    s = s.lower()
    s = re.sub(r"(?<!\d)\.|\.(?!\d)", " ", s)
    s = re.sub(r"[^\w$€£₹¥%.]+", " ", s, flags=re.UNICODE)
    s = s.replace("_", " ")
    return re.sub(r"\s+", " ", s).strip()


def sentences(lines, min_words):
    """Return list of (normalised, original) for sentences long enough to count."""
    out = []
    seen = set()
    for ln in lines:
        for part in re.split(r"(?<=[.!?])\s+", ln):
            n = norm(part)
            if len(n.split()) >= min_words and n not in seen:
                seen.add(n)
                out.append((n, part))
    return out


_MONTH = (r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|"
          r"jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|"
          r"dec(?:ember)?)")
_FACT_PATTERNS = [
    ("price", re.compile(r"[$€£₹¥]\s?\d[\d,]*(?:\.\d+)?|\b\d[\d,]*(?:\.\d+)?\s?"
                         r"(?:usd|eur|gbp|inr|dollars|euros|pounds|rupees)\b", re.I)),
    ("percent", re.compile(r"\b\d+(?:\.\d+)?\s?(?:%|percent\b)", re.I)),
    ("date", re.compile(
        r"\b" + _MONTH + r"\.?\s+\d{1,2}(?:st|nd|rd|th)?,?\s+\d{4}\b|"
        r"\b\d{1,2}(?:st|nd|rd|th)?\s+" + _MONTH + r"\.?,?\s+\d{4}\b|"
        r"\b\d{4}-\d{2}-\d{2}\b|\b\d{1,2}/\d{1,2}/\d{2,4}\b", re.I)),
    ("number", re.compile(r"\b\d{1,3}(?:,\d{3})+(?:\.\d+)?\b|\b\d+\.\d+\b|"
                          r"\b(?!(?:19|20)\d\d\b)\d{3,}\b")),  # bare years are not facts
]
_ENTITY = re.compile(r"\b(?:[A-Z][a-zA-Z&'-]+\s+){1,3}[A-Z][a-zA-Z&'-]+\b")
HARD_TYPES = ("price", "percent", "date", "number")


def facts(text):
    """Mechanically extract facts as a set of 'type:value' strings."""
    out = set()
    taken = []          # spans already claimed, so $150,000 is a price and not also a number
    for kind, pat in _FACT_PATTERNS:
        spans = []
        for mo in pat.finditer(text):
            m = mo.group(0)
            if kind == "number" and any(mo.start() < e and s < mo.end() for s, e in taken):
                continue
            spans.append(mo.span())
            v = re.sub(r"[\s,]+", "", m.lower())
            # Not facts: runs of zeros (colour codes, padding) and long bare
            # digit strings (identifiers, timestamps, phone numbers).
            if kind == "number" and (not v.strip("0.") or
                                     (v.isdigit() and len(v) > 7 and "," not in m)):
                continue
            out.add(kind + ":" + v)
        taken.extend(spans)
    for m in _ENTITY.findall(text):
        out.add("entity:" + re.sub(r"\s+", " ", m.lower()))
    return out


def pick_inner_link(links, base_url):
    """Deterministic rule for the inner page: the first same-host link, in
    document order, whose path is at least 15 characters long."""
    base = urlsplit(base_url)
    for href in links:
        if href.startswith(("#", "mailto:", "javascript:", "tel:")):
            continue
        u = urlsplit(urljoin(base_url, href))
        if u.scheme not in ("http", "https") or u.hostname != base.hostname:
            continue
        if len(u.path) < 15:
            continue
        if re.search(r"\.(pdf|jpg|jpeg|png|gif|zip|mp4|mp3|xml|css|js)$", u.path, re.I):
            continue
        if re.search(r"login|signin|sign-in|account|cart|checkout|logout", u.path, re.I):
            continue
        return u._replace(fragment="").geturl()
    return None
