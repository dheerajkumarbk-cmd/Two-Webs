"""Draw the report figures from docs/data.json.  python analysis/make_figures.py
Writes vector PDFs for the report and PNG previews."""
import json
import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
D = json.load(open(os.path.join(ROOT, "docs", "data.json"), encoding="utf-8"))
OUT = os.path.join(ROOT, "report")
RED, BLUE, GREEN, GREY, INK, MUTE = "#B4411F", "#2F5D8A", "#2E6B4A", "#9A968C", "#1C1B19", "#5F5B54"
plt.rcParams.update({
    "font.family": "sans-serif", "font.sans-serif": ["Arial", "Helvetica", "DejaVu Sans"],
    "font.size": 9, "axes.titlesize": 10, "axes.titleweight": "bold", "axes.titlelocation": "left",
    "axes.spines.top": False, "axes.spines.right": False, "axes.spines.left": False,
    "axes.edgecolor": "#BEBAB1", "axes.labelcolor": MUTE, "xtick.color": MUTE, "ytick.color": INK,
    "ytick.left": False, "axes.axisbelow": True, "grid.color": "#E6E2DA", "grid.linewidth": 0.8,
    "pdf.fonttype": 42, "savefig.dpi": 220, "savefig.bbox": "tight", "savefig.pad_inches": 0.04})


def save(fig, name):
    fig.savefig(os.path.join(OUT, name + ".pdf"))
    fig.savefig(os.path.join(OUT, name + ".png"))
    plt.close(fig)


# ---------------- Figure 1: which request signal reveals a twin ----------------
T = D["triggers"]
rows = [("Same browser again (control)", "browser-control", GREY),
        ("AI User-Agent only", "Claude-User", BLUE),
        ("Markdown Accept header only", "browser-md", RED),
        ("Both headers", "Claude-User-md", RED)]
fig, axes = plt.subplots(1, 2, figsize=(7.0, 2.35), sharey=True, gridspec_kw={"wspace": 0.08})
for ax, key, title in ((axes[0], "sites_markdown", "A. Sites serving a Markdown twin"),
                       (axes[1], "sites_differ", "B. Sites serving any different page")):
    top = 100 * max(T[r[1]][key]["high"] for r in rows)
    for i, (name, ident, col) in enumerate(rows):
        m = T[ident][key]
        ax.barh(i, 100 * m["rate"], color=col, height=0.58, zorder=2)
        ax.plot([100 * m["low"], 100 * m["high"]], [i, i], color=INK, lw=1.1, zorder=3)
        for x in (m["low"], m["high"]):
            ax.plot([100 * x, 100 * x], [i - 0.13, i + 0.13], color=INK, lw=1.1, zorder=3)
        ax.text(100 * m["high"] + top * 0.03, i, "%.1f%%" % (100 * m["rate"]), va="center",
                fontsize=9.5, fontweight="bold", color=INK)
        ax.text(100 * m["high"] + top * 0.03 + top * 0.29, i, "%d of %s" % (m["k"], "{:,}".format(m["n"])),
                va="center", fontsize=8, color=MUTE)
    ax.set_yticks(range(len(rows)))
    ax.set_yticklabels([r[0] for r in rows], fontsize=9)
    if ax is axes[0]:
        ax.invert_yaxis()          # axes share y, so invert once
    ax.set_title(title, pad=8)
    ax.set_xlabel("Share of comparable sites (%)")
    ax.set_xlim(0, top * 1.85)
    ax.grid(axis="x")
    ax.tick_params(axis="x", length=0)
save(fig, "fig1_triggers")

# ---------------- Figure 2: what happens to the facts ----------------
tw = sorted([x for x in D["twin_facts_all"] if x[0] >= 3], key=lambda x: -(x[1] / float(x[0])))
sh = [100.0 * x[1] / x[0] for x in tw]
n_none = sum(1 for s in sh if s == 0)
n_half = sum(1 for s in sh if s >= 50)
fig, axes = plt.subplots(1, 2, figsize=(7.0, 2.5), gridspec_kw={"width_ratios": [1.9, 1], "wspace": 0.42})
ax = axes[0]
ax.bar(range(len(tw)), [s if s > 0 else 1.6 for s in sh],
       color=[GREEN if s == 0 else RED for s in sh], width=0.82, zorder=2)
ax.axhline(50, color=MUTE, lw=0.8, ls=(0, (3, 3)), zorder=1)
ax.text(len(tw) - 0.5, 53, "half of the page's facts", ha="right", fontsize=8, color=MUTE)
ax.annotate("%d sites omit\nat least half" % n_half, xy=(n_half / 2.0 - 0.5, 101), xytext=(n_half + 5, 88),
            fontsize=8.5, color=RED, fontweight="bold", va="center",
            arrowprops={"arrowstyle": "-", "color": RED, "lw": 0.8})
ax.text(len(tw) - n_none / 2.0 - 0.5, 9, "%d sites omit nothing" % n_none, ha="center",
        fontsize=8.5, color=GREEN, fontweight="bold")
ax.set_xlim(-0.8, len(tw) - 0.2)
ax.set_ylim(0, 108)
ax.set_xticks([])
ax.set_yticks([0, 25, 50, 75, 100])
ax.grid(axis="y")
ax.set_xlabel("%d Markdown twins, one bar per site, sorted" % len(tw))
ax.set_ylabel("Facts absent from the twin (%)")
ax.set_title("A. Fact loss is concentrated in a minority of sites", pad=8)
ax = axes[1]
bt = sorted(D["by_type"], key=lambda m: -m["rate"])
names = {"price": "Prices", "percent": "Percentages", "number": "Other numbers", "date": "Dates"}
for i, m in enumerate(bt):
    ax.barh(i, 100 * m["rate"], color=RED, height=0.58, zorder=2)
    ax.text(100 * m["rate"] + 1.2, i, "%.0f%%" % (100 * m["rate"]), va="center", fontsize=9.5,
            fontweight="bold", color=INK)
    ax.text(100 * m["rate"] + 14.5, i, "%d of %d" % (m["k"], m["n"]), va="center", fontsize=8, color=MUTE)
ax.set_yticks(range(len(bt)))
ax.set_yticklabels([names.get(m["type"], m["type"]) for m in bt])
ax.invert_yaxis()
ax.set_xlim(0, 100 * bt[0]["rate"] * 2.35)
ax.grid(axis="x")
ax.tick_params(axis="x", length=0)
ax.set_xlabel("Absent from the twin (%), pooled")
ax.set_title("B. By type of fact", pad=8)
save(fig, "fig2_facts")
print("figures written to", OUT)
