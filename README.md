# Two Webs: project structure

Measures how often real websites serve AI fetchers different facts than they serve browsers. This file is the contract every module is built against. Research design: `../two_webs_overview.md`.

## Layers

Data flows downward. A layer reads only from layers above it. Every stage is stored in one SQLite file, so any layer can be re-run without re-crawling.

| # | Layer | Module | Reads | Writes |
|---|---|---|---|---|
| 0 | Config | `twowebs/config.py`, `config/settings.json` | - | - |
| 1 | Storage | `twowebs/store.py` | - | all tables |
| 2 | Fetch | `twowebs/fetch.py` | `sites` | `pages`, `fetches` (raw only) |
| 3 | Extract | `twowebs/extract.py` | raw bodies | (pure functions) |
| 4 | Compare | `twowebs/diff.py` | `fetches` | `extracts`, `comparisons` |
| 5 | Label | `twowebs/label.py` | `comparisons` | `labels`, `confirmations`, CSV files |
| 6 | Stats | `twowebs/stats.py` | everything | printed summary, JSON |
| 7 | Witness | `twowebs/witness.py` | live URL | console report |
| - | CLI | `twowebs/cli.py`, `twowebs/__main__.py` | - | - |

To add something later: a new fetcher identity is a `settings.json` entry; a new fact type is a pattern in `extract.py`; a new label is a `settings.json` entry plus a rule in `diff.compare`; a new sample is a text file loaded with `load-sample`.

## Constraints

- Python 3.7 compatible (no walrus, no `list[str]` annotations, no `match`). Only dependency: `httpx` (0.24).
- Windows laptop. Paths are built from `config.ROOT`.
- No LLM anywhere in the measurement path.
- Fetch stores raw responses and makes no judgement. All judgement is in `diff.py`, so thresholds can change without re-crawling.

## Core rules of the method

1. Every page is fetched twice per identity, with identities interleaved in time: round 1 is browser, AI 1..n; round 2 repeats. Every identity has `reps: 2`; with one fetch, rotating page content would look AI-only.
2. All identities send identical headers except `User-Agent`.
3. Cookies are cleared after every request.
4. A page is **comparable** under an AI identity only if both browser fetches and every fetch under that identity have outcome `ok`.
5. A sentence or fact is **AI-only** only if it appears in every fetch under the AI identity and in no browser fetch. Browser-only is the mirror image.
6. Robots: if `respect_robots` is true, skip the whole site when `*` is disallowed for the homepage, and record a `robots_skip` fetch (error `"robots_skip"`, no request sent) for any identity whose token is disallowed.
7. Samples are never pooled in statistics.

## Database (see `store.py` for exact DDL)

- `sites(site_id, domain, sample, rank, crawled, note)`
- `pages(page_id, site_id, url, kind)` where kind is `home` or `inner`
- `fetches(fetch_id, page_id, identity, rep, ts, status, final_url, content_type, n_bytes, truncated, error, headers JSON, body_sha)`; `body` (zlib) is the older per-fetch format, still readable
- `bodies(sha, data)`: each distinct response body stored once, LZMA-compressed. `python -m twowebs compact` converts old rows; it is lossless
- `extracts(fetch_id, outcome, n_words, text)`
- `comparisons(page_id, identity, comparable, reason, noise_sim, cross_sim, n_ai_only, n_browser_only, ai_only_text, browser_only_text, ai_only_facts JSON, auto_label, flagged)`
- `labels(page_id, identity, labeller, label, note)`
- `confirmations(page_id, identity, assistant, repeated, note)`
- `meta(key, value)`

## Fetch dict (the record passed between fetch, diff and witness)

`{identity, rep, url, ts, status, final_url, content_type, headers, body (bytes), truncated, error}`. After `diff.analyse_fetch` it also has `outcome`, `lines`, `n_words`.

Outcomes: `ok`, `blocked` (status 401/403/406/429/451/503), `challenge` (short page matching bot-check wording), `thin` (fewer than `min_page_words` words), `error`, `robots_skip`.

## Module interfaces

**fetch.py**
- `headers_for(identity, cfg) -> dict`
- `make_client(cfg) -> httpx.AsyncClient` (follow redirects, max 5, timeout from config)
- `async fetch_one(client, url, identity, cfg, rep=1) -> fetch dict` (streams, caps body at `max_body_bytes`, never raises, clears cookies)
- `async load_robots(client, base_url, cfg) -> RobotFileParser`
- `async fetch_page(client, url, cfg, robots=None, prefetched=None, identities=None) -> [fetch dict]` (rule 1; `prefetched` is reused as browser rep 1)
- `async crawl_site(domain, cfg) -> (pages, note)`; pages is `[{url, kind, fetches}]`. Tries `https://domain/`, then `https://www.domain/`. Inner page chosen by `extract.pick_inner_link` from the first browser fetch.
- `async crawl(db, cfg, sample=None, limit=None)` (semaphore over sites, saves each site with `store.save_site`, prints progress, resumable through `sites.crawled`)

**diff.py**
- `analyse_fetch(f, cfg) -> f` (adds outcome, lines, n_words)
- `compare(browser_fetches, ai_fetches, cfg) -> dict` with every `comparisons` column except the keys. Pure function.
- `analyse_all(db, cfg)` (clears and rebuilds `extracts` and `comparisons`)

Automatic label, first match wins, on comparable pages: `addressed_to_ai` (AI-only text addresses a model), `sponsored_ai_only`, `facts_differ` (AI-only sentences contain a price, percent, date or number absent from both browser fetches), `ai_sees_more`, `ai_sees_less`, `cosmetic`, `same`. `flagged` is 1 when the label is in `labels.headline`. Not comparable because the AI fetch was blocked or challenged: `blocked_or_challenged`.

Sentence matching uses `extract.norm` and substring containment in the other version's full normalised text, so HTML and Markdown renderings of one sentence match.

**label.py**
- `export_labels(db, cfg, path, include=None, max_cases=None, seed=0)`: one CSV row per (page, distinct AI-only text), identities joined with `|`, automatic label hidden, shuffled.
- `import_labels(db, cfg, path, labeller)`: validates against `labels.all`.
- `kappa(db) -> dict or None`: Cohen's kappa for the first two labellers.
- `export_confirm(db, cfg, path, max_cases=30)`, `import_confirm(db, path)`: the end-to-end check with real assistants; `repeated` is `yes`, `no` or `blocked`.

**stats.py**
- `wilson(k, n) -> (rate, low, high)`
- `summary(db, cfg) -> dict`, `print_summary(db, cfg)`: per sample, site-level divergence rate (automatic, and human-confirmed where every labeller chose a headline label) with Wilson intervals; per-identity outcome counts; browser-vs-browser noise quantiles; kappa; confirmation counts.

**witness.py**
- `async witness(url, cfg, only=None) -> {identity: compare result}`
- `report(url, results) -> bool` (prints; True if any identity is flagged)

**cli.py** (`python -m twowebs <command>`)

`init`, `load-sample --name N --file F [--limit K]` (lines are `domain` or `rank,domain`), `crawl [--sample N] [--limit K]`, `analyse`, `stats [--json F]`, `export-labels F [--include ...] [--max K]`, `import-labels F --labeller NAME`, `export-confirm F`, `import-confirm F`, `witness URL [--only NAME ...]`.

## Run order

```
python -m twowebs init
python -m twowebs load-sample --name pilot --file config/samples/pilot.txt
python -m twowebs crawl --sample pilot
python -m twowebs analyse
python -m twowebs stats
python -m twowebs export-labels data/labels_A.csv
```

## Known limits (from the verification pass)

- Label only after the final `analyse`. Labels are keyed by page and identity and are not cleared if a later `analyse` changes that row.
- "Human-confirmed" needs two labellers who both chose a headline label.
- `stats` does not warn if `crawl` ran after the last `analyse`. Always run `analyse` then `stats`.
- A site whose crawl crashed is marked crawled with a `crash:` note and is not retried.
- robots.txt is read for the starting host only, even if the homepage redirects to another host.
- Decompressed response size is not capped; a hostile gzip response can use a lot of memory. Lower `concurrency` if memory is tight.
- Raw bodies are stored. The pilot used about 0.37 MB of disk per site, so 20,000 sites needs roughly 7 GB.
- Image alt text is kept from Markdown but not from HTML, which can create a spurious AI-only sentence.
- Kappa is computed over all samples together.

## Trigger crawl and reproducing the report

The headline result comes from the trigger crawl, which fetches each page as: browser, an identical control, browser + `Accept: text/markdown`, an AI User-Agent, and both.

```
pip install .                                   # or run as: python -m twowebs ...
twowebs load-sample --name trigger --file config/samples/trigger.txt
twowebs crawl --sample trigger --set trigger
twowebs analyse
twowebs triggers --sample trigger               # site-level summary per identity
python analysis/build_site_data.py              # robustness analyses -> docs/data.json
python analysis/make_numbers.py                 # report/numbers.tex (every number in the report)
python analysis/make_figures.py                 # report/fig*.pdf
tectonic report/main.tex                        # report/main.pdf
```

`docs/` is a static site (open `docs/index.html` through any web server, or GitHub Pages) that draws every chart from `docs/data.js` and lets a reader inspect each site's lost facts and machine-only text.

`twowebs witness <url>` checks a single live URL under all identities.

## Tests

`tests/` uses `unittest` with synthetic HTML and Markdown fixtures. No network. Run with `python -m unittest discover -s tests`.
