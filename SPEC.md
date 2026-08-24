# Implementation spec — pipeline + Supabase (for code generation)

Python 3.11. Deps: feedparser, httpx, jinja2, trafilatura, pyyaml, python-dateutil.
No other deps. All times Asia/Kolkata unless stated. Read configs from `config/*.yaml`
(already present — see them for exact schemas). Prompts in `prompts/` (present).
Style: plain, typed where cheap, small functions, no framework.

## State abstraction — `pipeline/common.py`
- `load_config()` → dict of feeds/keywords-seed/topics yaml.
- `class Store`: if env `SUPABASE_URL` + `SUPABASE_SERVICE_KEY` set → Supabase REST
  (PostgREST via httpx: headers apikey+Authorization, `POST /rest/v1/<table>`,
  `GET ...?select=`); else → local JSON files under `state/` (one file per table,
  same record shapes). Methods: `insert(table, rows)`, `upsert(table, rows, on)`,
  `select(table, filters: dict, order=None, limit=None)`, `update(table, filters, patch)`.
- `recipients()` → from store table `recipients`; if empty and env `RECIPIENTS`
  (comma-sep emails) set, create rows with `secrets.token_urlsafe(16)` tokens.
- `now_ist()`, `today_ist()` helpers (zoneinfo).
- `http_get(url, **kw)` → httpx with browser UA
  `Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36`,
  timeout 20s, follow_redirects, 2 retries with backoff.
- Logging: stdlib logging, INFO to stdout.

## Tables (records used by both Store backends)
- `recipients`: id (email), token, active (bool)
- `articles`: id (sha1 of url), url, source, source_label, feed, title, snippet,
  topic, keywords (list), published_at, premium (bool), cluster_id, first_seen
- `issues`: id (`{date}:{recipient}`), date, recipient, sent_at, partial (bool), subject
- `issue_items`: issue_id, article_id, topic, slot (lead|brief|noted|number|finally)
- `ratings`: recipient, kind (topic|article), item, score (1-5), rated_at, date
- `keyword_weights`: recipient, topic, keyword, weight (float), observations (int),
  status (active|retired), changed_by (seed|bandit|critique), updated_at
- `prompt_versions`: version, file, diff, rationale, created_at, verdict
- `followup_requests`: id, recipient, article_id, status (offered|consented|sent), created_at
- `send_log`: date, recipient, message_id, status, detail

## Modules (`pipeline/`)
### ingest.py
`ingest(cfg) -> list[Article]` — fetch every feed in `config/feeds.yaml`.
- type rss (default): feedparser on fetched bytes. **Validate: bozo/no entries →
  count as failed feed, never crash the run.** Accept only entries with parseable
  pubdate within `window_hours` (24).
- type news_sitemap (Financial Express): parse XML `<url><news:news>` entries:
  loc, news:title, news:publication_date. Mark `premium=False`, snippet="".
  Source has `headline_only: true` → NEVER fetch these article bodies later.
- Premium detection heuristic: title/snippet containing "ET Prime", "Mint Premium",
  "TOI+", or link path containing `/prime/` → premium=True.
- Dedupe by URL hash. Return articles + a `feed_health` list [(feed, ok, n, err)].

### filter.py
`assign(articles, weights) -> list[Article]` — per recipient.
- Keyword match: case-insensitive word-boundary regex per keyword/phrase against
  title+snippet. Store matched keywords. Article topic = argmax over topics of
  sum(weight of matched keywords in that topic); tie → feed's topic_hint; no
  matches and no hint → drop.
- Score = sum(matched weights) × source weight (feeds.yaml) × recency factor
  (1.0 newest → 0.6 at 24h, linear).

### dedup.py
`cluster(articles, threshold=0.5)` — Jaccard clustering (own implementation):
lowercase title, strip punctuation, remove English stop words (small inline list),
tokenize; inverted token→article index to find candidate pairs; union-find merge
pairs with Jaccard ≥ threshold. Cluster keeps highest-score article as primary,
others become `cluster_links`. Set cluster_id on all.

### select.py
`select(articles, cfg, rng_seed)` → per topic: top `per_topic` (5) primaries by
score, but reserve `exploration_share` (10% → 1 slot in ~half the topics via
seeded random) for an article whose matched keywords have `observations < 3`.
Then `briefly_noted` 3–5 next headlines per topic. Also `lead_candidates`: top 3
overall by score. Deterministic given rng_seed (use date+recipient).

### fetch.py
`enrich(selected)` — full text for primary selected articles only (not noted):
1. skip if premium or source headline_only;
2. direct `http_get` + `trafilatura.extract` (≥400 chars = success);
3. else ScraperAPI if env `SCRAPERAPI_KEY`: `http://api.scraperapi.com?api_key=..&url=..`
   — budget: read+update a daily counter in store (`send_log` table, date row
   `scraperapi:{date}`), hard cap 30/day;
4. else keep snippet. Truncate full_text to 2500 chars.

### generate.py
`generate(edition_input: dict, prompts_dir) -> dict`
- Build prompt: contents of `prompts/edition.md` + "\n\n## INPUT JSON\n" + json.
- Call Claude Code CLI: `claude -p <PROMPT_VIA_STDIN> --model $MODEL --output-format text`
  (subprocess, pass prompt on stdin with `-p` reading stdin: use
  `subprocess.run(["claude","-p","--model",model,"--output-format","text"], input=prompt...)`,
  timeout 600s). 2 retries. Parse JSON (strip ``` fences if present). Validate
  every article_id exists; on validation failure retry once with an appended
  correction note. Raise GenerationError after retries.

### render.py
`render(edition, articles_by_id, recipient, cfg, partial_missing: list) -> (subject, html)`
- Jinja2 `templates/email.html.j2` (CREATE IT): 620px table layout, inline CSS only,
  system-font stack, works in Gmail. Dark-on-light. Sections per style guide order:
  masthead, partial-banner (if any), lead essay, per-topic sections each headed by
  the topic label + a topic rating row, article briefs each followed by a small
  1★-5★ link row and "also: <src>" cluster links, briefly-noted, Number of the Day
  (big number + gloss), And Finally, footer (keyword-change note placeholder,
  "Rate today's topics" reminder, unsubscribe mailto:harshad422@gmail.com?subject=unsubscribe).
- Rating URLs: `{RATE_BASE}/rate?t={token}&kind=topic|article&item={id}&s={1-5}&d={date}`
  (env RATE_BASE; if unset → render plain text "ratings offline" instead of links).
- Subject: `Daily News Briefing – {Mon, 25 Aug 2026}: {subject_suffix}`.

### send.py
`send(recipient, subject, html, when_ist)` — Brevo `POST https://api.brevo.com/v3/smtp/email`,
header `api-key: $BREVO_API_KEY`, sender {name: "Daily News Briefing",
email: env SENDER_EMAIL default harshad422@gmail.com}. If now < 08:00 IST →
`scheduledAt` 08:00 IST in ISO-8601 with offset; else send immediately.
Record send_log. Raise on non-2xx with response text.

### bandit.py
`update_weights(store, recipient)` — for yesterday's ratings: normalized
r = (score-3)/2 → per matched keyword: weight = clamp(0.8*weight + 0.2*(1+r), 0.2, 3.0),
observations += 1; same EMA for source and topic weights (store source/topic weights
as keyword_weights rows with topic='__source__'/'__topic__'). Global negative decay:
if BOTH recipients rated same item ≤2 → extra ×0.9 on that keyword for both.
Seed on first run from `config/keywords-seed.yaml` at weight 1.0.

### run.py  (`python -m pipeline.run --mode daily [--dryrun]`)
1. Guard: if send_log already has today's date for all active recipients → log+exit 0.
2. ingest (collect feed_health; failed feeds → partial_missing).
3. For each recipient: bandit.update → filter.assign → dedup.cluster → select →
   fetch.enrich → generate (on GenerationError after retries: build degraded
   edition = headline+snippet briefs w/out essay, add to partial_missing) →
   render → write `work/{date}/{recipient}.html` → if not --dryrun: send.
4. Persist articles/issues/issue_items. Print one-line summary per recipient.
Top-level: whole run wrapped so one recipient's failure doesn't kill the other's.

### followup.py (`python -m pipeline.followup --request-id X`)
Load request+article; gather related: same cluster_id or ≥2 shared keywords, last
7 days from `articles`, plus fresh ingest pass; build prompt from `prompts/followup.md`;
claude call as generate.py; send email "More on: …" immediately; mark request sent.

### critique.py (`python -m pipeline.critique`)
Export last 7 days ratings+weights+issue list → prompt from `prompts/critique.md` +
data → claude call → apply keyword_changes via store (changed_by=critique),
apply prompt_change find/replace in file if exact-match found (log to
prompt_versions either way), REVERT verdict → restore from prompt_versions diff.
Regenerate `docs/keywords.html` via keywords_html.py.

### keywords_html.py
`generate(store, path="docs/keywords.html")` — self-contained HTML: per recipient
per topic, table of keywords with weight bars, status, changed_by, updated_at;
plus a changelog section from prompt_versions. No external assets.

### scripts/health_check.py
Fetch every configured feed, XML-parse validation, print a table
(source/feed/status/entries/newest), exit 1 if >30% feeds fail.

## Supabase (`supabase/`)
- `migrations/001_init.sql`: all tables above with sensible PKs/indexes
  (ratings: idx on (recipient,date); articles: PK id; RLS ENABLED with no anon
  policies — service key only).
- `functions/rate/index.ts` (Deno, no imports beyond std): GET t,kind,item,s,d →
  validate recipient token (query recipients via SUPABASE_URL env + service key,
  both auto-available in edge functions as Deno.env), upsert rating (unique on
  recipient+kind+item+d), return tiny HTML page "Thanks — recorded ⭐s". If
  kind=article && s>=4: insert followup_requests (status=offered) and include
  link `<a href="/functions/v1/followup?req={id}&go=1">Yes — send me a deep dive</a>`
  plus a "No thanks" line. Invalid token → 403.
- `functions/followup/index.ts`: GET req,go → mark consented; POST to
  `https://api.github.com/repos/${Deno.env.GH_REPO}/actions/workflows/followup.yml/dispatches`
  body `{ref:"main", inputs:{request_id:req}}`, auth `Bearer ${Deno.env.GH_PAT}`;
  return "On it — your deep dive lands in ~15 minutes." HTML.
- Both functions: `verify_jwt = false` (add `supabase/config.toml` entries) since
  links come from email.

## Acceptance
- `python scripts/health_check.py` runs and prints the table.
- `RECIPIENTS=test@example.com python -m pipeline.run --mode daily --dryrun`
  completes against live feeds WITHOUT claude/brevo/supabase (no SUPABASE_URL:
  local state; no claude binary → generate must raise, run degrades to
  headline+snippet edition, still writes work/{date}/test@example.com.html).
- No secrets or emails hardcoded anywhere; everything via env.
