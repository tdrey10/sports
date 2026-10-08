"""Temporary probe #4: ESPN odds layouts (scoreboard odds, prop bets, live odds)."""
import json
from collections import Counter
import requests

S = requests.Session()
S.headers["User-Agent"] = "Mozilla/5.0"
SITE = "https://site.api.espn.com/apis/site/v2/sports"
CORE = "https://sports.core.api.espn.com/v2/sports"


def get(url, **params):
    r = S.get(url, params=params, timeout=30)
    try:
        return r.status_code, r.json()
    except ValueError:
        return r.status_code, {}


def strip_links(o):
    if isinstance(o, dict):
        return {k: strip_links(v) for k, v in o.items() if k not in ("link", "links", "logos", "logo", "tracking")}
    if isinstance(o, list):
        return [strip_links(v) for v in o]
    return o


_, sb = get(f"{SITE}/football/nfl/scoreboard")
ev = sb["events"][0]
print("== NFL scoreboard odds (full, links stripped) ==")
print(json.dumps(strip_links(ev["competitions"][0].get("odds")))[:3000])
eid = ev["id"]

print("\n== NFL prop bets ==")
code, pb = get(f"{CORE}/football/leagues/nfl/events/{eid}/competitions/{eid}/odds/100/propBets", limit=1000)
print(code, "count", pb.get("count"), "pageCount", pb.get("pageCount"), "items", len(pb.get("items", [])))
items = pb.get("items", [])
print("types:", Counter(i.get("type", {}).get("name") for i in items).most_common(40))
seen = set()
for it in items:
    t = it.get("type", {}).get("name")
    if t in seen:
        continue
    if t and any(w in t for w in ("Passing Yards", "Receptions", "Rushing Yards", "Receiving Yards")):
        seen.add(t)
        print("\nSAMPLE", t, json.dumps(strip_links(it))[:900])
ath = next((i["athlete"]["$ref"] for i in items if i.get("athlete")), None)
if ath:
    c, a = get(ath)
    print("\nathlete lookup", c, a.get("displayName"), "| team ref:", (a.get("team") or {}).get("$ref"))

print("\n== live odds (NBA preseason or NHL) ==")
for path, sport, lg in (("hockey/nhl", "hockey", "nhl"), ("basketball/nba", "basketball", "nba"), ("baseball/mlb", "baseball", "mlb")):
    _, sb = get(f"{SITE}/{path}/scoreboard")
    live = [e for e in sb.get("events", []) if e["status"]["type"]["state"] == "in"]
    if not live:
        print(path, "no live games")
        continue
    lid = live[0]["id"]
    _, od = get(f"{CORE}/{sport}/leagues/{lg}/events/{lid}/competitions/{lid}/odds")
    for it in od.get("items", []):
        _, o = get(it["$ref"])
        print(path, "provider", (o.get("provider") or {}).get("id"), (o.get("provider") or {}).get("name"),
              "| ML", o.get("homeTeamOdds", {}).get("moneyLine"), o.get("awayTeamOdds", {}).get("moneyLine"),
              "| spread", o.get("spread"), o.get("homeTeamOdds", {}).get("spreadOdds"), o.get("awayTeamOdds", {}).get("spreadOdds"),
              "| total", o.get("overUnder"), o.get("overOdds"), o.get("underOdds"))
    _, od2 = get(f"{CORE}/{sport}/leagues/{lg}/events/{lid}/competitions/{lid}/odds/200")
    print("  direct /odds/200 ->", (od2.get("provider") or {}).get("name"), od2.get("details"))
    break

print("\n== NBA player props for an upcoming game, if any ==")
_, sb = get(f"{SITE}/basketball/nba/scoreboard", dates="20261009")
for e in sb.get("events", [])[:1]:
    i2 = e["id"]
    c, pb = get(f"{CORE}/basketball/leagues/nba/events/{i2}/competitions/{i2}/odds/100/propBets", limit=1000)
    print(c, "count", pb.get("count"), Counter(i.get("type", {}).get("name") for i in pb.get("items", [])).most_common(15))
