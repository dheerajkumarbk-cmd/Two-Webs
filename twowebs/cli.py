"""Command line interface: python -m twowebs <command>."""
import argparse
import asyncio
import json
import os
import sys
from urllib.parse import urlsplit

from . import config, store


def _db(cfg):
    return store.connect(cfg["db_path"])


def _resolve_input(path):
    """A relative input path is tried from the current directory, then from the
    project root."""
    if os.path.isabs(path) or os.path.exists(path):
        return path
    alt = os.path.join(config.ROOT, path)
    return alt if os.path.exists(alt) else path


def _ensure_parent(path):
    parent = os.path.dirname(os.path.abspath(path))
    os.makedirs(parent, exist_ok=True)


def clean_domain(text):
    """'https://Example.com/a/b?x' -> 'example.com'."""
    t = text.strip().strip("\"'").lower()
    if "://" in t:
        t = urlsplit(t).netloc
    t = t.split("/")[0].split("?")[0].split("#")[0]
    if "@" in t:
        t = t.rsplit("@", 1)[1]
    return t.strip(".")


def parse_sample(lines):
    """Lines are 'domain' or 'rank,domain'. Blank lines and # lines are skipped.
    Rank defaults to line order. Returns [(rank, domain)] without duplicates."""
    out = []
    seen = set()
    n = 0
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        n += 1
        rank = n
        dom = line
        if "," in line:
            first, rest = line.split(",", 1)
            if first.strip().isdigit():
                rank = int(first.strip())
                dom = rest.split(",")[0]
            else:
                dom = first
        dom = clean_domain(dom)
        if not dom or dom in seen:
            continue
        seen.add(dom)
        out.append((rank, dom))
    return out


def cmd_init(args, cfg):
    db = _db(cfg)
    db.close()
    print("database ready: %s" % cfg["db_path"])
    return 0


def cmd_load_sample(args, cfg):
    path = _resolve_input(args.file)
    with open(path, "r", encoding="utf-8-sig") as f:
        rows = parse_sample(f.read().splitlines())
    if args.limit:
        rows = rows[:args.limit]
    db = _db(cfg)
    added = store.add_sites(db, args.name, rows)
    print("sample %r: %d domain(s) read, %d new" % (args.name, len(rows), added))
    return 0


def cmd_crawl(args, cfg):
    from . import fetch
    db = _db(cfg)
    if getattr(args, "set", None) == "trigger":
        from . import config as C
        cfg = dict(cfg, identities=C.trigger_set(cfg))
    asyncio.run(fetch.crawl(db, cfg, sample=args.sample, limit=args.limit))
    return 0


def cmd_triggers(args, cfg):
    from . import stats
    db = _db(cfg)
    stats.print_triggers(db, cfg, args.sample)
    return 0


def cmd_compact(args, cfg):
    import os
    db = _db(cfg)
    before = os.path.getsize(cfg["db_path"])
    n = store.compact(db)
    db.close()
    after = os.path.getsize(cfg["db_path"])
    print("compact: %d fetch(es) converted, %.1f MB -> %.1f MB"
          % (n, before / 1e6, after / 1e6))
    return 0


def cmd_analyse(args, cfg):
    from . import diff
    db = _db(cfg)
    diff.analyse_all(db, cfg)
    return 0


def cmd_stats(args, cfg):
    from . import stats
    db = _db(cfg)
    stats.print_summary(db, cfg)
    if args.json:
        _ensure_parent(args.json)
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(stats.summary(db, cfg), f, indent=2, default=str)
        print("wrote %s" % args.json)
    return 0


def cmd_export_labels(args, cfg):
    from . import label
    db = _db(cfg)
    _ensure_parent(args.file)
    label.export_labels(db, cfg, args.file, include=args.include,
                        max_cases=args.max, seed=args.seed)
    print("wrote %s" % args.file)
    return 0


def cmd_import_labels(args, cfg):
    from . import label
    db = _db(cfg)
    label.import_labels(db, cfg, _resolve_input(args.file), args.labeller)
    return 0


def cmd_export_confirm(args, cfg):
    from . import label
    db = _db(cfg)
    _ensure_parent(args.file)
    label.export_confirm(db, cfg, args.file, max_cases=args.max)
    print("wrote %s" % args.file)
    return 0


def cmd_import_confirm(args, cfg):
    from . import label
    db = _db(cfg)
    label.import_confirm(db, _resolve_input(args.file))
    return 0


def cmd_witness(args, cfg):
    from . import witness
    url = args.url
    if "://" not in url:
        url = "https://" + url
    try:
        results = asyncio.run(witness.witness(url, cfg, only=args.only))
    except ValueError as e:
        print("error: %s" % e, file=sys.stderr)
        return 2
    return 1 if witness.report(url, results) else 0


def build_parser():
    p = argparse.ArgumentParser(prog="twowebs", description="Two Webs research tool")
    p.add_argument("--config", help="path to a settings.json (default: config/settings.json)")
    sub = p.add_subparsers(dest="command")
    sub.required = True

    s = sub.add_parser("init", help="create the database")
    s.set_defaults(func=cmd_init)

    s = sub.add_parser("load-sample", help="load a sample of domains from a text file")
    s.add_argument("--name", required=True)
    s.add_argument("--file", required=True)
    s.add_argument("--limit", type=int)
    s.set_defaults(func=cmd_load_sample)

    s = sub.add_parser("crawl", help="fetch pending sites")
    s.add_argument("--sample")
    s.add_argument("--limit", type=int)
    s.add_argument("--set", choices=["default", "trigger"], default="default",
                   help="identity set: default (User-Agent identities) or trigger "
                        "(browser, control, and the User-Agent x Accept 2x2)")
    s.set_defaults(func=cmd_crawl)

    s = sub.add_parser("triggers", help="summarise the trigger crawl: Markdown twins, "
                                        "fact loss and gain, control floor")
    s.add_argument("--sample", required=True)
    s.set_defaults(func=cmd_triggers)

    s = sub.add_parser("compact", help="convert old page storage to the smaller shared format")
    s.set_defaults(func=cmd_compact)

    s = sub.add_parser("analyse", help="extract and compare all fetched pages")
    s.set_defaults(func=cmd_analyse)

    s = sub.add_parser("stats", help="print the summary")
    s.add_argument("--json", help="also write the summary as JSON to this file")
    s.set_defaults(func=cmd_stats)

    s = sub.add_parser("export-labels", help="write a CSV for human labelling")
    s.add_argument("file")
    s.add_argument("--include", nargs="+", help="auto labels to include")
    s.add_argument("--max", type=int)
    s.add_argument("--seed", type=int, default=0)
    s.set_defaults(func=cmd_export_labels)

    s = sub.add_parser("import-labels", help="read a labelled CSV")
    s.add_argument("file")
    s.add_argument("--labeller", required=True)
    s.set_defaults(func=cmd_import_labels)

    s = sub.add_parser("export-confirm", help="write cases for the end-to-end check")
    s.add_argument("file")
    s.add_argument("--max", type=int, default=30)
    s.set_defaults(func=cmd_export_confirm)

    s = sub.add_parser("import-confirm", help="read the end-to-end check results")
    s.add_argument("file")
    s.set_defaults(func=cmd_import_confirm)

    s = sub.add_parser("witness", help="check one live URL")
    s.add_argument("url")
    s.add_argument("--only", nargs="+", metavar="NAME")
    s.set_defaults(func=cmd_witness)
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    cfg = config.load(args.config)
    return args.func(args, cfg) or 0
