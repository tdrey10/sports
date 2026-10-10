"""Temporary probe #4: how Covers labels each team's injury table."""
import re

import requests

S = requests.Session()
S.headers["User-Agent"] = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/126 Safari/537.36"
html = S.get("https://www.covers.com/sport/football/ncaaf/injuries", timeout=25).text
starts = [m.start() for m in re.finditer(r"<table", html)]
print("tables", len(starts))
for s in starts[:3]:
    print("=" * 30)
    print(re.sub(r"\n\s*\n", "\n", html[max(0, s - 1800): s + 300]))
s = starts[len(starts) // 2]
print("=" * 30, "middle")
print(re.sub(r"\n\s*\n", "\n", html[max(0, s - 1200): s + 200]))
print("no-injury markers:", html.count("No injuries"), html.count("no injuries"), html.count("No Injuries"))
