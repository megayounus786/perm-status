#!/usr/bin/env python3
"""SEO quick fixes 2026-09-24 (item 1 of the SEO plan) — one-shot, idempotent
static edits. Run from the repo root of perm-status-staging (or, on Younus's
go, perm-status). Pair with scripts/render_live.py, which runs at deploy time.

What it does (no visual change; only tags/meta/JSON-LD/robots):
  1. One topical H1 per page: on guides/about-perm/faq/legal/about-us/support
     and the five guide pages the tiny eyebrow label was the <h1> and the real
     title an <h2>. Swap the tags and move the CSS with them (.hero h1 -> big
     title style, .hero .eyebrow -> eyebrow style) so nothing moves on screen.
     faq.html: H1 text "Frequently Asked Questions" -> "PERM Frequently Asked
     Questions" (the only visible text change).
  2. Duplicate <h1> inside <noscript> on index.html / estimate.html -> <p>.
  3. JSON-LD "url" still pointing at *.html on estimate, visa-bulletin, guides,
     about-us, support -> clean canonical URLs.
  4. SSR markers: <!--il-ssr:stats-grid--> in index.html, <!--il-ssr:tbl-*-->
     in the two visa-bulletin <tbody>s (render_live.py fills them).
  5. visa-bulletin.html: JS document.title matches the static <title> pattern
     "Visa Bulletin <Month Year> — EB Final Action Dates | ImmiLane".
  6. estimate.html: the JS literal '/wk</div></div>' is what Googlebot crawled
     as https://immilane.com/wk</div></div> (URL-like string discovery in
     inline JS). Written as '&#47;wk' — renders identically via innerHTML.
  7. robots.txt: Disallow: /api/ (Googlebot discovers '/api/...' fetch paths
     from inline JS the same way; there is nothing to index there).
  8. visa-bulletin-mockup.html deleted (noindex mockup, never linked).
"""
from __future__ import annotations

import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

SWAP_PAGES = ["guides.html", "about-perm.html", "faq.html", "terms.html", "privacy.html",
              "refund.html", "disclaimer.html", "about-us.html", "support.html",
              "guide-how-dol-processes-perm.html", "guide-final-action-vs-filing-dates.html",
              "guide-eb1-eb2-eb3-explained.html", "guide-perm-audits-rfis.html",
              "guide-estimate-methodology.html"]
JSONLD_URL_PAGES = ["estimate.html", "visa-bulletin.html", "guides.html", "about-us.html", "support.html"]

changed: list[str] = []


def rd(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
        return f.read()


def wr(rel, s, before):
    if s != before:
        with open(os.path.join(ROOT, rel), "w", encoding="utf-8") as f:
            f.write(s)
        changed.append(rel)


def must(cnt, what, rel, expect=1):
    if cnt != expect:
        raise SystemExit(f"{rel}: expected {expect} match(es) for {what}, got {cnt}")


# 1. H1 swap (markup + CSS) ---------------------------------------------------
for rel in SWAP_PAGES:
    src = rd(rel)
    s = src
    if 'class="eyebrow"' not in s:  # not yet swapped
        # CSS: move the styles with the tags (base + mobile media query)
        s, c = re.subn(r"(\.hero )h1( \{ font-size: 1[24]px;)", r"\1.eyebrow\2", s)
        must(c, ".hero h1 css", rel, 2)
        s, c = re.subn(r"(\.hero )h2( \{ font-size: 2[08]px)", r"\1h1\2", s)
        must(c, ".hero h2 css", rel, 2)
        # markup: <h1>label</h1>\n    <h2>Title</h2>  ->  eyebrow + h1
        s, c = re.subn(r'(<div class="hero">\s*)<h1>([^<]*)</h1>(\s*)<h2>([^<]*)</h2>',
                       r'\1<div class="eyebrow">\2</div>\3<h1>\4</h1>', s)
        must(c, "hero h1/h2 markup", rel)
    if rel == "faq.html":
        s = s.replace("<h1>Frequently Asked Questions</h1>", "<h1>PERM Frequently Asked Questions</h1>")
    assert s.count("<h1") == 1, f"{rel}: expected exactly one <h1>, found {s.count('<h1')}"
    wr(rel, s, src)

# 2. noscript duplicate H1 ----------------------------------------------------
for rel, text in (("index.html", "ImmiLane PERM Tracker"), ("estimate.html", "ImmiLane PERM Estimate")):
    src = rd(rel)
    s = src.replace(f'<h1 style="margin-bottom:16px;">{text}</h1>',
                    f'<p style="font-size:2em;font-weight:700;margin-bottom:16px;">{text}</p>')
    assert s.count("<h1") == 1, f"{rel}: expected exactly one <h1>, found {s.count('<h1')}"
    wr(rel, s, src)

# 3. JSON-LD url -> clean canonical ------------------------------------------
for rel in JSONLD_URL_PAGES:
    src = rd(rel)
    s = re.sub(r'("url":\s*"https://immilane\.com/[a-z0-9-]+)\.html(")', r"\1\2", src)
    wr(rel, s, src)

# 4. SSR markers ---------------------------------------------------------------
src = rd("index.html")
s = src.replace('<div class="stats-grid" id="stats-grid"></div>',
                '<div class="stats-grid" id="stats-grid"><!--il-ssr:stats-grid--><!--/il-ssr:stats-grid--></div>')
must(s.count("<!--il-ssr:stats-grid-->"), "stats-grid marker", "index.html")
wr("index.html", s, src)

src = rd("visa-bulletin.html")
s = src
if "il-ssr:tbl-final-action" not in s:
    s, c = re.subn(r'(<table class="vb" id="tbl-final-action"[^>]*>.*?)<tbody></tbody>',
                   r"\1<tbody><!--il-ssr:tbl-final-action--><!--/il-ssr:tbl-final-action--></tbody>", s, count=1, flags=re.S)
    must(c, "final-action tbody", "visa-bulletin.html")
    s, c = re.subn(r'(<table class="vb" id="tbl-dates-filing"[^>]*>.*?)<tbody></tbody>',
                   r"\1<tbody><!--il-ssr:tbl-dates-filing--><!--/il-ssr:tbl-dates-filing--></tbody>", s, count=1, flags=re.S)
    must(c, "dates-filing tbody", "visa-bulletin.html")
# 5. JS title pattern = static title pattern
s = s.replace("document.title = 'Visa Bulletin ' + data.bulletin_date + ' | ImmiLane';",
              "document.title = 'Visa Bulletin ' + data.bulletin_date + ' — EB Final Action Dates | ImmiLane';")
wr("visa-bulletin.html", s, src)

# 6. estimate.html '/wk' literal ----------------------------------------------
src = rd("estimate.html")
s = src.replace("+ '/wk</div></div>';", "+ '&#47;wk</div></div>';")
wr("estimate.html", s, src)

# 7. robots.txt ----------------------------------------------------------------
src = rd("robots.txt")
if "Disallow: /api/" not in src:
    s = src.replace("User-agent: *\nAllow: /\n", "User-agent: *\nAllow: /\nDisallow: /api/\n")
    must(s.count("Disallow: /api/"), "robots Disallow", "robots.txt")
    wr("robots.txt", s, src)

# 8. mockup --------------------------------------------------------------------
p = os.path.join(ROOT, "visa-bulletin-mockup.html")
if os.path.exists(p):
    os.remove(p)  # tracked file: `git rm` equivalent once committed
    changed.append("visa-bulletin-mockup.html (deleted)")

print("seo_20260924: changed", len(changed), "file(s):", ", ".join(changed) or "-")
