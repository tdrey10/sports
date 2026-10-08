"""Temporary probe #3: what betting odds does ESPN expose for free?"""
import json
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


def dump(label, obj, n=1500):
    print(f"--- {label}\n{json.dumps(obj)[:n]}\n")


for path, league in [("football/nfl", "nfl"), ("baseball/mlb", "mlb"), ("hockey/nhl", "nhl"),
                     ("football/college-football", "college-football"), ("soccer/eng.1", "eng.1"),
                     ("basketball/nba", "nba")]:
    code, sb = get(f"{SITE}/{path}/scoreboard")
    evs = sb.get("events", [])
    print(f"===== {path}: scoreboard {code}, {len(evs)} events")
    pre = [e for e in evs if e["status"]["type"]["state"] == "pre"]
    live = [e for e in evs if e["status"]["type"]["state"] == "in"]
    print("pre", len(pre), "live", len(live))
    for label, group in (("PRE", pre[:1]), ("LIVE", live[:1])):
        for e in group:
            comp = e["competitions"][0]
            dump(f"{label} scoreboard competitions[0].odds", comp.get("odds"))
            eid = e["id"]
            code, summ = get(f"{SITE}/{path}/summary", event=eid)
            dump(f"{label} summary pickcenter", summ.get("pickcenter"), 1200)
            dump(f"{label} summary odds", summ.get("odds"), 600)
            sport, lg = path.split("/")
            code, od = get(f"{CORE}/{sport}/leagues/{lg}/events/{eid}/competitions/{eid}/odds")
            items = od.get("items", [])
            print(f"{label} core odds {code}: {len(items)} providers")
            for it in items[:6]:
                ref = it.get("$ref")
                if ref:
                    c2, o = get(ref)
                    prov = (o.get("provider") or {}).get("name")
                    print("  provider:", prov, "| keys:", list(o.keys())[:20])
                    dump(f"  {prov} odds object", {k: o.get(k) for k in ("details", "overUnder", "spread", "overOdds", "underOdds", "homeTeamOdds", "awayTeamOdds", "open", "current", "moneylineWinner")}, 900)
                    pb = o.get("propBets") or {}
                    if pb.get("$ref"):
                        c3, props = get(pb["$ref"])
                        print(f"  propBets {c3}: count={props.get('count')}")
                        dump("  first prop", (props.get("items") or [None])[0], 700)
            break
