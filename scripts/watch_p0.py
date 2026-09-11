#!/usr/bin/env python3
"""Faster-polling scanner for big-company listings only. Reuses SOURCES,
parsers, and the cross-source dedup logic from watch_new_grad.py, but keeps
its own state file (DATA_FILE) so it doesn't race with that script's commits,
and posts to its own Slack channel (SLACK_P0_WEBHOOK_URL).

Intended to be invoked repeatedly (every ~60s) within a single workflow run,
since GitHub Actions' schedule trigger can't go tighter than 5 minutes on its
own - see .github/workflows/watch-p0.yml.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import watch_new_grad as w

w.DATA_FILE = "data/p0-seen.json"
SLACK_P0_WEBHOOK_URL = os.environ.get("SLACK_P0_WEBHOOK_URL", "")


def notify_p0(listings: list) -> None:
    if not SLACK_P0_WEBHOOK_URL:
        return
    for batch in w.chunked(listings, 15):
        lines = [
            f"🚨 *{l['company']}* — {l['role']} ({l['location']})\n<{l['apply_url']}|Apply> · _{l['source']}_"
            for l in batch
        ]
        w.post_json(SLACK_P0_WEBHOOK_URL, {"text": "\n\n".join(lines)})


def main() -> None:
    all_listings = [l for l in w.collect_listings() if w.is_big_company(l)]

    first_run = not os.path.exists(w.DATA_FILE)
    seen_records = w.load_seen_records()
    seen_ids = {r["id"] for r in seen_records}

    primary_ids = w.dedupe_cross_source(all_listings, seen_records)
    unseen_by_id = {l["id"]: l for l in all_listings if l["id"] not in seen_ids}
    new_listings = [l for l in unseen_by_id.values() if l["id"] in primary_ids]
    suppressed = [l for l in unseen_by_id.values() if l["id"] not in primary_ids]

    if first_run:
        print(f"first run: seeding p0 state with {len(all_listings)} existing big-company listings, no notifications sent")
    else:
        if new_listings:
            print(f"found {len(new_listings)} new big-company listing(s)")
            for l in new_listings:
                print(f"  - [{l['source']}/{l['category']}] {l['company']} - {l['role']} ({l['location']})")
        if suppressed:
            print(f"suppressed {len(suppressed)} as cross-source duplicate(s)")
        if not new_listings and not suppressed:
            print("no new big-company listings")
        notify_p0(new_listings)

    existing_ids = set(seen_ids)
    updated_records = list(seen_records)
    for l in all_listings:
        if l["id"] not in existing_ids:
            updated_records.append(
                {
                    "id": l["id"],
                    "key": w.dedupe_key(l),
                    "location": w.normalize_location(l["location"]),
                    "source_key": l["source_key"],
                }
            )
            existing_ids.add(l["id"])
    w.save_seen_records(updated_records)


if __name__ == "__main__":
    main()
