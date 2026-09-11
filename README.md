# new-grad-watcher

Watches multiple new-grad job-listing repos for new postings and posts them to
Slack. Currently configured sources:

- [SimplifyJobs/New-Grad-Positions](https://github.com/SimplifyJobs/New-Grad-Positions)
  — Software Engineering, Data Science AI & ML, and Product Management categories
- [jobright-ai/2026-Software-Engineer-New-Grad](https://github.com/jobright-ai/2026-Software-Engineer-New-Grad)
  — Software Engineering
- [jobright-ai/2026-Product-Management-New-Grad](https://github.com/jobright-ai/2026-Product-Management-New-Grad)
  — Product Management

Product Management listings (from either repo) post to a **separate Slack
channel** than everything else - see "Multiple Slack channels" below.

Runs on a schedule (every 5 min) via GitHub Actions, since we don't own either
repo and can't attach a `push`-triggered workflow to them. Each run:

1. Fetches each source's README (raw, from that repo's own default branch).
2. Parses out listing rows — SimplifyJobs uses HTML tables split by category
   heading; Jobright uses a single plain markdown pipe table between
   `TABLE_START`/`TABLE_END` markers. `scripts/watch_new_grad.py` picks the
   right parser per source.
3. Diffs against `data/new-grad-seen.json` (committed back to this repo,
   IDs namespaced per source) to find new ones.
4. **Cross-source dedup**: the same real posting often shows up on both
   SimplifyJobs and Jobright (they scrape overlapping sources). Since Jobright
   doesn't expose the actual employer application URL (its links are its own
   client-rendered redirect pages, not something we can resolve to a canonical
   URL), matching is done by normalized `(company, role)` + overlapping
   location instead. A match against a *different* source is suppressed
   (recorded as seen, but not notified); repeats *within* the same source are
   left alone, since SimplifyJobs legitimately reposts the same role over time
   under a new id. This is a heuristic, not exact — false negatives (missed
   dupes) are more likely than false positives, given the location-overlap
   check requires normalized company+role to match exactly first.
5. Posts new (non-duplicate) listings to a Slack webhook.

The **first run seeds the state file silently** (no notifications) so you don't
get flooded with thousands of historical listings on day one.

## P0 (big-company) alerts

A separate workflow, `.github/workflows/watch-p0.yml`, watches the same
sources but filters to just "big company" listings and posts them to their
own Slack channel — for when you want to know the second Google/Meta/etc.
posts something, not just eventually.

- **Detection** (`is_big_company()` in `scripts/watch_new_grad.py`): a listing
  counts if SimplifyJobs already tagged it 🔥 (their own FAANG+ marker), or if
  its company name matches `BIG_COMPANY_ALLOWLIST` (a plain list of names,
  pre-filled with FAANG+/notable tech + quant-trading companies — edit it
  directly to add/remove companies).
- **Not exclusive**: a big-company listing still posts to its normal category
  channel as usual; the P0 channel is an *additional* fan-out, not a
  replacement.
- **Polling rate**: GitHub Actions' `schedule` trigger has a hard 5-minute
  floor, so `watch-p0.yml` works around it by looping *inside* one
  5-minute-triggered run — it calls `scripts/watch_p0.py`, sleeps ~60s,
  and repeats 4 times per invocation, giving ~60–90s effective granularity
  for this specific check.
- **Own state file**: `data/p0-seen.json`, separate from
  `data/new-grad-seen.json`, so the two workflows' commits never touch the
  same file. Both workflows now retry `git pull --rebase` + `git push` a few
  times on failure, since running two workflows against the same repo makes
  push races (not file conflicts — just "the branch moved") more likely.
- Requires its own secret: `SLACK_P0_WEBHOOK_URL`.
- Uses the same cross-source dedup as the main watcher (against its own state
  file), so the same big-company job appearing on both SimplifyJobs and
  Jobright only pages once.

## Setup

1. Create a GitHub repo and push this folder to it:
   ```bash
   git init
   git add -A
   git commit -m "chore: initial watcher setup"
   gh repo create new-grad-watcher --private --source=. --remote=origin --push
   ```
2. Add repo secrets (Settings → Secrets and variables → Actions):
   - `SLACK_WEBHOOK_URL` — an [incoming webhook URL](https://api.slack.com/messaging/webhooks) for the default channel.
   - `SLACK_PM_WEBHOOK_URL` — a second incoming webhook, pointed at whatever
     channel you want Product Management listings to land in instead (e.g.
     `#new-grad-pm`). A Slack app can have multiple incoming webhooks, one per
     channel - see "Multiple Slack channels" below.
   - `SLACK_P0_WEBHOOK_URL` — a third incoming webhook for the big-company P0
     channel - see "P0 (big-company) alerts" below.
3. Trigger the workflow once manually (Actions tab → "Watch New-Grad-Positions" →
   Run workflow) to seed `data/new-grad-seen.json`, or just let the first
   scheduled run do it.
4. After that, new listings show up in Slack as they're added upstream.

## Adjusting

- **Polling frequency**: edit the `cron` line in
  `.github/workflows/watch-new-grad.yml`. 5 minutes is the shortest interval
  GitHub Actions' `schedule` trigger supports.
- **Sources / categories**: edit `SOURCES` in `scripts/watch_new_grad.py`. Each
  entry has a `parser` (`"html_categories"` or `"markdown_table"`) and a
  `categories` map. For an `html_categories` source, valid SimplifyJobs
  headings (as of writing) are Software Engineering, Product Management, Data
  Science AI & ML, Quantitative Finance, and Hardware Engineering — check the
  live README for exact heading text if they rename a section. To add a new
  repo, add an entry to `SOURCES`; if its README isn't in one of these two
  formats, `collect_listings()` will need a third parser branch.
- **Multiple Slack channels**: `CATEGORY_WEBHOOK_ENV` in
  `scripts/watch_new_grad.py` maps a category name to an env var holding a
  webhook URL; anything not listed falls back to `SLACK_WEBHOOK_URL`. To route
  another category to its own channel, add an entry there, add the matching
  secret in GitHub, and pass it through in the `env:` block of
  `.github/workflows/watch-new-grad.yml`. Note this routes by **category**,
  not by source repo - e.g. both Jobright's and SimplifyJobs' Product
  Management listings share `SLACK_PM_WEBHOOK_URL`, since that's the option
  picked when this was set up.
