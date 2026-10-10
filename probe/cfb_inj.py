"""Temporary probe #2: more free places college football injuries might live."""
import json
import re

import requests

S = requests.Session()
S.headers["User-Agent"] = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/126 Safari/537.36"
GAME, MSU, ILL = "401858480", "127", "356"


def get(url, **params):
    try:
        r = S.get(url, params=params, timeout=25)
        ct = r.headers.get("content-type", "")
        return r.status_code, (r.json() if "json" in ct else r.text)
    except Exception as exc:  # noqa: BLE001
        return "ERR", str(exc)


def show(name, code, data, keys=("injuries",)):
    if isinstance(data, dict):
        print(f"{name}: {code} keys={list(data)[:25]}")
        for k in keys:
            if k in data:
                print(f"   {k}:", json.dumps(data[k])[:1200])
    else:
        print(f"{name}: {code} text[{len(data)}]")
        for w in ("Burt", "Chappell", "Keshawn", "injur"):
            i = data.find(w)
            if i >= 0:
                print(f"   found {w!r}:", re.sub(r"\s+", " ", data[max(0, i - 150): i + 250]))


code, d = get("https://site.web.api.espn.com/apis/site/v2/sports/football/college-football/summary", event=GAME)
show("site.web summary", code, d)
code, d = get("https://cdn.espn.com/core/college-football/game", xhr=1, gameId=GAME)
if isinstance(d, dict):
    gp = d.get("gamepackageJSON", {})
    show("cdn gamepackage", code, gp)
else:
    show("cdn gamepackage", code, d)
code, d = get(f"https://site.api.espn.com/apis/site/v2/sports/football/college-football/teams/{MSU}/injuries")
show("site team injuries MSU", code, d)
code, d = get(f"https://site.web.api.espn.com/apis/common/v3/sports/football/college-football/teams/{MSU}/injuries")
show("common v3 team injuries MSU", code, d)
code, d = get(f"https://site.api.espn.com/apis/site/v2/sports/football/college-football/teams/{MSU}/depthcharts")
show("depth chart MSU", code, d, keys=("depthchart", "items"))
code, d = get("https://www.espn.com/college-football/game/_/gameId/" + GAME)
show("espn.com game page", code, d)
code, d = get("https://www.cbssports.com/college-football/injuries/")
show("cbssports injuries", code, d)
code, d = get("https://www.covers.com/sport/football/ncaaf/injuries")
show("covers injuries", code, d)
code, d = get("https://api.collegefootballdata.com/player/portal")
show("cfbd (needs key)", code, d)
