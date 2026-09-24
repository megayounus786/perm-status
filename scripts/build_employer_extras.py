#!/usr/bin/env python3
"""build_employer_extras.py — offline extras for the static employer pages.

Reads the raw DOL OFLC PERM disclosure workbooks (not in the site repo; local
copies under ../dl/) and, for every employer in data/employers_top300.json,
computes what the worker API does not carry:

  states   top primary-worksite states  [[state, cases], ...]
  cities   top primary-worksite cities  [[city, state, cases], ...]
  n_rows   PERM cases for the employer in the window (all dispositions)
  proc     per decision fiscal year, filing→decision days for CERTIFIED cases:
           {FY: {n, median, p25, p75}}

Employer matching uses the same clean_name()+slugify() as the H-1B/PERM store
build (h1b-build/multi/common.py) against the registry's member slugs (the
canonical slug, the display-name slug and every reunified member slug), so the
rows roll up exactly like the store's permByYear.

Output: data/employers_top300_extras.json (committed; the deploy-time render
reads it if present). Re-run after a new disclosure file is downloaded.

Usage: python3 scripts/build_employer_extras.py [--dl ../dl] [--out data/employers_top300_extras.json]
"""
import argparse
import datetime as dt
import glob
import json
import os
import re
import statistics
import sys
import time
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "..", "h1b-build", "multi"))
from common import clean_name, slugify, header_index, pick, is_certified  # noqa: E402

import openpyxl  # noqa: E402

STATE_NAMES = {
    "ALABAMA": "AL", "ALASKA": "AK", "ARIZONA": "AZ", "ARKANSAS": "AR", "CALIFORNIA": "CA", "COLORADO": "CO",
    "CONNECTICUT": "CT", "DELAWARE": "DE", "DISTRICT OF COLUMBIA": "DC", "FLORIDA": "FL", "GEORGIA": "GA",
    "HAWAII": "HI", "IDAHO": "ID", "ILLINOIS": "IL", "INDIANA": "IN", "IOWA": "IA", "KANSAS": "KS", "KENTUCKY": "KY",
    "LOUISIANA": "LA", "MAINE": "ME", "MARYLAND": "MD", "MASSACHUSETTS": "MA", "MICHIGAN": "MI", "MINNESOTA": "MN",
    "MISSISSIPPI": "MS", "MISSOURI": "MO", "MONTANA": "MT", "NEBRASKA": "NE", "NEVADA": "NV", "NEW HAMPSHIRE": "NH",
    "NEW JERSEY": "NJ", "NEW MEXICO": "NM", "NEW YORK": "NY", "NORTH CAROLINA": "NC", "NORTH DAKOTA": "ND", "OHIO": "OH",
    "OKLAHOMA": "OK", "OREGON": "OR", "PENNSYLVANIA": "PA", "RHODE ISLAND": "RI", "SOUTH CAROLINA": "SC",
    "SOUTH DAKOTA": "SD", "TENNESSEE": "TN", "TEXAS": "TX", "UTAH": "UT", "VERMONT": "VT", "VIRGINIA": "VA",
    "WASHINGTON": "WA", "WEST VIRGINIA": "WV", "WISCONSIN": "WI", "WYOMING": "WY", "PUERTO RICO": "PR", "GUAM": "GU",
    "VIRGIN ISLANDS": "VI", "NORTHERN MARIANA ISLANDS": "MP", "AMERICAN SAMOA": "AS",
}
STATE_CODES = set(STATE_NAMES.values())


def norm_state(v):
    if v is None:
        return None
    s = str(v).strip().upper()
    if not s:
        return None
    if s in STATE_CODES:
        return s
    return STATE_NAMES.get(s)


def norm_city(v):
    if v is None:
        return None
    s = re.sub(r"\s+", " ", str(v)).strip()
    if not s:
        return None
    return s.title().replace("'S", "'s")


def to_date(v):
    if isinstance(v, dt.datetime):
        return v.date()
    if isinstance(v, dt.date):
        return v
    if isinstance(v, str):
        for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%Y-%m-%d %H:%M:%S"):
            try:
                return dt.datetime.strptime(v.strip()[:19], fmt).date()
            except ValueError:
                pass
    return None


def fy_of(path):
    m = re.search(r"FY(\d{4})", os.path.basename(path))
    return "FY" + m.group(1) if m else None


def pctile(sorted_vals, p):
    if not sorted_vals:
        return None
    k = (len(sorted_vals) - 1) * p
    f = int(k)
    c = min(f + 1, len(sorted_vals) - 1)
    return round(sorted_vals[f] + (sorted_vals[c] - sorted_vals[f]) * (k - f))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dl", default=os.path.join(ROOT, "..", "dl"))
    ap.add_argument("--out", default=os.path.join(ROOT, "data", "employers_top300_extras.json"))
    ap.add_argument("--registry", default=os.path.join(ROOT, "data", "employers_top300.json"))
    a = ap.parse_args()

    reg = json.load(open(a.registry, encoding="utf-8"))
    member_map = {}
    for m in reg["employers"]:
        h = m.get("h1b") or {}
        for s in [m["slug"], slugify(m["name"]), m.get("h1bSlug")] + list(h.get("memberSlugs") or []):
            if s:
                member_map.setdefault(s, m["slug"])
    print(f"registry: {len(reg['employers'])} employers, {len(member_map)} member slugs", flush=True)

    files = sorted(glob.glob(os.path.join(a.dl, "PERM_Disclosure_Data_FY20*.xlsx")))
    if not files:
        print("no PERM disclosure files found", file=sys.stderr)
        return 1
    states = defaultdict(lambda: defaultdict(int))
    cities = defaultdict(lambda: defaultdict(int))
    rows_n = defaultdict(int)
    days = defaultdict(lambda: defaultdict(list))  # slug -> fy -> [days]
    matched_total = 0
    for path in files:
        fy = fy_of(path)
        t0 = time.time()
        wb = openpyxl.load_workbook(path, read_only=True)
        ws = wb.active
        it = ws.iter_rows(values_only=True)
        idx = header_index(next(it))
        c_status = pick(idx, "CASE_STATUS")
        c_emp = pick(idx, "EMPLOYER_NAME", "EMP_BUSINESS_NAME", "EMP_LEGAL_BUSINESS_NAME")
        c_rcv = pick(idx, "RECEIVED_DATE", "CASE_RECEIVED_DATE")
        c_dec = pick(idx, "DECISION_DATE")
        c_city = pick(idx, "WORKSITE_CITY", "PRIMARY_WORKSITE_CITY", "JOB_INFO_WORK_CITY")
        c_state = pick(idx, "WORKSITE_STATE", "PRIMARY_WORKSITE_STATE", "JOB_INFO_WORK_STATE")
        n_rows = matched = 0
        for r in it:
            n_rows += 1
            emp = r[c_emp] if c_emp is not None else None
            if not emp:
                continue
            slug = slugify(clean_name(emp))
            key = member_map.get(slug)
            if not key:
                continue
            matched += 1
            rows_n[key] += 1
            st = norm_state(r[c_state]) if c_state is not None else None
            if st:
                states[key][st] += 1
                city = norm_city(r[c_city]) if c_city is not None else None
                if city:
                    cities[key][(city, st)] += 1
            if c_status is not None and is_certified(r[c_status]) and c_rcv is not None and c_dec is not None:
                d1, d2 = to_date(r[c_rcv]), to_date(r[c_dec])
                if d1 and d2:
                    dd = (d2 - d1).days
                    if 0 < dd < 3000:
                        days[key][fy].append(dd)
        wb.close()
        matched_total += matched
        print(f"{os.path.basename(path)}: {n_rows} rows, {matched} matched, {time.time() - t0:.0f}s", flush=True)

    out = {"generatedAt": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
           "files": [os.path.basename(f) for f in files],
           "window": "FY2020 – FY2026 Q2",
           "windowNote": "DOL PERM disclosure files, grouped by the fiscal year of the DOL decision",
           "matchedRows": matched_total, "employers": {}}
    for key in rows_n:
        st = sorted(states[key].items(), key=lambda kv: -kv[1])[:8]
        ct = sorted(cities[key].items(), key=lambda kv: -kv[1])[:8]
        proc = {}
        for fy, lst in days[key].items():
            lst.sort()
            proc[fy] = {"n": len(lst), "median": round(statistics.median(lst)), "p25": pctile(lst, 0.25), "p75": pctile(lst, 0.75)}
        out["employers"][key] = {"n_rows": rows_n[key], "states": [[s, c] for s, c in st],
                                 "cities": [[c[0], c[1], n] for c, n in ct], "proc": proc}
    tmp = a.out + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(out, f, separators=(",", ":"), ensure_ascii=False)
    os.replace(tmp, a.out)
    print(f"wrote {a.out}: {len(out['employers'])} employers, {matched_total} rows matched", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
