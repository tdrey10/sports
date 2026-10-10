"""Temporary probe #3: Covers robots.txt and the injury page's layout."""
import re

import requests

S = requests.Session()
S.headers["User-Agent"] = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/126 Safari/537.36"
r = S.get("https://www.covers.com/robots.txt", timeout=25)
print("robots", r.status_code)
print(r.text[:3000])
html = S.get("https://www.covers.com/sport/football/ncaaf/injuries", timeout=25).text
print("page bytes", len(html))
i = html.find("Michigan State")
print("MSU at", i)
print(re.sub(r"\n\s*\n", "\n", html[max(0, i - 1500): i + 3500]))
heads = re.findall(r"<(h\d|div|span|a)[^>]*class=['\"][^'\"]*team[^'\"]*['\"][^>]*>", html)[:15]
print("team-ish tags:", heads)
print("tables:", html.count("<table"), "rows:", html.count("<tr"))
