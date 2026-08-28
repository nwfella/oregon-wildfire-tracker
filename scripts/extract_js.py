#!/usr/bin/env python3
"""Extract <script> blocks from template.html into scratch/appN.js for `node --check`.

Kept in scripts/ (tracked) because the scratch/ dir is ephemeral in this
environment — scratch output is recreated on every run.
"""
import os, re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "template.html")
OUT = os.path.join(ROOT, "scratch")

html = open(SRC, encoding="utf-8").read()
blocks = re.findall(r"<script>(.*?)</script>", html, re.S)
os.makedirs(OUT, exist_ok=True)
for i, b in enumerate(blocks):
    with open(os.path.join(OUT, "app%d.js" % i), "w", encoding="utf-8", newline="\n") as f:
        f.write(b)
print("script blocks: %d" % len(blocks))
for i in range(len(blocks)):
    print("scratch/app%d.js %d" % (i, os.path.getsize(os.path.join(OUT, "app%d.js" % i))))
