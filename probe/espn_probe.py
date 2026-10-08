"""Temporary: find which ESPN request formats work. Prints status, event counts and key fields."""
import json
import requests

S = requests.Session()
S.headers["User-Agent"] = "Mozilla/5.0"
B = "https://site.api.espn.com/apis/site/v2/sports"


def get(path, **params):
    r = S.get(f"{B}/{path}", params=params, timeout=30)
    try:
        data = r.json()
    except ValueError:
        data = {}
    return r.status_code, data


def show(label, path, **params):
    code, data = get(path, **params)
    evs = data.get("events", []) if isinstance(data, dict) else []
    done = sum(1 for e in evs if e.get("status", {}).get("type", {}).get("completed"))
    print(f"{code}  events={len(evs):4d} completed={done:4d}  {label}  {params}")
    return data


print("== scoreboard formats ==")
show("nba single day", "basketball/nba/scoreboard", dates="20260301")
show("nba 7-day range", "basketball/nba/scoreboard", dates="20260301-20260307")
show("nba 7-day range limit=1000", "basketball/nba/scoreboard", dates="20260301-20260307", limit=1000)
show("nba 7-day range limit=500", "basketball/nba/scoreboard", dates="20260301-20260307", limit=500)
show("nba 30-day range limit=500", "basketball/nba/scoreboard", dates="20260301-20260330", limit=500)
show("nba month yyyymm", "basketball/nba/scoreboard", dates="202603")
show("nba no params", "basketball/nba/scoreboard")
show("nfl single day", "football/nfl/scoreboard", dates="20261004")
show("nfl range limit=500", "football/nfl/scoreboard", dates="20260901-20261007", limit=500)
show("nfl season week", "football/nfl/scoreboard", seasontype=2, week=4, dates=2026)
show("ncaaf day groups=80", "football/college-football/scoreboard", dates="20261003", groups=80, limit=500)
show("ncaab day groups=50", "basketball/mens-college-basketball/scoreboard", dates="20260301", groups=50, limit=500)
show("ncaab range groups=50", "basketball/mens-college-basketball/scoreboard", dates="20260301-20260307", groups=50, limit=500)
show("mlb single day", "baseball/mlb/scoreboard", dates="20260901")
show("mlb range limit=500", "baseball/mlb/scoreboard", dates="20260901-20260907", limit=500)
show("nhl single day", "hockey/nhl/scoreboard", dates="20260301")
show("epl range limit=500", "soccer/eng.1/scoreboard", dates="20260901-20260930", limit=500)

print("\n== upcoming MLB (probables) ==")
data = show("mlb today", "baseball/mlb/scoreboard")
for ev in data.get("events", [])[:2]:
    for c in ev["competitions"][0]["competitors"]:
        print("  probables:", json.dumps(c.get("probables", []))[:400])

print("\n== a finished NBA game summary ==")
data = show("nba day", "basketball/nba/scoreboard", dates="20260301")
evs = [e for e in data.get("events", []) if e.get("status", {}).get("type", {}).get("completed")]
if evs:
    eid = evs[0]["id"]
    code, summ = get("basketball/nba/summary", event=eid)
    print("summary", code)
    for team in summ.get("boxscore", {}).get("players", [])[:1]:
        for grp in team.get("statistics", [])[:2]:
            print("  group name:", grp.get("name"), "labels:", grp.get("labels"))
            ath = (grp.get("athletes") or [{}])[0]
            print("  first athlete:", (ath.get("athlete") or {}).get("displayName"), ath.get("stats"))

print("\n== a finished NFL game summary ==")
data = show("nfl day", "football/nfl/scoreboard", dates="20261004")
evs = [e for e in data.get("events", []) if e.get("status", {}).get("type", {}).get("completed")]
if evs:
    code, summ = get("football/nfl/summary", event=evs[0]["id"])
    print("summary", code)
    for team in summ.get("boxscore", {}).get("players", [])[:1]:
        for grp in team.get("statistics", [])[:3]:
            print("  group name:", grp.get("name"), "labels:", grp.get("labels"))

print("\n== injuries ==")
for path in ("basketball/nba/injuries", "football/nfl/injuries"):
    code, inj = get(path)
    blocks = inj.get("injuries", []) if isinstance(inj, dict) else []
    print(code, path, "teams:", len(blocks))
    if blocks:
        b = blocks[0]
        print("  keys:", list(b.keys())[:8], "first:", json.dumps((b.get("injuries") or [{}])[0])[:300])
