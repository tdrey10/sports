"""NFL blind test on twenty seasons of closing lines (nflverse, free).

nflverse publishes every NFL game since 1999 with the closing moneyline, spread and
total since 2006, plus each team's rest days, weather and starting quarterback. That
is thousands of games with lines, enough to test the blueprint properly:

1. Ratings from results: margin-based (points), home field, rest, starting-QB change,
   regression to the mean between seasons. Every prediction uses only games before it.
2. Calibration: rating gap -> win chance through a logistic fit, checked bin by bin.
3. Market blend: read = trust * model + (1 - trust) * market no-vig; trust learned.
4. Edge = read - market. Bet when the edge is 2%+ and the price is -170 or better,
   quarter-Kelly units (1 unit = 1% of bankroll, 0.5 minimum).

Every setting (rating speed, home field, rest, QB, carryover, calibration, trust) is
chosen on the early seasons only. The later seasons stay blind: they are scored once,
at the closing price, and that is the result that counts.

The spots from the early-bet list are tested the same way, each with its own record.
These are closing lines, so they show whether a spot beat the close, not the open.
"""

from __future__ import annotations

import csv
import gzip
import itertools
import math
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import requests

from .odds import american_to_decimal, kelly_fraction, no_vig

URL = "https://raw.githubusercontent.com/nflverse/nfldata/master/data/games.csv"
KEEP = ["game_id", "season", "game_type", "week", "gameday", "weekday", "away_team", "away_score", "home_team",
        "home_score", "location", "away_rest", "home_rest", "away_moneyline", "home_moneyline", "spread_line",
        "away_spread_odds", "home_spread_odds", "total_line", "under_odds", "over_odds", "div_game", "roof",
        "temp", "wind", "away_qb_id", "home_qb_id", "espn"]

FIRST_SEASON = 2006  # closing lines start here
TUNE_LAST = 2017  # settings are chosen on 2006-2017 (2006 itself only warms the ratings up)
WARMUP_LAST = 2006
MIN_EDGE = 0.02
MAX_FAVORITE = -170
MAX_UNDERDOG = 300  # long shots are noise: one +8000 upset would swamp a whole test
DOG_RANGE = (100, 220)
MARGIN_CAP = 24  # a 45-point win teaches no more than a 24-point one
PROVEN_BETS = 60

ZONE = {  # team -> 0 east, 1 central, 2 mountain, 3 pacific
    **dict.fromkeys(["BUF", "MIA", "NE", "NYJ", "BAL", "CIN", "CLE", "PIT", "JAX", "IND", "NYG", "PHI",
                     "WAS", "DET", "ATL", "CAR", "TB"], 0),
    **dict.fromkeys(["CHI", "GB", "MIN", "TEN", "HOU", "KC", "NO", "DAL", "STL"], 1),
    **dict.fromkeys(["DEN", "ARI"], 2),
    **dict.fromkeys(["LAC", "SD", "LA", "LV", "OAK", "SF", "SEA"], 3),
}


# ---------------------------------------------------------------------- data

def fetch(folder: Path, session: requests.Session | None = None) -> int:
    """Download nflverse games and save one CSV per season. Returns games saved."""
    resp = (session or requests).get(URL, timeout=60)
    resp.raise_for_status()
    rows = list(csv.DictReader(resp.text.splitlines()))
    return save(folder, rows)


def save(folder: Path, rows: list[dict]) -> int:
    by_season: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        if int(r["season"]) >= FIRST_SEASON:
            by_season[r["season"]].append({k: r.get(k, "") for k in KEEP})
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    for season, group in by_season.items():
        with open(folder / f"{season}.csv", "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=KEEP)
            w.writeheader()
            w.writerows(group)
    return sum(len(g) for g in by_season.values())


def _f(v: str) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


@dataclass
class Game:
    season: int
    week: int
    day: date
    weekday: str
    home: str
    away: str
    home_score: float | None
    away_score: float | None
    neutral: bool
    home_rest: float
    away_rest: float
    home_ml: float | None
    away_ml: float | None
    spread: float | None  # home team favored by this many (nflverse spread_line)
    home_spread_odds: float | None
    away_spread_odds: float | None
    total: float | None
    over_odds: float | None
    under_odds: float | None
    home_qb: str
    away_qb: str
    wind: float | None = None  # mph at kickoff (nflverse); None indoors or unknown
    outdoors: bool = True
    gid: str = ""  # game id (nflverse game_id, or the ESPN id for college)

    @property
    def done(self) -> bool:
        return self.home_score is not None and self.away_score is not None

    @property
    def margin(self) -> float:
        return self.home_score - self.away_score


def load(folder: Path) -> list[Game]:
    games = []
    files = sorted(Path(folder).glob("*.csv")) + sorted(Path(folder).glob("*.csv.gz"))
    for f in files:
        opener = gzip.open if f.suffix == ".gz" else open
        for r in csv.DictReader(opener(f, "rt", newline="")):
            games.append(Game(
                season=int(r["season"]), week=int(r["week"]), day=date.fromisoformat(r["gameday"]),
                weekday=r["weekday"], home=r["home_team"], away=r["away_team"],
                home_score=_f(r["home_score"]), away_score=_f(r["away_score"]), neutral=r["location"] == "Neutral",
                home_rest=_f(r["home_rest"]) or 7, away_rest=_f(r["away_rest"]) or 7,
                home_ml=_f(r["home_moneyline"]), away_ml=_f(r["away_moneyline"]), spread=_f(r["spread_line"]),
                home_spread_odds=_f(r["home_spread_odds"]), away_spread_odds=_f(r["away_spread_odds"]),
                total=_f(r["total_line"]), over_odds=_f(r["over_odds"]), under_odds=_f(r["under_odds"]),
                home_qb=r["home_qb_id"], away_qb=r["away_qb_id"],
                wind=_f(r["wind"]), outdoors=r["roof"] in ("outdoors", "open"), gid=r.get("game_id", ""),
            ))
    return sorted(games, key=lambda g: (g.day, g.home))


# ------------------------------------------------------------------- ratings

@dataclass(frozen=True)
class Params:
    k: float = 0.08  # share of each game's surprise that moves the ratings
    hfa: float = 1.5  # home field, points
    rest: float = 0.15  # points per day of extra rest (capped at 7 days' difference)
    qb: float = 3.0  # points lost when a team starts a different QB than its regular one
    carry: float = 0.7  # share of last season's rating kept (regression to the mean)


@dataclass
class Pred:
    game: Game
    margin: float  # predicted home margin before the game
    home_qb_new: bool
    away_qb_new: bool
    prev_margin: dict = field(default_factory=dict)  # team -> its last game's margin (this season)
    home_played_prev_week: bool = True
    away_played_prev_week: bool = True


def _regular_qb(history: list[str]) -> str | None:
    """A team's regular starter: the most frequent of its last 8 starters (ties: the most recent)."""
    last = [q for q in history[-8:] if q]
    return max(reversed(last), key=last.count) if last else None


def replay(games: list[Game], p: Params) -> list[Pred]:
    """Predict every game from the games before it, then learn from its result."""
    rating: dict[str, float] = defaultdict(float)
    qbs: dict[str, list[str]] = defaultdict(list)
    last_margin: dict[str, float] = {}
    last_week: dict[str, tuple[int, int]] = {}
    season = None
    out = []
    for g in games:
        if g.season != season:
            season = g.season
            for t in rating:
                rating[t] *= p.carry
            last_margin.clear()
        hq = _regular_qb(qbs[g.home])
        aq = _regular_qb(qbs[g.away])
        home_new = bool(hq and g.home_qb and g.home_qb != hq)
        away_new = bool(aq and g.away_qb and g.away_qb != aq)
        rest = max(min(g.home_rest - g.away_rest, 7), -7)
        pred = (rating[g.home] - rating[g.away] + (0 if g.neutral else p.hfa) + p.rest * rest
                - p.qb * home_new + p.qb * away_new)
        out.append(Pred(g, pred, home_new, away_new,
                        prev_margin={g.home: last_margin.get(g.home), g.away: last_margin.get(g.away)},
                        home_played_prev_week=last_week.get(g.home) == (g.season, g.week - 1),
                        away_played_prev_week=last_week.get(g.away) == (g.season, g.week - 1)))
        if not g.done:
            continue
        actual = max(min(g.margin, MARGIN_CAP), -MARGIN_CAP)
        err = actual - pred
        rating[g.home] += p.k * err
        rating[g.away] -= p.k * err
        for team, qb in ((g.home, g.home_qb), (g.away, g.away_qb)):
            qbs[team].append(qb)
        last_margin[g.home], last_margin[g.away] = g.margin, -g.margin
        last_week[g.home] = last_week[g.away] = (g.season, g.week)
    return out


GRID = {
    "k": (0.05, 0.08, 0.11),
    "hfa": (0.5, 1.5, 2.5),
    "rest": (0.0, 0.15, 0.3),
    "qb": (0.0, 3.0, 6.0),
    "carry": (0.5, 0.7, 0.9),
}


def tune(games: list[Game], last_season: int = TUNE_LAST, grid: dict = GRID) -> Params:
    """Settings with the smallest squared margin error on the tuning seasons only."""
    best, best_err = Params(), math.inf
    for combo in itertools.product(*grid.values()):
        p = Params(**dict(zip(grid, combo)))
        err = n = 0
        for pr in replay([g for g in games if g.season <= last_season], p):
            if pr.game.done and pr.game.season > WARMUP_LAST:
                err += (pr.game.margin - pr.margin) ** 2
                n += 1
        if n and err / n < best_err:
            best, best_err = p, err / n
    return best


# ------------------------------------------------------- calibration + blend

def _sigmoid(x: float) -> float:
    return 1 / (1 + math.exp(-x))


def fit_logistic(xs: list[float], ys: list[int]) -> tuple[float, float]:
    """Win chance = sigmoid(a * predicted margin + b), fit by Newton's method."""
    a, b = 0.1, 0.0
    for _ in range(50):
        ga = gb = haa = hab = hbb = 0.0
        for x, y in zip(xs, ys):
            p = _sigmoid(a * x + b)
            ga += (y - p) * x
            gb += y - p
            w = p * (1 - p)
            haa += w * x * x
            hab += w * x
            hbb += w
        det = haa * hbb - hab * hab
        if det <= 0:
            break
        a += (hbb * ga - hab * gb) / det
        b += (haa * gb - hab * ga) / det
    return a, b


def _ll(p: float, y: int) -> float:
    p = min(max(p, 1e-4), 1 - 1e-4)
    return -(y * math.log(p) + (1 - y) * math.log(1 - p))


def _phi(z: float) -> float:
    return 0.5 * (1 + math.erf(z / math.sqrt(2)))


@dataclass
class Row:
    """One market on one game: the two sides' model chance, market chance, prices, result."""
    pred: Pred
    market: str  # "ml" or "spread"
    model: dict[str, float]
    fair: dict[str, float]
    price: dict[str, float]
    won: dict[str, float]  # side -> 1 win, 0 loss, 0.5 push


def rows(preds: list[Pred], cal: tuple[float, float], sd: float) -> list[Row]:
    out = []
    a, b = cal
    for pr in preds:
        g = pr.game
        if not g.done or g.margin == 0:
            continue
        if g.home_ml and g.away_ml:
            ph = _sigmoid(a * pr.margin + b)
            out.append(Row(pr, "ml", {"home": ph, "away": 1 - ph},
                           no_vig({"home": g.home_ml, "away": g.away_ml}),
                           {"home": g.home_ml, "away": g.away_ml},
                           {"home": float(g.margin > 0), "away": float(g.margin < 0)}))
        if g.spread is not None and g.home_spread_odds and g.away_spread_odds:
            ph = _phi((pr.margin - g.spread) / sd)
            push = g.margin == g.spread
            out.append(Row(pr, "spread", {"home": ph, "away": 1 - ph},
                           no_vig({"home": g.home_spread_odds, "away": g.away_spread_odds}),
                           {"home": g.home_spread_odds, "away": g.away_spread_odds},
                           {"home": 0.5 if push else float(g.margin > g.spread),
                            "away": 0.5 if push else float(g.margin < g.spread)}))
    return out


def learn_trust(rs: list[Row]) -> float:
    """Weight on the model in the blend, by log loss on the tuning games (0 = trust the market)."""
    best, best_ll = 0.0, math.inf
    for i in range(21):
        t = i / 20
        ll = sum(_ll(t * r.model["home"] + (1 - t) * r.fair["home"], int(r.won["home"] == 1))
                 for r in rs if r.won["home"] != 0.5)
        if ll < best_ll:
            best, best_ll = t, ll
    return best


# ------------------------------------------------------------------- betting

def units(read: float, price: float) -> float:
    """Quarter Kelly, 1 unit = 1% of bankroll: 0.5u minimum, 3u maximum."""
    f = 0.25 * kelly_fraction(read, 1 - read, american_to_decimal(price))
    return min(max(round(f * 100 * 2) / 2, 0.5), 3.0)


def profit(price: float, result: float) -> float:
    """Per unit staked: win pays the price, push returns the stake."""
    return american_to_decimal(price) - 1 if result == 1 else 0.0 if result == 0.5 else -1.0


@dataclass
class Record:
    bets: int = 0
    won: int = 0
    lost: int = 0
    push: int = 0
    staked: float = 0.0
    units: float = 0.0
    flat: list[float] = field(default_factory=list)

    def add(self, price: float, result: float, size: float = 1.0) -> None:
        pr = profit(price, result)
        self.bets += 1
        self.won += result == 1
        self.lost += result == 0
        self.push += result == 0.5
        self.staked += size
        self.units += size * pr
        self.flat.append(pr)

    @property
    def roi(self) -> float | None:
        return self.units / self.staked if self.staked else None

    @property
    def flat_roi(self) -> float | None:
        return sum(self.flat) / len(self.flat) if self.flat else None

    @property
    def se(self) -> float | None:
        n = len(self.flat)
        if n < 2:
            return None
        m = sum(self.flat) / n
        return math.sqrt(sum((x - m) ** 2 for x in self.flat) / (n - 1) / n)

    @property
    def proven(self) -> bool:
        return self.bets >= PROVEN_BETS and self.flat_roi is not None and self.flat_roi - (self.se or 0) > 0

    def to_dict(self) -> dict:
        return {"bets": self.bets, "won": self.won, "lost": self.lost, "push": self.push,
                "units": round(self.units, 2), "roi": self.roi, "flat_roi": self.flat_roi, "se": self.se,
                "proven": self.proven}


def bet(rs: list[Row], trust: float, min_edge: float = MIN_EDGE) -> Record:
    """Best side per game and market with edge >= min_edge at -170 or better."""
    rec = Record()
    for r in rs:
        best = None
        for side in ("home", "away"):
            read = trust * r.model[side] + (1 - trust) * r.fair[side]
            edge = read - r.fair[side]
            if MAX_FAVORITE <= r.price[side] <= MAX_UNDERDOG and edge >= min_edge and (best is None or edge > best[0]):
                best = (edge, side, read)
        if best:
            _, side, read = best
            rec.add(r.price[side], r.won[side], units(read, r.price[side]))
    return rec


# --------------------------------------------------------------------- spots

def spots(p: Pred) -> list[tuple[str, str]]:
    """(spot, side) for each early-bet spot this game falls in."""
    g = p.game
    out = []
    if not p.home_played_prev_week and p.away_played_prev_week and g.week > 2:
        out.append(("dog off a bye vs a team that played", "home"))
    if not p.away_played_prev_week and p.home_played_prev_week and g.week > 2:
        out.append(("dog off a bye vs a team that played", "away"))
    if g.weekday == "Monday":
        out += [("Monday night dog", "home"), ("Monday night dog", "away")]
    if ZONE.get(g.away) == 0 and ZONE.get(g.home) == 3 and not g.neutral:
        out.append(("East Coast team flying West", "away"))
    for side, team in (("home", g.home), ("away", g.away)):
        m = p.prev_margin.get(team)
        if m is not None and m >= 21:
            out.append(("blew someone out last week (21+)", side))
    return out


def spot_records(preds: list[Pred], blind_from: int) -> dict[str, dict[str, Record]]:
    """Each spot's record betting the dog moneyline at +100 to +220, tuning vs blind seasons."""
    out: dict[str, dict[str, Record]] = defaultdict(lambda: {"in_sample": Record(), "blind": Record()})
    for p in preds:
        g = p.game
        if not g.done or g.margin == 0 or not (g.home_ml and g.away_ml) or g.season <= WARMUP_LAST:
            continue
        price = {"home": g.home_ml, "away": g.away_ml}
        won = {"home": float(g.margin > 0), "away": float(g.margin < 0)}
        for spot, side in spots(p):
            if DOG_RANGE[0] <= price[side] <= DOG_RANGE[1]:
                out[spot]["blind" if g.season >= blind_from else "in_sample"].add(price[side], won[side])
    return dict(out)


WIND_MPH = 15


def wind_unders(games: list[Game], blind_from: int) -> dict[str, Record]:
    """Bet the under in every outdoor game with wind of WIND_MPH+, at the closing total and price.

    nflverse records the wind at kickoff; a bet is placed on the forecast, so a live
    version must use the forecast (weather.py) and will be somewhat weaker than this."""
    out = {"in_sample": Record(), "blind": Record()}
    for g in games:
        if (not g.done or g.season <= WARMUP_LAST or not g.outdoors or g.wind is None or g.wind < WIND_MPH
                or g.total is None or not g.under_odds):
            continue
        pts = g.home_score + g.away_score
        result = 1.0 if pts < g.total else 0.5 if pts == g.total else 0.0
        out["blind" if g.season >= blind_from else "in_sample"].add(g.under_odds, result)
    return out


WIND_DECIMAL = 1.9  # about -110: turns an ROI gap into win-chance points


def wind_under_weight(res: dict | None) -> float:
    """Win-chance bump the daily board may add to a windy under, from the 20-season blind test.

    Zero unless the blind seasons are proven (60+ bets, ROI minus its standard error above 0)
    AND the tuning seasons also profited. Otherwise one standard error is shaved off the blind
    ROI and the rest is turned into win-chance points, so the bump is the part luck can't explain."""
    w = (res or {}).get("wind_unders") or {}
    blind, tuned = w.get("blind") or {}, w.get("in_sample") or {}
    if not (blind.get("proven") and (tuned.get("roi") or 0) > 0 and blind.get("bets", 0) >= PROVEN_BETS):
        return 0.0
    return max(0.0, (blind["flat_roi"] - (blind.get("se") or 0)) / WIND_DECIMAL)


# ------------------------------------------------------------------ teasers

TEASE = 6.0  # a 6-point NFL teaser
TEASER_BREAK_EVEN = {2: 0.7385, 3: 0.7272}  # leg cover rate a 2-team at -120 / 3-team at +160 needs
TEASER_LOW_TOTAL = 49.0
TEASER_LEGS = {  # leg kind -> (lowest, highest) closing line from the bettor's side
    "wong_dog": (1.5, 2.5),  # a small dog teased up through 3 and 7
    "wong_fav": (-8.5, -7.5),  # a favorite teased down through 7 and 3
}


def teaser_kind(line: float, total: float | None) -> list[str]:
    out = []
    for kind, (lo, hi) in TEASER_LEGS.items():
        if lo <= line <= hi:
            out.append(kind)
            if total is not None and total <= TEASER_LOW_TOTAL:
                out.append(kind + "_low_total")
    return out


def teaser_legs(games: list[Game], blind_from: int) -> dict[str, dict]:
    """Each 6-point teaser leg kind's cover rate at the closing spread, tuning vs blind seasons.

    PROVEN when both periods beat what a 2-team teaser at -120 needs (73.85%) and the blind
    rate minus its standard error does too."""
    tally: dict[str, dict[str, list[int]]] = defaultdict(lambda: {"in_sample": [0, 0], "blind": [0, 0]})
    for g in games:
        if not g.done or g.spread is None or g.season <= WARMUP_LAST:
            continue
        period = "blind" if g.season >= blind_from else "in_sample"
        for line, margin in ((-g.spread, g.margin), (g.spread, -g.margin)):
            res = margin + line + TEASE
            if res == 0:
                continue  # a push: the leg drops out of the teaser
            for kind in teaser_kind(line, g.total):
                t = tally[kind][period]
                t[0] += 1
                t[1] += res > 0
    out = {}
    for kind, periods in tally.items():
        d = {}
        for period, (n, w) in periods.items():
            rate = w / n if n else None
            se = math.sqrt(rate * (1 - rate) / n) if n else None
            d[period] = {"legs": n, "won": w, "rate": rate, "se": se}
        b, t = d["blind"], d["in_sample"]
        need = TEASER_BREAK_EVEN[2]
        d["proven"] = bool(b["legs"] >= PROVEN_BETS and t["rate"] and t["rate"] > need
                           and b["rate"] - b["se"] > need)
        out[kind] = d
    return out


# ------------------------------------------------------------------- the test

def calibration(pairs: list[tuple[float, int]], bins: int = 10) -> list[dict]:
    out = []
    for i in range(bins):
        lo, hi = i / bins, (i + 1) / bins
        sel = [(p, y) for p, y in pairs if lo <= p < hi or (i == bins - 1 and p == 1)]
        if sel:
            out.append({"bin": f"{lo:.0%}-{hi:.0%}", "games": len(sel), "said": sum(p for p, _ in sel) / len(sel),
                        "won": sum(y for _, y in sel) / len(sel)})
    return out


@dataclass
class Fit:
    params: Params
    preds: list[Pred]
    cal: tuple[float, float]
    sd: float


def fit(games: list[Game], tune_last: int = TUNE_LAST, grid: dict = GRID) -> Fit:
    """Ratings settings, calibration and margin spread, all from the tuning seasons only."""
    p = tune(games, tune_last, grid)
    preds = replay(games, p)
    tune_preds = [x for x in preds if x.game.done and WARMUP_LAST < x.game.season <= tune_last and x.game.margin]
    cal = fit_logistic([x.margin for x in tune_preds], [int(x.game.margin > 0) for x in tune_preds])
    sd = math.sqrt(sum((x.game.margin - x.margin) ** 2 for x in tune_preds) / len(tune_preds))
    return Fit(p, preds, cal, sd)


def run(games: list[Game], tune_last: int = TUNE_LAST, grid: dict = GRID, fitted: Fit | None = None) -> dict:
    f = fitted or fit(games, tune_last, grid)
    p, preds, cal, sd = f.params, f.preds, f.cal, f.sd
    all_rows = [r for r in rows(preds, cal, sd) if r.pred.game.season > WARMUP_LAST]
    out = {"params": p.__dict__, "calibration_fit": cal, "margin_sd": sd, "tune_seasons": f"2007-{tune_last}",
           "blind_seasons": f"{tune_last + 1}-{max(g.season for g in games)}", "markets": {}}
    for market in ("ml", "spread"):
        tune_rows = [r for r in all_rows if r.market == market and r.pred.game.season <= tune_last]
        blind_rows = [r for r in all_rows if r.market == market and r.pred.game.season > tune_last]
        trust = learn_trust(tune_rows)
        scored = [r for r in blind_rows if r.won["home"] != 0.5]
        out["markets"][market] = {
            "trust": trust,
            "blind_games": len(blind_rows),
            "log_loss_market": sum(_ll(r.fair["home"], int(r.won["home"])) for r in scored) / max(len(scored), 1),
            "log_loss_model": sum(_ll(r.model["home"], int(r.won["home"])) for r in scored) / max(len(scored), 1),
            "in_sample": bet(tune_rows, trust).to_dict(),
            "blind": bet(blind_rows, trust).to_dict(),
            # Model alone (trust 1): what betting the raw ratings against the close would have done.
            "blind_model_only": bet(blind_rows, 1.0).to_dict(),
            "calibration_blind": calibration([(r.model["home"], int(r.won["home"])) for r in scored]),
        }
    out["wind_unders"] = {k: v.to_dict() for k, v in wind_unders(games, tune_last + 1).items()}
    out["teasers"] = teaser_legs(games, tune_last + 1)
    out["spots"] = {k: {s: rec.to_dict() for s, rec in v.items()}
                    for k, v in spot_records(preds, tune_last + 1).items()}
    return out


def held_up(rec: dict) -> str:
    """PROVEN only when the blind seasons pass AND the tuning seasons made money too:
    an idea that lost in one period and won in the other is a coin flip, not an edge."""
    ins = rec.get("in_sample") or {}
    ok = rec["blind"]["proven"] and (ins.get("flat_roi") or 0) > 0
    return "  PROVEN" if ok else ("  (blind only: lost in the tuning seasons)" if rec["blind"]["proven"] else "")


def report(res: dict, league: str = "NFL") -> str:
    def rec(d):
        roi = f"{d['flat_roi']:+.1%} ± {d['se']:.1%}" if d["flat_roi"] is not None and d["se"] else "-"
        return f"{d['bets']:>5} bets  {d['won']}-{d['lost']}-{d['push']}  {d['units']:+.1f}u  ROI {roi}"

    p = res["params"]
    lines = [f"{league} ratings (chosen on {res['tune_seasons']}): speed {p['k']}, home field {p['hfa']} pts, "
             f"rest {p['rest']} pts/day, new QB -{p['qb']} pts, carryover {p['carry']}; margin sd {res['margin_sd']:.1f}",
             f"Blind seasons: {res['blind_seasons']}", ""]
    for m, d in res["markets"].items():
        name = {"ml": "Moneyline", "spread": "Spread"}[m]
        lines += [f"{name}: trust in model {d['trust']:.2f}; blind log loss model {d['log_loss_model']:.4f} "
                  f"vs market {d['log_loss_market']:.4f} ({d['blind_games']} games)",
                  f"  in-sample       {rec(d['in_sample'])}",
                  f"  BLIND           {rec(d['blind'])}" + held_up(d),
                  f"  blind, model only {rec(d['blind_model_only'])}"]
        lines.append("  calibration (blind, model): " + "  ".join(
            f"{c['bin']} said {c['said']:.0%} won {c['won']:.0%} (n={c['games']})" for c in d["calibration_blind"]))
        lines.append("")
    w = res.get("wind_unders")
    lines += [] if w is None else [f"Totals: under in outdoor games with {WIND_MPH}+ mph wind (closing total and price)",
              f"    in-sample {rec(w['in_sample'])}",
              f"    BLIND     {rec(w['blind'])}" + held_up(w), ""]
    for kind, d in sorted((res.get("teasers") or {}).items()):
        t, bl = d["in_sample"], d["blind"]
        if t["legs"] and bl["legs"]:
            lines.append(f"6-point teaser legs, {kind}: in-sample {t['won']}/{t['legs']} ({t['rate']:.1%}), "
                         f"BLIND {bl['won']}/{bl['legs']} ({bl['rate']:.1%} ± {bl['se']:.1%})"
                         + ("  PROVEN" if d["proven"] else ""))
    lines.append("")
    lines.append("Early-bet spots (dog moneyline +100 to +220, at the closing price):")
    for spot, d in sorted(res["spots"].items()):
        lines.append(f"  {spot}")
        lines.append(f"    in-sample {rec(d['in_sample'])}")
        lines.append(f"    BLIND     {rec(d['blind'])}" + held_up(d))
    return "\n".join(lines)
