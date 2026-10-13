#!/usr/bin/env python3
"""Refresh the shared page-script include on every page.

Every page loads assets/il-session.js through one small include between the
il-session-check markers. That file holds the session-check measurement (one
anonymous yes/no beacon per pageview: does this browser filter ad content) and
the ad-block notice built on it. Aggregate counters only: no IDs, no cookies,
no IP storage. The wall's on/off switch is assets/il-config.json.

This script rewrites the include on every page that already has the markers
and stamps ?v=<first 8 hex of the file's sha1>, so browsers fetch a changed
file at once (static files are browser-cached for 4 hours on immilane.com).
Run it after every edit of assets/il-session.js.

Pages without the markers are left alone on purpose: 404.html and
uscis-case-check.html never load the script. To add the include to a new page:
    python3 scripts/insert_session_check.py --add new-page.html

No-peek head block (2026-10-13). Every page that loads the script, carries the
Monumetric tag and is not one of the open pages also gets a small inline block
right after the Monumetric tag, between <!-- il-shield --> markers. It hides
the page body before anything is drawn, only when Monumetric's script did not
run, until il-session.js has decided (page or notice). The block is written
from assets/il-session.js (crawler pattern, open pages, ad-host address,
WALL_IF_ADS_RUN) and assets/il-config.json ("wall"): it hides nothing unless
the switch is "on". So after changing the switch, run this script too (the
switch file alone still works, but blocked visitors then see a blank page for
a moment before the page, instead of nothing at all). The employer and
visa-bulletin builders copy the block from the guide template at deploy time.

Idempotent. Usage (from anywhere):
    python3 scripts/insert_session_check.py            # rewrite
    python3 scripts/insert_session_check.py --check    # exit 1 if anything is stale
"""
import hashlib
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
ASSET = ROOT / "assets" / "il-session.js"
CONFIG = ROOT / "assets" / "il-config.json"
MARK_START = "<!-- il-session-check -->"
MARK_END = "<!-- /il-session-check -->"
BLOCK_RE = re.compile(re.escape(MARK_START) + r".*?" + re.escape(MARK_END), re.S)
HEAD_START = "<!-- il-shield -->"
HEAD_END = "<!-- /il-shield -->"
HEAD_RE = re.compile(r"\n?" + re.escape(HEAD_START) + r".*?" + re.escape(HEAD_END), re.S)
MONU_RE = re.compile(r'<script[^>]*src="//monu\.delivery/site/[^"]+"[^>]*></script>')

HEAD_JS = """(function(){try{
var M=%(mode)s,A=%(ads)s,L=%(loader)s,O=%(open)s,B=%(bot)s;
if(M!=='on'||window.__ilShield)return;
if(!A&&window.$MMT&&window.$MMT.dsloaded)return;
var n=navigator;if(B.test(n.userAgent||'')||n.webdriver===true)return;
if(O.indexOf((location.pathname||'/').toLowerCase().replace(/\\.html$/,'').split('/')[1]||'')!==-1)return;
if(!window.fetch||!window.Promise||!window.performance||!performance.now)return;
var de=document.documentElement,S={t0:performance.now(),e:{}},e=S.e;
try{S.flag=+localStorage.getItem('il-wall')>Date.now()}catch(x){}
try{S.retry=sessionStorage.getItem('il-retry')==='1';sessionStorage.removeItem('il-retry')}catch(x){}
S.done=function(){de.classList.remove('il-shield');de.removeAttribute('aria-busy')};
window.__ilShield=S;de.classList.add('il-shield');de.setAttribute('aria-busy','true');
var d=document.createElement('script');d.async=true;d.onload=function(){e.s=0};d.onerror=function(){e.s=1};d.src='/ad_header.js';(document.head||de).appendChild(d);
fetch(L,{mode:'no-cors'}).then(function(){e.f=0},function(){e.f=1});
fetch('/assets/il-config.json',{cache:'no-store'}).then(function(r){if(!r.ok)throw 0;return r.json()}).then(function(j){e.m=j&&typeof j.wall==='string'?j.wall:'off'},function(){e.m='off'});
document.addEventListener('DOMContentLoaded',function(){if(!S.owned)S.done()});
setTimeout(function(){if(!S.owned)S.done()},2500);
}catch(x){try{document.documentElement.classList.remove('il-shield')}catch(y){}}})();"""


def _js_const(src, name, pattern):
    m = re.search(r"var " + name + r" = (" + pattern + r");", src)
    if not m:
        sys.exit(f"FATAL: {name} not found in assets/il-session.js")
    return m.group(1)


def head_block():
    """The inline no-peek block, built from il-session.js + il-config.json."""
    src = ASSET.read_text(encoding="utf-8")
    try:
        mode = json.loads(CONFIG.read_text(encoding="utf-8")).get("wall")
    except Exception:
        mode = "off"
    js = HEAD_JS % {
        "mode": json.dumps(mode if isinstance(mode, str) else "off"),
        "ads": _js_const(src, "WALL_IF_ADS_RUN", r"true|false"),
        "loader": _js_const(src, "LOADER", r"'[^']+'"),
        "open": _js_const(src, "OPEN_PAGES", r"\[[^\]]*\]"),
        "bot": _js_const(src, "BOT_RE", r"/[^\n]+/i"),
    }
    return (HEAD_START + "\n"
            + '<style id="il-shield-css">html.il-shield body{opacity:0!important;visibility:hidden!important}</style>\n'
            + '<script id="il-shield">\n/* Ad-block notice, part 1 of 2 (part 2: assets/il-session.js). Written by scripts/insert_session_check.py -- edit there.'
            + ' Hides the page until the ad-block check has answered, only when the Monumetric script above did not run;'
            + ' never for crawlers, the open pages, or a switch that is not "on". */\n'
            + js + "\n</script>\n" + HEAD_END)


def open_page(path):
    """Billing/legal pages that never get the notice (same list as il-session.js)."""
    src = ASSET.read_text(encoding="utf-8")
    names = json.loads(_js_const(src, "OPEN_PAGES", r"\[[^\]]*\]").replace("'", '"'))
    return path.relative_to(ROOT).parts[0].lower().replace(".html", "") in names


def with_head(html, head, wanted):
    """Insert, refresh or remove the head block. wanted=False removes it."""
    found = HEAD_RE.findall(html)
    if len(found) > 1:
        raise ValueError("more than one il-shield block")
    stripped = HEAD_RE.sub("", html) if found else html
    if not wanted:
        return stripped
    m = MONU_RE.search(stripped)
    if not m:
        return stripped
    return stripped[:m.end()] + "\n" + head + stripped[m.end():]
SKIP_DIRS = {".git", "scripts", "node_modules"}


def snippet():
    version = hashlib.sha1(ASSET.read_bytes()).hexdigest()[:8]
    return (MARK_START + "\n"
            + '<script src="/assets/il-session.js?v=' + version + '" defer></script>\n'
            + MARK_END)


def pages():
    for path in sorted(ROOT.rglob("*.html")):
        if SKIP_DIRS.isdisjoint(path.relative_to(ROOT).parts):
            yield path


def process(path, block, add, check, head):
    html = path.read_text(encoding="utf-8")
    found = BLOCK_RE.findall(html)
    if len(found) > 1:
        sys.exit(f"FATAL: {path.relative_to(ROOT)} has {len(found)} il-session-check blocks")
    if found:
        updated = BLOCK_RE.sub(lambda _: block, html)
    elif add:
        idx = html.rfind("</body>")
        if idx == -1:
            sys.exit(f"FATAL: no </body> in {path.relative_to(ROOT)}")
        updated = html[:idx] + block + "\n" + html[idx:]
    else:
        if HEAD_START in html:
            sys.exit(f"FATAL: {path.relative_to(ROOT)} has an il-shield block but no il-session-check include")
        return "skipped"
    try:
        updated = with_head(updated, head, not open_page(path))
    except ValueError as e:
        sys.exit(f"FATAL: {path.relative_to(ROOT)}: {e}")
    if updated == html:
        return "current"
    if not check:
        path.write_text(updated, encoding="utf-8")
    return "stale" if check else "updated"


def main():
    args = sys.argv[1:]
    check = "--check" in args
    add = set()
    if "--add" in args:
        add = {(ROOT / a).resolve() for a in args[args.index("--add") + 1:] if not a.startswith("--")}
    block = snippet()
    head = head_block()
    counts = {}
    shielded = 0
    for path in pages():
        result = process(path, block, path.resolve() in add, check, head)
        counts[result] = counts.get(result, 0) + 1
        if result == "skipped":
            print(f"no markers, left alone: {path.relative_to(ROOT)}")
        else:
            shielded += HEAD_START in path.read_text(encoding="utf-8")
    print(block.splitlines()[1])
    print(", ".join(f"{k}: {v}" for k, v in sorted(counts.items())) + f"; pages with the no-peek head block: {shielded}")
    if check and counts.get("stale"):
        sys.exit(1)


if __name__ == "__main__":
    main()
