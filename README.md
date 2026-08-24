# Daily News Brief

A self-improving daily email newsletter. Every morning at 8:00 IST it delivers a
Bloomberg-briefing-style edition — signed lead essay, five briefs per topic
(Finance, Business, Tech, Politics), a Number of the Day, and a light closer —
built from the last 24 hours of Economic Times, The Hindu BusinessLine, Mint,
Financial Express and Times of India headlines.

Each recipient gets their own edition. In-email 1–5★ rating links feed a
feedback loop: keyword/source/topic weights update daily (bandit-style EMA), and
a weekly Claude critique pass proposes logged, reversible prompt and keyword
changes. Rating an article 4★+ offers an on-demand deep-dive follow-up email.

## Architecture
- **GitHub Actions** — daily generation (cron ~07:15 IST + backup), follow-up
  jobs, Sunday critique. Writing is done by Claude via the Claude Code CLI on a
  subscription OAuth token (or an API key).
- **Supabase** — Postgres for articles, issues, ratings, keyword weights,
  follow-up requests; Edge Functions serve the rating and consent links.
- **Brevo** — transactional email delivery, scheduled for 08:00 IST.
- **ScraperAPI** — fallback article-text fetcher, hard-capped at 30 credits/day.

`SPEC.md` holds the module contracts; `docs/runbook.md` holds setup and
operations (switching Claude accounts/models, secrets, failure policy).
`docs/keywords.html` is the living keyword list — regenerated on every change.

Design notes: articles are deduplicated across sources with Jaccard clustering
(the five sources reprint the same wire copy); Financial Express is ingested
headline-only from its public news sitemap out of respect for its robots policy;
paywalled pieces appear as headline + snippet tagged [Premium]. No recipient
data lives in this repository — it stays in Supabase and GitHub Secrets.
