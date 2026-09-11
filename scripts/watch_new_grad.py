#!/usr/bin/env python3
"""Watch multiple new-grad job-listing repos for new postings and notify Slack.
State (which listings we've already seen, namespaced per source) is kept in
DATA_FILE, committed back to the repo by the workflow.
"""
import hashlib
import json
import os
import re
import sys
import unicodedata
import urllib.request
from html.parser import HTMLParser

DATA_FILE = "data/new-grad-seen.json"

# Each source is a repo README to watch. "parser" selects how its table(s) are
# read: "html_categories" for SimplifyJobs-style repos (HTML <table>s under
# "## <emoji> <Category>" headings), "markdown_table" for a single plain
# markdown pipe table between the TABLE_START/TABLE_END markers.
SOURCES = [
    {
        "key": "simplifyjobs-new-grad",
        "label": "SimplifyJobs New Grad Positions",
        "readme_url": "https://raw.githubusercontent.com/SimplifyJobs/New-Grad-Positions/dev/README.md",
        "parser": "html_categories",
        "categories": {
            "Software Engineering": "💻 Software Engineering New Grad Roles",
            "Data Science, AI & ML": "🤖 Data Science, AI & Machine Learning New Grad Roles",
            "Product Management": "📱 Product Management New Grad Roles",
        },
    },
    {
        "key": "jobright-swe-new-grad",
        "label": "Jobright SWE New Grad",
        "readme_url": "https://raw.githubusercontent.com/jobright-ai/2026-Software-Engineer-New-Grad/master/README.md",
        "parser": "markdown_table",
        "categories": {
            "Software Engineering": None,
        },
    },
    {
        "key": "jobright-pm-new-grad",
        "label": "Jobright PM New Grad",
        "readme_url": "https://raw.githubusercontent.com/jobright-ai/2026-Product-Management-New-Grad/master/README.md",
        "parser": "markdown_table",
        "categories": {
            "Product Management": None,
        },
    },
]

# Which Slack webhook (env var name) a listing's category should be posted
# to. Anything not listed here falls back to SLACK_WEBHOOK_URL.
CATEGORY_WEBHOOK_ENV = {
    "Product Management": "SLACK_PM_WEBHOOK_URL",
}

SLACK_WEBHOOK_URL = os.environ.get("SLACK_WEBHOOK_URL", "")

LINK_RE = re.compile(r"\[([^\]]*)\]\(([^)]*)\)")


class HtmlRowParser(HTMLParser):
    """Parses the <tbody> rows of the first HTML table in a chunk of a README."""

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
        # some READMEs use stray "</br>" (no opening "<br>") for line breaks
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


def fetch(url: str) -> str:
    with urllib.request.urlopen(url, timeout=30) as resp:
        return resp.read().decode("utf-8")


def slice_category_html(readme: str, heading: str) -> str:
    start_marker = f"## {heading}"
    start = readme.find(start_marker)
    if start == -1:
        raise RuntimeError(f"heading not found in README: {heading!r}")
    start += len(start_marker)
    next_heading = readme.find("\n## ", start)
    end = next_heading if next_heading != -1 else len(readme)
    return readme[start:end]


def slice_table_markers(readme: str) -> str:
    start_marker = "TABLE_START (DO NOT CHANGE THIS LINE) -->"
    end_marker = "TABLE_END (DO NOT CHANGE THIS LINE) -->"
    start = readme.find(start_marker)
    end = readme.find(end_marker)
    if start == -1 or end == -1:
        raise RuntimeError("TABLE_START/TABLE_END markers not found in README")
    return readme[start + len(start_marker) : end]


def parse_html_listings(chunk: str, category: str, source: dict):
    parser = HtmlRowParser()
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
            raw_id = m.group(1) if m else simplify_url
        else:
            raw_id = hashlib.sha1(f"{category}|{company}|{role}|{location}".encode()).hexdigest()
        listings.append(
            {
                "id": f"{source['key']}:{raw_id}",
                "source": source["label"],
                "source_key": source["key"],
                "category": category,
                "company": company,
                "role": role,
                "location": location,
                "apply_url": apply_url or simplify_url,
            }
        )
    return listings


def parse_markdown_listings(chunk: str, category: str, source: dict):
    listings = []
    last_company = ""
    for line in chunk.splitlines():
        line = line.strip()
        if not line.startswith("|"):
            continue
        if re.match(r"^\|[\s:|-]+\|?$", line):
            continue  # header separator row
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) < 5:
            continue
        company_cell, role_cell, location, _work_model, posted = cells[:5]

        if company_cell == "↳":
            company = last_company
        else:
            m = LINK_RE.search(company_cell)
            company = (m.group(1) if m else company_cell).strip("* ")
            if company:
                last_company = company

        role_match = LINK_RE.search(role_cell)
        role = (role_match.group(1) if role_match else role_cell).strip("* ")
        apply_url = role_match.group(2) if role_match else ""

        if not company or not role or company.lower() == "company" or role.lower() == "job title":
            continue

        id_match = re.search(r"info/([0-9a-fA-F]+)", apply_url)
        raw_id = id_match.group(1) if id_match else (apply_url or f"{company}|{role}|{location}")
        listings.append(
            {
                "id": f"{source['key']}:{raw_id}",
                "source": source["label"],
                "source_key": source["key"],
                "category": category,
                "company": company,
                "role": role,
                "location": location,
                "apply_url": apply_url,
            }
        )
    return listings


def collect_listings() -> list:
    all_listings = []
    for source in SOURCES:
        readme = fetch(source["readme_url"])
        if source["parser"] == "html_categories":
            for category, heading in source["categories"].items():
                chunk = slice_category_html(readme, heading)
                all_listings.extend(parse_html_listings(chunk, category, source))
        elif source["parser"] == "markdown_table":
            chunk = slice_table_markers(readme)
            for category in source["categories"]:
                all_listings.extend(parse_markdown_listings(chunk, category, source))
        else:
            raise RuntimeError(f"unknown parser type: {source['parser']!r}")
    return all_listings


# Different sources describe the same city differently ("SF" vs "San
# Francisco, CA, United States") - expand common ones so location overlap
# checks below can actually match them.
CITY_ALIASES = {
    "sf": "san francisco",
    "nyc": "new york",
    "la": "los angeles",
    "dc": "washington",
}


def normalize_text(s: str) -> str:
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"[^\x00-\x7f]", " ", s)  # drop emoji / non-ascii flag glyphs
    s = s.lower()
    s = re.sub(r"[^a-z0-9]+", " ", s).strip()
    return s


def normalize_location(s: str) -> str:
    words = normalize_text(s).split()
    words = [CITY_ALIASES.get(w, w) for w in words]
    return " ".join(words)


def dedupe_key(listing: dict) -> str:
    return f"{normalize_text(listing['company'])}|||{normalize_text(listing['role'])}"


def locations_overlap(a: str, b: str) -> bool:
    if not a or not b:
        return False
    return a in b or b in a


def load_seen_records() -> list:
    if not os.path.exists(DATA_FILE):
        return []
    with open(DATA_FILE, "r") as f:
        return json.load(f).get("seen", [])


def save_seen_records(records: list) -> None:
    os.makedirs(os.path.dirname(DATA_FILE), exist_ok=True)
    with open(DATA_FILE, "w") as f:
        json.dump({"seen": records}, f, indent=2)
        f.write("\n")


def dedupe_cross_source(listings: list, seen_records: list) -> set:
    """Return the ids that should be treated as the "real" representative of
    their (company, role, overlapping location) cluster, considering both
    this run's listings and everything ever recorded. Only listings from a
    *different* source than an existing match are folded together - repeat
    postings within the same source (which already happen legitimately, e.g.
    a role reposted months later) are left untouched."""
    buckets: dict[str, list] = {}
    for r in seen_records:
        buckets.setdefault(r["key"], []).append(r)

    primary_ids = set()
    for l in listings:
        key = dedupe_key(l)
        loc = normalize_location(l["location"])
        bucket = buckets.setdefault(key, [])
        match = next(
            (r for r in bucket if r["source_key"] != l["source_key"] and locations_overlap(loc, r["location"])),
            None,
        )
        if match is None:
            primary_ids.add(l["id"])
        bucket.append({"id": l["id"], "key": key, "location": loc, "source_key": l["source_key"]})
    return primary_ids


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


def webhook_for_category(category: str) -> str:
    env_name = CATEGORY_WEBHOOK_ENV.get(category)
    if env_name:
        return os.environ.get(env_name, "")
    return SLACK_WEBHOOK_URL


def notify_slack(listings: list) -> None:
    by_category: dict[str, list] = {}
    for l in listings:
        by_category.setdefault(l["category"], []).append(l)

    for category, category_listings in by_category.items():
        webhook_url = webhook_for_category(category)
        if not webhook_url:
            continue
        for batch in chunked(category_listings, 15):
            lines = [
                f"*{l['company']}* — {l['role']} ({l['location']})\n<{l['apply_url']}|Apply> · _{l['source']}_"
                for l in batch
            ]
            post_json(webhook_url, {"text": "\n\n".join(lines)})


def main() -> None:
    all_listings = collect_listings()

    first_run = not os.path.exists(DATA_FILE)
    seen_records = load_seen_records()
    seen_ids = {r["id"] for r in seen_records}

    primary_ids = dedupe_cross_source(all_listings, seen_records)
    unseen_by_id = {l["id"]: l for l in all_listings if l["id"] not in seen_ids}
    new_listings = [l for l in unseen_by_id.values() if l["id"] in primary_ids]
    suppressed = [l for l in unseen_by_id.values() if l["id"] not in primary_ids]

    if first_run:
        print(f"first run: seeding state with {len(all_listings)} existing listings, no notifications sent")
    else:
        if new_listings:
            print(f"found {len(new_listings)} new listing(s)")
            for l in new_listings:
                print(f"  - [{l['source']}/{l['category']}] {l['company']} - {l['role']} ({l['location']})")
        if suppressed:
            print(f"suppressed {len(suppressed)} as cross-source duplicate(s):")
            for l in suppressed:
                print(f"  - [{l['source']}] {l['company']} - {l['role']} ({l['location']})")
        if not new_listings and not suppressed:
            print("no new listings")
        notify_slack(new_listings)

    existing_ids = set(seen_ids)
    updated_records = list(seen_records)
    for l in all_listings:
        if l["id"] not in existing_ids:
            updated_records.append(
                {
                    "id": l["id"],
                    "key": dedupe_key(l),
                    "location": normalize_location(l["location"]),
                    "source_key": l["source_key"],
                }
            )
            existing_ids.add(l["id"])
    save_seen_records(updated_records)


if __name__ == "__main__":
    main()
