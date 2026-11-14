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
    return cfg["identities"][1:]
