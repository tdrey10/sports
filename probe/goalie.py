import json, requests
S = "https://site.api.espn.com/apis/site/v2/sports/hockey/nhl"
sb = requests.get(f"{S}/scoreboard", timeout=30).json()
for ev in sb.get("events", [])[:3]:
    comp = ev["competitions"][0]
    for c in comp["competitors"]:
        print(ev["name"], c["homeAway"], "probables:", json.dumps(c.get("probables"))[:400])
    s = requests.get(f"{S}/summary", params={"event": ev["id"]}, timeout=30).json()
    print("summary keys:", list(s.keys()))
    for k in ("probables", "goalies", "rosters"):
        if k in s: print(k, json.dumps(s[k])[:600])
