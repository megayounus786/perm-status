#!/usr/bin/env python3
"""site_notice.py — site-wide notice strip, switched by data/site_notice.json.

Called by render_live.py as its LAST step at deploy time (after the employer and
visa-bulletin page renders, which rewrite employers/*.html from scratch).

data/site_notice.json:
  {"enabled": true, "title": "...", "text": "...", "since": "YYYY-MM-DD"}

enabled  -> a calm amber notice strip goes right under the site <nav> on every
            page in PAGES (+ employers/*.html), its CSS goes into <head>, and on
            subscribe.html a small script adds the note to "case not found" style
            results while live checks are paused.
disabled / file missing -> every page is left EXACTLY as it was (any block left by
            an earlier run is stripped, so the output is byte-identical to a render
            without this step).

Everything this step writes sits between <!--il-site-notice:*:start--> and
<!--il-site-notice:*:end--> markers, so re-running is idempotent. A malformed
notice file is reported and ignored (no notice), never fatal: render_live's
other renders are kept.

Turn the notice off: set "enabled": false in data/site_notice.json (or delete the
file), commit, push; the next deploy renders the pages without it.
"""
from __future__ import annotations

import glob
import html as _html
import json
import os
import re

NOTICE_FILE = "data/site_notice.json"

# Pages that show live case-status / processing data.
PAGES = (
    "index.html", "leaderboard.html", "estimate.html",
    "perm-status-check.html", "perm-case-status.html", "dol-case-status.html",
    "perm-processing-time.html",
    "subscribe.html", "manage.html",
    "employer.html", "employers.html",
)
PAGE_GLOBS = ("employers/*.html",)
SCRIPT_PAGES = ("subscribe.html",)

MARK = "<!--il-site-notice:{part}:{edge}-->"
_STRIP = re.compile(r"\n?<!--il-site-notice:(css|html|js):start-->.*?<!--il-site-notice:\1:end-->",
                    re.S)

CSS = (
    "<style id=\"il-site-notice-css\">"
    ".il-site-notice{max-width:1200px;margin:12px auto 0;padding:0 24px;box-sizing:border-box}"
    ".il-site-notice-inner{display:flex;gap:10px;align-items:flex-start;padding:10px 14px;"
    "border:1px solid rgba(245,158,11,.42);border-left:4px solid #f59e0b;border-radius:10px;"
    "background:rgba(245,158,11,.10);color:#f1f5f9;font-size:14px;line-height:1.45;text-align:left}"
    ".il-site-notice-inner svg{flex:0 0 auto;margin-top:2px;color:#fbbf24}"
    ".il-site-notice-inner p{margin:0;padding:0;font-size:inherit;line-height:inherit;color:inherit}"
    ".il-site-notice-inner strong{font-weight:700;color:inherit}"
    "html.light .il-site-notice-inner{background:#fffbeb;border-color:#fcd34d;border-left-color:#d97706;color:#1f2937}"
    "html.light .il-site-notice-inner svg{color:#b45309}"
    ".il-site-notice-result{display:block;margin-top:10px;padding:10px 12px;border-left:4px solid #f59e0b;"
    "border-radius:8px;background:rgba(245,158,11,.10);color:inherit;text-align:left}"
    "html.light .il-site-notice-result{background:#fffbeb;border-left-color:#d97706;color:#1f2937}"
    "@media (max-width:640px){.il-site-notice{margin-top:10px;padding:0 12px}"
    ".il-site-notice-inner{font-size:13px;padding:9px 12px}}"
    "</style>"
)

ICON = ('<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
        'stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
        '<circle cx="12" cy="12" r="10"/><line x1="12" y1="11" x2="12" y2="16"/>'
        '<line x1="12" y1="8" x2="12.01" y2="8"/></svg>')

# subscribe.html: while the notice is on, a lookup that comes back "not found"
# (or fails) gets the paused note. Pure page-side; the subs API is unchanged.
JS = r"""<script id="il-site-notice-js">
(function () {
  var box = document.getElementById('il-site-notice');
  if (!box || !window.MutationObserver) return;
  var title = box.getAttribute('data-title') || '';
  var NOTE = 'We’re working to resolve a technical issue, so we can’t check case numbers ' +
    'right now. This doesn’t mean your case number is wrong. Please try again once checks resume.';
  var LOCAL_ERR = /valid email address|doesn’t look right|doesn't look right/i;
  var NOT_FOUND = /couldn’t find that case|couldn't find that case|isn’t in DOL|isn't in DOL|not in DOL’s public data|not in DOL's public data/i;
  var LOOKUP_FAIL = /couldn’t verify your case|couldn't verify your case|taking longer than expected|lookup_busy|sample generation failed|case validation failed|went wrong building your sample/i;
  function esc(s) { return String(s).replace(/[&<>"]/g, function (c) {
    return {'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]; }); }
  function note(extra) {
    var el = document.createElement('span');
    el.className = 'il-site-notice-result';
    el.setAttribute('role', 'status');
    el.innerHTML = '<strong>' + esc(title) + '</strong> ' + esc(NOTE) + (extra ? ' ' + esc(extra) : '');
    return el;
  }
  function watch(id, isTrial) {
    var el = document.getElementById(id);
    if (!el) return;
    function check() {
      if (el.querySelector('.il-site-notice-result')) return;
      var t = el.textContent || '';
      if (!t || LOCAL_ERR.test(t)) return;
      var isErr = /(^|\s)err(\s|$)/.test(el.className);
      if (isTrial && isErr && NOT_FOUND.test(t)) {
        el.className = 'result info';
        el.innerHTML = '';
        el.appendChild(note('No email has been sent.'));
      } else if (NOT_FOUND.test(t) || (isErr && LOOKUP_FAIL.test(t))) {
        el.appendChild(note(''));
      }
    }
    new MutationObserver(check).observe(el, {childList: true, attributes: true,
                                             attributeFilter: ['class']});
  }
  watch('trial-result', true);
  watch('result', false);
})();
</script>"""


def load_notice(root: str):
    """Returns (notice_dict_or_None, message)."""
    p = os.path.join(root, NOTICE_FILE)
    if not os.path.exists(p):
        return None, "no notice file"
    try:
        with open(p, encoding="utf-8") as f:
            d = json.load(f)
        if not isinstance(d, dict):
            raise ValueError("not a JSON object")
    except Exception as e:  # malformed -> no notice, loudly
        return None, f"WARN notice file unreadable ({type(e).__name__}: {e}) - no notice rendered"
    if not d.get("enabled"):
        return None, "notice disabled"
    title = str(d.get("title") or "").strip()
    text = str(d.get("text") or "").strip()
    if not title and not text:
        return None, "WARN notice enabled but empty - no notice rendered"
    return {"title": title, "text": text, "since": str(d.get("since") or "").strip()}, "notice on"


def block(part: str, inner: str) -> str:
    return MARK.format(part=part, edge="start") + inner + MARK.format(part=part, edge="end")


def notice_html(n: dict) -> str:
    t = _html.escape(n["title"], quote=True)
    body = (f"<strong>{_html.escape(n['title'])}</strong> " if n["title"] else "") + _html.escape(n["text"])
    since = f' data-since="{_html.escape(n["since"], quote=True)}"' if n["since"] else ""
    return (f'<div class="il-site-notice" id="il-site-notice" role="status" data-title="{t}"{since}>'
            f'<div class="il-site-notice-inner">{ICON}<p>{body}</p></div></div>')


def strip(html: str) -> str:
    return _STRIP.sub("", html)


def apply(html: str, n: dict | None, with_script: bool) -> str:
    """Strip any earlier notice; insert a fresh one when n is set. A page without
    a </nav> or </head> is returned stripped only."""
    out = strip(html)
    if not n:
        return out
    if out.count("</head>") < 1 or out.count("</nav>") < 1:
        return out
    out = out.replace("</head>", "\n" + block("css", CSS) + "</head>", 1)
    out = out.replace("</nav>", "</nav>\n" + block("html", notice_html(n)), 1)
    if with_script:
        i = out.rfind("</body>")
        if i != -1:
            out = out[:i] + block("js", JS) + "\n" + out[i:]
    return out


def page_list(root: str):
    rels = [p for p in PAGES if os.path.exists(os.path.join(root, p))]
    for g in PAGE_GLOBS:
        rels += sorted(os.path.relpath(p, root) for p in glob.glob(os.path.join(root, g)))
    return rels


def deploy_step(root: str, check: bool = False) -> str:
    n, why = load_notice(root)
    changed = 0
    pages = page_list(root)
    for rel in pages:
        p = os.path.join(root, rel)
        with open(p, encoding="utf-8") as f:
            before = f.read()
        after = apply(before, n, rel in SCRIPT_PAGES)
        if after != before:
            changed += 1
            if not check:
                tmp = p + ".notice.tmp"
                with open(tmp, "w", encoding="utf-8") as f:
                    f.write(after)
                os.replace(tmp, p)
    verb = "would change" if check else "changed"
    return f"site notice: {why}; {verb} {changed} of {len(pages)} page(s)"


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args()
    print(deploy_step(a.root, check=a.check))
