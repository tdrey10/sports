"""Temporary probe: where does ESPN publish college football injuries?"""
import json

import requests

S = requests.Session()
S.headers["User-Agent"] = "Mozilla/5.0"


def get(url, **params):
    try:
        r = S.get(url, params=params, timeout=20)
        return r.status_code, (r.json() if r.headers.get("content-type", "").startswith("application/json") else r.text[:200])
    except Exception as exc:  # noqa: BLE001
        return "ERR", str(exc)


SITE = "https://site.api.espn.com/apis/site/v2/sports/football/college-football"
CORE = "https://sports.core.api.espn.com/v2/sports/football/leagues/college-football"

code, sb = get(f"{SITE}/scoreboard", groups="80", limit=300)
events = sb.get("events", []) if isinstance(sb, dict) else []
print("scoreboard", code, len(events))
target = None
for ev in events:
    if any(k in ev.get("name", "") for k in ("Purdue", "Michigan State")):
        target = ev
        print("game", ev["id"], ev["name"])
        break

print("\n== league injuries endpoint")
code, data = get(f"{SITE}/injuries")
print(code, (list(data)[:10] if isinstance(data, dict) else data), len(data.get("injuries", [])) if isinstance(data, dict) else "")

if target:
    print("\n== game summary keys")
    code, summ = get(f"{SITE}/summary", event=target["id"])
    print(code, list(summ)[:40] if isinstance(summ, dict) else summ)
    inj = summ.get("injuries") if isinstance(summ, dict) else None
    print("summary.injuries:", json.dumps(inj)[:1500] if inj else inj)
    for c in target["competitions"][0]["competitors"]:
        tid = c["team"]["id"]
        print(f"\n== core team injuries {c['team']['displayName']} ({tid})")
        code, ti = get(f"{CORE}/teams/{tid}/injuries", limit=100)
        print(code, json.dumps(ti)[:600])
        items = ti.get("items", []) if isinstance(ti, dict) else []
        for it in items[:3]:
            code, one = get(it["$ref"])
            print("  item", code, json.dumps(one)[:500])
        code, roster = get(f"{SITE}/teams/{tid}/roster")
        if isinstance(roster, dict):
            ath = [a for g in roster.get("athletes", []) for a in (g.get("items", []) if isinstance(g, dict) else [g])]
            hurt = [(a.get("displayName"), a.get("injuries")) for a in ath if a.get("injuries")]
            print("  roster", code, len(ath), "athletes; with injuries:", hurt[:6])
