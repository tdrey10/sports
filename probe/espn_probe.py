"""Temporary probe #2."""
import json
import requests

S = requests.Session()
S.headers["User-Agent"] = "Mozilla/5.0"
B = "https://site.api.espn.com/apis/site/v2/sports"


def get(path, **params):
    r = S.get(f"{B}/{path}", params=params, timeout=30)
    try:
        return r.status_code, r.json()
    except ValueError:
        return r.status_code, {}


def count(label, path, **params):
    code, data = get(path, **params)
    evs = data.get("events", []) if isinstance(data, dict) else []
    print(f"{code} events={len(evs):5d} {label} {params}")


count("nba month", "basketball/nba/scoreboard", dates="202603")
count("nba month limit=1000", "basketball/nba/scoreboard", dates="202603", limit=1000)
count("nba month limit=300", "basketball/nba/scoreboard", dates="202603", limit=300)
count("ncaab month g50 limit=1000", "basketball/mens-college-basketball/scoreboard", dates="202602", groups=50, limit=1000)
count("ncaab month g50 limit=3000", "basketball/mens-college-basketball/scoreboard", dates="202602", groups=50, limit=3000)
count("ncaaf month g80 limit=1000", "football/college-football/scoreboard", dates="202609", groups=80, limit=1000)
count("mlb month limit=1000", "baseball/mlb/scoreboard", dates="202608", limit=1000)
count("nfl month limit=1000", "football/nfl/scoreboard", dates="202609", limit=1000)
count("epl month limit=1000", "soccer/eng.1/scoreboard", dates="202609", limit=1000)
count("nba year limit=1000", "basketball/nba/scoreboard", dates="2026", limit=1000)
# daily total for nba march as ground truth
total = 0
for d in range(1, 32):
    _, data = get("basketball/nba/scoreboard", dates=f"202603{d:02d}")
    total += len(data.get("events", []))
print("nba march summed daily:", total)

print("\n== MLB probable stats ==")
_, data = get("baseball/mlb/scoreboard")
for ev in data.get("events", [])[:1]:
    for c in ev["competitions"][0]["competitors"]:
        for p in c.get("probables", []):
            print("probable keys:", list(p.keys()))
            print("statistics:", json.dumps(p.get("statistics"))[:600])
            print("record:", json.dumps(p.get("record"))[:200])

print("\n== injury item ==")
_, inj = get("basketball/nba/injuries")
item = inj["injuries"][0]["injuries"][0]
print("item keys:", list(item.keys()))
print("status:", item.get("status"), "| type:", json.dumps(item.get("type"))[:200])
print("athlete:", json.dumps(item.get("athlete"))[:300])
print("details:", json.dumps(item.get("details"))[:300])
