import json, requests
B = "https://site.api.espn.com/apis/site/v2/sports"
for path, day in [("basketball/nba", "20260301"), ("hockey/nhl", "20260301"), ("football/nfl", "20251012"),
                  ("baseball/mlb", "20260801"), ("soccer/eng.1", "20260301")]:
    sb = requests.get(f"{B}/{path}/scoreboard", params={"dates": day}, timeout=30).json()
    evs = sb.get("events", [])
    if not evs:
        print(path, "no events"); continue
    eid = evs[0]["id"]
    s = requests.get(f"{B}/{path}/summary", params={"event": eid}, timeout=30)
    print("==", path, eid, s.status_code, len(s.content), "bytes")
    d = s.json()
    print("keys", list(d))
    for t in (d.get("boxscore") or {}).get("teams", [])[:1]:
        print("team stats:", json.dumps([(x.get("name"), x.get("label"), x.get("displayValue")) for x in t.get("statistics", [])])[:1500])
