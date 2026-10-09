import json, requests
S = "https://site.api.espn.com/apis/site/v2/sports/mma/ufc"
C = "https://sports.core.api.espn.com/v2/sports/mma/leagues/ufc"
def get(u, **p):
    r = requests.get(u, params=p, timeout=30)
    print("GET", r.url, r.status_code)
    return r.json() if r.ok else {}
def short(x, n=1500): return json.dumps(x)[:n]

sb = get(f"{S}/scoreboard")
evs = sb.get("events", [])
print("events", len(evs), [ (e.get("name"), e.get("date"), len(e.get("competitions", []))) for e in evs])
if evs:
    ev = evs[0]
    print("EVENT KEYS", list(ev))
    comp = ev["competitions"][0]
    print("COMP KEYS", list(comp))
    print("COMP", short(comp, 3000))
    print("ODDS INLINE", short(comp.get("odds"), 1500))
    cid = comp["id"]; eid = ev["id"]
    lst = get(f"{C}/events/{eid}/competitions/{cid}/odds")
    print("CORE ODDS LIST", short(lst, 2500))
    for it in (lst.get("items") or [])[:3]:
        ref = it.get("$ref")
        if ref and "homeAthleteOdds" not in it and "awayAthleteOdds" not in it:
            print("ODDS ITEM", short(get(ref.replace("http://", "https://")), 2500))
# Past cards: a month of results
for d in ("202609", "20260912"):
    past = get(f"{S}/scoreboard", dates=d, limit=500)
    pe = past.get("events", [])
    print("PAST", d, len(pe), [(e.get("name"), e.get("date"), len(e.get("competitions", []))) for e in pe][:6])
    if pe:
        comp = pe[0]["competitions"][0]
        print("PAST COMP", short(comp, 2500))
        eid, cid = pe[0]["id"], comp["id"]
        lst = get(f"{C}/events/{eid}/competitions/{cid}/odds")
        print("PAST CORE ODDS", short(lst, 2500))
        for it in (lst.get("items") or [])[:2]:
            ref = it.get("$ref")
            if ref:
                print("PAST ODDS ITEM", short(get(ref.replace("http://", "https://")), 2500))
# Calendar / how far back
cal = get(f"{S}/scoreboard", dates="2025")
print("YEAR 2025 events", len(cal.get("events", [])), short(sb.get("leagues", [{}])[0].get("calendar", [])[:5], 800))
