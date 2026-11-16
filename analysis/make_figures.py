"""Draw the report figures from docs/data.json.  python analysis/make_figures.py"""
import json
import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
D = json.load(open(os.path.join(ROOT, "docs", "data.json"), encoding="utf-8"))
OUT = os.path.join(ROOT, "report")
A, B, G, C = "#b4411f", "#2f5d8a", "#2e6b4a", "#8a8578"
plt.rcParams.update({"font.size": 8.5, "font.family": "serif", "axes.spines.top": False,
                     "axes.spines.right": False, "pdf.fonttype": 42})

# Figure 1: trigger comparison
T = D["triggers"]
rows = [("Same browser again\n(control)", "browser-control", C), ("AI User-Agent only", "Claude-User", B),
        ("Markdown Accept\nheader only", "browser-md", A), ("Both headers", "Claude-User-md", A)]
fig, axes = plt.subplots(1, 2, figsize=(6.6, 2.1), sharey=True)
for ax, key, title in ((axes[0], "sites_markdown", "Serve a Markdown twin"),
                       (axes[1], "sites_differ", "Serve any different page")):
    for i, (name, ident, col) in enumerate(rows):
        m = T[ident][key]
        ax.barh(i, 100 * m["rate"], color=col, height=0.62,
                xerr=[[100 * (m["rate"] - m["low"])], [100 * (m["high"] - m["rate"])]],
                error_kw={"lw": 0.9, "capsize": 2})
        ax.text(100 * m["high"] + 0.25, i, "%.1f%% (%d/%d)" % (100 * m["rate"], m["k"], m["n"]),
                va="center", fontsize=7.5)
    ax.set_yticks(range(len(rows)))
    ax.set_yticklabels([r[0] for r in rows])
    ax.invert_yaxis()
    ax.set_title(title, fontsize=9)
    ax.set_xlabel("% of comparable sites")
    ax.set_xlim(0, 100 * max(T[r[1]][key]["high"] for r in rows) * 1.45)
fig.tight_layout()
fig.savefig(os.path.join(OUT, "fig1_triggers.pdf"))

# Figure 2: per-site fact loss, and loss by type
tw = sorted([t for t in D["twins"] if t["facts"] >= 3], key=lambda t: -(t["lost"] / float(t["facts"])))
fig, axes = plt.subplots(1, 2, figsize=(6.6, 2.1), gridspec_kw={"width_ratios": [2.1, 1]})
ax = axes[0]
sh = [100.0 * t["lost"] / t["facts"] for t in tw]
ax.bar(range(len(tw)), [max(s, 0.8) for s in sh], color=[G if s == 0 else A for s in sh], width=0.8)
ax.set_xlabel("Markdown twins (one bar per site, sorted)")
ax.set_ylabel("% of human-page facts\nabsent from twin")
ax.set_xticks([])
ax.set_ylim(0, 105)
ax.set_title("Fact loss per site (green: nothing lost)", fontsize=9)
ax = axes[1]
bt = D["by_type"]
for i, m in enumerate(bt):
    ax.barh(i, 100 * m["rate"], color=A, height=0.6)
    ax.text(100 * m["rate"] + 1, i, "%.0f%% (%d/%d)" % (100 * m["rate"], m["k"], m["n"]), va="center", fontsize=7.5)
ax.set_yticks(range(len(bt)))
ax.set_yticklabels([m["type"] for m in bt])
ax.invert_yaxis()
ax.set_xlim(0, 100 * max(m["rate"] for m in bt) * 1.9)
ax.set_xlabel("% absent, pooled")
ax.set_title("By fact type", fontsize=9)
fig.tight_layout()
fig.savefig(os.path.join(OUT, "fig2_facts.pdf"))
print("figures written to", OUT)
