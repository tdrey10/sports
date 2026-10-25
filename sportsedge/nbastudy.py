"""NBA "who's due" study (owner, Oct 25: "what team is due? who's shitting the bed?").

Each team's luck and slump, from earlier games this season only (10+ games played):
- record luck: win % minus the win % its point differential says (Pythagorean, exponent 14);
- close-game luck: net wins in games decided by 5 or fewer points (or overtime);
- slump: last-5 average margin minus its season average (hot when positive).
Each rule bets the "due" team (unlucky / slumping) or fades the "lucky" / hot one, on the
moneyline (dogs +100..+300 or favorites to -170) and against the spread, at the closing price.
Seasons through 2021 tune, 2022 on are blind; the luck control runs the same bars on random
tenths of all games. Only a STRONG variant (blind and tuning both beat zero by a standard error)
is worth money. Data: data/lines/espn/nba/<season>.csv.gz.
"""

from __future__ import annotations

import random
from collections import defaultdict, deque
from pathlib import Path

from .linestudies import DOG, MAX_FAV, TUNE_LAST, G, load, verdict
from .nflhist import Record

STUDY = "NBA due"
MIN_GAMES = 10
PYTH_EXP = 14.0
CLOSE = 5
SPREAD_PRICE = -110.0


class Team:
    def __init__(self) -> None:
        self.w = self.l = 0
        self.pf = self.pa = 0.0
        self.close = 0  # net close-game wins
        self.last5: deque = deque(maxlen=5)
        self.margins: list[float] = []

    @property
    def games(self) -> int:
        return self.w + self.l

    def luck(self) -> float:
        pyth = self.pf ** PYTH_EXP / (self.pf ** PYTH_EXP + self.pa ** PYTH_EXP) if self.pf + self.pa else 0.5
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
        if abs(m) <= CLOSE or ot:
            self.close += 1 if m > 0 else -1
        self.last5.append(m)
        self.margins.append(m)


def features(games: list[G]) -> list[dict | None]:
    """Per game, each side's luck / close / slump before tip-off (None until both played 10)."""
    teams: dict[str, Team] = defaultdict(Team)
    season, out = None, []
    for g in games:
        if g.season != season:
            season = g.season
            teams.clear()
        h, a = teams[g.home], teams[g.away]
        if h.games >= MIN_GAMES and a.games >= MIN_GAMES:
            out.append({s: {"luck": t.luck(), "close": t.close, "slump": t.slump()}
                        for s, t in (("home", h), ("away", a))})
        else:
            out.append(None)
        ot = False  # the archive has no OT flag; a 5-point game covers most of them
        h.add(g.hs, g.as_, ot)
        a.add(g.as_, g.hs, ot)
    return out


RULES = {  # name -> (test on this side's numbers, test on the opponent's)
    "due: record 6+ pts under its point differential": lambda me, op: me["luck"] <= -0.06,
    "fade: record 6+ pts over its point differential": lambda me, op: op["luck"] >= 0.06,
    "due: 3+ net close-game losses": lambda me, op: me["close"] <= -3,
    "fade: 3+ net close-game wins": lambda me, op: op["close"] >= 3,
    "due: slumping (last 5 games 8+ pts under its season)": lambda me, op: me["slump"] <= -8,
    "fade: slumping team (last 5 games 8+ pts under its season)": lambda me, op: op["slump"] <= -8,
    "ride: hot (last 5 games 8+ pts over its season)": lambda me, op: me["slump"] >= 8,
    "fade: hot team (last 5 games 8+ pts over its season)": lambda me, op: op["slump"] >= 8,
    "due vs lucky: unlucky team vs a lucky one (6+ pts each way)": lambda me, op: me["luck"] <= -0.06 and op["luck"] >= 0.06,
}


def _cover(g: G, side: str) -> float | None:
    if g.spread is None:
        return None
    m = (g.margin + g.spread) if side == "home" else -(g.margin + g.spread)
    return 1.0 if m > 0 else 0.0 if m < 0 else 0.5


def study(games: list[G], recs: dict, control: dict, n_random: int = 100, seed: int = 25) -> None:
    feats = features(games)
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
            cover = _cover(g, side)
            if cover is not None:
                markets.append(("spread", g.spread_odds.get(side) or SPREAD_PRICE, cover))
            for market, pr, res in markets:
                for k in range(n_random):
                    if chosen[k][i]:
                        put(control, (market, k), g, pr, res)
                for name, rule in RULES.items():
                    if rule(f[side], f[other]):
                        put(recs, (market, name), g, pr, res)


def run(lines_dir: Path, random_n: int = 100) -> dict:
    recs: dict = {}
    control: dict = {}
    folder = Path(lines_dir) / "nba"
    if any(folder.glob("*.csv.gz")):
        study(load(folder, "nba"), recs, control, random_n)
    rows = [{"study": STUDY, "sport": "nba", "market": k[0], "variant": k[1], "verdict": verdict(r, k[1]),
             **{a: r[a].to_dict() for a in r}} for k, r in sorted(recs.items())]
    fakes = [verdict(r) for r in control.values()]
    return {"rows": rows, "control": {"spots": len(fakes), "proven": sum(1 for x in fakes if x),
                                      "strong": fakes.count("STRONG")}}


def report(res: dict) -> str:
    lines = [f"{STUDY}: tune 2018-{TUNE_LAST}, blind {TUNE_LAST + 1} on, at the closing price"]
    for r in res["rows"]:
        t, b = r["in_sample"], r["blind"]
        fmt = lambda x: "  -  " if x["flat_roi"] is None else f"{x['flat_roi']:+.1%}"  # noqa: E731
        lines.append(f"  {r['market']:<19} {r['variant']:<62} tune {t['bets']:>4} {fmt(t)}  "
                     f"blind {b['bets']:>4} {fmt(b)} {r['verdict']}")
    c = res["control"]
    lines.append(f"  luck control: {c['proven']} of {c['spots']} random spots pass, {c['strong']} STRONG")
    return "\n".join(lines)


__all__ = ["run", "report", "features", "RULES"]
