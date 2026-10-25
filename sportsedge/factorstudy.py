"""The game-factor study (owner, Oct 25: "there needs to be a study on every game played in every sport...
injuries, teams that play well on short rest, hot and cold streaks, if they been sick, how they have
been playing, whether they like to play inside or outside, anything that can affect a game").

Every situation we can measure before a game starts, for both teams, in every sport with closing lines
(NFL, college football, NBA, college hoops, MLB, NHL), 2016 on:
- schedule: back-to-back, 3rd game in 4 days, rest edge, short week, off a bye, 3rd+ straight road
  game, long home stand, early season;
- form: win and losing streaks, off a blowout win or loss, off an upset win, home and road splits;
- travel and venue: 2+ time zones from home, visiting altitude, a dome team playing outside, cold,
  wind and rain;
- matchup: revenge (lost the last meeting);
- the team itself: does THIS team beat the closing line in this spot (short rest, long rest, the road,
  outdoors, the cold, off a loss), from its own earlier games.
Luck, close games and slumps are the "who's due" study (nbastudy.py); player absences are the absence
study (absencestudy.py); injuries and illness on today's games are in the player reports. Sickness has
no history to test (ESPN keeps no past injury reports), so it stays a note.

Each factor is tested both ways (back the team in the spot, fade it) on the moneyline (dogs +100..+300,
favorites to -170) and, where spreads mean something, against the spread, at the closing price.
Seasons through 2021 tune, 2022 on are blind, and a luck control runs the same bars on random tenths of
all games. Only STRONG (blind and tuning both beat zero by a standard error) is worth money; PROVEN is
worth watching. game_study() applies it to today's games: which factors each team is in and what the
test says about them.
"""

from __future__ import annotations

import csv
import random
from collections import Counter, defaultdict, deque
from datetime import datetime, timezone
from pathlib import Path

from .linestudies import (
    BLOWOUT,
    DOG,
    MAX_FAV,
    SPREAD_SPORTS,
    TUNE_LAST,
    G,
    _fair,
    load,
    verdict,
)
from .nflhist import Record

SPORTS = ("nfl", "ncaaf", "nba", "ncaab", "mlb", "nhl")
DAILY = ("nba", "ncaab", "nhl", "mlb")
FOOTBALL = ("nfl", "ncaaf")
SPREAD_PRICE = -110.0
ALTITUDE_M = 1400
REVENGE_DAYS = 400
SPLIT_MIN = 8  # a team needs this many earlier games in a spot before its own split counts
SPLIT_EDGE = 0.12  # its average result this far over (or under) the closing line = thrives (or struggles)
SPOTS = ("on short rest", "on long rest", "on the road", "outdoors", "in the cold", "off a loss")
NAME = {"nfl": "NFL", "ncaaf": "College football", "nba": "NBA", "ncaab": "College hoops", "mlb": "MLB", "nhl": "NHL"}


def _rest(sport: str) -> tuple[float, float]:
    """(short, long) rest in days for the sport."""
    return {"nfl": (5, 12), "ncaaf": (5, 13)}.get(sport, (1.5, 3.5))


class Tracker:
    """Team states replayed game by game: everything a team's next game can know beforehand."""

    def __init__(self, sport: str) -> None:
        self.sport = sport
        self.season = None
        self.last: dict[str, datetime] = {}
        self.recent: dict[str, deque] = defaultdict(lambda: deque(maxlen=3))
        self.streak: Counter = Counter()
        self.last_margin: dict[str, tuple[float, bool]] = {}
        self.played: Counter = Counter()
        self.road_run: Counter = Counter()  # straight road games before this one
        self.home_run: Counter = Counter()
        self.split: dict[str, list[int]] = defaultdict(lambda: [0, 0, 0, 0])  # home w, l, road w, l
        self.met: dict[tuple, tuple[datetime, float]] = {}
        self.zone: dict[str, Counter] = defaultdict(Counter)
        self.dome: dict[str, Counter] = defaultdict(Counter)
        self.elev: dict[str, Counter] = defaultdict(Counter)
        self.spot_hist: dict[tuple, list[float]] = defaultdict(list)  # (team, spot) -> results vs the close

    def _new_season(self, season: int) -> None:
        if season != self.season:
            self.season = season
            for d in (self.streak, self.played, self.road_run, self.home_run):
                d.clear()
            self.last_margin.clear()
            self.split.clear()
            self.last.clear()
            self.recent.clear()

    def ctx(self, team: str, opp: str, side: str, start: datetime, neutral: bool, *, tzo=None, elev=None,
            indoor=None, temp=None, wind=None, rain=None, home_team: str | None = None) -> dict:
        """One side's situation before a game. Venue fields default to the home team's usual venue."""
        short, long_ = _rest(self.sport)
        rest = (start - self.last[team]).total_seconds() / 86400 if team in self.last else None
        host = home_team if home_team is not None else (team if side == "home" else opp)
        if tzo is None and self.zone[host]:
            tzo = self.zone[host].most_common(1)[0][0]
        if elev is None and self.elev[host]:
            elev = self.elev[host].most_common(1)[0][0]
        if self.sport not in FOOTBALL + ("mlb",):
            indoor = True  # hoops and hockey are always inside
        elif indoor is None and self.dome[host]:
            indoor = self.dome[host].most_common(1)[0][0]
        home_zone = self.zone[team].most_common(1)[0][0] if self.zone[team] else None
        dome_team = bool(self.dome[team]) and self.dome[team].most_common(1)[0][0]
        lm = self.last_margin.get(team)
        hw, hl, rw, rl = self.split[team]
        met = self.met.get((team, opp))
        spots = []
        if rest is not None and rest <= short:
            spots.append("on short rest")
        if rest is not None and rest >= long_:
            spots.append("on long rest")
        if side == "away" and not neutral:
            spots.append("on the road")
        if indoor is False:
            spots.append("outdoors")
        if indoor is False and temp is not None and temp <= 40:
            spots.append("in the cold")
        if lm is not None and lm[0] < 0:
            spots.append("off a loss")
        own = {}
        for s in spots:
            h = self.spot_hist.get((team, s), [])
            if len(h) >= SPLIT_MIN:
                own[s] = (sum(h) / len(h), len(h))
        return {
            "side": side, "neutral": neutral, "rest": rest, "short": short, "long": long_,
            "in4": sum((start - d).total_seconds() < 4 * 86400 for d in self.recent[team]),
            "streak": self.streak[team], "last_margin": lm[0] if lm else None, "last_upset": bool(lm and lm[1]),
            "played": self.played[team], "road_run": self.road_run[team] if side == "away" else 0,
            "home_run": self.home_run[team] if side == "home" else 0,
            "home_pct": hw / (hw + hl) if hw + hl >= 4 else None, "road_pct": rw / (rw + rl) if rw + rl >= 4 else None,
            "road_n": rw + rl,
            "revenge": bool(met and met[1] < 0 and (start - met[0]).days <= REVENGE_DAYS),
            "zones": abs(tzo - home_zone) if tzo is not None and home_zone is not None and side == "away" else 0,
            "east": tzo is not None and home_zone is not None and tzo > home_zone,
            "elev": elev, "dome_team": dome_team, "indoor": indoor, "temp": temp, "wind": wind, "rain": rain,
            "spots": spots, "own": own,
        }

    def add(self, g: G) -> None:
        """Fold a final into the states (after its features were read)."""
        fair = _fair(g.ml)
        sides = {"home": (g.home, g.away), "away": (g.away, g.home)}
        for side, (t, o) in sides.items():
            m = g.margin if side == "home" else -g.margin
            res = _vs_close(g, side, fair)
            if res is not None:
                c = self.ctx(t, o, side, g.start, g.neutral, tzo=g.tzo, elev=g.elev, indoor=g.indoor if g.sport in
                             FOOTBALL + ("mlb",) else True, temp=g.temp, wind=g.wind, rain=g.rain)
                for s in c["spots"]:
                    self.spot_hist[(t, s)].append(res)
            price = g.ml.get(side)
            self.last_margin[t] = (m, bool(price and price > 0 and m > 0))
            self.streak[t] = (max(self.streak[t], 0) + 1) if m > 0 else (min(self.streak[t], 0) - 1) if m < 0 else 0
            self.last[t] = g.start
            self.recent[t].append(g.start)
            self.played[t] += 1
            self.met[(t, o)] = (g.start, m)
            hw = self.split[t]
            if not g.neutral:
                idx = (0 if m > 0 else 1) if side == "home" else (2 if m > 0 else 3)
                hw[idx] += m != 0
            if side == "away" and not g.neutral:
                self.road_run[t] += 1
                self.home_run[t] = 0
            elif side == "home" and not g.neutral:
                self.home_run[t] += 1
                self.road_run[t] = 0
        if not g.neutral:
            if g.tzo is not None:
                self.zone[g.home][g.tzo] += 1
            if g.elev is not None:
                self.elev[g.home][g.elev] += 1
            if self.sport in FOOTBALL + ("mlb",):
                self.dome[g.home][bool(g.indoor)] += 1


def _vs_close(g: G, side: str, fair: dict | None) -> float | None:
    """How a side did against the closing line: cover minus a coin flip (spread sports), or its win
    minus the no-vig chance the market gave it."""
    if g.sport in SPREAD_SPORTS and g.spread is not None:
        m = (g.margin + g.spread) if side == "home" else -(g.margin + g.spread)
        return (1.0 if m > 0 else 0.0 if m < 0 else 0.5) - 0.5
    if fair:
        return g.won(side) - fair[side]
    return None


def factors(sport: str) -> dict:
    """label -> test on (this side's situation, the opponent's). Each is tested as 'back' (the side is
    in it) and 'fade' (the opponent is in it)."""
    blow = BLOWOUT[sport]
    f = {}
    if sport in DAILY and sport != "mlb":
        f["on a back-to-back"] = lambda c: c["rest"] is not None and c["rest"] <= 1.5
        f["3rd game in 4 days"] = lambda c: c["in4"] >= 2
    if sport in FOOTBALL:
        f["on a short week"] = lambda c: c["rest"] is not None and c["rest"] <= c["short"]
        f["off a bye"] = lambda c: c["rest"] is not None and c["rest"] >= c["long"]
    if sport != "mlb":
        f["2+ more days of rest than the other team"] = None  # pairwise, below
    f["on a 3+ game win streak"] = lambda c: c["streak"] >= 3
    f["on a 3+ game losing streak"] = lambda c: c["streak"] <= -3
    if sport in DAILY:
        f["on a 5+ game win streak"] = lambda c: c["streak"] >= 5
        f["on a 5+ game losing streak"] = lambda c: c["streak"] <= -5
    f[f"off a blowout loss ({blow}+)"] = lambda c: c["last_margin"] is not None and c["last_margin"] <= -blow
    f[f"off a blowout win ({blow}+)"] = lambda c: c["last_margin"] is not None and c["last_margin"] >= blow
    f["off an upset win as a dog"] = lambda c: c["last_upset"]
    f["3rd+ straight road game"] = lambda c: c["road_run"] >= 2
    f["3rd+ straight home game"] = lambda c: c["home_run"] >= 2
    f["revenge (lost the last meeting)"] = lambda c: c["revenge"]
    f["strong home team at home (home win% .25+ over road)"] = (
        lambda c: c["side"] == "home" and c["home_pct"] is not None and c["road_pct"] is not None
        and c["home_pct"] - c["road_pct"] >= 0.25)
    f["bad road team on the road (won 30% or less away)"] = (
        lambda c: c["side"] == "away" and c["road_pct"] is not None and c["road_n"] >= 5 and c["road_pct"] <= 0.30)
    f["2+ time zones from home"] = lambda c: c["zones"] >= 2
    f["visiting altitude"] = lambda c: c["side"] == "away" and c["elev"] is not None and c["elev"] >= ALTITUDE_M
    f["early season (first 2 games)" if sport in FOOTBALL else "early season (first 5 games)"] = (
        lambda c, n=2 if sport in FOOTBALL else 5: c["played"] < n)
    if sport in FOOTBALL + ("mlb",):
        f["dome team playing outside"] = lambda c: c["dome_team"] and c["indoor"] is False and c["side"] == "away"
        f["dome team outside in the cold (40F or less)"] = (
            lambda c: c["dome_team"] and c["indoor"] is False and c["temp"] is not None and c["temp"] <= 40)
        f["windy game (15+ mph)"] = lambda c: c["indoor"] is False and c["wind"] is not None and c["wind"] >= 15
        f["rain"] = lambda c: c["indoor"] is False and bool(c["rain"])
    for s in SPOTS:
        f[f"this team thrives {s} (its own history)"] = (
            lambda c, s=s: s in c["own"] and c["own"][s][0] >= SPLIT_EDGE)
        f[f"this team struggles {s} (its own history)"] = (
            lambda c, s=s: s in c["own"] and c["own"][s][0] <= -SPLIT_EDGE)
    return f


GAME_WIDE = ("windy game (15+ mph)", "rain")  # both teams are in it: tested once, the market says which side


def active(sport: str, me: dict, op: dict) -> list[tuple[str, str]]:
    """[(direction, factor)] that apply to this side: 'back' = it's in the spot, 'fade' = the opponent is."""
    out = []
    for name, test in factors(sport).items():
        if name in GAME_WIDE:
            if test(me):
                out.append(("back", name))
            continue
        if test is None:  # rest edge
            if me["rest"] is not None and op["rest"] is not None and min(me["rest"], 10) - min(op["rest"], 10) >= 2:
                out.append(("back", name))
            elif me["rest"] is not None and op["rest"] is not None and min(op["rest"], 10) - min(me["rest"], 10) >= 2:
                out.append(("fade", name))
            continue
        if test(me):
            out.append(("back", name))
        if test(op):
            out.append(("fade", name))
    return out


def _markets(g: G, side: str) -> list[tuple[str, float, float]]:
    price = g.ml.get(side)
    out = []
    if price is not None and DOG[0] <= price <= DOG[1]:
        out.append(("moneyline dog", price, g.won(side)))
    elif price is not None and MAX_FAV <= price < 0:
        out.append(("moneyline favorite", price, g.won(side)))
    if g.sport in SPREAD_SPORTS and g.spread is not None:
        m = (g.margin + g.spread) if side == "home" else -(g.margin + g.spread)
        out.append(("spread", g.spread_odds.get(side) or SPREAD_PRICE, 1.0 if m > 0 else 0.0 if m < 0 else 0.5))
    return out


def study(games: list[G], sport: str, n_random: int = 100, seed: int = 25) -> tuple[dict, dict, Tracker]:
    tr = Tracker(sport)
    recs: dict = {}
    control: dict = {}
    rng = random.Random(seed)
    venue = sport in FOOTBALL + ("mlb",)

    def put(book, key, g, price, result):
        arm = "blind" if g.season > TUNE_LAST else "in_sample"
        book.setdefault(key, {"in_sample": Record(), "blind": Record()})[arm].add(price, result)

    for g in games:
        tr._new_season(g.season)
        if g.season >= 2018 and g.stype != "3":
            kw = {"tzo": g.tzo, "elev": g.elev, "indoor": g.indoor if venue else True, "temp": g.temp, "wind": g.wind, "rain": g.rain}
            c = {"home": tr.ctx(g.home, g.away, "home", g.start, g.neutral, **kw),
                 "away": tr.ctx(g.away, g.home, "away", g.start, g.neutral, **kw)}
            picks = [rng.random() < 0.1 for _ in range(n_random)]
            for side in ("home", "away"):
                other = "away" if side == "home" else "home"
                acts = active(sport, c[side], c[other])
                for market, pr, res in _markets(g, side):
                    for k in range(n_random):
                        if picks[k]:
                            put(control, (market, k), g, pr, res)
                    for direction, name in acts:
                        put(recs, (market, f"{direction}: {name}"), g, pr, res)
        tr.add(g)
    return recs, control, tr


def run_sport(lines_dir: Path, sport: str, random_n: int = 100) -> dict:
    folder = Path(lines_dir) / sport
    if not any(folder.glob("*.csv.gz")):
        return {"rows": [], "control": {"spots": 0, "proven": 0, "strong": 0}}
    recs, control, _ = study(load(folder, sport), sport, random_n)
    rows = [{"study": f"{NAME[sport]} game factors", "sport": sport, "market": k[0], "variant": k[1],
             "verdict": verdict(r, k[1]), **{a: r[a].to_dict() for a in r}} for k, r in sorted(recs.items())]
    fakes = [verdict(r) for r in control.values()]
    return {"rows": rows, "control": {"spots": len(fakes), "proven": sum(1 for x in fakes if x),
                                      "strong": fakes.count("STRONG")}}


def run_all(lines_dir: Path, sports=SPORTS, random_n: int = 100) -> dict[str, dict]:
    return {s: run_sport(lines_dir, s, random_n) for s in sports}


def report(res: dict) -> str:
    rows = res.get("rows") or []
    if not rows:
        return "game factors: no lines"
    name = rows[0]["study"]
    lines = [f"{name}: tune 2018-{TUNE_LAST}, blind {TUNE_LAST + 1} on, at the closing price ({len(rows)} tests)"]
    fmt = lambda x: "  -  " if x["flat_roi"] is None else f"{x['flat_roi']:+.1%}"
    for r in rows:
        t, b = r["in_sample"], r["blind"]
        if r["verdict"] or (b["bets"] >= 60 and b["flat_roi"] is not None and abs(b["flat_roi"]) >= 0.08):
            lines.append(f"  {r['market']:<19} {r['variant'][:66]:<66} tune {t['bets']:>5} {fmt(t)}  "
                         f"blind {b['bets']:>5} {fmt(b)} {r['verdict']}")
    c = res["control"]
    passed = sum(1 for r in rows if r["verdict"])
    rate = c["proven"] / c["spots"] if c["spots"] else 0
    lines.append(f"  {passed} of {len(rows)} passed; luck alone would pass about {rate * len(rows):.0f} "
                 f"(control {c['proven']} of {c['spots']} random spots, {c['strong']} STRONG)")
    return "\n".join(lines)


# ------------------------------------------------------------------ today's games

def _names(root: Path, sport: str, games: list[G]) -> dict[str, str]:
    """Display name -> archive team id, joined on ESPN event ids (data/games has names, the archive ids)."""
    by_id = {g.gid: g for g in games}
    out = {}
    for f in sorted((Path(root) / "games" / sport).glob("*.csv")):
        with f.open() as fh:
            for r in csv.DictReader(fh):
                g = by_id.get(r.get("event_id"))
                if g:
                    out[r["home"]] = g.home
                    out[r["away"]] = g.away
    return out


def _recent(root: Path, sport: str, names: dict[str, str], after: datetime) -> list[G]:
    """Finals in data/games newer than the archive (last night's games), as archive-shaped games."""
    out = []
    for f in sorted((Path(root) / "games" / sport).glob("*.csv")):
        with f.open() as fh:
            for r in csv.DictReader(fh):
                try:
                    when = datetime.fromisoformat(r["date"])
                    hs, as_ = float(r["home_score"]), float(r["away_score"])
                except (KeyError, ValueError):
                    continue
                h, a = names.get(r["home"]), names.get(r["away"])
                if when <= after or not h or not a:
                    continue
                pr = lambda v: float(v) if v not in (None, "") else None
                out.append(G(sport, r["event_id"], 0, when, h, a, hs, as_, r.get("neutral") == "1",
                             {"home": pr(r.get("close_home_ml")), "away": pr(r.get("close_away_ml"))}, {},
                             pr(r.get("close_home_spread")), {}, None, {}, {}, None, None, None, None, None, False,
                             {"home": "", "away": ""}, "2", {}, None, {}, {}))
    return sorted(out, key=lambda g: g.start)


def load_results(path: Path) -> dict:
    """{sport: {(market, variant): row}} from data/factor_study.json."""
    import json

    try:
        raw = json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return {}
    return {s: {(r["market"], r["variant"]): r for r in res.get("rows", [])} for s, res in raw.items()}


def _tag(row: dict | None) -> tuple[str, str]:
    """(verdict word, what the blind test found) for one factor."""
    if not row:
        return "untested", "not enough games to test yet"
    b = row["blind"]
    if b["bets"] == 0 or b["flat_roi"] is None:
        return "untested", "no blind games yet"
    rec = f"{b['bets']} blind bets, {b['flat_roi']:+.1%}"
    if row["verdict"] == "STRONG":
        return "edge", f"PROVEN money spot: {rec}"
    if row["verdict"] == "PROVEN":
        return "lean", f"passed the blind test (not strong enough for units yet): {rec}"
    return "none", f"no edge: {rec}"


def game_study(sport: str, matchups: list[dict], root="data", lines_dir=None, results: dict | None = None,
               now: datetime | None = None) -> dict:
    """For each of today's games: every factor each team is in and what the study found. Keyed by
    "away @ home". Rows: {team, side, factor, direction, markets: [{market, tag, text}]}."""
    root = Path(root)
    lines_dir = Path(lines_dir) if lines_dir else root / "lines" / "espn"
    folder = lines_dir / sport
    if sport not in SPORTS or not any(folder.glob("*.csv.gz")) or not matchups:
        return {}
    games = load(folder, sport)
    names = _names(root, sport, games)
    newer = _recent(root, sport, names, games[-1].start if games else datetime.min.replace(tzinfo=timezone.utc))
    tr = Tracker(sport)
    for g in games + newer:
        tr._new_season(g.season or tr.season)
        tr.add(g)
    res = (results if results is not None else load_results(root / "factor_study.json")).get(sport, {})
    out = {}
    for m in matchups:
        h, a = names.get(m.get("home")), names.get(m.get("away"))
        if not h or not a:
            continue
        try:
            start = datetime.fromisoformat(str(m.get("start")).replace("Z", "+00:00"))
        except ValueError:
            continue
        neutral = bool(m.get("neutral"))
        c = {"home": tr.ctx(h, a, "home", start, neutral, home_team=h), "away": tr.ctx(a, h, "away", start, neutral, home_team=h)}
        rows = []
        for side, team in (("home", m["home"]), ("away", m["away"])):
            other = "away" if side == "home" else "home"
            for direction, name in active(sport, c[side], c[other]):
                if direction != "back":
                    continue  # each factor once, on the team that's in it
                mk = []
                for market in ("spread", "moneyline favorite", "moneyline dog"):
                    if market == "spread" and sport not in SPREAD_SPORTS:
                        continue
                    for d in ("back", "fade"):
                        tag, text = _tag(res.get((market, f"{d}: {name}")))
                        mk.append({"market": market, "bet": d, "tag": tag, "text": text})
                rows.append({"team": team, "side": side, "factor": name, "markets": mk,
                             "best": max((x["tag"] for x in mk), key=["untested", "none", "lean", "edge"].index)})
        out[f"{m['away']} @ {m['home']}"] = rows
    return out


def notes(rows: list[dict]) -> list[str]:
    """Plain lines for a matchup: proven spots first, then everything else that applies."""
    word = {"spread": "ATS", "moneyline favorite": "as a favorite", "moneyline dog": "as a dog"}
    lines = []
    for r in sorted(rows, key=lambda r: ["edge", "lean", "none", "untested"].index(r["best"])):
        hits = [x for x in r["markets"] if x["tag"] in ("edge", "lean")]
        if hits:
            bits = "; ".join(f"{'back' if x['bet'] == 'back' else 'fade'} them {word[x['market']]} — {x['text']}" for x in hits)
            lines.append(f"🧪 {r['team']} {r['factor']}: {bits}")
        else:
            lines.append(f"🧪 {r['team']} {r['factor']}: tested, no edge either way")
    return lines


__all__ = ["SPORTS", "Tracker", "active", "factors", "game_study", "notes", "report", "run_all", "run_sport"]
