#!/usr/bin/env python3
"""render_live.py — bake the live numbers into the static HTML at deploy time.

Runs in the deploy clone right before `wrangler pages deploy` (see
immilane_staging_cf_autoresync.sh / immilane_cf_autoresync.sh). It reads
  data/dashboard_data.json   (PERM scanner, pushed every ~3 h)
  data/visa_bulletin.json    (DOS bulletin, pushed on release)
and rewrites, IN PLACE, only these things:

  index.html            #updated-text, the four stat cards (#stats-grid)
  visa-bulletin.html    <title>/og:title/description, #bulletin-month,
                        #last-updated, #bulletin-title, both <tbody>s
  perm-status-check.html, perm-case-status.html, dol-case-status.html,
  perm-processing-time.html
                        every [data-live] value; FAQ JSON-LD numbers;
                        perm-processing-time <title>/og:title/description
                        ("PERM Processing Time Tracker — <Month Year>")
  sitemap.xml           real per-URL <lastmod> (git last commit of the file;
                        data-driven pages use the data timestamp if newer)

Everything the page JavaScript renders on load is reproduced byte-for-byte
(same markup as renderStatCards() / renderTable()), so the JS re-render is a
no-op visually and the raw HTML seen by crawlers already carries the numbers.

Idempotent: safe to run repeatedly on already-rendered files. Never touches
ads, scripts, layout or the DASHBOARD_DATA block. Exit 0 on success, 1 on any
error (the deploy script then restores the files with `git checkout -- .`).

Usage:  python3 scripts/render_live.py [--root <site dir>] [--check]
  --check   render into memory and report what would change, write nothing.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import os
import re
import subprocess
import sys
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
SITE = "https://immilane.com"

LANDING_PAGES = ("perm-status-check.html", "perm-case-status.html",
                 "dol-case-status.html", "perm-processing-time.html")
# Pages whose content is driven by dashboard_data.json (sitemap lastmod).
DASHBOARD_PAGES = ("/", "/leaderboard") + tuple("/" + p[:-5] for p in LANDING_PAGES)


# ── helpers ─────────────────────────────────────────────────────────────────
def js_round(x: float) -> int:
    """JavaScript Math.round (halves round toward +infinity)."""
    return int(math.floor(x + 0.5))


def js_num(v) -> str:
    """Render a number the way JS string-concatenation would ("2.2", "7", "0")."""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        if v.is_integer():
            return str(int(v))
        return repr(v)
    return "" if v is None else str(v)


def n(v) -> str:
    """JS Number.prototype.toLocaleString() for en-US (grouping commas)."""
    if isinstance(v, float) and not v.is_integer():
        whole, frac = repr(round(v, 3)).split(".")
        return f"{int(whole):,}.{frac}"
    return f"{int(v):,}"


def esc(s) -> str:
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def parse_iso(s: str) -> dt.datetime:
    s = s.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    d = dt.datetime.fromisoformat(s)
    if d.tzinfo is None:
        d = d.replace(tzinfo=dt.timezone.utc)
    return d


def fmt_et_short(iso: str) -> str:
    """'Sep 24, 3:18 PM ET' — same as the landing pages' JS set('updated')."""
    d = parse_iso(iso).astimezone(ET)
    return d.strftime("%b %-d, %-I:%M %p") + " ET"


def fmt_et_long(iso: str) -> str:
    """'Sep 24, 2026, 3:18 PM ET'."""
    d = parse_iso(iso).astimezone(ET)
    return d.strftime("%b %-d, %Y, %-I:%M %p") + " ET"


def fmt_date_et(iso: str) -> str:
    """'Aug 21, 2026' — visa-bulletin formatLastUpdated() for an ET viewer."""
    return parse_iso(iso).astimezone(ET).strftime("%b %-d, %Y")


def sub_once(html: str, pattern: str, repl: str, label: str, flags=re.S) -> str:
    new, cnt = re.subn(pattern, repl, html, count=1, flags=flags)
    if cnt != 1:
        raise RuntimeError(f"pattern not found: {label}")
    return new


def set_title(html: str, title: str) -> str:
    html = sub_once(html, r"<title>[^<]*</title>", f"<title>{esc(title)}</title>", "<title>")
    html = re.sub(r'(<meta property="og:title" content=")[^"]*(")',
                  lambda m: m.group(1) + esc(title).replace('"', "&quot;") + m.group(2), html, count=1)
    return html


def set_description(html: str, desc: str) -> str:
    d = esc(desc).replace('"', "&quot;")
    html = re.sub(r'(<meta name="description" content=")[^"]*(")', lambda m: m.group(1) + d + m.group(2), html, count=1)
    html = re.sub(r'(<meta property="og:description" content=")[^"]*(")', lambda m: m.group(1) + d + m.group(2), html, count=1)
    return html


def set_text_by_id(html: str, el_id: str, text: str) -> str:
    return sub_once(html, rf'(<[a-z0-9]+[^>]*\bid="{re.escape(el_id)}"[^>]*>)[^<]*(</)',
                    lambda m: m.group(1) + esc(text) + m.group(2), f"#{el_id}")


def set_marked(html: str, name: str, inner: str) -> str:
    """Replace everything between <!--il-ssr:name--> and <!--/il-ssr:name-->."""
    return sub_once(html, rf"(<!--il-ssr:{re.escape(name)}-->).*?(<!--/il-ssr:{re.escape(name)}-->)",
                    lambda m: m.group(1) + inner + m.group(2), f"marker {name}")


# ── dashboard numbers ───────────────────────────────────────────────────────
def dashboard_values(d: dict) -> dict:
    dol = d["dol"]
    m = re.search(r"'(\d\d)", dol.get("label", ""))
    yr = ("20" + m.group(1)) if m else ""
    frontier = dol["monthFull"] + (f" {yr}" if yr else "")
    nx = next((t for t in d.get("statusDistribution", []) if t.get("tabKey") == "next"), None)
    v = {
        "frontier": frontier,
        "pct": f"{js_num(dol['pctProcessed'])}%",
        "remain": n(dol["casesRemaining"]),
        "avg": js_num(d["avgDays"]["current"]),
        "avgCert": js_num(d["avgDays"]["certified"]),
        "pending": n(d["backlog"]["total"]),
        "todayTotal": n(d["today"]["total"]),
        "move": js_num(dol["movementPerWeek"]),
        "updated": fmt_et_short(d["updatedAt"]),
    }
    if nx:
        for k in ("certified", "review", "withdrawn", "rfi", "denied"):
            v["nx-" + k] = n(nx[k])
    return v


def render_stat_cards(D: dict) -> str:
    """Exact port of index.html renderStatCards() (same template, same text)."""
    backlog, avgDays, today, dol = D["backlog"], D["avgDays"], D["today"], D["dol"]
    dolMonth = next((m for m in D["timeline"] if m.get("status") == "active"), None)
    netThis = backlog["thisMonth"]["processed"] - backlog["thisMonth"]["intake"]
    netLast = backlog["lastMonth"]["processed"] - backlog["lastMonth"]["intake"]

    def fmtNet(v):
        if v > 0:
            return {"str": "−" + n(v), "color": "#22c55e"}
        if v < 0:
            return {"str": "+" + n(abs(v)), "color": "#ef4444"}
        return {"str": "0", "color": "#64748b"}

    ntThis, ntLast = fmtNet(netThis), fmtNet(netLast)
    delta = avgDays["deltaFromLastMonth"]
    deltaDir = "▲" if delta > 0 else "▼" if delta < 0 else "—"
    deltaClass = "trend-down" if delta > 0 else "trend-up" if delta < 0 else "trend-neutral"
    prog = js_round(dol["day"] / dol["daysInMonth"] * 100)
    todayStatus = ("⏳ Still updating — yesterday: " + n(today["yesterday"]["total"])) if today.get("stillUpdating") else "Final count"
    t90 = avgDays["trend90Day"]
    t90color = "#ef4444" if t90 > 0 else "#22c55e"
    t90sign = "+" if t90 > 0 else ""
    return f"""
    <div class="stat-card" style="--accent:#3b82f6;">
      <div class="stat-label">Total Backlog
        <span class="info-icon">?<span class="tooltip">
          <div class="tt-section">This month ({backlog['thisMonth']['name']}, so far)</div>
          <div class="tt-row"><span class="tt-label">Processed</span><span class="tt-val">{n(backlog['thisMonth']['processed'])}</span></div>
          <div class="tt-row"><span class="tt-label">New intake</span><span class="tt-val">+{n(backlog['thisMonth']['intake'])}</span></div>
          <div class="tt-row"><span class="tt-label">Net change</span><span class="tt-val" style="color:{ntThis['color']};">{ntThis['str']}</span></div>
        </span></span>
      </div>
      <div class="stat-value" style="color:var(--text-primary);">{n(backlog['total'])}</div>
      <div class="stat-trend trend-neutral">Backlog at start of month: {n(backlog['startOfMonth'])}</div>
      <div class="stat-details">
        <div class="stat-detail-row"><span class="stat-detail-label" style="color:var(--text-secondary);font-weight:500;">Last month ({backlog['lastMonth']['name']})</span></div>
        <div class="stat-detail-row"><span class="stat-detail-label">Processed</span><span class="stat-detail-value">{n(backlog['lastMonth']['processed'])}</span></div>
        <div class="stat-detail-row"><span class="stat-detail-label">New intake</span><span class="stat-detail-value">+{n(backlog['lastMonth']['intake'])}</span></div>
        <div class="stat-detail-row"><span class="stat-detail-label">Net change</span><span class="stat-detail-value" style="color:{ntLast['color']};">{ntLast['str']}</span></div>
      </div>
    </div>

    <div class="stat-card" style="--accent:#f59e0b;">
      <div class="stat-label">Avg Processing Days
        <span class="info-icon">?<span class="tooltip">Average calendar days from PERM filing to DOL decision, based on cases decided in the last 30 days.</span></span>
      </div>
      <div class="stat-value" style="color:#f59e0b;">{n(avgDays['current'])}</div>
      <div class="stat-trend {deltaClass}">{deltaDir} {js_num(abs(delta))} days from last month</div>
      <div class="stat-details">
        <div class="stat-detail-row"><span class="stat-detail-label">Certified cases avg</span><span class="stat-detail-value">{n(avgDays['certified'])} days</span></div>
        <div class="stat-detail-row"><span class="stat-detail-label">RFI cases avg</span><span class="stat-detail-value">{n(avgDays['rfi'])} days</span></div>
        <div class="stat-detail-row"><span class="stat-detail-label">Denied cases avg</span><span class="stat-detail-value">{n(avgDays['denied'])} days</span></div>
        <div class="stat-detail-row"><span class="stat-detail-label">90-day trend</span><span class="stat-detail-value" style="color:{t90color};">{t90sign}{js_num(t90)} days</span></div>
      </div>
    </div>

    <div class="stat-card" style="--accent:#22c55e;">
      <div class="stat-label">Processed Today
        <span class="info-icon">?<span class="tooltip">
          <div class="tt-section">Today's breakdown</div>
          <div class="tt-row"><span class="tt-label">Certified</span><span class="tt-val" style="color:#22c55e;">{n(today['certified'])}</span></div>
          <div class="tt-row"><span class="tt-label">RFI</span><span class="tt-val" style="color:#f59e0b;">{n(today['rfi'])}</span></div>
          <div class="tt-row"><span class="tt-label">Denied</span><span class="tt-val" style="color:#ef4444;">{n(today['denied'])}</span></div>
          <div class="tt-row"><span class="tt-label">Withdrawn</span><span class="tt-val" style="color:#64748b;">{n(today['withdrawn'])}</span></div>
        </span></span>
      </div>
      <div class="stat-value" style="color:#22c55e;">{n(today['total'])}</div>
      <div class="stat-trend trend-neutral">{todayStatus}</div>
      <div class="stat-details">
        <div class="stat-detail-row"><span class="stat-detail-label">Yesterday certified</span><span class="stat-detail-value" style="color:#22c55e;">{n(today['yesterday']['certified'])}</span></div>
        <div class="stat-detail-row"><span class="stat-detail-label">Yesterday denied</span><span class="stat-detail-value" style="color:#ef4444;">{n(today['yesterday']['denied'])}</span></div>
        <div class="stat-detail-row"><span class="stat-detail-label">Yesterday RFI</span><span class="stat-detail-value" style="color:#f59e0b;">{n(today['yesterday']['rfi'])}</span></div>
        <div class="stat-detail-row"><span class="stat-detail-label">Yesterday withdrawn</span><span class="stat-detail-value">{n(today['yesterday']['withdrawn'])}</span></div>
      </div>
    </div>

    <div class="stat-card" style="--accent:#8b5cf6;">
      <div class="stat-label">DOL Currently Processing
        <span class="info-icon">?<span class="tooltip">The filing month DOL is currently reviewing.</span></span>
      </div>
      <div class="stat-value" style="color:#8b5cf6;">{dol['label']}</div>
      <div class="stat-trend trend-neutral">{prog}% processed · {n(dol['casesRemaining'])} remaining</div>
      <div class="stat-details">
        <div class="stat-detail-row"><span class="stat-detail-label">Total cases</span><span class="stat-detail-value">{n(dolMonth['total'] if dolMonth else 0)}</span></div>
        <div class="stat-detail-row"><span class="stat-detail-label">Est. finish</span><span class="stat-detail-value">{dol.get('estFinish') or 'TBD'}</span></div>
        <div class="stat-detail-row"><span class="stat-detail-label">Movement/week</span><span class="stat-detail-value">~{js_num(dol.get('movementPerWeek')) or '—'} days</span></div>
        <div class="stat-detail-row"><span class="stat-detail-label">Cases remaining</span><span class="stat-detail-value" style="color:#f59e0b;">~{n(dol['casesRemaining'])}</span></div>
      </div>
    </div>"""


# ── visa bulletin tables ────────────────────────────────────────────────────
VB_CATEGORY_ROWS = [
    ("1st", "EB-1", "Priority Workers"),
    ("2nd", "EB-2", "Advanced Degrees / Exceptional Ability"),
    ("3rd", "EB-3", "Skilled Workers / Professionals"),
    ("Other Workers", "EB-3 Other", "Other Workers"),
    ("4th", "EB-4", "Special Immigrants"),
    ("Certain Religious Workers", "EB-4 Religious", "Certain Religious Workers"),
    ("5th Unreserved", "EB-5", "Investors — Unreserved"),
    ("5th Set Aside Rural", "EB-5", "Set Aside — Rural"),
    ("5th Set Aside High Unemployment", "EB-5", "Set Aside — High Unemployment"),
    ("5th Set Aside Infrastructure", "EB-5", "Set Aside — Infrastructure"),
]
VB_COUNTRIES = ("all_other", "china", "india", "mexico", "philippines")
VB_MONTHS = {"JAN": "Jan", "FEB": "Feb", "MAR": "Mar", "APR": "Apr", "MAY": "May", "JUN": "Jun",
             "JUL": "Jul", "AUG": "Aug", "SEP": "Sep", "OCT": "Oct", "NOV": "Nov", "DEC": "Dec"}


def vb_format_date(raw) -> str:
    """Port of visa-bulletin.html formatDate()."""
    if raw is None:
        return '<span class="date muted">—</span>'
    v = str(raw).strip().upper()
    if v == "C":
        return '<span class="badge-c">Current</span>'
    if v == "U":
        return '<span class="badge-u">U</span>'
    m = re.fullmatch(r"(\d{2})([A-Z]{3})(\d{2})", v)
    if m:
        day = int(m.group(1))
        mon = VB_MONTHS.get(m.group(2), m.group(2))
        yr = int(m.group(3))
        year = 1900 + yr if yr >= 90 else 2000 + yr
        return f"{mon} {day}, {year}"
    return esc(v)


def vb_render_rows(dataset: dict) -> str:
    """Port of visa-bulletin.html renderTable() — same markup, joined with ''."""
    rows = []
    for key, label, sub in VB_CATEGORY_ROWS:
        data = dataset.get(key)
        if not data:
            continue
        html = "<tr>"
        html += f'<td class="cat">{esc(label)}<span class="sub">{esc(sub)}</span></td>'
        for c in VB_COUNTRIES:
            html += f'<td class="date">{vb_format_date(data.get(c))}</td>'
        html += "</tr>"
        rows.append(html)
    return "".join(rows)


# ── page renderers ──────────────────────────────────────────────────────────
def render_index(html: str, D: dict) -> str:
    html = set_text_by_id(html, "updated-text", "Last updated " + fmt_et_long(D["updatedAt"]))
    html = set_marked(html, "stats-grid", render_stat_cards(D))
    return html


def render_visa_bulletin(html: str, vb: dict) -> str:
    month = vb.get("bulletin_date") or ""
    if month:
        html = set_title(html, f"Visa Bulletin {month} — EB Final Action Dates | ImmiLane")
        html = set_description(html, f"{month} Visa Bulletin: final action dates and dates for filing for EB-1, EB-2, "
                                     f"and EB-3 by country (India, China, Mexico, Philippines, all other), with "
                                     f"month-over-month movement tracking.")
        html = set_text_by_id(html, "bulletin-title", f"Employment-Based Priority Dates — {month}")
    html = set_text_by_id(html, "bulletin-month", month or "—")
    html = set_text_by_id(html, "last-updated", fmt_date_et(vb["last_updated"]) if vb.get("last_updated") else "—")
    html = set_marked(html, "tbl-final-action", vb_render_rows(vb.get("final_action_dates") or {}))
    html = set_marked(html, "tbl-dates-filing", vb_render_rows(vb.get("dates_for_filing") or {}))
    return html


def render_landing(name: str, html: str, D: dict, vals: dict) -> str:
    # 1. every data-live element (identical to the page's own set() at load)
    def repl(m):
        key = m.group(2)
        if key not in vals:
            return m.group(0)
        return m.group(1) + esc(vals[key]) + m.group(3)
    html = re.sub(r'(<[a-z0-9]+[^>]*\bdata-live="([a-zA-Z-]+)"[^>]*>)[^<]*(<)', repl, html)

    # 2. FAQ JSON-LD numbers that mirror the visible answers
    avg = int(D["avgDays"]["current"])
    months = js_round(avg / 30.4)
    html = re.sub(r"averaging roughly [\d,]+ days \(about \d+ months\)",
                  f"averaging roughly {avg:,} days (about {months} months)", html)
    html = re.sub(r"Current cases average roughly [\d,]+ days from filing to decision",
                  f"Current cases average roughly {avg:,} days from filing to decision", html)

    # 3. perm-processing-time: month-stamped title + live description
    if name == "perm-processing-time.html":
        now_et = dt.datetime.now(ET)
        html = set_title(html, f"PERM Processing Time Tracker — {now_et.strftime('%B %Y')} | ImmiLane")
        html = set_description(html, (
            f"PERM processing time, {now_et.strftime('%B %Y')}: the DOL is adjudicating {vals['frontier']} filings "
            f"({vals['pct']} through that month), averaging {vals['avg']} days from filing to decision. "
            f"Live DOL data, updated daily."))
    return html


# ── sitemap ─────────────────────────────────────────────────────────────────
def git_date(root: str, rel: str) -> dt.date | None:
    try:
        out = subprocess.run(["git", "-C", root, "log", "-1", "--format=%cI", "--", rel],
                             capture_output=True, text=True, timeout=20).stdout.strip()
        return parse_iso(out).astimezone(ET).date() if out else None
    except Exception:
        return None


def render_sitemap(xml: str, root: str, D: dict, vb: dict) -> str:
    dash_date = parse_iso(D["updatedAt"]).astimezone(ET).date()
    vb_date = parse_iso(vb["last_updated"]).astimezone(ET).date() if vb.get("last_updated") else None

    def one(m):
        loc = m.group(1)
        path = loc[len(SITE):] or "/"
        rel = "index.html" if path == "/" else path.strip("/") + ".html"
        d = git_date(root, rel)
        if path in DASHBOARD_PAGES:
            d = max(filter(None, [d, dash_date]), default=d)
        if path == "/visa-bulletin":
            d = max(filter(None, [d, vb_date]), default=d)
        if d is None:
            return m.group(0)  # keep whatever was there
        return f"{m.group(0).split('<lastmod>')[0]}<lastmod>{d.isoformat()}</lastmod>"

    return re.sub(rf"<loc>({re.escape(SITE)}[^<]*)</loc>\s*<lastmod>[^<]*</lastmod>", one, xml)


# ── main ────────────────────────────────────────────────────────────────────
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    ap.add_argument("--check", action="store_true", help="report only, write nothing")
    a = ap.parse_args()
    root = a.root

    def rd(rel):
        with open(os.path.join(root, rel), encoding="utf-8") as f:
            return f.read()

    def wr(rel, s):
        p = os.path.join(root, rel)
        tmp = p + ".render.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(s)
        os.replace(tmp, p)

    D = json.loads(rd("data/dashboard_data.json"))
    vb = json.loads(rd("data/visa_bulletin.json"))
    vals = dashboard_values(D)

    changed = []
    jobs = [("index.html", lambda h: render_index(h, D)),
            ("visa-bulletin.html", lambda h: render_visa_bulletin(h, vb)),
            ("sitemap.xml", lambda h: render_sitemap(h, root, D, vb))]
    for p in LANDING_PAGES:
        jobs.append((p, (lambda nm: (lambda h: render_landing(nm, h, D, vals)))(p)))

    for rel, fn in jobs:
        if not os.path.exists(os.path.join(root, rel)):
            print(f"render_live: skip {rel} (missing)")
            continue
        before = rd(rel)
        after = fn(before)
        if after != before:
            changed.append(rel)
            if not a.check:
                wr(rel, after)

    print(f"render_live: DOL {vals['frontier']} · {vals['pct']} · {vals['avg']} avg days · "
          f"{vals['pending']} pending · updated {vals['updated']} · bulletin {vb.get('bulletin_date')}")
    print(f"render_live: {'would change' if a.check else 'rendered'} {len(changed)} file(s): {', '.join(changed) or '-'}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:  # any failure → non-zero so the deploy script restores files
        print(f"render_live: ERROR {type(e).__name__}: {e}", file=sys.stderr)
        sys.exit(1)
