"""Blind tests for NBA, NHL, MLB and college basketball on eight seasons of closing lines.

Source: the public archive at github.com/D503therapper/autonomous-crypto-engine
(data/sports/games/<sport>/<month>.csv), one row per ESPN game since 2016, with the
closing moneyline, spread and total from 2018 on and the opening moneyline from 2023
(MLB from 2019). Checked against our own ESPN closing lines on the 2025-26 games both
archives hold: the same games, prices within 3 points of win chance on 97%+ of them,
and the favorites won as often as the prices said (closing lines, not post-game ones).

Built once into data/lines/<sport>/<season>.csv.gz in the NFL file's columns, then run
through the same engine as the NFL and college football tests (nflhist):

1. Ratings from results + market blend at the closing price, moneyline and spread
   (run line / puck line for MLB and NHL). Tuned on the seasons up to TUNE_LAST,
   judged blind on the rest.
2. The same model bet at the OPENING moneyline, with its closing line value.
3. Schedule spots for daily sports, each with its own record.
"""

from __future__ import annotations

import csv
import gzip
import io
import math
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path

from . import nflhist
from .cfbhist import bet_with_clv, open_rows
from .nflhist import Record, learn_trust

REPO = "https://github.com/D503therapper/autonomous-crypto-engine"
SPORTS = ("nba", "nhl", "mlb", "ncaab")
SEASON_START = {"nba": 8, "nhl": 8, "ncaab": 8, "mlb": 1, "ncaaf": 8, "nfl": 8}  # month a season's file starts
TUNE_LAST = 2021  # lines start 2018: tune on 2018-2021, blind on 2022 onward
GRID = {  # home edge in each sport's own scoring units (points, goals, runs)
    "nba": {"k": (0.04, 0.07, 0.1), "hfa": (1.5, 2.5, 3.5), "rest": (0.0, 0.5, 1.0), "qb": (0.0,), "carry": (0.5, 0.7, 0.9)},
    "ncaab": {"k": (0.05, 0.08, 0.11), "hfa": (2.0, 3.0, 4.0), "rest": (0.0, 0.3), "qb": (0.0,), "carry": (0.4, 0.6, 0.8)},
    "nhl": {"k": (0.02, 0.04, 0.06), "hfa": (0.1, 0.2, 0.3), "rest": (0.0, 0.1, 0.2), "qb": (0.0,), "carry": (0.5, 0.7, 0.9)},
    "mlb": {"k": (0.01, 0.02, 0.04), "hfa": (0.1, 0.2, 0.3), "rest": (0.0, 0.1), "qb": (0.0,), "carry": (0.5, 0.7, 0.9)},
}
EXTRA = ["open_home_ml", "open_away_ml"]
SPOT_DOG = (100, 220)
SPOT_KEYS = {"dog rested vs a team on a back-to-back": "rested_dog"}  # test spot -> live spot key (spots.py)
DOG_DECIMAL = 2.5  # about +150, the middle of the +100..+220 dog range: turns ROI into win-chance points


def _f(v) -> float | None:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(x) else x


def _ml(v) -> float | None:
    x = _f(v)
    return x if x is not None and abs(x) >= 100 else None


def season_of(sport: str, day: date) -> int:
    return day.year if day.month >= SEASON_START.get(sport, 8) else day.year - 1


def read_source(folder: Path) -> list[dict]:
    """Final games from the source archive's monthly CSVs, oldest first, one row per game id."""
    games: dict[str, dict] = {}
    for f in sorted(Path(folder).glob("*.csv")):
        for r in csv.DictReader(open(f, newline="")):
            if r.get("status") == "final" and _f(r.get("home_score")) is not None and _f(r.get("away_score")) is not None:
                games[r["id"]] = r
    return sorted(games.values(), key=lambda r: (r["start"], r["id"]))


def build(src: Path, out: Path, sport: str) -> int:
    """Write data/lines/<sport>/<season>.csv.gz from the source archive. Returns games written."""
    rows_by_season: dict[int, list[dict]] = defaultdict(list)
    last_played: dict[str, date] = {}
    first_day: dict[int, date] = {}
    for r in read_source(src):
        when = datetime.fromisoformat(r["start"].replace("Z", "+00:00"))
        day = when.date()
        season = season_of(sport, day)
        first_day.setdefault(season, day)
        rest = {}
        for side in ("home", "away"):
            prev = last_played.get(r[side])
            rest[side] = min((day - prev).days, 21) if prev else ""
            last_played[r[side]] = day
        home_ml, away_ml = _ml(r.get("ml_home")), _ml(r.get("ml_away"))
        spread = _f(r.get("spread_home"))
        sh, sa = _ml(r.get("spread_home_odds")), _ml(r.get("spread_away_odds"))
        row = {k: "" for k in nflhist.KEEP + EXTRA}
        row.update({
            "game_id": r["id"].split(":")[-1], "season": season, "game_type": "REG",
            "week": (day - first_day[season]).days // 7 + 1, "gameday": day.isoformat(),
            "weekday": day.strftime("%A"), "home_team": r["home"], "away_team": r["away"],
            "home_score": r["home_score"], "away_score": r["away_score"],
            "location": "Neutral" if r.get("neutral") == "1" else "Home",
            "home_rest": rest["home"], "away_rest": rest["away"], "roof": "dome" if r.get("indoor") == "1" else "outdoors",
            "espn": r["id"].split(":")[-1],
        })
        if home_ml and away_ml:
            row.update({"home_moneyline": home_ml, "away_moneyline": away_ml})
        if spread is not None and sh and sa:  # nflverse sign: home favored by spread_line
            row.update({"spread_line": -spread, "home_spread_odds": sh, "away_spread_odds": sa})
        if _f(r.get("total")) and _ml(r.get("over_odds")) and _ml(r.get("under_odds")):
            row.update({"total_line": r["total"], "over_odds": r["over_odds"], "under_odds": r["under_odds"]})
        oh, oa = _ml(r.get("ml_home_open")), _ml(r.get("ml_away_open"))
        if oh and oa:
            row.update({"open_home_ml": oh, "open_away_ml": oa})
        rows_by_season[season].append(row)
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    for season, rows in rows_by_season.items():
        buf = io.StringIO(newline="")
        w = csv.DictWriter(buf, fieldnames=nflhist.KEEP + EXTRA)
        w.writeheader()
        w.writerows(rows)
        # mtime=0: the same seasons give the same bytes, so an unchanged season is no new commit.
        (out / f"{season}.csv.gz").write_bytes(gzip.compress(buf.getvalue().encode(), mtime=0))
    return sum(len(v) for v in rows_by_season.values())


def extras(folder: Path) -> dict[str, dict[str, float | None]]:
    out = {}
    for f in sorted(Path(folder).glob("*.csv.gz")):
        with gzip.open(f, "rt", newline="") as fh:
            for r in csv.DictReader(fh):
                out[r["game_id"]] = {k: _f(r.get(k)) for k in EXTRA}
    return out


# --------------------------------------------------------------------- spots

def daily_spots(p: nflhist.Pred) -> list[tuple[str, str]]:
    """(spot, side) for the schedule spots of a daily sport."""
    g = p.game
    out = []
    if g.home_rest == 1 and g.away_rest >= 2:
        out.append(("dog rested vs a team on a back-to-back", "away"))
    if g.away_rest == 1 and g.home_rest >= 2:
        out.append(("dog rested vs a team on a back-to-back", "home"))
    if not g.neutral:
        out.append(("home dog", "home"))
    return out


def spot_records(preds: list[nflhist.Pred], blind_from: int) -> dict[str, dict[str, Record]]:
    out: dict[str, dict[str, Record]] = defaultdict(lambda: {"in_sample": Record(), "blind": Record()})
    for p in preds:
        g = p.game
        if not g.done or g.margin == 0 or not (g.home_ml and g.away_ml):
            continue
        price = {"home": g.home_ml, "away": g.away_ml}
        won = {"home": float(g.margin > 0), "away": float(g.margin < 0)}
        for spot, side in daily_spots(p):
            if SPOT_DOG[0] <= price[side] <= SPOT_DOG[1]:
                out[spot]["blind" if g.season >= blind_from else "in_sample"].add(price[side], won[side])
    return dict(out)


# -------------------------------------------------------------------- the test

def run(folder: Path, sport: str, tune_last: int = TUNE_LAST, grid: dict | None = None) -> dict:
    games = nflhist.load(folder)
    grid = grid or GRID[sport]
    f = nflhist.fit(games, tune_last, grid)
    res = nflhist.run(games, tune_last, grid, fitted=f)
    res.pop("wind_unders", None)
    res["tune_seasons"] = f"{min(g.season for g in games if g.home_ml)}-{tune_last}"
    res["spots"] = {k: {s: rec.to_dict() for s, rec in v.items()}
                    for k, v in spot_records(f.preds, tune_last + 1).items()}
    pairs = open_rows(f, extras(folder))
    tune_pairs = [p for p in pairs if p[0].pred.game.season <= tune_last]
    blind_pairs = [p for p in pairs if p[0].pred.game.season > tune_last]
    if blind_pairs:
        # Opening lines start in 2023, so the blend weight comes from the closing-line tuning seasons.
        trust = res["markets"]["ml"]["trust"] if not tune_pairs else learn_trust([r for r, _ in tune_pairs])
        res["opening"] = {"trust": trust, "in_sample": bet_with_clv(tune_pairs, trust),
                          "blind": bet_with_clv(blind_pairs, trust), "blind_model_only": bet_with_clv(blind_pairs, 1.0)}
    res["lined_games"] = sum(1 for g in games if g.home_ml and g.away_ml)
    res["source"] = REPO
    return res


def proven(res: dict | None) -> list[str]:
    """Bet types that passed: blind proven AND profitable in the tuning seasons."""
    out = []
    for m, d in ((res or {}).get("markets") or {}).items():
        if nflhist.held_up(d).strip() == "PROVEN":
            out.append(m)
    o = (res or {}).get("opening")
    if o and nflhist.held_up(o).strip() == "PROVEN":
        out.append("opening")
    for spot, d in ((res or {}).get("spots") or {}).items():
        if nflhist.held_up(d).strip() == "PROVEN":
            out.append(spot)
    return out


def spot_weights(results: dict[str, dict] | None) -> dict[str, float]:
    """{"sport:spot": win-chance bump} for every spot proven on these seasons (blind AND tuning).

    One standard error is shaved off the blind ROI first, so the bump is only the part luck
    can't explain, then it is turned into win-chance points at a typical dog price."""
    out = {}
    for sport, res in (results or {}).items():
        for name, d in ((res or {}).get("spots") or {}).items():
            key = SPOT_KEYS.get(name)
            blind = d.get("blind") or {}
            if key and nflhist.held_up(d).strip() == "PROVEN":
                w = (blind["flat_roi"] - (blind.get("se") or 0)) / DOG_DECIMAL
                if w > 0:
                    out[f"{sport}:{key}"] = w
    return out


def fetch(dest: Path) -> Path:
    """Shallow, sparse clone of the source archive's games folder. Returns the games folder."""
    import shutil
    import subprocess

    dest = Path(dest)
    if dest.exists():
        shutil.rmtree(dest)
    subprocess.run(["git", "clone", "--depth", "1", "--filter=blob:none", "--sparse", REPO, str(dest)],
                   check=True, capture_output=True, timeout=600)
    subprocess.run(["git", "-C", str(dest), "sparse-checkout", "set", "data/sports/games"],
                   check=True, capture_output=True, timeout=600)
    return dest / "data" / "sports" / "games"


def report(res: dict, sport: str) -> str:
    def rec(d):
        roi = f"{d['flat_roi']:+.1%} ± {d['se']:.1%}" if d["flat_roi"] is not None and d["se"] else "-"
        s = f"{d['bets']:>5} bets  {d['won']}-{d['lost']}-{d['push']}  {d['units']:+.1f}u  ROI {roi}"
        if d.get("clv") is not None:
            s += f"  CLV {d['clv']:+.1%} (beat the close {d['beat_close']:.0%})"
        return s

    text = nflhist.report(res, league=sport.upper()).replace(
        "Early-bet spots (dog moneyline +100 to +220, at the closing price):",
        "Spots (dog moneyline +100 to +220, at the closing price):").replace(", new QB -0.0 pts", "")
    o = res.get("opening")
    if o:
        text += "\n\n" + "\n".join([
            f"Model bet at the OPENING moneyline (trust {o['trust']:.2f}):",
            f"  in-sample         {rec(o['in_sample'])}",
            f"  BLIND             {rec(o['blind'])}" + nflhist.held_up(o),
            f"  blind, model only {rec(o['blind_model_only'])}",
        ])
    text += f"\n\n{res['lined_games']} games with closing moneylines. Proven: {', '.join(proven(res)) or 'nothing'}."
    return text
