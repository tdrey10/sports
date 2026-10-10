"""Temporary probe #5: run the Covers parser on the live page and match today's ESPN teams."""
import sys

import requests

sys.path.insert(0, "probe")
import covers  # noqa: E402

rep = covers.college_injuries()
print("teams parsed:", None if rep is None else len(rep))
for t in ("Michigan State Spartans", "Illinois Fighting Illini", "Purdue Boilermakers", "Minnesota Golden Gophers"):
    print(t, "->", rep.get(t))
print("sample names:", list(rep)[:12])
sb = requests.get("https://site.api.espn.com/apis/site/v2/sports/football/college-football/scoreboard",
                  params={"groups": "80", "limit": 300}, timeout=20).json()
names = {c["team"]["displayName"] for e in sb["events"] for c in e["competitions"][0]["competitors"]}
from difflib import SequenceMatcher  # noqa: E402
exact = [n for n in names if n in rep]
miss = sorted(n for n in names if n not in rep)
print(f"ESPN teams today: {len(names)}; exact matches: {len(exact)}; not exact: {len(miss)}")
for n in miss:
    best = max(rep, key=lambda r: SequenceMatcher(None, n.lower(), r.lower()).ratio())
    print("  ", n, "~", best, round(SequenceMatcher(None, n.lower(), best.lower()).ratio(), 2))
statuses = {}
for rows in rep.values():
    for _, s, _ in rows:
        statuses[s] = statuses.get(s, 0) + 1
print("statuses:", statuses)
