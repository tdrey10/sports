import json, requests
S = "https://site.api.espn.com/apis/site/v2/sports"
C = "https://sports.core.api.espn.com/v2/sports"
for path, month in [("football/nfl", "202609"), ("baseball/mlb", "202609"), ("basketball/nba", "202604"), ("hockey/nhl", "202604"), ("soccer/eng.1", "202609")]:
    sb = requests.get(f"{S}/{path}/scoreboard", params={"dates": month, "limit": 1000}, timeout=30).json()
    evs = [e for e in sb.get("events", []) if e["status"]["type"]["completed"]]
    print("=====", path, "completed", len(evs))
    if not evs: continue
    e = evs[len(evs)//2]
    comp = e["competitions"][0]
    print("scoreboard odds:", json.dumps(comp.get("odds"), indent=None)[:1500])
    with_odds = sum(1 for x in evs if x["competitions"][0].get("odds"))
    print("events with scoreboard odds:", with_odds, "of", len(evs))
    sport, league = path.split("/")
    url = f"{C}/{sport}/leagues/{league}/events/{e['id']}/competitions/{e['id']}/odds"
    lst = requests.get(url, timeout=30).json()
    print("core items:", [i["$ref"].split("/odds/")[1][:20] for i in lst.get("items", [])])
    for it in lst.get("items", [])[:1]:
        o = requests.get(it["$ref"].replace("http://", "https://"), timeout=30).json()
        o.pop("links", None)
        print("core provider obj:", json.dumps(o)[:2500])
