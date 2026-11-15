"""Layer 0: configuration. Everything tunable lives in config/settings.json."""
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load(path=None):
    path = path or os.path.join(ROOT, "config", "settings.json")
    with open(path, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    if not os.path.isabs(cfg["db_path"]):
        cfg["db_path"] = os.path.join(ROOT, cfg["db_path"])
    names = [i["name"] for i in cfg["identities"]]
    assert len(names) == len(set(names)), "identity names must be unique"
    assert cfg["identities"][0]["kind"] == "browser", "first identity must be the browser"
    return cfg


def browser(cfg):
    return cfg["identities"][0]


def ai_identities(cfg):
    """Every non-browser identity that can appear in the database: the
    User-Agent identities plus the trigger-test identities."""
    out = list(cfg["identities"][1:])
    seen = set(i["name"] for i in cfg["identities"])
    for i in cfg.get("trigger_identities", []):
        if i["name"] not in seen:
            seen.add(i["name"])
            out.append(i)
    return out


def trigger_set(cfg):
    """Identities for the trigger crawl: browser, an identical control, and the
    2x2 of User-Agent (browser / AI) by Accept header (HTML / Markdown)."""
    by_name = dict((i["name"], i) for i in cfg["identities"])
    out = [browser(cfg)]
    for i in cfg.get("trigger_identities", []):
        if i.get("same_as"):
            out.append(by_name[i["same_as"]])
        else:
            out.append(i)
    return out
