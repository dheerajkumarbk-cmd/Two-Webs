"""Layer 6: statistics. The unit of the headline rate is the site, not the page
or the comparison. Samples are never pooled."""
import math

from . import label


def wilson(k, n, z=1.96):
    """Wilson score interval for k successes in n trials: (rate, low, high).
    (None, None, None) when n == 0."""
    if n == 0:
        return (None, None, None)
    p = k / float(n)
    z2 = z * z
    denom = 1.0 + z2 / n
    centre = (p + z2 / (2.0 * n)) / denom
    half = z * math.sqrt(p * (1.0 - p) / n + z2 / (4.0 * n * n)) / denom
    low = 0.0 if k == 0 else max(0.0, centre - half)
    high = 1.0 if k == n else min(1.0, centre + half)
    return (p, low, high)


def _rate(k, n):
    rate, low, high = wilson(k, n)
    return {"k": k, "n": n, "rate": rate, "low": low, "high": high}


def quantile(values, q):
    """Linear-interpolation quantile (same as numpy's default). None if empty."""
    if not values:
        return None
    v = sorted(values)
    pos = (len(v) - 1) * q
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    return v[lo] + (v[hi] - v[lo]) * (pos - lo)


def summary(db, cfg):
    """Returns {"samples": {name: {...}}, "kappa": {...} or None}. Everything
    that depends on the data is split by sample."""
    headline = set(cfg["labels"]["headline"])
    samples = {}
    for r in db.execute("SELECT sample, COUNT(*) AS n, SUM(crawled) AS c"
                        " FROM sites GROUP BY sample ORDER BY sample"):
        samples[r["sample"]] = {
            "sites_loaded": r["n"], "sites_crawled": r["c"] or 0,
            "_comparable": set(), "_auto": set(), "_human": set(),
            "_flagged_labelled": set(), "by_identity": {}, "_noise": {},
            "confirmations": {"yes": 0, "no": 0, "blocked": 0},
        }

    # Labels per comparison: set of labels given by each labeller.
    given = {}
    for r in db.execute("SELECT page_id, identity, label FROM labels"):
        given.setdefault((r["page_id"], r["identity"]), []).append(r["label"])

    rows = db.execute(
        "SELECT c.page_id, c.identity, c.comparable, c.reason, c.noise_sim,"
        " c.auto_label, c.flagged, p.site_id, s.sample"
        " FROM comparisons c JOIN pages p ON p.page_id=c.page_id"
        " JOIN sites s ON s.site_id=p.site_id").fetchall()
    for r in rows:
        s = samples[r["sample"]]
        bucket = r["auto_label"] or r["reason"] or "unknown"
        ident = s["by_identity"].setdefault(r["identity"], {})
        ident[bucket] = ident.get(bucket, 0) + 1
        if not r["comparable"]:
            continue
        site = r["site_id"]
        s["_comparable"].add(site)
        if r["noise_sim"] is not None:
            # One value per page: the browser pair is the same for every identity.
            s["_noise"].setdefault(r["page_id"], r["noise_sim"])
        labs = given.get((r["page_id"], r["identity"]))
        if r["flagged"]:
            s["_auto"].add(site)
            if labs:
                s["_flagged_labelled"].add(site)
        # Human-confirmed needs two labellers, both choosing a headline label.
        if labs and len(labs) >= 2 and all(x in headline for x in labs):
            s["_human"].add(site)

    for r in db.execute(
            "SELECT s.sample, f.repeated, COUNT(*) AS n FROM confirmations f"
            " JOIN pages p ON p.page_id=f.page_id JOIN sites s ON s.site_id=p.site_id"
            " GROUP BY s.sample, f.repeated"):
        s = samples.get(r["sample"])
        if s is not None:
            s["confirmations"][r["repeated"]] = (
                s["confirmations"].get(r["repeated"], 0) + r["n"])

    out = {}
    for name, s in samples.items():
        n = len(s["_comparable"])
        noise = list(s["_noise"].values())
        out[name] = {
            "sites_loaded": s["sites_loaded"],
            "sites_crawled": s["sites_crawled"],
            "sites_comparable": n,
            "divergent_auto": _rate(len(s["_auto"]), n),
            "divergent_human_confirmed": _rate(len(s["_human"]), n),
            "flagged_sites_labelled": len(s["_flagged_labelled"]),
            "by_identity": s["by_identity"],
            "noise_sim": {"n_pages": len(noise), "p05": quantile(noise, 0.05),
                          "p25": quantile(noise, 0.25),
                          "median": quantile(noise, 0.5)},
            "confirmations": s["confirmations"],
        }
    return {"samples": out, "kappa": label.kappa(db)}


def _pct(x):
    return "n/a" if x is None else "%.1f%%" % (100.0 * x)


def _fmt_rate(d):
    if d["n"] == 0:
        return "n/a (0 comparable sites)"
    return "%d/%d = %s  (95%% Wilson CI %s to %s)" % (
        d["k"], d["n"], _pct(d["rate"]), _pct(d["low"]), _pct(d["high"]))


def print_summary(db, cfg):
    res = summary(db, cfg)
    print("Two Webs summary (unit: sites; samples are never pooled)")
    if not res["samples"]:
        print("  no sites loaded")
    for name, s in res["samples"].items():
        print("")
        print("Sample: %s" % name)
        print("  sites loaded %d, crawled %d, comparable %d"
              % (s["sites_loaded"], s["sites_crawled"], s["sites_comparable"]))
        print("  divergent (automatic):       %s" % _fmt_rate(s["divergent_auto"]))
        print("  divergent (human-confirmed): %s" % _fmt_rate(s["divergent_human_confirmed"]))
        print("    flagged sites with at least one human label: %d of %d"
              % (s["flagged_sites_labelled"], s["divergent_auto"]["k"]))
        nz = s["noise_sim"]
        if nz["n_pages"]:
            print("  browser-vs-browser similarity over %d pages: p05 %.3f, p25 %.3f, median %.3f"
                  % (nz["n_pages"], nz["p05"], nz["p25"], nz["median"]))
        else:
            print("  browser-vs-browser similarity: no comparable pages")
        c = s["confirmations"]
        print("  confirmation by real assistants: repeated yes %d, no %d, blocked %d"
              % (c.get("yes", 0), c.get("no", 0), c.get("blocked", 0)))
        print("  comparisons by identity (page level):")
        for ident in sorted(s["by_identity"]):
            parts = ", ".join("%s %d" % (b, n) for b, n in
                              sorted(s["by_identity"][ident].items(),
                                     key=lambda kv: (-kv[1], kv[0])))
            print("    %-16s %s" % (ident, parts))
    print("")
    k = res["kappa"]
    if k is None:
        print("Inter-labeller agreement: needs two labellers with shared cases")
    else:
        kv = "undefined" if k["kappa"] is None else "%.3f" % k["kappa"]
        print("Inter-labeller agreement (%s vs %s): %d cases, agreement %s, Cohen's kappa %s"
              % (k["labellers"][0], k["labellers"][1], k["n"], _pct(k["agreement"]), kv))
    return res
