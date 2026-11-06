#!/usr/bin/env python3
"""Graph Virtaal downloads from GitHub releases and SourceForge.

Fetches two sources and writes a self-contained HTML page of charts:

- GitHub: the ``download-stats`` branch of translate/virtaal, where the
  ``track-download-stats`` workflow appends a daily snapshot of each release
  asset's running download total to ``download-stats.csv``.
- SourceForge: the download statistics API for
  ``projects/translate/files/Virtaal`` (monthly totals, per-release folder
  totals, operating systems and countries).

Usage::

    python3 devsupport/download_stats.py [-o download-stats.html]
                                         [--github-csv download-stats.csv]

Only the standard library is needed. The page layout lives in
``download_stats.html`` next to this script; the data is inlined into it.
"""

import argparse
import collections
import csv
import datetime
import io
import json
import re
import sys
import urllib.parse
import urllib.request
from pathlib import Path

GITHUB_CSV_URL = (
    "https://raw.githubusercontent.com/translate/virtaal/"
    "download-stats/download-stats.csv"
)
SF_FILES_URL = "https://sourceforge.net/projects/translate/files/Virtaal/"
SF_START_DATE = "2007-01-01"
TEMPLATE = Path(__file__).with_name("download_stats.html")
USER_AGENT = "virtaal-download-stats"

PLATFORMS = ["Windows", "macOS", "Linux", "Translation packs", "Source & notes"]
TOP_COUNTRIES = 12


def fetch(url):
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=60) as response:
        return response.read().decode("utf-8")


def sf_stats(path, end_date):
    query = urllib.parse.urlencode(
        {"start_date": SF_START_DATE, "end_date": end_date, "period": "monthly"}
    )
    return json.loads(fetch(f"{SF_FILES_URL}{path}stats/json?{query}"))


def platform(asset):
    """Group a release asset by the platform it installs on."""
    ext = asset.rsplit(".", 1)[-1]
    if ext == "exe":
        return "Windows"
    if ext == "dmg":
        return "macOS"
    if ext in ("AppImage", "flatpak", "rpm", "deb"):
        return "Linux"
    if "translation-packs" in asset:
        return "Translation packs"
    return "Source & notes"


def github_data(csv_text):
    rows = list(csv.DictReader(io.StringIO(csv_text)))
    if not rows:
        sys.exit("The GitHub download-stats CSV has no rows.")
    dates = sorted({row["date"] for row in rows})
    last = dates[-1]

    cumulative = {date: collections.Counter() for date in dates}
    by_tag = collections.Counter()
    first_seen = {}
    for row in rows:
        count = int(row["downloads"])
        cumulative[row["date"]][platform(row["asset"])] += count
        first_seen.setdefault(row["tag"], row["date"])
        if row["date"] == last:
            by_tag[row["tag"]] += count

    daily = [
        [date] + [cumulative[date][p] - cumulative[prev][p] for p in PLATFORMS]
        for prev, date in zip(dates, dates[1:])
    ]
    return {
        "dates": dates,
        "platforms": PLATFORMS,
        "cum": [[date, sum(cumulative[date].values())] for date in dates],
        "daily": daily,
        "byPlatform": [[p, cumulative[last][p]] for p in PLATFORMS],
        "byTag": sorted(by_tag.items()),
        # Tags that first appear after the first snapshot were released
        # while tracking was running; the page marks them on the timeline.
        "released": [[t, d] for t, d in first_seen.items() if d != dates[0]],
    }


def sourceforge_data():
    end_date = datetime.date.today().isoformat()
    project = sf_stats("", end_date)

    monthly = [[month[:7], count] for month, count in project["downloads"]]
    if project.get("last_period_incomplete"):
        monthly = monthly[:-1]
    # Drop the empty months before the first upload.
    while monthly and not monthly[0][1]:
        monthly.pop(0)

    listing = fetch(SF_FILES_URL)
    folders = sorted(
        set(re.findall(r'href="/projects/translate/files/Virtaal/([^"/]+)/"', listing)),
        key=lambda v: [int(p) if p.isdigit() else p for p in re.split(r"[.-]", v)],
    )
    versions = []
    for folder in folders:
        stats = sf_stats(urllib.parse.quote(folder) + "/", end_date)
        months = [m[:7] for m, count in stats["downloads"] if count]
        if months:
            versions.append([folder, stats["total"], months[0]])

    return {
        "total": project["total"],
        "updated": project["stats_updated"],
        "monthly": monthly,
        "versions": versions,
        "oses": project["oses"],
        "countries": project["countries"][:TOP_COUNTRIES],
        "countryCount": len(project["countries"]),
    }


def js_const(name, value):
    # "</" would end the inline <script> early.
    payload = json.dumps(value, separators=(",", ":")).replace("</", "<\\/")
    return f"const {name}={payload};"


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "-o", "--output", default="download-stats.html",
        help="HTML file to write (default: %(default)s)",
    )
    parser.add_argument(
        "--github-csv",
        help="read the GitHub download-stats CSV from this file instead of "
             "fetching it from the download-stats branch",
    )
    args = parser.parse_args()

    if args.github_csv:
        csv_text = Path(args.github_csv).read_text(encoding="utf-8")
    else:
        print("Fetching GitHub download-stats CSV...")
        csv_text = fetch(GITHUB_CSV_URL)
    gh = github_data(csv_text)

    print("Fetching SourceForge statistics...")
    sf = sourceforge_data()

    page = TEMPLATE.read_text(encoding="utf-8")
    page = page.replace("/*GH*/", js_const("GH", gh), 1)
    page = page.replace("/*DATA*/", js_const("DATA", sf), 1)
    Path(args.output).write_text(page, encoding="utf-8")

    print(
        f"Wrote {args.output}: {gh['cum'][-1][1]:,} GitHub downloads "
        f"(to {gh['dates'][-1]}), {sf['total']:,} SourceForge downloads."
    )


if __name__ == "__main__":
    main()
