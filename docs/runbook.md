# Runbook

## One-time setup (owner actions)
1. **GitHub**: push this repo to `harshad22491/Daily-News-Brief` (public).
2. **Supabase**: create a free project → run `supabase/migrations/001_init.sql`
   in the SQL editor → deploy both edge functions
   (`supabase functions deploy rate followup --no-verify-jwt`) → set function
   secrets: `supabase secrets set GH_PAT=<PAT> GH_REPO=harshad22491/Daily-News-Brief`.
   The PAT needs only `repo` + `workflow` scope (fine-grained: Actions read/write).
3. **Seed recipients** (SQL editor — emails never go in the repo):
   ```sql
   insert into recipients (id, token, active) values
     ('vaibhav@vibrantglobalgroup.com', encode(gen_random_bytes(12),'hex'), true),
     ('harshad422@gmail.com',           encode(gen_random_bytes(12),'hex'), true);
   ```
4. **Brevo**: verify `harshad422@gmail.com` as a sender (Senders & IPs → Add a
   sender → confirmation email). Both recipients should add that address to
   contacts / mark "not spam" on the first issue — gmail-from-Brevo can land in
   spam otherwise.
5. **Claude token**: on any machine logged into the desired Claude subscription:
   `claude setup-token` → copy the long-lived OAuth token.
6. **GitHub repo settings**:
   - Secrets: `CLAUDE_CODE_OAUTH_TOKEN`, `BREVO_API_KEY`, `SUPABASE_URL`,
     `SUPABASE_SERVICE_KEY`, `SCRAPERAPI_KEY` (optional: `ANTHROPIC_API_KEY`)
   - Variables: `MODEL` (default `opus`), `RATE_BASE`
     (= `https://<project-ref>.functions.supabase.co`), and `SENDER_EMAIL`
     (= `harshad422@gmail.com`, the Brevo-verified sender — kept out of the
     public code, so it lives in a repo variable)
7. **Dry run**: Actions → "Daily edition" → Run workflow with `dryrun=true` →
   download the rendered HTML artifact and review before going live.

## Switching things
| What | How |
|---|---|
| **Claude account** | Run `claude setup-token` under the other account; paste the new value into the `CLAUDE_CODE_OAUTH_TOKEN` secret. Nothing else changes. |
| **Subscription → API billing** | Set `ANTHROPIC_API_KEY` secret and delete/blank `CLAUDE_CODE_OAUTH_TOKEN`. |
| **Model** | Repo → Settings → Variables → `MODEL` = `opus`, `sonnet`, or a full model id. |
| **Mailer sender** | `SENDER_EMAIL` env in `daily.yml` (defaults to harshad422@gmail.com). If the group later gets DNS access to a domain, switch to Resend/authenticated domain for better deliverability. |
| **Recipients** | Insert/deactivate rows in `recipients` (Supabase). |

## Daily operation
- 07:15 IST: cron-job.org (Harshad's account, job "Daily-News-Brief") POSTs to
  `https://api.github.com/repos/harshad22491/Daily-News-Brief/actions/workflows/daily.yml/dispatches`
  with `Authorization: Bearer <PAT>`, `Accept: application/vnd.github+json`,
  body `{"ref":"main"}`. The build takes ~15–25 min; the email is scheduled to
  land at 08:00 IST via Brevo's `scheduledAt` (sent immediately if the build
  finishes after 08:00). A second cron-job.org job at 07:55 IST is the backup —
  the run exits early if today is already sent. GitHub's `schedule:` cron was
  removed on 01-Sep-2026 (it fired 1–12 h late; `trigger/` has cron/Task
  Scheduler equivalents if cron-job.org is ever dropped).
- **Trigger auth**: fine-grained GitHub PAT, repository access limited to this
  repo (and bulk-deals-mailer, which shares it), permission Actions:
  read/write, 1-year expiry — **rotate before it lapses** (cron-job.org starts
  reporting 401). Job URL must be `https://` (http → 301, counted as failure);
  leave "treat redirects as success" off.
- **Missing edition**: (1) check harshad422@gmail.com for a cron-job.org
  failure notice; (2) Actions tab — no run for the day = trigger/PAT problem,
  a run exists = read its log. Recover with `gh workflow run daily.yml`.
- **Failure policy**: generation retries twice; a degraded edition
  (headline+snippet, no essay) plus a banner listing what's missing is sent
  rather than skipping the day. Check the Actions run log + `send_log` table.
- Subscription limit hit at 7am → the run degrades as above; either wait for the
  window to reset and re-run manually, or temporarily set `ANTHROPIC_API_KEY`.

## Feedback loop
- Ratings arrive via the `rate` edge function; weights update at the start of
  the next daily run (EMA, clamped 0.2–3.0, 10% exploration slots).
- Sunday's critique workflow lets Claude propose keyword changes and at most one
  prompt diff per week; everything is logged in `prompt_versions` and visible in
  `docs/keywords.html`. A change judged harmful the following week is reverted.

## Health
`python scripts/health_check.py` validates every feed by XML parse (never trust
HTTP status — Financial Express returns 200 HTML on dead feed paths).
