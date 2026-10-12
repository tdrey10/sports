"""Tennis blind test on 2012+ closing odds (ATP and WTA, every tour-level match).

Data: the tennis-data.co.uk results the public archive D503therapper/autonomous-crypto-engine
keeps in data/sports/tennis/hist_odds.csv.gz: winner, loser, surface, round, best-of, and the
closing match odds at Pinnacle (the sharpest book), Bet365, the best price on the market and
the market average. Saved here as data/lines/tennis/hist_odds.csv.gz.

The model: a surface-aware Elo rating for every player (the standard tennis Elo: each
match moves both ratings by K/(matches+5)^0.4 times the surprise; the rating used for a
match blends the player's overall and surface ratings). Settings are chosen on 2013-2019 and
judged blind on 2021-2025 (2020 only updates the ratings: a broken season).

Tests, each tuned on the early seasons and judged on the blind ones, at real prices:
1. The model blended with the Pinnacle price, bet at Pinnacle when the edge is 2%+.
2. Sharp value: Bet365's price against Pinnacle's no-vig price (no model): a real, single
   book beating the sharp price by 2%-15%. Same idea that proved out in college football.
3. The best price on the market against Pinnacle (a caveat: "best" includes stale numbers).
4. Price bands at Pinnacle: are heavy favorites or long shots mispriced on their own?
5. The model's dogs and favorites by how far it disagrees with the market.
A luck control runs the same rules on random tenths of the matches.
"""

from __future__ import annotations

import csv
import gzip
import itertools
import math
import random
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from .nflhist import PROVEN_BETS, Record
from .odds import no_vig

TUNE_FIRST, TUNE_LAST = 2013, 2019
BLIND_FIRST = 2021
MAX_FAVORITE = -170
MAX_UNDERDOG = 300
SURFACES = ("Hard", "Clay", "Grass", "Carpet")


def _f(v) -> float | None:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(x) or x <= 1.0 else x


def to_american(dec: float) -> float:
    return (dec - 1) * 100 if dec >= 2 else -100 / (dec - 1)


@dataclass
class Match:
    tour: str
    season: int
    date: str
    surface: str
    bo: int
    a: str  # players in alphabetical order, so the side never gives away the winner
    b: str
    a_won: bool
    odds: dict = field(default_factory=dict)  # book -> {"a": decimal, "b": decimal}
    tourney: str = ""
    round: str = ""


def load(path: Path) -> list[Match]:
    out = []
    opener = gzip.open if str(path).endswith(".gz") else open
    with opener(path, "rt", newline="") as fh:
        for r in csv.DictReader(fh):
            if (r.get("comment") or "").strip() not in ("Completed", ""):
                continue  # retirements and walkovers: no real result to bet on
            w, l = r["winner"].strip(), r["loser"].strip()
            if not w or not l:
                continue
            a, b = sorted((w, l))
            odds = {}
            for book, (wk, lk) in {"pinnacle": ("psw", "psl"), "b365": ("b365w", "b365l"),
                                   "max": ("maxw", "maxl"), "avg": ("avgw", "avgl")}.items():
                ow, ol = _f(r.get(wk)), _f(r.get(lk))
                if ow and ol:
                    odds[book] = {"a": ow, "b": ol} if a == w else {"a": ol, "b": ow}
            try:
                bo = int(float(r.get("bo") or 3))
            except ValueError:
                bo = 3
            out.append(Match(r["tour"], int(r["season"]), r["date"], (r.get("surface") or "Hard").strip(), bo,
                             a, b, a == w, odds, r.get("tourney", ""), r.get("round", "")))
    return sorted(out, key=lambda m: (m.date, m.tour, m.a, m.b))


# ------------------------------------------------------------------- ratings

@dataclass(frozen=True)
class Params:
    k: float = 250.0
    surface_weight: float = 0.5  # share of the surface rating in the rating used for a match


class Elo:
    def __init__(self, p: Params):
        self.p = p
        self.overall: dict[str, float] = defaultdict(lambda: 1500.0)
        self.surface: dict[tuple, float] = defaultdict(lambda: 1500.0)
        self.played: dict[str, int] = defaultdict(int)
        self.played_s: dict[tuple, int] = defaultdict(int)

    def rating(self, tour: str, player: str, surface: str) -> float:
        w = self.p.surface_weight
        return (1 - w) * self.overall[(tour, player)] + w * self.surface[(tour, player, surface)]

    def prob(self, m: Match) -> float:
        diff = self.rating(m.tour, m.a, m.surface) - self.rating(m.tour, m.b, m.surface)
        return 1 / (1 + 10 ** (-diff / 400))

    def update(self, m: Match) -> None:
        for table, keys, counts in (
            (self.overall, ((m.tour, m.a), (m.tour, m.b)), self.played),
            (self.surface, ((m.tour, m.a, m.surface), (m.tour, m.b, m.surface)), self.played_s),
        ):
            ka, kb = keys
            e = 1 / (1 + 10 ** ((table[kb] - table[ka]) / 400))
            s = 1.0 if m.a_won else 0.0
            table[ka] += self.p.k / (counts[ka] + 5) ** 0.4 * (s - e)
            table[kb] -= self.p.k / (counts[kb] + 5) ** 0.4 * (s - e)
            counts[ka] += 1
            counts[kb] += 1

    def known(self, m: Match, min_matches: int = 10) -> bool:
        return self.played[(m.tour, m.a)] >= min_matches and self.played[(m.tour, m.b)] >= min_matches


def replay(matches: list[Match], p: Params) -> list[tuple[Match, float, bool]]:
    """(match, model chance the first player wins, both players have a record) for every match,
    each predicted from the matches before it only."""
    elo = Elo(p)
    out = []
    for m in matches:
        out.append((m, elo.prob(m), elo.known(m)))
        elo.update(m)
    return out


GRID = {"k": (150.0, 250.0, 350.0), "surface_weight": (0.0, 0.3, 0.6)}


def _ll(p: float, y: bool) -> float:
    p = min(max(p, 1e-6), 1 - 1e-6)
    return -math.log(p if y else 1 - p)


def tune(matches: list[Match], grid: dict = GRID) -> Params:
    best, best_ll = Params(), math.inf
    for combo in itertools.product(*grid.values()):
        p = Params(**dict(zip(grid, combo)))
        tot = n = 0
        for m, pa, known in replay([m for m in matches if m.season <= TUNE_LAST], p):
            if known and m.season >= TUNE_FIRST:
                tot += _ll(pa, m.a_won)
                n += 1
        if n and tot / n < best_ll:
            best, best_ll = p, tot / n
    return best


# --------------------------------------------------------------------- bets

def _in_range(dec: float) -> bool:
    am = to_american(dec)
    return MAX_FAVORITE <= am <= MAX_UNDERDOG


def _period(m: Match) -> str | None:
    if TUNE_FIRST <= m.season <= TUNE_LAST:
        return "in_sample"
    if m.season >= BLIND_FIRST:
        return "blind"
    return None


def learn_trust(rows) -> float:
    best, best_ll = 0.0, math.inf
    for i in range(21):
        t = i / 20
        ll = sum(_ll(t * pa + (1 - t) * fair, m.a_won) for m, pa, fair in rows)
        if ll < best_ll:
            best, best_ll = t, ll
    return best


def run(matches: list[Match], grid: dict = GRID, random_n: int = 200, seed: int = 11) -> dict:
    p = tune(matches, grid)
    preds = replay(matches, p)
    recs: dict[str, dict[str, Record]] = defaultdict(lambda: {"in_sample": Record(), "blind": Record()})

    def add(name, period, dec, won):
        recs[name][period].add(to_american(dec), 1.0 if won else 0.0)

    # Calibration of the model and the market, blind.
    tune_rows = []
    for m, pa, known in preds:
        pin = m.odds.get("pinnacle")
        if known and pin and _period(m) == "in_sample":
            fair = no_vig({"a": to_american(pin["a"]), "b": to_american(pin["b"])})["a"]
            tune_rows.append((m, pa, fair))
    trust = learn_trust(tune_rows)

    ll = {"model": [0.0, 0], "market": [0.0, 0], "blend": [0.0, 0]}
    rng = random.Random(seed)
    picks = [[rng.random() < 0.1 for _ in preds] for _ in range(random_n)]
    control = [{"in_sample": Record(), "blind": Record()} for _ in range(random_n)]
    for i, (m, pa, known) in enumerate(preds):
        period = _period(m)
        pin = m.odds.get("pinnacle")
        if period is None or not pin:
            continue
        fair = no_vig({"a": to_american(pin["a"]), "b": to_american(pin["b"])})
        won = {"a": m.a_won, "b": not m.a_won}
        if known:
            read_a = trust * pa + (1 - trust) * fair["a"]
            read = {"a": read_a, "b": 1 - read_a}
            model = {"a": pa, "b": 1 - pa}
            if period == "blind":
                for name, q in (("model", pa), ("market", fair["a"]), ("blend", read_a)):
                    ll[name][0] += _ll(q, m.a_won)
                    ll[name][1] += 1
            # 1. Model + market blend at Pinnacle.
            best = max(("a", "b"), key=lambda s: read[s] * pin[s])
            if read[best] * pin[best] - 1 >= 0.02 and _in_range(pin[best]):
                add("01 model blend at Pinnacle (2%+ edge)", period, pin[best], won[best])
            # 5. The model's disagreements, dogs vs favorites.
            for s in ("a", "b"):
                gap = model[s] - fair[s]
                if gap >= 0.06 and _in_range(pin[s]):
                    kind = "dog" if pin[s] >= 2.0 else "favorite"
                    add(f"05 model 6+ pts above market: {kind}", period, pin[s], won[s])
        # 2. Bet365 vs Pinnacle no-vig.
        b365 = m.odds.get("b365")
        if b365:
            cand = [(fair[s] * b365[s] - 1, s) for s in ("a", "b")]
            ev, s = max(cand)
            if 0.02 <= ev <= 0.15 and _in_range(b365[s]):
                add("02 Bet365 beats Pinnacle no-vig by 2-15%", period, b365[s], won[s])
        # 3. Best price vs Pinnacle no-vig.
        mx = m.odds.get("max")
        if mx:
            ev, s = max((fair[s] * mx[s] - 1, s) for s in ("a", "b"))
            if 0.02 <= ev <= 0.15 and _in_range(mx[s]):
                add("03 best price beats Pinnacle no-vig by 2-15% (caveat: stale prices)", period, mx[s], won[s])
        # 4. Price bands at Pinnacle.
        for s in ("a", "b"):
            am = to_american(pin[s])
            for lo, hi, name in ((-170, -131, "fav -131..-170"), (-130, -100, "fav -100..-130"),
                                 (100, 150, "dog +100..+150"), (151, 220, "dog +151..+220"), (221, 300, "dog +221..+300")):
                if lo <= am <= hi:
                    add(f"04 Pinnacle band: {name}", period, pin[s], won[s])
            for k in range(random_n):
                if picks[k][i] and (k % 2 == 0) == (pin[s] >= 2.0) and _in_range(pin[s]):
                    control[k][period].add(am, 1.0 if won[s] else 0.0)

    def verdict(rec) -> str:
        b, t = rec["blind"], rec["in_sample"]
        if not (b.proven and (t.flat_roi or 0) > 0):
            return ""
        return "STRONG" if t.bets >= PROVEN_BETS and t.flat_roi - (t.se or 0) > 0 else "PROVEN"

    rows = [{"study": k, "verdict": verdict(v), **{p_: r.to_dict() for p_, r in v.items()}} for k, v in sorted(recs.items())]
    fakes = [verdict(c) for c in control]
    return {
        "params": p.__dict__, "trust": trust,
        "log_loss_blind": {k: v[0] / v[1] for k, v in ll.items() if v[1]},
        "matches": len(matches), "rows": rows,
        "control": {"spots": len(fakes), "passed": sum(1 for f in fakes if f), "strong": fakes.count("STRONG")},
        "tune_seasons": f"{TUNE_FIRST}-{TUNE_LAST}", "blind_seasons": f"{BLIND_FIRST}+",
    }


def report(res: dict) -> str:
    def rec(d):
        roi = f"{d['flat_roi']:+.1%} ± {d['se']:.1%}" if d["flat_roi"] is not None and d["se"] else "-"
        return f"{d['bets']:>5} {d['won']}-{d['lost']}  ROI {roi}"

    p = res["params"]
    out = [f"Tennis Elo (chosen on {res['tune_seasons']}): K {p['k']}, surface weight {p['surface_weight']}; "
           f"trust in model {res['trust']:.2f}",
           "Blind log loss: " + ", ".join(f"{k} {v:.4f}" for k, v in res["log_loss_blind"].items()), ""]
    for r in res["rows"]:
        out.append(f"{r['study']}")
        out.append(f"    tuning {rec(r['in_sample'])}")
        out.append(f"    BLIND  {rec(r['blind'])}  {r['verdict']}")
    c = res["control"]
    out.append(f"\nLuck control: {c['passed']} of {c['spots']} random spots passed, {c['strong']} STRONG.")
    return "\n".join(out)
