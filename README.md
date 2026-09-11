# new-grad-watcher

Watches multiple new-grad job-listing repos for new postings and posts them to
Slack. Currently configured sources:

- [SimplifyJobs/New-Grad-Positions](https://github.com/SimplifyJobs/New-Grad-Positions)
  — Software Engineering + Data Science, AI & ML categories
- [jobright-ai/2026-Software-Engineer-New-Grad](https://github.com/jobright-ai/2026-Software-Engineer-New-Grad)
  — Software Engineering

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

## Setup

1. Create a GitHub repo and push this folder to it:
   ```bash
   git init
   git add -A
   git commit -m "chore: initial watcher setup"
   gh repo create new-grad-watcher --private --source=. --remote=origin --push
   ```
2. Add a repo secret (Settings → Secrets and variables → Actions):
   - `SLACK_WEBHOOK_URL` — an [incoming webhook URL](https://api.slack.com/messaging/webhooks) for the Slack channel you want.
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
