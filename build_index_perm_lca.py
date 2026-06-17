#!/usr/bin/env python3
"""
Rebuild data/employer_index.json keyed off the FULL HISTORICAL employer universe.

UNIVERSE = the H-1B/PERM multi-year store (h1b-build/multi/out/store_multi.json),
which holds every employer that appears in ANY DOL OFLC disclosure file
(PERM + LCA, FY2008-FY2026, Method-B reunified canonical employers) -- ~97K
employers. This is the SAME store the live worker /h1b endpoint reads, so the
leaderboard row, the profile headline and the approximate rank all derive from
ONE consistent data universe.

  perm  -- PERM (green-card labor certification) filings disclosed by DOL,
           summed cert+denied+withdrawn over permByYear[] (all FYs, all
           statuses). EXACTLY the number employer.html's permStatsFromDisclosure()
           computes and the /h1b endpoint returns (e.g. Cognizant 27,788).
  lca   -- H-1B LCA (ETA-9035) filings, sum of lcaByYear[].total over all FYs.
  total -- perm + lca.

This replaces the previous universe (data/employer_data.json, the realtime
PERM-tracker scanner catch ~46K) which DROPPED every historically-filing
employer the 3h scanner had not seen recently. The realtime scanner number is
NOT used here at all -- it lives only in the separate "Live tracker" widget.

Display names: where a store employer slug-matches the realtime PERM-tracker
dataset (data/employer_data.json) on its canonical slug OR any member slug, we
prefer that dataset's prettier mixed-case name (so the big employers keep
"Cognizant Technology Solutions US Corporation" rather than the store's ALLCAPS
"...US CORP"), and that pretty name is exactly what the profile page's <h1>
shows -- so leaderboard name == profile name == deep-link slug. Employers only
in the store (the long tail) use the store's canonical name. The store canonical
name is always added to the `a` (search alias) field so search finds either.

Output keeps backward-compat: `t` mirrors `total` for any reader keying off `t`;
`perm`, `lca`, `total`, `a` are all present.
"""
import json, re, unicodedata, datetime, os

HERE = os.path.dirname(os.path.abspath(__file__))
PERM_DATA = os.path.join(HERE, 'data', 'employer_data.json')           # realtime scanner catch (pretty names only)
H1B_STORE = os.path.abspath(os.path.join(HERE, '..', 'h1b-build', 'multi', 'out', 'store_multi.json'))
OUT = os.path.join(HERE, 'data', 'employer_index.json')


def normalize_slug(s):
    if not s:
        return ''
    s = unicodedata.normalize('NFKD', str(s))
    s = ''.join(c for c in s if not unicodedata.combining(c))
    s = s.lower().replace('&', ' and ')
    s = re.sub(r'[^a-z0-9]+', '-', s)
    s = s.strip('-')
    return s[:200]


def clean_text(s):
    return re.sub(r'\s+', ' ', '' if s is None else str(s)).strip()


def build_pretty_name_map(perm_path):
    """slug -> best (mixed-case) display name from the realtime PERM-tracker
    dataset, used ONLY to prettify display names. Higher total wins on collision;
    a mixed-case name beats an ALLCAPS one at equal total."""
    pj = json.load(open(perm_path))
    out = {}
    for e in (pj.get('employers') or []):
        if not e or not e.get('name'):
            continue
        name = clean_text(e['name'])
        if not name:
            continue
        slug = normalize_slug(name)
        if not slug:
            continue
        total = e.get('total') or 0
        is_caps = 1 if name == name.upper() else 0
        cand = (total, -is_caps, len(name))
        cur = out.get(slug)
        if cur is None or cand > cur[0]:
            out[slug] = (cand, name)
    return {k: v[1] for k, v in out.items()}


def perm_sum(rec):
    return sum(((y.get('cert') or 0) + (y.get('denied') or 0) + (y.get('withdrawn') or 0))
               for y in (rec.get('permByYear') or []))


def lca_sum(rec):
    return sum((y.get('total') or 0) for y in (rec.get('lcaByYear') or []))


def main():
    print(f'[build] UNIVERSE (H-1B/PERM store) <- {H1B_STORE}')
    store = json.load(open(H1B_STORE))
    emps = store['employers']
    print(f'[build] store employers: {len(emps)}')

    print(f'[build] pretty names      <- {PERM_DATA}')
    pretty = build_pretty_name_map(PERM_DATA)
    print(f'[build] pretty-name slugs: {len(pretty)}')

    rows = []
    total_perm = 0
    total_lca = 0
    pretty_named = 0
    for canon_slug, rec in emps.items():
        perm = perm_sum(rec)
        lca = lca_sum(rec)
        total = perm + lca
        if total <= 0:
            continue  # no disclosed PERM or LCA filings -> not on the leaderboard
        store_name = clean_text(rec.get('name')) or canon_slug
        # Prefer a prettier realtime-dataset name when this employer (canonical or
        # any member slug) matches it -- the exact name the profile <h1> shows.
        candidates = [canon_slug] + list(rec.get('memberSlugs') or [])
        best = None
        for cs in candidates:
            nm = pretty.get(cs)
            if nm and (best is None or len(nm) > len(best)):
                best = nm
        if best:
            display = best
            pretty_named += 1
        else:
            display = store_name
        total_perm += perm
        total_lca += lca
        alias = store_name.lower()
        if display.lower() == alias:
            alias = canon_slug
        rows.append({
            'n': display,
            'perm': perm,
            'lca': lca,
            'total': total,
            't': total,
            'a': alias,
        })

    rows.sort(key=lambda r: (-r['total'], r['n'].lower()))

    out = {
        'generatedAt': datetime.datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ'),
        'totalEmployers': len(rows),
        'totalPerm': total_perm,
        'totalLca': total_lca,
        'totalCases': total_perm + total_lca,
        'universe': 'dol-disclosure-store FY2008-FY2026',
        'employers': rows,
    }
    json.dump(out, open(OUT, 'w'), separators=(',', ':'))
    print(f'[build] wrote {OUT}')
    print(f'[build] employers={len(rows)} pretty_named={pretty_named} '
          f'store_named={len(rows)-pretty_named} totalPerm={total_perm} '
          f'totalLca={total_lca} totalCases={total_perm+total_lca}')

    byslug = {normalize_slug(r['n']): r for r in rows}
    for s in ['cognizant-technology-solutions-us-corporation', 'microsoft-corporation',
              'amazon-com-services-llc', 'tata-consultancy-services-limited',
              'infosys-limited']:
        r = byslug.get(s)
        if r:
            print(f"  {r['n'][:44]:44} perm={r['perm']:>6} lca={r['lca']:>8} total={r['total']:>8}")
        else:
            print(f"  {s}: NOT FOUND (store-named)")

    by_perm = sorted(rows, key=lambda r: -r['perm'])
    print('[build] top 5 by PERM:')
    for i, r in enumerate(by_perm[:5], 1):
        print(f'  #{i} {r["n"][:40]:40} perm={r["perm"]}')


if __name__ == '__main__':
    main()
