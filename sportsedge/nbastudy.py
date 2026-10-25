"""The "who's due" study (owner, Oct 25: "what team is due? who's shitting the bed?"), NBA first,
then NFL, college football, college hoops and MLB (owner, Oct 25: "do we have this for nfl,
college football, college basketball, mlb as well?").

Each team's luck and slump, from earlier games this season only (each sport's minimum played):
- record luck: win % minus the win % its point differential says (Pythagorean, the sport's exponent);
- close-game luck: net wins in close games (NBA/college hoops 5 pts, football 7, MLB one run);
- slump: average margin over its last few games minus its season average (hot when positive).
Each rule bets the "due" team (unlucky / slumping) or fades the "lucky" / hot one, on the
moneyline (dogs +100..+300 or favorites to -170) and, outside MLB, against the spread, at the
closing price. Seasons through 2021 tune, 2022 on are blind; the luck control runs the same bars
on random tenths of all games. Only a STRONG variant (blind and tuning both beat zero by a standard
error) is worth money. Data: data/lines/espn/<sport>/<season>.csv.gz.
"""

from __future__ import annotations

import random
from collections import defaultdict, deque
from dataclasses import dataclass
from pathlib import Path

from .linestudies import DOG, MAX_FAV, TUNE_LAST, G, load, verdict
from .nflhist import Record

STUDY = "NBA due"
SPREAD_PRICE = -110.0


@dataclass(frozen=True)
class Params:
    name: str
    min_games: int  # luck reads start here
    pyth: float  # Pythagorean exponent
    close: float  # a close game is decided by this much or less
    recent: int  # games in the "last few" window
    slump: float  # last-few margin this far off its season = slumping / hot
    luck: float  # record this far off its point differential = due / lucky
    close_n: int  # net close wins (or losses) this many = lucky (or due)
    spread: bool = True  # bet against the spread too


PARAMS = {
    "nba": Params("NBA", 10, 14.0, 5, 5, 8, 0.06, 3),
    "ncaab": Params("College hoops", 8, 11.5, 5, 5, 10, 0.08, 3),
    "nfl": Params("NFL", 4, 2.37, 7, 3, 10, 0.15, 2),
    "ncaaf": Params("College football", 4, 2.37, 7, 3, 14, 0.15, 2),
    "mlb": Params("MLB", 20, 1.83, 1, 10, 1.5, 0.06, 4, spread=False),
    "nhl": Params("NHL", 10, 2.0, 1, 10, 1.0, 0.06, 3, spread=False),
}
DUE_SPORTS = tuple(PARAMS)

# The NBA numbers, kept for the player report (nbaplayers.py).
MIN_GAMES, PYTH_EXP, CLOSE = PARAMS["nba"].min_games, PARAMS["nba"].pyth, PARAMS["nba"].close


class Team:
    def __init__(self, p: Params = PARAMS["nba"]) -> None:
        self.p = p
        self.w = self.l = 0
        self.pf = self.pa = 0.0
        self.close = 0  # net close-game wins
        self.last5: deque = deque(maxlen=p.recent)  # the last few margins (5 in the NBA)
        self.margins: list[float] = []

    @property
    def games(self) -> int:
        return self.w + self.l

    def luck(self) -> float:
        e = self.p.pyth
        pyth = self.pf ** e / (self.pf ** e + self.pa ** e) if self.pf + self.pa else 0.5
        return self.w / self.games - pyth

    def slump(self) -> float:
        season = sum(self.margins) / len(self.margins)
        return sum(self.last5) / len(self.last5) - season

    def add(self, pf: float, pa: float, ot: bool) -> None:
        m = pf - pa
        self.w += m > 0
        self.l += m < 0
        self.pf += pf
        self.pa += pa
        if abs(m) <= self.p.close or ot:
            self.close += 1 if m > 0 else -1 if m < 0 else 0
        self.last5.append(m)
        self.margins.append(m)


def features(games: list[G], p: Params = PARAMS["nba"]) -> list[dict | None]:
    """Per game, each side's luck / close / slump before it starts (None until both played enough)."""
    teams: dict[str, Team] = defaultdict(lambda: Team(p))
    season, out = None, []
    for g in games:
        if g.season != season:
            season = g.season
            teams.clear()
        h, a = teams[g.home], teams[g.away]
        if h.games >= p.min_games and a.games >= p.min_games:
            out.append({s: {"luck": t.luck(), "close": t.close, "slump": t.slump()}
                        for s, t in (("home", h), ("away", a))})
        else:
            out.append(None)
        h.add(g.hs, g.as_, False)  # the archive has no OT flag; a close margin covers most of them
        a.add(g.as_, g.hs, False)
    return out


def rules(p: Params) -> dict:
    """name -> test on (this side's numbers, the opponent's)."""
    lk, cn, sl, rc = p.luck, p.close_n, p.slump, p.recent
    unit = {"MLB": "runs", "NHL": "goals"}.get(p.name, "pts")
    close = {"MLB": "one-run", "NHL": "one-goal"}.get(p.name, "close")
    return {
        f"due: record {lk * 100:.0f}+ pts under its point differential": lambda me, op: me["luck"] <= -lk,
        f"fade: record {lk * 100:.0f}+ pts over its point differential": lambda me, op: op["luck"] >= lk,
        f"due: {cn}+ net {close}-game losses": lambda me, op: me["close"] <= -cn,
        f"fade: {cn}+ net {close}-game wins": lambda me, op: op["close"] >= cn,
        f"due: slumping (last {rc} games {sl:g}+ {unit} under its season)": lambda me, op: me["slump"] <= -sl,
        f"fade: slumping team (last {rc} games {sl:g}+ {unit} under its season)": lambda me, op: op["slump"] <= -sl,
        f"ride: hot (last {rc} games {sl:g}+ {unit} over its season)": lambda me, op: me["slump"] >= sl,
        f"fade: hot team (last {rc} games {sl:g}+ {unit} over its season)": lambda me, op: op["slump"] >= sl,
        f"due vs lucky: unlucky team vs a lucky one ({lk * 100:.0f}+ pts each way)":
            lambda me, op: me["luck"] <= -lk and op["luck"] >= lk,
    }


RULES = rules(PARAMS["nba"])


def _cover(g: G, side: str) -> float | None:
    if g.spread is None:
        return None
    m = (g.margin + g.spread) if side == "home" else -(g.margin + g.spread)
    return 1.0 if m > 0 else 0.0 if m < 0 else 0.5


def study(games: list[G], recs: dict, control: dict, n_random: int = 100, seed: int = 25,
          p: Params = PARAMS["nba"]) -> None:
    feats = features(games, p)
    rs = rules(p)
    rng = random.Random(seed)
    chosen = [[rng.random() < 0.1 for _ in games] for _ in range(n_random)]

    def put(book, key, g, price, result):
        arm = "blind" if g.season > TUNE_LAST else "in_sample"
        book.setdefault(key, {"in_sample": Record(), "blind": Record()})[arm].add(price, result)

    for i, (g, f) in enumerate(zip(games, feats)):
        if f is None or g.season < 2018:  # closing lines start 2018
            continue
        for side in ("home", "away"):
            other = "away" if side == "home" else "home"
            price = g.ml.get(side)
            markets = []
            if price is not None and DOG[0] <= price <= DOG[1]:
                markets.append(("moneyline dog", price, g.won(side)))
            elif price is not None and MAX_FAV <= price < 0:
                markets.append(("moneyline favorite", price, g.won(side)))
            cover = _cover(g, side) if p.spread else None
            if cover is not None:
                markets.append(("spread", g.spread_odds.get(side) or SPREAD_PRICE, cover))
            for market, pr, res in markets:
                for k in range(n_random):
                    if chosen[k][i]:
                        put(control, (market, k), g, pr, res)
                for name, rule in rs.items():
                    if rule(f[side], f[other]):
                        put(recs, (market, name), g, pr, res)


def run_sport(lines_dir: Path, sport: str = "nba", random_n: int = 100) -> dict:
    p = PARAMS[sport]
    recs: dict = {}
    control: dict = {}
    folder = Path(lines_dir) / sport
    if any(folder.glob("*.csv.gz")):
        study(load(folder, sport), recs, control, random_n, p=p)
    rows = [{"study": f"{p.name} due", "sport": sport, "market": k[0], "variant": k[1], "verdict": verdict(r, k[1]),
             **{a: r[a].to_dict() for a in r}} for k, r in sorted(recs.items())]
    fakes = [verdict(r) for r in control.values()]
    return {"rows": rows, "control": {"spots": len(fakes), "proven": sum(1 for x in fakes if x),
                                      "strong": fakes.count("STRONG")}}


def run(lines_dir: Path, random_n: int = 100) -> dict:
    """The NBA study (kept: the NBA tab reads this shape)."""
    return run_sport(lines_dir, "nba", random_n)


def run_all(lines_dir: Path, sports=DUE_SPORTS, random_n: int = 100) -> dict[str, dict]:
    return {s: run_sport(lines_dir, s, random_n) for s in sports}


def report(res: dict) -> str:
    name = res["rows"][0]["study"] if res.get("rows") else STUDY
    lines = [f"{name}: tune 2018-{TUNE_LAST}, blind {TUNE_LAST + 1} on, at the closing price"]
    for r in res["rows"]:
        t, b = r["in_sample"], r["blind"]
        fmt = lambda x: "  -  " if x["flat_roi"] is None else f"{x['flat_roi']:+.1%}"  # noqa: E731
        lines.append(f"  {r['market']:<19} {r['variant']:<62} tune {t['bets']:>4} {fmt(t)}  "
                     f"blind {b['bets']:>4} {fmt(b)} {r['verdict']}")
    c = res["control"]
    lines.append(f"  luck control: {c['proven']} of {c['spots']} random spots pass, {c['strong']} STRONG")
    return "\n".join(lines)


__all__ = ["run", "run_sport", "run_all", "report", "features", "rules", "RULES", "PARAMS", "DUE_SPORTS", "Team"]
