import json, requests
S = "https://site.api.espn.com/apis/site/v2/sports/hockey/nhl"
sb = requests.get(f"{S}/scoreboard", timeout=30).json()
ev = sb["events"][0]
c = ev["competitions"][0]["competitors"][0]
p = c.get("probables", [])[0]
print("probable statistics:", json.dumps(p.get("statistics"))[:500])
gid = p["athlete"]["id"]
for url in [
    f"https://sports.core.api.espn.com/v2/sports/hockey/leagues/nhl/athletes/{gid}/statistics",
    f"https://site.web.api.espn.com/apis/common/v3/sports/hockey/nhl/athletes/{gid}/stats",
    f"https://site.web.api.espn.com/apis/common/v3/sports/hockey/nhl/athletes/{gid}/overview",
]:
    r = requests.get(url, timeout=30)
    print("URL", url, r.status_code)
    if r.ok:
        t = json.dumps(r.json())
        i = t.find("save")
        print(t[:300]); print("...save at", i, t[max(0,i-300):i+300] if i >= 0 else "")
