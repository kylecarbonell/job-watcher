#!/usr/bin/env python3
"""Watch SimplifyJobs/New-Grad-Positions for new listings in selected categories
and notify Slack + Discord. State (which listings we've already seen) is kept in
DATA_FILE, committed back to the repo by the workflow.
"""
import hashlib
import json
import os
import re
import sys
import urllib.request
from html.parser import HTMLParser

README_URL = "https://raw.githubusercontent.com/SimplifyJobs/New-Grad-Positions/dev/README.md"
DATA_FILE = "data/new-grad-seen.json"

# category label -> exact heading text as it appears in the README (without "## ")
CATEGORIES = {
    "Software Engineering": "💻 Software Engineering New Grad Roles",
    "Data Science, AI & ML": "🤖 Data Science, AI & Machine Learning New Grad Roles",
}

SLACK_WEBHOOK_URL = os.environ.get("SLACK_WEBHOOK_URL", "")
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "")


class RowParser(HTMLParser):
    """Parses the <tbody> rows of the first HTML table in a chunk of the README."""

    def __init__(self):
        super().__init__()
        self.in_tbody = False
        self.cell_index = -1
        self.rows = []
        self._row = None
        self._buf = ""
        self._hrefs = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "tbody":
            self.in_tbody = True
        elif tag == "tr" and self.in_tbody:
            self._row = ["", "", "", "", ""]
            self.cell_index = -1
        elif tag == "td" and self.in_tbody:
            self.cell_index += 1
            self._buf = ""
            self._hrefs = []
        elif tag == "a" and self.in_tbody and self.cell_index == 3:
            href = attrs.get("href", "")
            if href:
                self._hrefs.append(href)
        elif tag == "br" and self.in_tbody:
            self._buf += ", "

    def handle_endtag(self, tag):
        # the README uses stray "</br>" (no opening "<br>") for line breaks
        if tag == "br" and self.in_tbody:
            self._buf += ", "
        elif tag == "tbody":
            self.in_tbody = False
        elif tag == "td" and self.in_tbody and self._row is not None:
            text = re.sub(r"\s+", " ", self._buf).strip()
            if self.cell_index == 3:
                apply_url = next((h for h in self._hrefs if "simplify.jobs/p/" not in h), "")
                simplify_url = next((h for h in self._hrefs if "simplify.jobs/p/" in h), "")
                self._row[3] = apply_url
                self._row.append(simplify_url)
            elif 0 <= self.cell_index < 3:
                self._row[self.cell_index] = text
            elif self.cell_index == 4:
                self._row[4] = text
        elif tag == "tr" and self.in_tbody and self._row is not None:
            if any(self._row):
                self.rows.append(self._row)
            self._row = None

    def handle_data(self, data):
        if self.in_tbody and self._row is not None and self.cell_index != 3:
            self._buf += data
        elif self.in_tbody and self._row is not None and self.cell_index == 3:
            # ignore raw text (img alt text etc.) inside the application cell
            pass


def fetch_readme() -> str:
    with urllib.request.urlopen(README_URL, timeout=30) as resp:
        return resp.read().decode("utf-8")


def slice_category(readme: str, heading: str) -> str:
    start_marker = f"## {heading}"
    start = readme.find(start_marker)
    if start == -1:
        raise RuntimeError(f"heading not found in README: {heading!r}")
    start += len(start_marker)
    next_heading = readme.find("\n## ", start)
    end = next_heading if next_heading != -1 else len(readme)
    return readme[start:end]


def parse_listings(chunk: str, category: str):
    parser = RowParser()
    parser.feed(chunk)
    listings = []
    last_company = ""
    for company, role, location, apply_url, age, simplify_url in parser.rows:
        if company == "↳":
            company = last_company
        elif company:
            last_company = company
        if not company or not role:
            continue
        if simplify_url:
            m = re.search(r"simplify\.jobs/p/([0-9a-fA-F-]+)", simplify_url)
            listing_id = m.group(1) if m else simplify_url
        else:
            listing_id = hashlib.sha1(f"{category}|{company}|{role}|{location}".encode()).hexdigest()
        listings.append(
            {
                "id": listing_id,
                "category": category,
                "company": company,
                "role": role,
                "location": location,
                "apply_url": apply_url or simplify_url,
                "age": age,
            }
        )
    return listings


def load_seen_ids() -> set:
    if not os.path.exists(DATA_FILE):
        return set()
    with open(DATA_FILE, "r") as f:
        return set(json.load(f).get("seen_ids", []))


def save_seen_ids(ids: set) -> None:
    os.makedirs(os.path.dirname(DATA_FILE), exist_ok=True)
    with open(DATA_FILE, "w") as f:
        json.dump({"seen_ids": sorted(ids)}, f, indent=2)
        f.write("\n")


def chunked(items, size):
    for i in range(0, len(items), size):
        yield items[i : i + size]


def post_json(url: str, payload: dict) -> None:
    if not url:
        return
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=15)
    except Exception as e:  # noqa: BLE001 - notify best-effort, don't fail the run
        print(f"warning: failed to post to {url}: {e}", file=sys.stderr)


def notify_slack(listings: list) -> None:
    if not SLACK_WEBHOOK_URL:
        return
    for batch in chunked(listings, 15):
        lines = [
            f"*{l['company']}* — {l['role']} ({l['location']})\n<{l['apply_url']}|Apply>"
            for l in batch
        ]
        post_json(SLACK_WEBHOOK_URL, {"text": "\n\n".join(lines)})


def notify_discord(listings: list) -> None:
    if not DISCORD_WEBHOOK_URL:
        return
    for batch in chunked(listings, 8):
        lines = [f"**{l['company']}** — {l['role']} ({l['location']})\n{l['apply_url']}" for l in batch]
        post_json(DISCORD_WEBHOOK_URL, {"content": "\n\n".join(lines)})


def main() -> None:
    readme = fetch_readme()
    all_listings = []
    for category, heading in CATEGORIES.items():
        chunk = slice_category(readme, heading)
        all_listings.extend(parse_listings(chunk, category))

    current_ids = {l["id"] for l in all_listings}
    first_run = not os.path.exists(DATA_FILE)
    seen_ids = load_seen_ids()

    new_listings = [l for l in all_listings if l["id"] not in seen_ids]

    if first_run:
        print(f"first run: seeding state with {len(current_ids)} existing listings, no notifications sent")
    elif new_listings:
        print(f"found {len(new_listings)} new listing(s)")
        for l in new_listings:
            print(f"  - [{l['category']}] {l['company']} - {l['role']} ({l['location']})")
        notify_slack(new_listings)
        notify_discord(new_listings)
    else:
        print("no new listings")

    save_seen_ids(seen_ids | current_ids)


if __name__ == "__main__":
    main()
