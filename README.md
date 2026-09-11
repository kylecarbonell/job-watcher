# new-grad-watcher

Watches [SimplifyJobs/New-Grad-Positions](https://github.com/SimplifyJobs/New-Grad-Positions)
for new job listings in the **Software Engineering** and **Data Science, AI & ML**
categories, and posts new ones to Slack + Discord.

Runs on a schedule (every 30 min) via GitHub Actions, since we don't own that
repo and can't attach a `push`-triggered workflow to it. Each run:

1. Fetches their README (raw, from the `dev` branch — that's their default branch).
2. Parses out listing rows for the selected categories.
3. Diffs against `data/new-grad-seen.json` (committed back to this repo) to find new ones.
4. Posts new listings to Slack/Discord webhooks.

The **first run seeds the state file silently** (no notifications) so you don't
get flooded with ~1,800 historical listings on day one.

## Setup

1. Create a GitHub repo and push this folder to it:
   ```bash
   git init
   git add -A
   git commit -m "chore: initial watcher setup"
   gh repo create new-grad-watcher --private --source=. --remote=origin --push
   ```
2. Add two repo secrets (Settings → Secrets and variables → Actions):
   - `SLACK_WEBHOOK_URL` — an [incoming webhook URL](https://api.slack.com/messaging/webhooks) for the Slack channel you want.
   - `DISCORD_WEBHOOK_URL` — a [Discord webhook URL](https://support.discord.com/hc/en-us/articles/228383668-Intro-to-Webhooks) for the Discord channel you want.
   (Either can be left unset if you only want one of the two.)
3. Trigger the workflow once manually (Actions tab → "Watch New-Grad-Positions" →
   Run workflow) to seed `data/new-grad-seen.json`, or just let the first
   scheduled run do it.
4. After that, new listings show up in Slack/Discord as they're added upstream.

## Adjusting

- **Polling frequency**: edit the `cron` line in
  `.github/workflows/watch-new-grad.yml`.
- **Categories**: edit `CATEGORIES` in `scripts/watch_new_grad.py`. Valid
  headings (as of writing) are Software Engineering, Product Management,
  Data Science AI & ML, Quantitative Finance, and Hardware Engineering — check
  the current README for exact heading text if SimplifyJobs renames a section.
