"""College football blind test on 2006-2022 closing (and opening) lines, free.

Lines: cfbfastR-data's betting file (from CollegeFootballData): moneylines, spreads
and totals from up to fifteen books per game, Pinnacle among them, with opening
numbers from some books. Scores: cfbfastR schedules (ESPN game ids, the same ids the
lines use). 2023-2024 have lines but no scores in that source, so they are left out.

Built once into data/lines/ncaaf/<season>.csv in the NFL file's columns (consensus
closing prices), plus opening, Pinnacle and best-available prices. Then three tests,
each tuned on 2006-2016 and judged blind on 2017-2022:

1. The ratings model + market blend at the closing price (same engine as the NFL).
2. The same model bet at the OPENING price, before the market moves; with its
   closing line value (did the line move our way?).
3. Sharp value: the best price among the other books against Pinnacle's no-vig
   price (no model at all). Caveat: the books' "closing" prices may be logged at
   slightly different times, so some of this edge may be stale numbers.
"""

from __future__ import annotations

import csv
import gzip
import math
import statistics
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

from . import nflhist
from .nflhist import Record, Row, learn_trust
from .odds import american_to_decimal, decimal_to_american, implied_prob, no_vig

TUNE_LAST = 2016
SHARP_BOOK = "PINNACLE"
SHARP_MIN_EV, SHARP_MAX_EV = 0.02, 0.15
GRID = {
    "k": (0.06, 0.09, 0.12),
    "hfa": (1.5, 2.5, 3.5),
    "rest": (0.0, 0.15),
    "qb": (0.0,),  # no starting-QB data for college
    "carry": (0.4, 0.6, 0.8),
}
EXTRA = ["open_home_ml", "open_away_ml", "open_spread_line", "open_home_spread_odds", "open_away_spread_odds",
         "pin_home_ml", "pin_away_ml", "best_home_ml", "best_away_ml", "books"]


def _f(v) -> float | None:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(x) else x


# ---------------------------------------------------------------- schedules

def schedule_from_info(path: Path) -> list[dict]:
    """cfbfastR schedules/cfb_games_info.csv (2002-2020, CFBD columns)."""
    out = []
    for r in csv.DictReader(open(path, encoding="latin-1")):
        out.append({"game_id": r["game_id"], "season": int(r["season"]), "week": int(r["week"]),
                    "postseason": r["season_type"] != "regular", "date": r["start_date"][:10],
                    "home": r["home_id"], "away": r["away_id"], "home_score": _f(r["home_points"]),
                    "away_score": _f(r["away_points"]), "neutral": r["neutral_site"] == "TRUE"})
    return out


def schedule_from_parquet(path: Path) -> list[dict]:
    """cfbfastR cfb/schedules/parquet/cfb_schedule_<season>.parquet (ESPN columns). Needs pyarrow."""
    import pyarrow.parquet as pq

    names = pq.read_schema(path).names
    if "game_id" not in names:
        return []  # placeholder file
    out = []
    for r in pq.read_table(path, columns=["game_id", "season", "week", "season_type", "date", "home_id", "away_id",
                                          "home_score", "away_score", "neutral_site"]).to_pylist():
        out.append({"game_id": str(r["game_id"]), "season": int(r["season"]), "week": int(_f(r["week"]) or 0),
                    "postseason": r["season_type"] == 3, "date": str(r["date"])[:10],
                    "home": str(r["home_id"]), "away": str(r["away_id"]), "home_score": _f(r["home_score"]),
                    "away_score": _f(r["away_score"]), "neutral": bool(r["neutral_site"])})
    return out


# -------------------------------------------------------------------- lines

def team_ids(lines: list[dict]) -> dict[str, Counter]:
    """Book abbreviation -> how many games each team id played under it.

    Books reuse abbreviations ("MIA" is Miami FL at one book and Miami OH at another), so
    an abbreviation is not one team. In a given game it is the side whose team it names
    most often across the whole file (see ``side_of``)."""
    seen: dict[str, set[tuple[str, str]]] = defaultdict(set)
    for r in lines:
        if r["market_type"] != "total":
            seen[r["abbr"]].add((r["game_id"], r["home_team_id"]))
            seen[r["abbr"]].add((r["game_id"], r["away_team_id"]))
    return {a: Counter(t for _, t in pairs) for a, pairs in seen.items()}


def side_of(abbr: str, home_id: str, away_id: str, ids: dict[str, Counter]) -> str | None:
    c = ids.get(abbr, Counter())
    h, a = c[home_id], c[away_id]
    return "home" if h > a else "away" if a > h else None


def _price(v, market: str) -> float | None:
    """A real American price or None. Some rows hold the spread number in the odds field
    (e.g. -1.5), which would read as 200-to-1; spreads and totals beyond +/-1000 are junk too."""
    x = _f(v)
    if x is None or abs(x) < 100:
        return None
    if market != "money_line" and abs(x) > 1000:
        return None
    return x


def _median(prices: list[float]) -> float | None:
    """Median American price, taken on implied chances: the plain median of -105 and +105
    is 0, which would read as a 100-to-1 payout."""
    if not prices:
        return None
    q = statistics.median(implied_prob(p) for p in prices)
    return round(decimal_to_american(1 / q), 1)


def game_lines(rows: list[dict], ids: dict[str, Counter]) -> dict:
    """Consensus closing and opening numbers, Pinnacle's and the best other price, for one game."""
    ml: dict[str, dict[str, float]] = defaultdict(dict)
    ml_open: dict[str, dict[str, float]] = defaultdict(dict)
    sp: dict[str, dict[str, tuple[float, float]]] = defaultdict(dict)
    sp_open: dict[str, dict[str, tuple[float, float]]] = defaultdict(dict)
    tot: dict[str, dict[str, tuple[float, float]]] = defaultdict(dict)
    for r in rows:
        if r["market_type"] == "total":
            side = r["abbr"]
        else:
            side = side_of(r["abbr"], r["home_team_id"], r["away_team_id"], ids)
        if side not in ("home", "away", "over", "under"):
            continue
        odds, line = _price(r["odds"], r["market_type"]), _f(r["lines"])
        o_odds, o_line = _price(r["opening_odds"], r["market_type"]), _f(r["opening_lines"])
        book = r["book"]
        if r["market_type"] == "money_line":
            if odds:
                ml[book][side] = odds
            if o_odds:
                ml_open[book][side] = o_odds
        elif r["market_type"] == "spread":
            if odds and line is not None:
                sp[book][side] = (line, odds)
            if o_odds and o_line is not None:
                sp_open[book][side] = (o_line, o_odds)
        elif odds and line is not None:
            tot[book][side] = (line, odds)

    def both(d):
        return {b: v for b, v in d.items() if "home" in v and "away" in v}

    def ml_cons(d):
        d = both(d)
        return _median([v["home"] for v in d.values()]), _median([v["away"] for v in d.values()])

    def line_cons(d, a="home", b="away"):
        d = {k: v for k, v in d.items() if a in v and b in v}
        if not d:
            return None, None, None
        line = Counter(v[a][0] for v in d.values()).most_common(1)[0][0]
        at = [v for v in d.values() if v[a][0] == line]
        return line, _median([v[a][1] for v in at]), _median([v[b][1] for v in at])

    close_h, close_a = ml_cons(ml)
    open_h, open_a = ml_cons(ml_open)
    sp_line, sp_h, sp_a = line_cons(sp)
    osp_line, osp_h, osp_a = line_cons(sp_open)
    tot_line, over, under = line_cons(tot, "over", "under")
    pin = both(ml).get(SHARP_BOOK, {})
    others = {b: v for b, v in both(ml).items() if b != SHARP_BOOK}
    return {
        "home_moneyline": close_h, "away_moneyline": close_a,
        "spread_line": -sp_line if sp_line is not None else None,  # nflverse: home favored by this many
        "home_spread_odds": sp_h, "away_spread_odds": sp_a,
        "total_line": tot_line, "over_odds": over, "under_odds": under,
        "open_home_ml": open_h, "open_away_ml": open_a,
        "open_spread_line": -osp_line if osp_line is not None else None,
        "open_home_spread_odds": osp_h, "open_away_spread_odds": osp_a,
        "pin_home_ml": pin.get("home"), "pin_away_ml": pin.get("away"),
        "best_home_ml": max((v["home"] for v in others.values()), default=None),
        "best_away_ml": max((v["away"] for v in others.values()), default=None),
        "books": len(both(ml)),
    }


def build(lines: list[dict], schedule: list[dict], folder: Path, first: int = 2006, last: int = 2022) -> int:
    """Write data/lines/ncaaf/<season>.csv: every scored game (lined or not, so the ratings
    see every result), with its lines when the betting file has them. Returns games written."""
    ids = team_ids(lines)
    by_game: dict[str, list[dict]] = defaultdict(list)
    for r in lines:
        by_game[r["game_id"].split(".")[0]].append(r)
    games = {g["game_id"]: g for g in schedule if first <= g["season"] <= last and g["home_score"] is not None}
    last_played: dict[str, str] = {}
    out: dict[int, list[dict]] = defaultdict(list)
    for g in sorted(games.values(), key=lambda g: (g["date"], g["game_id"])):
        day = datetime.fromisoformat(g["date"]).date()
        rest = {}
        for side in ("home", "away"):
            prev = last_played.get(g[side])
            rest[side] = min((day - datetime.fromisoformat(prev).date()).days, 21) if prev else ""
            last_played[g[side]] = g["date"]
        row = {k: "" for k in nflhist.KEEP + EXTRA}
        row.update({
            "game_id": g["game_id"], "season": g["season"], "game_type": "POST" if g["postseason"] else "REG",
            "week": g["week"] + (20 if g["postseason"] else 0), "gameday": day.isoformat(),
            "weekday": day.strftime("%A"), "home_team": g["home"], "away_team": g["away"],
            "home_score": g["home_score"], "away_score": g["away_score"],
            "location": "Neutral" if g["neutral"] else "Home", "home_rest": rest["home"], "away_rest": rest["away"],
            "espn": g["game_id"],
        })
        if g["game_id"] in by_game:
            row.update({k: ("" if v is None else v) for k, v in game_lines(by_game[g["game_id"]], ids).items()})
        out[g["season"]].append(row)
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    for season, rows in out.items():
        with open(folder / f"{season}.csv", "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=nflhist.KEEP + EXTRA)
            w.writeheader()
            w.writerows(rows)
    return sum(len(v) for v in out.values())


def read_lines_gz(path: Path) -> list[dict]:
    with gzip.open(path, "rt", newline="") as fh:
        return list(csv.DictReader(fh))


def extras(folder: Path) -> dict[str, dict[str, float | None]]:
    out = {}
    for f in sorted(Path(folder).glob("*.csv")):
        for r in csv.DictReader(open(f, newline="")):
            out[r["game_id"]] = {k: _f(r.get(k)) for k in EXTRA}
    return out


# -------------------------------------------------------------------- tests

def open_rows(fit: nflhist.Fit, ex: dict) -> list[tuple[Row, dict[str, float]]]:
    """Moneyline rows at the opening price, each with the closing no-vig chance (for CLV)."""
    a, b = fit.cal
    out = []
    for pr in fit.preds:
        g = pr.game
        e = ex.get(g.gid, {})
        oh, oa = e.get("open_home_ml"), e.get("open_away_ml")
        if not g.done or g.margin == 0 or not (oh and oa) or g.season <= nflhist.WARMUP_LAST:
            continue
        if not (g.home_ml and g.away_ml):
            continue
        ph = nflhist._sigmoid(a * pr.margin + b)
        row = Row(pr, "ml", {"home": ph, "away": 1 - ph}, no_vig({"home": oh, "away": oa}),
                  {"home": oh, "away": oa}, {"home": float(g.margin > 0), "away": float(g.margin < 0)})
        out.append((row, no_vig({"home": g.home_ml, "away": g.away_ml})))
    return out


def bet_with_clv(pairs: list[tuple[Row, dict]], trust: float) -> dict:
    """Bet the opening price like nflhist.bet, and measure each bet against the close."""
    rec, clv = Record(), []
    for r, close in pairs:
        best = None
        for side in ("home", "away"):
            read = trust * r.model[side] + (1 - trust) * r.fair[side]
            edge = read - r.fair[side]
            if (nflhist.MAX_FAVORITE <= r.price[side] <= nflhist.MAX_UNDERDOG and edge >= nflhist.MIN_EDGE
                    and (best is None or edge > best[0])):
                best = (edge, side, read)
        if best:
            _, side, read = best
            rec.add(r.price[side], r.won[side], nflhist.units(read, r.price[side]))
            clv.append(close[side] * american_to_decimal(r.price[side]) - 1)
    d = rec.to_dict()
    d["clv"] = sum(clv) / len(clv) if clv else None
    d["beat_close"] = sum(c > 0 for c in clv) / len(clv) if clv else None
    return d


def sharp_value(games: list[nflhist.Game], ex: dict, blind_from: int) -> dict[str, Record]:
    """Bet the best other-book price that beats Pinnacle's no-vig price by 2%-15%, -170 or better."""
    out = {"in_sample": Record(), "blind": Record()}
    for g in games:
        e = ex.get(g.gid, {})
        ph, pa, bh, ba = e.get("pin_home_ml"), e.get("pin_away_ml"), e.get("best_home_ml"), e.get("best_away_ml")
        if not g.done or g.margin == 0 or not (ph and pa and bh and ba) or g.season <= nflhist.WARMUP_LAST:
            continue
        fair = no_vig({"home": ph, "away": pa})
        best = None
        for side, price in (("home", bh), ("away", ba)):
            ev = fair[side] * american_to_decimal(price) - 1
            if (nflhist.MAX_FAVORITE <= price <= nflhist.MAX_UNDERDOG and SHARP_MIN_EV <= ev <= SHARP_MAX_EV
                    and (best is None or ev > best[0])):
                best = (ev, side, price)
        if best:
            _, side, price = best
            won = float(g.margin > 0) if side == "home" else float(g.margin < 0)
            out["blind" if g.season >= blind_from else "in_sample"].add(price, won)
    return out


def run(folder: Path, tune_last: int = TUNE_LAST, grid: dict = GRID) -> dict:
    games = nflhist.load(folder)
    ex = extras(folder)
    f = nflhist.fit(games, tune_last, grid)
    res = nflhist.run(games, tune_last, grid, fitted=f)
    res.pop("wind_unders", None)  # no weather in the college file
    pairs = open_rows(f, ex)
    tune_pairs = [p for p in pairs if p[0].pred.game.season <= tune_last]
    blind_pairs = [p for p in pairs if p[0].pred.game.season > tune_last]
    trust = learn_trust([r for r, _ in tune_pairs])
    res["opening"] = {"trust": trust, "in_sample": bet_with_clv(tune_pairs, trust),
                      "blind": bet_with_clv(blind_pairs, trust), "blind_model_only": bet_with_clv(blind_pairs, 1.0)}
    res["sharp"] = {k: v.to_dict() for k, v in sharp_value(games, ex, tune_last + 1).items()}
    res["lined_games"] = sum(1 for g in games if g.home_ml and g.away_ml)
    return res


def report(res: dict) -> str:
    def rec(d):
        roi = f"{d['flat_roi']:+.1%} ± {d['se']:.1%}" if d["flat_roi"] is not None and d["se"] else "-"
        s = f"{d['bets']:>5} bets  {d['won']}-{d['lost']}-{d['push']}  {d['units']:+.1f}u  ROI {roi}"
        if d.get("clv") is not None:
            s += f"  CLV {d['clv']:+.1%} (beat the close {d['beat_close']:.0%})"
        return s

    text = nflhist.report(res, league="College football").replace(
        "Early-bet spots (dog moneyline +100 to +220, at the closing price):",
        "Spots (dog moneyline +100 to +220, at the closing price):")
    o, s = res["opening"], res["sharp"]
    text += "\n\n" + "\n".join([
        f"Model bet at the OPENING moneyline (trust {o['trust']:.2f}):",
        f"  in-sample         {rec(o['in_sample'])}",
        f"  BLIND             {rec(o['blind'])}" + nflhist.held_up(o),
        f"  blind, model only {rec(o['blind_model_only'])}",
        "",
        "Sharp value: best other-book moneyline vs Pinnacle's no-vig price (2%-15% edge, -170 or better):",
        f"  2007-{TUNE_LAST}         {rec(s['in_sample'])}",
        f"  {TUNE_LAST + 1}-2022         {rec(s['blind'])}" + nflhist.held_up(s),
        f"\n{res['lined_games']} games with closing moneylines.",
    ])
    return text
