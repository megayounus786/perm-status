#!/usr/bin/env python3
"""Insert (or refresh) the session-check measurement snippet on every page.

The snippet measures, per pageview, whether the visitor's browser filters ad
content (three independent bait signals) and reports a single anonymous
yes/no beacon to the immilane-api Worker at /api/session-check. Aggregate
daily counters only — no IDs, no cookies, no IP storage.

Idempotent: replaces any existing block between the markers, else inserts
before </body>. Run from the repo root: python3 scripts/insert_session_check.py
"""
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
MARK_START = "<!-- il-session-check -->"
MARK_END = "<!-- /il-session-check -->"

SNIPPET = MARK_START + """
<script>
/* Anonymous content-filter measurement: one yes/no beacon per pageview
   (did ad content render), daily aggregate counters only. No cookies, no
   identifiers, no IP storage — consistent with the Privacy Policy. */
(function () {
  'use strict';
  try {
    if (window.__ilSessionCheck) return;
    window.__ilSessionCheck = 1;
    var h = location.hostname;
    var API = (h === 'immilane.com' || h === 'www.immilane.com')
      ? 'https://immilane-api.younusahamad.workers.dev'
      : 'https://immilane-api-staging.younusahamad.workers.dev';
    var DECOY = h.slice(-9) === 'github.io'
      ? '/perm-status-staging/ad_header.js'
      : '/ad_header.js';
    var sent = false;
    var tripped = {};
    function send() {
      if (sent) return;
      sent = true;
      try {
        var s = [];
        for (var k in tripped) { if (tripped[k]) s.push(k); }
        var payload = JSON.stringify({
          b: s.length ? 1 : 0,
          s: s,
          p: (location.pathname || '/').slice(0, 64),
          dv: (window.matchMedia && matchMedia('(pointer:coarse)').matches) ? 'm' : 'd'
        });
        var url = API + '/api/session-check';
        if (!(navigator.sendBeacon && navigator.sendBeacon(url, payload))) {
          fetch(url, { method: 'POST', body: payload, keepalive: true }).catch(function () {});
        }
      } catch (e) {}
    }
    function run() {
      try {
        var pending = 3;
        function done() { pending -= 1; if (pending <= 0) send(); }
        // Signal d: bait element using ids/classes generic filter lists hide.
        var bait = document.createElement('div');
        bait.id = 'adsbox';
        bait.className = 'ad_slot sponsored-ad textAd';
        bait.setAttribute('aria-hidden', 'true');
        bait.style.cssText = 'position:absolute;left:-9999px;top:-9999px;width:1px;height:1px;overflow:hidden;';
        bait.innerHTML = '&nbsp;';
        document.body.appendChild(bait);
        setTimeout(function () {
          try {
            var cs = window.getComputedStyle ? getComputedStyle(bait) : null;
            tripped.d = !bait.offsetParent || bait.offsetHeight === 0 ||
              !!(cs && (cs.display === 'none' || cs.visibility === 'hidden'));
            if (bait.parentNode) { bait.parentNode.removeChild(bait); }
          } catch (e) {}
          done();
        }, 350);
        // Signal s: same-origin decoy script with a filter-listed filename.
        var decDone = false;
        var dec = document.createElement('script');
        dec.async = true;
        dec.onload = function () { if (!decDone) { decDone = true; done(); } };
        dec.onerror = function () { if (!decDone) { decDone = true; tripped.s = true; done(); } };
        dec.src = DECOY;
        (document.head || document.documentElement).appendChild(dec);
        setTimeout(function () { if (!decDone) { decDone = true; done(); } }, 2000);
        // Signal f: the real ad-script host (also catches DNS-level filtering).
        var fDone = false;
        try {
          fetch('https://monu.delivery/site/6/7/24ec8e-ce2f-4a85-ba1a-e1a4ecaea496.js', { mode: 'no-cors' })
            .then(function () { if (!fDone) { fDone = true; done(); } },
                  function () { if (!fDone) { fDone = true; tripped.f = true; done(); } });
        } catch (e) { if (!fDone) { fDone = true; done(); } }
        setTimeout(function () { if (!fDone) { fDone = true; done(); } }, 2000);
        // Backstop: exactly one beacon per pageview no matter what.
        setTimeout(send, 3000);
      } catch (e) {}
    }
    function kick() { setTimeout(run, 200); }
    if (document.readyState === 'complete' || document.readyState === 'interactive') { kick(); }
    else { document.addEventListener('DOMContentLoaded', kick); }
  } catch (e) {}
})();
</script>
""" + MARK_END

BLOCK_RE = re.compile(re.escape(MARK_START) + r".*?" + re.escape(MARK_END), re.S)


def process(path):
    html = path.read_text(encoding="utf-8")
    if BLOCK_RE.search(html):
        updated = BLOCK_RE.sub(lambda _: SNIPPET, html)
        action = "refreshed"
    else:
        idx = html.rfind("</body>")
        if idx == -1:
            return f"SKIP (no </body>): {path.name}"
        updated = html[:idx] + SNIPPET + "\n" + html[idx:]
        action = "inserted"
    path.write_text(updated, encoding="utf-8")
    return f"{action}: {path.name}"


def main():
    pages = sorted(ROOT.glob("*.html"))
    if not pages:
        sys.exit("no HTML pages found")
    for page in pages:
        print(process(page))


if __name__ == "__main__":
    main()
