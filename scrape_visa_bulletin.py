#!/usr/bin/env python3
"""Scrape the U.S. Department of State Visa Bulletin and emit JSON.

Output: data/visa_bulletin.json with Final Action Dates and Dates for Filing
for the Employment-Based preference categories.

Usage:
    python3 scrape_visa_bulletin.py            # current bulletin
    python3 scrape_visa_bulletin.py <url>      # explicit bulletin URL
    python3 scrape_visa_bulletin.py --history  # also build 12-month history

The DOS site occasionally changes table layouts; if the parser cannot find
the expected EB tables it exits non-zero so a human can intervene.
"""

from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timezone
from html.parser import HTMLParser
from urllib.parse import urljoin
from urllib.request import Request, urlopen

DOS_INDEX = "https://travel.state.gov/content/travel/en/legal/visa-law0/visa-bulletin.html"
USER_AGENT = "Mozilla/5.0 (compatible; ImmiLane-VisaBulletin/1.0; +https://immilane.com)"

CATEGORY_KEYS = {
    "1st": "1st",
    "2nd": "2nd",
    "3rd": "3rd",
    "other workers": "Other Workers",
    "4th": "4th",
    "certain religious workers": "Certain Religious Workers",
    "5th unreserved": "5th Unreserved",
    "5th set aside": None,  # resolved below
}

# Country header tokens we recognize, mapped to canonical keys.
COUNTRY_TOKENS = [
    ("all chargeability", "all_other"),
    ("china", "china"),
    ("india", "india"),
    ("mexico", "mexico"),
    ("philippines", "philippines"),
    ("el salvador", "el_salvador_guatemala_honduras"),
]


def fetch(url: str) -> str:
    req = Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen(req, timeout=30) as resp:
        return resp.read().decode("utf-8", errors="replace")


def find_current_bulletin_url(index_html: str) -> str:
    # The index page lists the current bulletin; pick the first
    # "visa-bulletin-for-<month>-<year>" link.
    match = re.search(
        r'href="([^"]*visa-bulletin-for-[a-z]+-\d{4}\.html)"',
        index_html,
        re.IGNORECASE,
    )
    if not match:
        raise RuntimeError("Could not find current bulletin link on index page")
    return urljoin(DOS_INDEX, match.group(1))


def parse_bulletin_month(html: str) -> str:
    m = re.search(
        r"Visa Bulletin\s+(?:For|for)\s+([A-Z][a-z]+)\s+(\d{4})", html
    )
    if m:
        return f"{m.group(1)} {m.group(2)}"
    m = re.search(r"<title>[^<]*?([A-Z][a-z]+)\s+(\d{4})[^<]*</title>", html)
    if m:
        return f"{m.group(1)} {m.group(2)}"
    return "Unknown"


class TableExtractor(HTMLParser):
    """Collect all <table> rows as lists of cell text."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tables: list[list[list[str]]] = []
        self._in_table = 0
        self._row: list[str] | None = None
        self._cell: list[str] | None = None
        self._current_table: list[list[str]] | None = None

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            self._in_table += 1
            self._current_table = []
        elif tag == "tr" and self._in_table:
            self._row = []
        elif tag in ("td", "th") and self._row is not None:
            self._cell = []
        elif tag == "br" and self._cell is not None:
            self._cell.append(" ")

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self._cell is not None:
            text = " ".join("".join(self._cell).split()).strip()
            self._row.append(text)  # type: ignore[union-attr]
            self._cell = None
        elif tag == "tr" and self._row is not None:
            if self._row:
                self._current_table.append(self._row)  # type: ignore[union-attr]
            self._row = None
        elif tag == "table" and self._in_table:
            self._in_table -= 1
            if self._current_table:
                self.tables.append(self._current_table)
            self._current_table = None

    def handle_data(self, data):
        if self._cell is not None:
            self._cell.append(data)


def extract_tables(html: str) -> list[list[list[str]]]:
    p = TableExtractor()
    p.feed(html)
    return p.tables


def is_eb_table(table: list[list[str]]) -> bool:
    """An EB table has a header row mentioning All Chargeability + a country,
    and a data row whose first cell is one of the EB category labels."""
    if len(table) < 2:
        return False
    flat = " ".join(c.lower() for row in table[:2] for c in row)
    if "all chargeability" not in flat:
        return False
    cats = " ".join(table[i][0].lower() if table[i] else "" for i in range(len(table)))
    return any(label in cats for label in ("1st", "2nd", "3rd", "4th", "5th"))


def parse_eb_table(table: list[list[str]]) -> dict[str, dict[str, str]]:
    header = [c.strip() for c in table[0]]
    # Map each header column index to a canonical country key.
    col_country: dict[int, str] = {}
    for idx, cell in enumerate(header):
        low = cell.lower()
        for token, key in COUNTRY_TOKENS:
            if token in low:
                col_country[idx] = key
                break

    out: dict[str, dict[str, str]] = {}
    for row in table[1:]:
        if not row:
            continue
        label_raw = row[0].strip()
        label_low = label_raw.lower()
        if not label_low:
            continue

        category = None
        if label_low.startswith("1st"):
            category = "1st"
        elif label_low.startswith("2nd"):
            category = "2nd"
        elif label_low.startswith("3rd") and "other" not in label_low:
            category = "3rd"
        elif "other workers" in label_low:
            category = "Other Workers"
        elif label_low.startswith("4th") and "certain" not in label_low:
            category = "4th"
        elif "certain religious" in label_low:
            category = "Certain Religious Workers"
        elif label_low.startswith("5th"):
            if "unreserved" in label_low:
                category = "5th Unreserved"
            elif "rural" in label_low:
                category = "5th Set Aside Rural"
            elif "high unemployment" in label_low or "targeted" in label_low:
                category = "5th Set Aside High Unemployment"
            elif "infrastructure" in label_low:
                category = "5th Set Aside Infrastructure"
        if not category:
            continue

        row_data: dict[str, str] = {}
        for idx, key in col_country.items():
            if idx < len(row):
                val = row[idx].strip().upper()
                # Normalize: bare "C", or DDMMMYY date, or "U" (unauthorized).
                if val in ("C", "U"):
                    row_data[key] = val
                elif re.match(r"^\d{2}[A-Z]{3}\d{2}$", val):
                    row_data[key] = val
                else:
                    # Sometimes contains footnote chars; strip and retry.
                    cleaned = re.sub(r"[^0-9A-Z]", "", val)
                    if re.match(r"^\d{2}[A-Z]{3}\d{2}$", cleaned):
                        row_data[key] = cleaned
                    elif cleaned == "C":
                        row_data[key] = "C"
                    else:
                        row_data[key] = val  # keep raw, render layer handles
        out[category] = row_data
    return out


def find_eb_tables(html: str) -> tuple[dict, dict]:
    tables = extract_tables(html)
    eb_tables = [t for t in tables if is_eb_table(t)]
    if len(eb_tables) < 2:
        raise RuntimeError(
            f"Expected at least 2 EB tables, found {len(eb_tables)}"
        )
    # The bulletin presents Final Action Dates first, then Dates for Filing.
    final_action = parse_eb_table(eb_tables[0])
    dates_filing = parse_eb_table(eb_tables[1])
    return final_action, dates_filing


MONTH_NAMES_FULL = [
    "january", "february", "march", "april", "may", "june",
    "july", "august", "september", "october", "november", "december",
]


def fiscal_year_dir(year: int, month_idx: int) -> int:
    # DOS files bulletins under the fiscal year (Oct-Dec roll forward).
    return year + 1 if month_idx >= 10 else year


def historical_bulletin_url(year: int, month_idx: int) -> str:
    fy = fiscal_year_dir(year, month_idx)
    month_name = MONTH_NAMES_FULL[month_idx - 1]
    return (
        f"https://travel.state.gov/content/travel/en/legal/visa-law0/"
        f"visa-bulletin/{fy}/visa-bulletin-for-{month_name}-{year}.html"
    )


def scrape_one(url: str) -> dict:
    html = fetch(url)
    month = parse_bulletin_month(html)
    final_action, dates_filing = find_eb_tables(html)
    return {
        "bulletin_date": month,
        "source_url": url,
        "final_action_dates": final_action,
        "dates_for_filing": dates_filing,
    }


def build_history(months_back: int = 12) -> list[dict]:
    """Walk backwards from the current month and collect parseable bulletins."""
    today = datetime.now(timezone.utc)
    year, month = today.year, today.month
    out: list[dict] = []
    attempts = 0
    while len(out) < months_back and attempts < months_back + 6:
        url = historical_bulletin_url(year, month)
        try:
            entry = scrape_one(url)
            out.append(entry)
            print(
                f"  + {entry['bulletin_date']:>14}  "
                f"({len(entry['final_action_dates'])} categories)",
                file=sys.stderr,
            )
        except Exception as exc:  # noqa: BLE001 — best-effort archive walk
            print(f"  ! skip {year}-{month:02d}: {exc}", file=sys.stderr)
        month -= 1
        if month == 0:
            month = 12
            year -= 1
        attempts += 1
    return out


def main() -> int:
    args = sys.argv[1:]
    do_history = False
    if "--history" in args:
        do_history = True
        args.remove("--history")

    if args:
        bulletin_url = args[0]
    else:
        index_html = fetch(DOS_INDEX)
        bulletin_url = find_current_bulletin_url(index_html)

    here = os.path.dirname(os.path.abspath(__file__))
    out_dir = os.path.join(here, "data")
    os.makedirs(out_dir, exist_ok=True)

    print(f"Fetching: {bulletin_url}", file=sys.stderr)
    current = scrape_one(bulletin_url)

    payload = {
        "bulletin_date": current["bulletin_date"],
        "last_updated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source_url": current["source_url"],
        "final_action_dates": current["final_action_dates"],
        "dates_for_filing": current["dates_for_filing"],
    }
    out_path = os.path.join(out_dir, "visa_bulletin.json")
    with open(out_path, "w") as f:
        json.dump(payload, f, indent=2)
    print(f"Wrote {out_path} ({current['bulletin_date']})", file=sys.stderr)

    if do_history:
        print("Building 24-month history…", file=sys.stderr)
        history = build_history(24)
        hist_payload = {
            "last_updated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "months": history,
        }
        hist_path = os.path.join(out_dir, "visa_bulletin_history.json")
        with open(hist_path, "w") as f:
            json.dump(hist_payload, f, indent=2)
        print(f"Wrote {hist_path} ({len(history)} months)", file=sys.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(main())
