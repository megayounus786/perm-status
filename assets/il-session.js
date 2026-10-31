/* ImmiLane shared page script: session check + ad-block notice.
 *
 * 1. Session check (the measurement that used to be inline on every page,
 *    unchanged): one anonymous beacon per pageview saying whether the browser
 *    filters ad content. Three signals: d = bait element hidden by page-hiding
 *    rules, s = same-origin decoy script /ad_header.js refused, f = the
 *    ad-script host unreachable. No cookies, no identifiers, no IP storage.
 * 2. Ad-block notice ("the wall"): when the evidence is strong (d, or s+f),
 *    the ad script did not run, and a second check a moment later agrees, a
 *    full-page notice asks the visitor to turn the blocker off for
 *    immilane.com. It is built by this script only -- never present in the
 *    HTML -- so crawlers always get the full page. Any error, timeout or doubt
 *    means no wall.
 * 3. No peek before the verdict (2026-10-13): a small inline block in each
 *    page's <head> (il-shield, written by scripts/insert_session_check.py)
 *    hides the page body before anything is drawn, but ONLY when Monumetric's
 *    script did not run (it runs, synchronously, before that block on every
 *    page with ads, so ordinary visitors are never hidden), and never for
 *    crawlers, the open pages, or a switch that is not "on" at build time. It
 *    also starts the decoy, ad-host and switch checks early. This file then
 *    decides fast: the page is shown as soon as the evidence is clear (bait
 *    visible after BAIT_MS and decoy loaded), or the notice replaces the blank
 *    page; anything unresolved by FAST_CAP_MS counts as not blocked. A wall in
 *    this tab or another within 24 h (localStorage "il-wall") shows the notice
 *    at once on the next load while the checks run again behind it; if they
 *    now pass, the notice goes away and the flag is cleared.
 *
 * KILL SWITCH: assets/il-config.json -> "wall": "on" | "measure" | "off".
 *   Read fresh (uncached) right before a wall would show; anything but "on"
 *   means no wall. "measure" counts would-be walls without showing one.
 *   "off" (or a switch file that cannot be read) also sends no wall events:
 *   the page then does exactly what the old inline check did, nothing more.
 *
 * Do not style #adsbox, .ad_slot, .sponsored-ad or .textAd in the site's CSS:
 * the bait relies on nothing but an ad blocker ever hiding them.
 * After editing this file run: python3 scripts/insert_session_check.py
 */
(function () {
  'use strict';
  try {
    if (window.__ilSessionCheck) return;
    window.__ilSessionCheck = 1;

    // Pages that always stay open, blocker or not (billing + legal). The 404 page does not load this file.
    var OPEN_PAGES = ['subscribe', 'manage', 'refund', 'terms', 'privacy', 'disclaimer', 'support'];
    // false = never wall a visitor whose Monumetric script ran (ads are being requested, even if a page rule hides them).
    var WALL_IF_ADS_RUN = false;
    var CONFIG_URL = '/assets/il-config.json';
    var RECHECK_MS = 900;   // pause before the confirming second check
    var LOADER = 'https://monu.delivery/site/6/7/24ec8e-ce2f-4a85-ba1a-e1a4ecaea496.js';
    var DECOY = '/ad_header.js';
    var FLAG = 'il-wall';   // localStorage: expiry time (ms) of "the notice was shown here"; never sent anywhere
    var FLAG_TTL = 24 * 3600 * 1000;
    var RETRY = 'il-retry'; // sessionStorage: the notice's own reload button was pressed (read by the head block)
    // No-peek timing (only when the head block is hiding the page).
    var BAIT_MS = 100;      // page-hiding rules apply at once (stylesheet) -- wait this long for the bait
    var FAST_CAP_MS = 1200; // decoy / ad host still unanswered: counts as not blocked, page shown
    var STRONG_CAP_MS = 1500; // strong evidence but the confirming check or the switch is still out: page shown (longest the page can stay blank)
    var BOT_RE = /bot|crawl|spider|slurp|google-|googleother|mediapartners|lighthouse|pagespeed|headless|bingpreview|facebookexternalhit|preview|embedly|ia_archiver|gtmetrix|pingdom|uptime|phantom|puppeteer|playwright|selenium/i;

    var h = location.hostname;
    var IS_PROD = (h === 'immilane.com' || h === 'www.immilane.com');
    var API = IS_PROD
      ? 'https://immilane-api.younusahamad.workers.dev'
      : 'https://immilane-api-staging.younusahamad.workers.dev';
    var IS_BOT = BOT_RE.test(navigator.userAgent || '') || navigator.webdriver === true;
    var IS_OPEN = OPEN_PAGES.indexOf(
      (location.pathname || '/').toLowerCase().replace(/\.html$/, '').split('/')[1] || '') !== -1;

    function page() { return (location.pathname || '/').slice(0, 64); }
    function device() { return (window.matchMedia && matchMedia('(pointer:coarse)').matches) ? 'm' : 'd'; }
    function list(r) { var s = [], k = ['d', 's', 'f']; for (var i = 0; i < 3; i++) { if (r[k[i]]) s.push(k[i]); } return s; }

    // The pageview beacon: same payload, same moment, same transport as before.
    var sent = false;
    var tripped = {};
    function send() {
      if (sent) return;
      sent = true;
      try {
        var s = list(tripped);
        var payload = JSON.stringify({ b: s.length ? 1 : 0, s: s, p: page(), dv: device() });
        var url = API + '/api/session-check';
        if (!(navigator.sendBeacon && navigator.sendBeacon(url, payload))) {
          fetch(url, { method: 'POST', body: payload, keepalive: true }).catch(function () {});
        }
      } catch (e) {}
    }

    // Wall events are separate from the pageview beacon, so that count stays
    // exactly what it was. w: s shown, a shown again after a reload, c cleared,
    // e open page, v ad script ran (no wall), m measure-only, n not confirmed.
    // Sent as a plain fetch: tracker lists block every third-party sendBeacon,
    // which would hide exactly the browsers this is about.
    function note(w, r) {
      try {
        fetch(API + '/api/session-step', {
          method: 'POST', keepalive: true,
          body: JSON.stringify({ w: w, s: list(r || {}), p: page(), dv: device() })
        }).catch(function () {});
      } catch (e) {}
    }

    function box(id, cls) {
      var el = document.createElement('div');
      if (id) el.id = id;
      el.className = cls;
      el.setAttribute('aria-hidden', 'true');
      el.setAttribute('data-il-probe', '');
      // visibility:visible: the head block may hide the body with visibility:hidden while the checks run
      el.style.cssText = 'position:absolute;left:-9999px;top:-9999px;width:1px;height:1px;overflow:hidden;visibility:visible;';
      el.innerHTML = '&nbsp;';
      document.body.appendChild(el);
      return el;
    }
    function hidden(el) {
      var cs = window.getComputedStyle ? getComputedStyle(el) : null;
      return !el.offsetParent || el.offsetHeight === 0 ||
        !!(cs && (cs.display === 'none' || cs.visibility === 'hidden'));
    }

    // One round of the three checks. Timeouts never count as blocked.
    // r gets d/s/f; x gets the sanity checks used only for the wall decision
    // (x.slow = a check timed out: the network is slow, so nothing is certain).
    function probe(round, r, x, cb, baitMs, capMs) {
      baitMs = baitMs || 350; capMs = capMs || 2000;
      var pending = 3, over = false;
      function done() { pending -= 1; if (pending <= 0 && !over) { over = true; cb(); } }
      // Signal d: bait element using ids/classes generic filter lists hide.
      // The control is the same box with neutral names: it must stay visible.
      var bait = box('adsbox', 'ad_slot sponsored-ad textAd');
      var ctl = box('', 'il-probe');
      setTimeout(function () {
        try {
          r.d = hidden(bait);
          x.ctl = hidden(ctl);
          if (bait.parentNode) { bait.parentNode.removeChild(bait); }
          if (ctl.parentNode) { ctl.parentNode.removeChild(ctl); }
        } catch (e) {}
        done();
      }, baitMs);
      // Signal s: same-origin decoy script with a filter-listed filename.
      var decDone = false;
      var dec = document.createElement('script');
      dec.async = true;
      dec.onload = function () { if (!decDone) { decDone = true; done(); } };
      dec.onerror = function () {
        if (decDone) return;
        decDone = true;
        r.s = true;
        if (round < 2) { done(); return; }
        // Second round only: make sure the decoy file really exists. Filter
        // lists refuse it as a script, not as a plain fetch, so a 404 here
        // means a deploy problem, not a blocker.
        var chk = false;
        function fin(ok) { if (!chk) { chk = true; x.decoyMissing = !ok; done(); } }
        try {
          fetch(DECOY + '?r=' + round + 'c', { cache: 'no-store' })
            .then(function (res) { fin(res.ok); }, function () { fin(true); });
        } catch (e) { fin(true); }
        setTimeout(function () { if (!chk) { x.slow = true; fin(true); } }, capMs);
      };
      dec.src = DECOY + (round > 1 ? '?r=' + round : '');
      (document.head || document.documentElement).appendChild(dec);
      setTimeout(function () { if (!decDone) { decDone = true; x.slow = true; done(); } }, capMs);
      // Signal f: the real ad-script host (also catches DNS-level filtering).
      var fDone = false;
      try {
        fetch(LOADER, { mode: 'no-cors' })
          .then(function () { if (!fDone) { fDone = true; done(); } },
                function () { if (!fDone) { fDone = true; r.f = true; done(); } });
      } catch (e) { if (!fDone) { fDone = true; done(); } }
      setTimeout(function () { if (!fDone) { fDone = true; x.slow = true; done(); } }, capMs);
    }

    // Strong evidence only: page-hiding rules (d), or the decoy script AND the
    // ad host both refused (s+f). "f only" can be a network problem and "s only"
    // is too weak, so neither ever shows the wall.
    function strong(r, x) {
      return !!((r.d && !x.ctl) || (r.s && r.f && !x.decoyMissing));
    }
    // Monumetric's loader sets this the moment it runs.
    function adsRan() { try { return !!(window.$MMT && window.$MMT.dsloaded); } catch (e) { return false; } }

    // The kill switch, read fresh. It doubles as the "is the network fine?"
    // control: if this same-origin file cannot be read, there is no wall.
    function getMode(cb) {
      var over = false;
      function fin(m) { if (!over) { over = true; cb(m); } }
      try {
        fetch(CONFIG_URL, { cache: 'no-store' })
          .then(function (res) { if (!res.ok) { throw new Error('config'); } return res.json(); })
          .then(function (j) { fin(j && typeof j.wall === 'string' ? j.wall : 'off'); },
                function () { fin('off'); });
      } catch (e) { fin('off'); }
      setTimeout(function () { fin('off'); }, 4000);
    }

    function flagGet() { try { return +localStorage.getItem(FLAG) > Date.now(); } catch (e) { return false; } }
    function flagSet() { try { localStorage.setItem(FLAG, String(Date.now() + FLAG_TTL)); } catch (e) {} }
    function flagClear() { try { localStorage.removeItem(FLAG); } catch (e) {} }

    var CSS =
      'html.il-gate-open,html.il-gate-open body{overflow:hidden!important}' +
      // Opaque: nothing of the page shows behind the notice.
      'html.il-gate-open body>:not(#il-gate):not([data-il-probe]){visibility:hidden!important}' +
      '#il-gate{position:fixed;top:0;right:0;bottom:0;left:0;z-index:2147483647;display:flex;padding:16px;overflow-y:auto;overscroll-behavior:contain;' +
        'background:#0a0a12;visibility:visible;' +
        'font-family:Inter,-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;font-size:16px;line-height:1.55;color:#e2e8f0;text-align:left}' +
      '#il-gate *{box-sizing:border-box}' +
      '#il-gate .il-gate-card{width:100%;max-width:560px;margin:auto;padding:28px;border-radius:16px;background:#14141f;border:1px solid rgba(255,255,255,.14);box-shadow:0 20px 60px rgba(0,0,0,.55)}' +
      '#il-gate h2{margin:0 0 12px;padding:0;font-size:22px;line-height:1.3;font-weight:700;color:#fff;outline:none}' +
      '#il-gate p{margin:0 0 12px;padding:0;font-size:16px;color:#e2e8f0}' +
      '#il-gate h3{margin:18px 0 6px;padding:0;font-size:14px;font-weight:600;letter-spacing:.02em;color:#94a3b8;text-transform:none}' +
      '#il-gate details{margin:0;padding:0;border-top:1px solid rgba(255,255,255,.1)}' +
      '#il-gate details:last-of-type{border-bottom:1px solid rgba(255,255,255,.1)}' +
      '#il-gate summary{display:list-item;margin:0;padding:11px 4px;min-height:44px;font-size:15px;font-weight:600;color:#f1f5f9;cursor:pointer}' +
      '#il-gate details p{margin:0;padding:0 4px 12px 22px;font-size:15px;color:#cbd5e1}' +
      '#il-gate .il-gate-btn{display:block;width:100%;min-height:48px;margin:20px 0 14px;padding:13px 18px;border:0;border-radius:10px;background:#2563eb;color:#fff;font:inherit;font-size:16px;font-weight:600;cursor:pointer;text-align:center}' +
      '#il-gate .il-gate-btn:hover{background:#1d4ed8}' +
      '#il-gate .il-gate-btn[disabled]{opacity:.7;cursor:default}' +
      '#il-gate .il-gate-foot{margin:0;font-size:14px;color:#94a3b8}' +
      '#il-gate .il-gate-foot+.il-gate-foot{margin-top:6px}' +
      '#il-gate a{color:#93c5fd;text-decoration:underline}' +
      '#il-gate summary:focus-visible,#il-gate a:focus-visible,#il-gate .il-gate-btn:focus-visible{outline:3px solid #93c5fd;outline-offset:2px;border-radius:6px}' +
      'html.light #il-gate{background:#f5f7fa;color:#1e293b}' +
      'html.light #il-gate .il-gate-card{background:#fff;border-color:rgba(0,0,0,.1);box-shadow:0 20px 60px rgba(15,23,42,.25)}' +
      'html.light #il-gate h2{color:#0f172a}' +
      'html.light #il-gate p{color:#1e293b}' +
      'html.light #il-gate h3,html.light #il-gate .il-gate-foot{color:#475569}' +
      'html.light #il-gate details,html.light #il-gate details:last-of-type{border-color:rgba(0,0,0,.12)}' +
      'html.light #il-gate summary{color:#0f172a}' +
      'html.light #il-gate details p{color:#334155}' +
      'html.light #il-gate a{color:#1d4ed8}' +
      'html.light #il-gate summary:focus-visible,html.light #il-gate a:focus-visible,html.light #il-gate .il-gate-btn:focus-visible{outline-color:#1d4ed8}' +
      '@media (max-width:480px){#il-gate{padding:10px}#il-gate .il-gate-card{padding:20px 18px}#il-gate h2{font-size:20px}}';

    var HOWTO = [
      ['uBlock Origin', 'Click the uBlock Origin icon in the browser toolbar, then click the large power button. It turns grey when blocking is off for this site. In uBlock Origin Lite, move the slider to \u201cno filtering\u201d instead.'],
      ['AdBlock or Adblock Plus', 'Click the extension’s icon in the toolbar. In AdBlock, choose “Pause on this site”. In Adblock Plus, switch off “Block ads on: This website”.'],
      ['AdGuard', 'Click the AdGuard icon and switch off protection for this website. In the AdGuard app, add immilane.com to the allowlist.'],
      ['Brave', 'Click the Brave Shields (lion) icon in the address bar and switch Shields off for this site.'],
      ['Safari on iPhone, iPad or Mac', 'iPhone or iPad: tap the page menu button in the address bar (“aA” on older versions), then “Turn Off Content Blockers”. Mac: open the Safari menu, choose “Settings for immilane.com” and untick “Enable content blockers”.'],
      ['DNS or network blockers (NextDNS, Pi-hole)', 'Add immilane.com and monu.delivery to the allowlist.']
    ];

    function showWall() {
      if (document.getElementById('il-gate')) return;
      var st = document.createElement('style');
      st.id = 'il-gate-css';
      st.textContent = CSS;
      (document.head || document.documentElement).appendChild(st);

      var how = '';
      for (var i = 0; i < HOWTO.length; i++) {
        how += '<details><summary>' + HOWTO[i][0] + '</summary><p>' + HOWTO[i][1] + '</p></details>';
      }
      var root = document.createElement('div');
      root.id = 'il-gate';
      root.setAttribute('role', 'dialog');
      root.setAttribute('aria-modal', 'true');
      root.setAttribute('aria-labelledby', 'il-gate-title');
      root.setAttribute('aria-describedby', 'il-gate-desc');
      root.innerHTML =
        '<div class="il-gate-card">' +
          '<h2 id="il-gate-title" tabindex="-1">Please turn off your ad blocker for ImmiLane</h2>' +
          '<div id="il-gate-desc">' +
            '<p>ImmiLane is free because of ads. They pay for the daily DOL data and keep every tool on this site free.</p>' +
            '<p>An ad blocker is on in this browser. Please turn it off for immilane.com (or add immilane.com to its allowlist), then press the button below.</p>' +
          '</div>' +
          '<h3>How to turn it off</h3>' + how +
          '<button type="button" class="il-gate-btn">I’ve turned it off — reload</button>' +
          '<p class="il-gate-foot">Still seeing this after turning it off? <a href="/support">Contact support</a>.</p>' +
          '<p class="il-gate-foot"><a href="/manage">Manage or cancel Case Watch</a> · <a href="/privacy">Privacy</a> · <a href="/terms">Terms</a></p>' +
        '</div>';

      // Everything behind the notice becomes inert: no clicks, no focus, hidden from screen readers.
      var kids = document.body.children;
      gated = [];
      for (var j = 0; j < kids.length; j++) {
        var el = kids[j];
        if (el.tagName === 'SCRIPT' || el.tagName === 'STYLE') continue;
        if (!el.hasAttribute('inert')) { el.setAttribute('inert', ''); gated.push([el, el.getAttribute('aria-hidden')]); }
        el.setAttribute('aria-hidden', 'true');
      }
      document.body.appendChild(root);
      document.documentElement.classList.add('il-gate-open');
      // Same task as the line above, so no frame is drawn between the blank page and the notice.
      shieldDone();

      var title = root.querySelector('#il-gate-title');
      var btn = root.querySelector('.il-gate-btn');
      btn.addEventListener('click', function () {
        btn.disabled = true;
        btn.textContent = 'Reloading…';
        // Tells the head block: the visitor says the blocker is off, so wait for
        // the checks (blank page) instead of showing the notice again at once.
        try { sessionStorage.setItem(RETRY, '1'); } catch (e) {}
        location.reload();
      });
      // Focus stays inside the notice (it cannot be dismissed; Escape does nothing).
      root.addEventListener('keydown', function (e) {
        if (e.key !== 'Tab') return;
        var f = root.querySelectorAll('summary, a[href], button:not([disabled])');
        if (!f.length) return;
        var first = f[0], last = f[f.length - 1], cur = document.activeElement;
        if (e.shiftKey && (cur === first || cur === title)) { e.preventDefault(); last.focus(); }
        else if (!e.shiftKey && cur === last) { e.preventDefault(); first.focus(); }
      });
      keepFocus = function (e) {
        if (!root.contains(e.target)) { try { title.focus(); } catch (err) {} }
      };
      document.addEventListener('focusin', keepFocus);
      try { title.focus(); } catch (e) {}
    }
    var gated = [], keepFocus = null;

    // Takes a notice shown from the remembered verdict down again (the checks
    // now pass, or doubt): the page behind gets back exactly what it had.
    function hideWall() {
      var g = document.getElementById('il-gate');
      if (!g) return;
      if (keepFocus) { document.removeEventListener('focusin', keepFocus); keepFocus = null; }
      g.parentNode.removeChild(g);
      for (var i = 0; i < gated.length; i++) {
        var el = gated[i][0];
        el.removeAttribute('inert');
        if (gated[i][1] === null) el.removeAttribute('aria-hidden'); else el.setAttribute('aria-hidden', gated[i][1]);
      }
      gated = [];
      document.documentElement.classList.remove('il-gate-open');
      try { if (document.activeElement && document.activeElement !== document.body) document.activeElement.blur(); } catch (e) {}
    }

    // The head block (il-shield) that hides the page until the verdict, if this page view has one.
    var SH = window.__ilShield;
    var SHIELD = !!(SH && SH.done && document.documentElement.classList.contains('il-shield'));
    if (SHIELD) SH.owned = 1;
    function shieldDone() { if (SHIELD) { try { SH.done(); } catch (e) {} } }

    // Runs after the pageview beacon has been decided; never delays or changes it.
    // Nothing is sent from here, and no wall shows, unless the switch file says
    // "on" or "measure": with "off" this whole function does nothing.
    // (Pages without the head block: the notice comes after the page, as before.)
    function decide(x1) {
      var before = flagGet();
      var st = strong(tripped, x1);
      if (!st && !before) return;   // the usual pageview: no evidence, nothing to do
      if (IS_BOT) return;
      var mode = null, waited = !st, started = false;
      function next() {
        if (mode === null || !waited || started) return;
        started = true;
        if (mode !== 'on' && mode !== 'measure') return;
        if (!st) { if (!IS_OPEN) { flagClear(); note('c', tripped); } return; }
        if (IS_OPEN) { note('e', tripped); return; }
        if (!WALL_IF_ADS_RUN && adsRan()) { note('v', tripped); return; }
        var r2 = {}, x2 = {};
        probe(2, r2, x2, function () {
          // A check that timed out means a slow network, not a blocker: no wall.
          if (!strong(r2, x2) || x2.slow || navigator.onLine === false) { note('n', r2); return; }
          if (!WALL_IF_ADS_RUN && adsRan()) { note('v', r2); return; }
          if (mode === 'measure') { note('m', r2); return; }
          try { showWall(); } catch (e) { note('n', r2); return; }
          flagSet();
          note(before ? 'a' : 's', r2);
        });
      }
      getMode(function (m) { mode = m; next(); });
      if (st) { setTimeout(function () { waited = true; next(); }, RECHECK_MS); }
    }

    // The no-peek path: the head block has hidden the page because Monumetric's
    // script did not run. Decide as fast as the evidence allows, then show the
    // page or the notice. The pageview beacon is unchanged (same three checks,
    // sent once all three are in, or after the usual 2 s timeouts).
    function shieldRun() {
      var e = SH.e || {}, t0 = SH.t0 || 0;
      var before = flagGet();
      var over = false, dHit = false, baitDone = false;
      function since() { return (window.performance && performance.now) ? performance.now() - t0 : 9e9; }
      function reveal() { hideWall(); shieldDone(); }
      // The switch, as read by the head block (or here, if it had not started).
      function withMode(cb) {
        if (e.m !== undefined) { cb(e.m); return; }
        var waited = 0;
        (function poll() {
          if (e.m !== undefined) { cb(e.m); return; }
          if ((waited += 25) > 4000) { cb('off'); return; }
          setTimeout(poll, 25);
        })();
      }

      // Remembered verdict: the notice at once, the checks run again behind it.
      // Not after the notice's own reload button: then the visitor says the
      // blocker is off, so the page stays blank until the checks answer.
      if (before && !SH.retry) { try { showWall(); } catch (err) { reveal(); } }

      // Round 1. The bait is read every 20 ms; it counts once the neutral control box is visible.
      var bait = box('adsbox', 'ad_slot sponsored-ad textAd');
      var ctl = box('', 'il-probe');
      var bStart = since();
      (function look() {
        try {
          if (hidden(bait) && !hidden(ctl)) dHit = true;
          if (since() - bStart >= BAIT_MS) baitDone = true;
        } catch (err) { baitDone = true; }
        tick();
        if (!dHit && since() - bStart < 350) setTimeout(look, 20);
      })();
      // The beacon keeps the old reading: bait after 350 ms, decoy and ad host as they answer (2 s max).
      var beaconTimer = setTimeout(function () {
        try {
          tripped.d = hidden(bait) || dHit;
          if (bait.parentNode) bait.parentNode.removeChild(bait);
          if (ctl.parentNode) ctl.parentNode.removeChild(ctl);
        } catch (err) {}
        var waited = 0;
        (function fin() {
          if ((e.s === undefined || e.f === undefined) && (waited += 50) < 1650) { setTimeout(fin, 50); return; }
          if (e.s === 1) tripped.s = true;
          if (e.f === 1) tripped.f = true;
          send();
        })();
      }, 350);
      setTimeout(tick, FAST_CAP_MS - Math.min(since(), FAST_CAP_MS) + 5);

      function tick() {
        if (over) return;
        if (dHit || (e.s === 1 && e.f === 1)) { over = true; confirm(); return; }
        if (!baitDone) return;
        // f alone never walls and s alone never walls, so once the bait is clear:
        if (e.s === 0 || (e.s === 1 && e.f === 0)) { over = true; clear(true); return; }
        if (since() >= FAST_CAP_MS) { over = true; clear(false); }   // still unanswered: not blocked
        else if (e.s === undefined || e.f === undefined) setTimeout(tick, 25);
      }

      // Not blocked (passed = the checks answered and passed; else a timeout).
      function clear(passed) {
        reveal();
        if (!before) return;
        withMode(function (m) {
          if (m !== 'on' && m !== 'measure') return;
          if (passed) { flagClear(); note('c', tripped); } else { note('n', tripped); }
        });
      }

      // Strong evidence in round 1: confirm with a fresh round (fast: blocked
      // requests fail at once) and read the switch; doubt means the page.
      function confirm() {
        var r2 = {}, x2 = {}, mode, gotMode = false, gotProbe = false, finished = false;
        var left = Math.max(300, STRONG_CAP_MS - since());
        function fin() {
          if (finished || !gotMode || !gotProbe) return;
          finished = true;
          clearTimeout(cap);
          if (mode !== 'on' && mode !== 'measure') { reveal(); if (mode === 'off') flagClear(); return; }
          if (IS_OPEN) { reveal(); note('e', tripped); return; }
          if (!WALL_IF_ADS_RUN && adsRan()) { reveal(); note('v', r2); return; }
          if (!strong(r2, x2) || x2.slow || navigator.onLine === false) { reveal(); note('n', r2); return; }
          if (mode === 'measure') { reveal(); note('m', r2); return; }
          try { showWall(); } catch (err) { reveal(); note('n', r2); return; }
          flagSet();
          note(before ? 'a' : 's', r2);
        }
        var cap = setTimeout(function () {
          if (finished) return;
          finished = true;
          reveal();
          if (gotMode && (mode === 'on' || mode === 'measure')) note('n', r2);
        }, left);
        withMode(function (m) { mode = m; gotMode = true; fin(); });
        probe(2, r2, x2, function () { gotProbe = true; fin(); }, 60, left);
      }
    }

    function run() {
      try {
        // Staging only: /any-page#il-gate-preview shows the notice for review, no blocker needed.
        if (!IS_PROD && location.hash === '#il-gate-preview') { showWall(); return; }
        var x1 = {};
        probe(1, tripped, x1, function () {
          send();
          try { decide(x1); } catch (e) {}
        });
        // Backstop: exactly one beacon per pageview no matter what.
        setTimeout(send, 3000);
      } catch (e) {}
    }
    function kick() { setTimeout(run, 200); }
    if (SHIELD && !(!IS_PROD && location.hash === '#il-gate-preview')) {
      // Last resort: whatever happens, the page is not left blank.
      setTimeout(function () { if (!document.getElementById('il-gate')) shieldDone(); }, STRONG_CAP_MS + 1500);
      try { shieldRun(); } catch (e) { shieldDone(); }
      setTimeout(send, 3000);
    } else {
      if (SHIELD) shieldDone();
      if (document.readyState === 'complete' || document.readyState === 'interactive') { kick(); }
      else { document.addEventListener('DOMContentLoaded', kick); }
    }
  } catch (e) {}
})();

/* Maintenance banner (2026-10-31, Pi outage). Thin dismissible bar at the top
 * of every page; dismissal remembered in localStorage per BANNER_ID.
 * REMOVE: set BANNER_ON = false (or delete this block), then run
 * python3 scripts/insert_session_check.py and push. */
(function () {
  'use strict';
  var BANNER_ON = true;
  var BANNER_ID = 'il-maint-2026-10-31';
  var BANNER_TEXT = 'Case Watch alerts and daily data updates are paused for maintenance. Service will be restored by Tuesday, Nov 3. Existing data stays available.';
  try {
    if (!BANNER_ON) return;
    try { if (localStorage.getItem('il-banner-closed') === BANNER_ID) return; } catch (e) {}
    function show() {
      if (document.getElementById('il-maint')) return;
      var bar = document.createElement('div');
      bar.id = 'il-maint';
      bar.setAttribute('role', 'status');
      bar.style.cssText = 'position:relative;z-index:2147483000;background:#fff4d6;color:#5c4400;border-bottom:1px solid #f0d58a;font:12.5px/1.4 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Arial,sans-serif;padding:5px 34px 5px 12px;text-align:center;margin:0;';
      var txt = document.createElement('span');
      txt.textContent = BANNER_TEXT;
      var x = document.createElement('button');
      x.type = 'button';
      x.setAttribute('aria-label', 'Dismiss');
      x.textContent = '×';
      x.style.cssText = 'position:absolute;right:6px;top:50%;transform:translateY(-50%);background:none;border:0;color:#5c4400;font-size:17px;line-height:1;cursor:pointer;padding:2px 6px;';
      x.onclick = function () {
        try { localStorage.setItem('il-banner-closed', BANNER_ID); } catch (e) {}
        if (bar.parentNode) bar.parentNode.removeChild(bar);
      };
      bar.appendChild(txt);
      bar.appendChild(x);
      document.body.insertBefore(bar, document.body.firstChild);
    }
    if (document.body) show();
    else document.addEventListener('DOMContentLoaded', show);
  } catch (e) {}
})();
