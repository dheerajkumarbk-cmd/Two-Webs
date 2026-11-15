# Two Webs: One Request Header Reveals a Second Web for AI Agents, and It Drops a Fifth of the Facts

> DRAFT on the official template's section structure. Paste into the Apart template, add author names, and delete this box.
> Every number comes from `python -m twowebs triggers --sample trigger` and `python -m twowebs stats` on the crawl of 14-15 November 2026.
> **All rates are automatic counts. No case has been hand-labelled yet.** If two-person labelling is done before submission, add the confirmed counts to Section 4 and update the abstract. If it is not, the text below is accurate as written.
> Items marked [VERIFY] must be opened and checked by a team member before submission.

**Authors:** [Author 1], [Author 2], [Author 3] · With Apart Research

## Abstract

An AI assistant's citation can be checked only if the page the assistant read is the page a person sees. Prior tools test this by imitating AI crawlers' User-Agent strings and comparing text similarity. We add a second trigger and a different unit of comparison. We crawled 1,415 high-traffic sites, fetching each page under five identities, twice each and interleaved: a browser, an identical control, a browser that adds only `Accept: text/markdown`, an AI fetcher's User-Agent, and both together. We then compared mechanically extracted facts (numbers, prices, percentages, dates) instead of similarity. Changing the User-Agent returned a separate Markdown page on 3 of 1,267 sites (0.2%). Adding the Accept header to an ordinary browser returned one on 76 of 1,403 (5.4%, 95% CI 4.3-6.7%); 75 of those 76 are invisible to User-Agent testing. Among twins with at least three facts, 20.9% of the human page's facts were absent from the Markdown version, and 17.5% of twins dropped at least half. The identical control produced no twins and no fact loss. Some twins also carry content written for machines: a sponsored block on a news homepage, a "you are an AI agent" briefing, and guidance on how to report an outage. All rates are automatic and unlabelled; we release the crawler, the data pipeline and a single-URL checking tool.

## 1. Introduction

When an AI assistant answers with a citation, the reader's only practical check is to open the link. That check assumes one page per address. The assumption is weakening. In mid-2026 a major news publisher was reported to be serving AI crawlers a Markdown version of its pages containing sponsored answers that human readers never see [1]. Separately, content negotiation for agents has become an ordinary web feature: a client that sends `Accept: text/markdown` can be given a Markdown rendition of the page instead of HTML [2, VERIFY].

If the machine-readable version states different facts from the human version, three things break at once: a reader cannot verify a citation, a citation-checking tool that re-fetches the page blames the model for a difference the site created, and an overseer reviewing an agent's sources inspects a document the agent never read.

Existing measurements ask whether pages differ when the request claims to be an AI crawler. We ask two narrower questions. Which request signal triggers a different page? And when a different page is served, what happens to the facts?

Our main contributions are:

1. **A second trigger.** A normal browser that adds one Accept header receives a separate Markdown page on 5.4% of sites, against 0.2% for User-Agent imitation, in the same crawl. 75 of 76 such sites are missed by User-Agent testing.
2. **Fact-level accounting with a built-in control.** We count the facts lost between the human page and the machine page, under a rule that requires agreement across repeated fetches, and we report the method's false-positive floor from an identical-request control measured in the same run.
3. **Open tooling.** A crawler, analysis pipeline and `witness` command that checks any URL under all identities, running on a laptop CPU with no model in the loop.

## 2. Related Work

**Demonstrations.** A Whole New World [3] shows that a site can serve a poisoned page only to AI agents. A previous Apart sprint project showed that hidden page content can mislead browsing models on 30 constructed questions [4]. Neither measures real sites.

**Access and blocking.** Liu et al. measure robots.txt rules and active blocking of AI crawlers at scale [5]; a practitioner study classifies the status codes sites return to GPTBot [6]. These concern whether AI may read a page, not what it is told.

**Prevalence by User-Agent.** The closest work is agentview [7], an open-source tool and study (August 2026) that fetched the Tranco top 1,000 as a browser and as seven AI User-Agents. It reports that 14.8% of 526 analysable sites serve at least one agent an altered 200 response, and zero sites with injection or manipulation text. We build on it and differ in four ways: (i) it varies only the User-Agent, so it cannot see content negotiation; (ii) it measures text similarity, where we count facts; (iii) it fetches each identity once and validates noise on eight sites, where we fetch twice per identity and run a control on every site; (iv) its dataset labels the news publisher in [1] as benign divergence, where our rules flag the sponsored block.

**When to use our method.** Use User-Agent comparison to find blocking and crawler-specific pages. Use ours to find the larger population of negotiated twins and to know whether the facts in them match.

## 3. Methods

**Sample.** Tranco top-sites list, ranks 1-3,300. A first crawl (User-Agent identities only) established which homepages load for a browser; those 1,415 sites form the trigger sample. Labelling rules were tuned on ranks 1-300 and frozen in a public commit before the trigger crawl.

**Identities.** Per page, in this order, repeated twice: (1) browser; (2) control, with headers identical to the browser; (3) browser plus `Accept: text/markdown, text/html;q=0.9`; (4) Claude-User User-Agent; (5) Claude-User plus the same Accept header. All other headers are identical. Cookies are cleared after every request. Homepage plus one inner page, chosen by a fixed rule (first same-host link with a path of 15 or more characters).

**Comparison.** Raw responses only; no JavaScript. Text is extracted by rule from HTML or Markdown and normalised so the same sentence in either format matches. A sentence or fact counts as machine-only only if it appears in both fetches under that identity and in neither browser fetch; human-only is the mirror. A page is comparable only if every fetch involved returned a real page (not blocked, challenged or near-empty).

**Markdown twin.** The browser receives HTML and the other identity receives a non-HTML rendition at the same address.

**Facts.** Prices, percentages, dates and numbers, extracted by regular expression after removing URLs and HTML comments. Standalone years are excluded.

**Automatic labels.** Same; cosmetic; AI sees less; AI sees more; different final URL; facts differ (each side states a fact of the same type the other lacks); sponsored content only for the machine; text addressed to an AI. The last three are "flagged".

**What did not work.** An earlier rule counted any machine-only sentence containing a number as "facts differ"; in the pilot 5 of 7 flags were false (country redirects, a bot wall shown to the browser, extra text). A first run of the Accept experiment silently sent identical headers because the crawler ignored the per-identity header; we kept that run as an additional noise measurement (16 of 1,388 homepages differed from themselves).

**Ethics.** Public pages, about ten requests per page at under two per second per site, no logins or forms. Sites whose robots.txt disallows an identity were skipped for it.

## 4. Results

**Table 1. Site-level outcomes by request identity (trigger sample, 95% Wilson intervals).**

| Identity | Comparable sites | Different page | Flagged | Markdown twin |
|---|---|---|---|---|
| Control (same browser) | 1,402 | 22 (1.6%, 1.0-2.4) | 2 (0.1%, 0.0-0.5) | 0 (0.0%, 0.0-0.3) |
| AI User-Agent only | 1,267 | 67 (5.3%, 4.2-6.7) | 9 (0.7%, 0.4-1.3) | 3 (0.2%, 0.1-0.7) |
| Accept header only | 1,403 | 121 (8.6%, 7.3-10.2) | 37 (2.6%, 1.9-3.6) | 76 (5.4%, 4.3-6.7) |
| Both headers | 1,265 | 138 (10.9%, 9.3-12.7) | 39 (3.1%, 2.3-4.2) | 80 (6.3%, 5.1-7.8) |

**Figure 1** (bar chart of Table 1's last three columns; see project website) shows the main result: the Accept header alone finds about 25 times as many twins as the User-Agent alone, and the control finds none. Of the 76 Accept-triggered twin sites, 75 were not twins under the User-Agent identity.

**Table 2. Fact retention in Markdown twins (Accept-only identity; twins whose human page states three or more facts).**

| Measure | Value |
|---|---|
| Twins analysed | 63 |
| Human-page facts absent from the twin, pooled | 304 of 1,457 (20.9%, 18.9-23.0) |
| Twins losing at least a quarter of facts | 18 (28.6%, 18.9-40.7) |
| Twins losing at least half | 11 (17.5%, 10.0-28.6) |
| Facts lost in the control comparison | 0 of 84 pooled |

**Observations, not yet labelled.** Machine-only text in the crawl includes: a block beginning "Ally is the sponsor and source of this material" on a news homepage, triggered by the Accept header as well as by User-Agent; a page beginning "You are an AI agent reading railway.com"; a status page adding "Scope note ... before concluding a Cloudflare-wide outage"; and a vendor page stating "43% of enterprise agent stacks already use Redis for agent data storage", a sentence absent from that page's raw HTML. In the separate User-Agent crawl of 3,000 sites, one cloud provider told AI fetchers "106 availability zones in 31 regions" and browsers "109 availability zones in 32 regions".

**Robustness.** The control bounds false positives at 0.1% for flags and 0% for twins. The twin rate is similar on homepages (70 pages) and inner pages (65). The "text addressed to an AI" label is the least reliable: it matches phrases such as "AI agents", which many of these vendors' pages use about their products, so the flagged rate (2.6%) is an upper bound on real cases. We do not report a rate for facts added by twins, because that count is dominated by metadata, postcodes and phone numbers.

**User-Agent crawl.** In 3,000 further sites (ranks 301-3,300) crawled under seven AI User-Agents, 1,227 were comparable and 13 (1.1%) were flagged, of which our reading finds two clear cases. This agrees with [7] that deliberate manipulation by User-Agent is rare among top sites.

## 5. Discussion and Limitations

The second web is mostly being built in the open, through content negotiation, by sites that want agents to read them. That makes it easy to miss: nothing is hidden, yet a person who clicks a citation sees a page that states, on average, a fifth more facts than the agent was given, and occasionally lacks a sentence the agent was given. For AI safety this is an oversight problem. Human review of an agent's sources, replayed-browsing evaluations and citation-faithfulness benchmarks all assume the reviewer can re-fetch what the agent read. A single request header is enough to break that assumption on about one top site in twenty.

**Limitations.**
- No human labels. Every rate is an automatic count; label reliability is unknown except through the control.
- We did not establish which deployed assistants send the Markdown Accept header. If few do, the practical exposure is smaller than the site-level rate.
- Raw HTML only. A fact "lost" in the twin is lost relative to raw HTML; a fact "added" may be visible to people after JavaScript runs.
- One day, one network location (India), homepage plus one inner page. Country redirects affect some comparisons.
- User-Agent results are a lower bound: sites that verify AI fetchers by IP address showed us the human page.
- Fact extraction is by regular expression and misses facts stated in words.
- The sample is the top of a popularity list and is weighted toward technology companies.

**Future Work.** Two-person labelling of all flagged cases and an end-to-end check that asks deployed assistants about confirmed pages; a JavaScript-rendered human baseline; repeat crawls to track adoption; more pages per site; several countries; requests from verified fetcher IP ranges; a per-site risk score combining trigger breadth, fact loss and machine-only claims; and a public tracker of sites whose twin diverges.

## 6. Conclusion

Testing for a second web by imitating AI crawlers finds almost nothing among top sites. Asking for Markdown with one header finds a twin on about 5% of them, and those twins omit about a fifth of the human page's numbers, prices and dates. A few also carry sponsored content or instructions meant for the machine. Verification tools and oversight procedures should fetch pages the way the agent did, and should compare facts, not similarity.

## Code and Data

- Code repository: https://github.com/dheerajkumarbk-cmd/Two-Webs
- Website with results explorer: [link once hosted]
- Data: per-site results in `docs/data.json`; raw crawl database available on request (1.4 GB+).

## Author Contributions

[To be written by the team. State the use of AI coding assistance for implementation.]

## References

1. Unsourced Research (2026). AI Is Being Shown a Different Website Than You Are. https://unsourced.app/research/ai-crawlers-see-a-different-web
2. Cloudflare (2026). Markdown for Agents. https://developers.cloudflare.com/fundamentals/reference/markdown-for-agents [VERIFY: not opened]
3. A Whole New World: Creating a Parallel-Poisoned Web Only AI-Agents Can See (2025). arXiv:2509.00124 [VERIFY authors; abstract read only]
4. Haroen, A. (2026). Eliciting Deception on Generative Search Engines. Apart Research AI Manipulation Hackathon. https://apartresearch.com/sprints/projects/eliciting-deception-on-generative-search-engines-6nuu
5. Somesite I Used To Crawl: Awareness, Agency and Efficacy in Protecting Content Creators From AI Crawlers (2025). IMC 2025. arXiv:2411.15091 [VERIFY authors]
6. HasData (2026). How Sites Answer GPTBot. https://dev.to/hasdata_com/how-sites-answer-gptbot-classified-in-python-546o
7. Islampure, T. (2026). agentview: The Agent-Facing Web, study results. https://github.com/tusharislampure29/agentview
8. Le Pochat, V. et al. (2019). Tranco: A Research-Oriented Top Sites Ranking Hardened Against Manipulation. NDSS. [VERIFY]

## Appendix A. Limitations and Dual-Use / Ethical Considerations

- **Identification.** Requests carried the User-Agent of an AI fetcher the authors do not operate. This is standard in cloaking measurement and is disclosed here. Sites that disallow that fetcher in robots.txt were not requested under it.
- **Load.** About ten requests per page, two pages per site, spaced under two per second per site.
- **Naming sites.** Sites are named only for content they serve publicly to any client that asks. No claim of intent is made; most twins are honest simplifications.
- **Dual use.** The method detects divergence. It could tell a site operator which signals to avoid, but the signals tested are already public. We describe detection and do not describe evasion.
- **No personal data** was collected.
