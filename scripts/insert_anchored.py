#!/usr/bin/env python3
"""Insert or refresh the Monumetric anchored block on every page with the loader.

Run from the repo root: python3 scripts/insert_anchored.py
Pages that already have the block (BEGIN/END markers) get it replaced in place;
pages without it get it inserted right after <body>.
"""
import subprocess, sys

BEGIN = "<!-- Monumetric anchored ad units (self-positioning via loader) — BEGIN -->"
END = "<!-- Monumetric anchored ad units — END -->"

with open("scripts/_anchored_block.html", "r", encoding="utf-8") as f:
    block = f.read().rstrip("\n")

# pages that have the loader
pages = subprocess.check_output("grep -l monu.delivery *.html", shell=True, text=True).split()

changed = []
for p in pages:
    with open(p, "r", encoding="utf-8") as f:
        html = f.read()
    if BEGIN in html:
        start = html.find(BEGIN)
        end = html.find(END)
        if end == -1 or end < start:
            print(f"ERROR: BEGIN marker without END marker in {p}")
            sys.exit(1)
        end += len(END)
        new = html[:start] + block + html[end:]
        action = "UPDATE"
    else:
        needle = "<body>\n"
        idx = html.find(needle)
        if idx == -1:
            print(f"ERROR no <body> newline in {p}")
            sys.exit(1)
        insert_at = idx + len(needle)
        new = html[:insert_at] + block + "\n" + html[insert_at:]
        action = "INSERT"
    if new == html:
        print(f"OK unchanged: {p}")
        continue
    with open(p, "w", encoding="utf-8") as f:
        f.write(new)
    changed.append(p)
    print(f"{action} anchored -> {p}")

print(f"\nTotal changed: {len(changed)}")
