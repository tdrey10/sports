"""What makes a good prediction: a blind test of projected scores and winners on 2018+ games.

Every game in the public ESPN archive (data/lines/espn/<sport>) is predicted with the engine's
own team ratings, refitted each week from the games before it, and set beside the closing
Vegas total, spread and moneyline. The tuning seasons (through 2021) pick how much weight our
ratings get against the market for three things: the total, the margin and the win chance.
The later seasons are the blind test of that choice.

The finding that shaped the predictions: in every sport the closing line predicts scores and
winners better than our ratings alone, and our ratings add a little on top only in some
(MLB and NHL totals). Our ratings also run high in the playoffs, where aces and top lines
play (MLB postseason: ratings 8.9 runs a game, actual 8.1). So a prediction starts from the
market and moves toward our ratings by the weight they earned here.
"""

from __future__ import annotations

import math
from datetime import timedelta
from pathlib import Path

import numpy as np

from . import linestudies
from .models.game import predict_game
from .models.ratings import GameResult, fit_ratings
from .sports import SPORTS as CONFIGS

TUNE_LAST = 2021
SPORTS = ("mlb", "nhl", "nba", "nfl", "ncaaf", "ncaab")
COUNT = ("mlb", "nhl")  # run lines and puck lines are always 1.5: the margin comes from the moneyline
WEIGHTS = np.linspace(0, 1, 21)


def _implied(price: float) -> float:
    return 100 / (price + 100) if price > 0 else -price / (-price + 100)


def replay(games: list[linestudies.G], sport: str, refit_days: int = 7) -> list[dict]:
    """Our ratings' read of every game, fitted only on the games before its week."""
    cfg = CONFIGS[sport]
    games = [g for g in games if g.stype != "1"]  # preseason out
    if not games:
        return []
    res = [GameResult(g.gid, g.start, g.home, g.away, g.hs, g.as_, g.neutral, state="post") for g in games]
    rows, i = [], 0
    cursor, end = games[0].start + timedelta(days=60), games[-1].start + timedelta(days=1)
    while i < len(games) and games[i].start < cursor:
        i += 1
    while cursor < end:
        nxt = cursor + timedelta(days=refit_days)
        j = i
        while j < len(games) and games[j].start < nxt:
            j += 1
        if j > i:
            train = [r for r in res[:i] if r.date >= cursor - timedelta(days=cfg.lookback_days)]
            if len(train) > 100:
                rt = fit_ratings(train, cursor, cfg.half_life_days, cfg.ridge, cfg.link, cfg.unknown_percentile,
                                 carryover=cfg.carryover)
                for g in games[i:j]:
                    p = predict_game(cfg, rt, g.home, g.away, g.neutral)
                    rows.append({"season": g.season, "post": g.stype == "3", "hs": g.hs, "as": g.as_,
                                 "m_total": p.exp_total, "m_margin": p.exp_margin, "p_home": p.p_home,
                                 "total": g.total, "spread": g.spread, "ml": g.ml})
        i, cursor = j, nxt
    return rows


def _best(errs) -> float:
    return float(WEIGHTS[int(np.argmin(errs))])


def analyze(rows: list[dict], sport: str, tune_last: int = TUNE_LAST) -> dict | None:
    keep = []
    for r in rows:
        h, a = r["ml"].get("home"), r["ml"].get("away")
        if r["total"] is None or not h or not a:
            continue
        ph, pa = _implied(h), _implied(a)
        q = ph / (ph + pa)
        if 0.02 < q < 0.98:
            keep.append((r, q))
    if len(keep) < 500:
        return None
    col = lambda f: np.array([f(r, q) for r, q in keep], float)  # noqa: E731
    season = col(lambda r, q: r["season"])
    post = col(lambda r, q: r["post"]).astype(bool)
    mt, vt = col(lambda r, q: r["m_total"]), col(lambda r, q: r["total"])
    mm, pm, q = col(lambda r, q: r["m_margin"]), col(lambda r, q: r["p_home"]), col(lambda r, q: q)
    hs, as_ = col(lambda r, q: r["hs"]), col(lambda r, q: r["as"])
    T, M = hs + as_, hs - as_
    tune, blind = season <= tune_last, season > tune_last
    if tune.sum() < 300 or blind.sum() < 300:
        return None
    lq = np.log(q / (1 - q))
    k = None
    if sport in COUNT:  # market margin from the moneyline: home margin = k * log-odds, fitted on tuning
        k = float(np.sum(lq[tune] * M[tune]) / np.sum(lq[tune] ** 2))
        vm = k * lq
    else:
        vm = col(lambda r, q: np.nan if r["spread"] is None else -r["spread"])
    has_m = ~np.isnan(vm)

    def mse(x, y, t, m):
        return [float(np.mean((w * x[m] + (1 - w) * y[m] - t[m]) ** 2)) for w in WEIGHTS]

    def ll(p, m):
        p = np.clip(p[m], 1e-4, 1 - 1e-4)
        y = M[m] > 0
        return float(-np.mean(np.where(y, np.log(p), np.log(1 - p))))

    w_total = _best(mse(mt, vt, T, tune))
    w_margin = _best(mse(mm, vm, M, tune & has_m))
    decided = M != 0
    w_win = _best([ll(w * pm + (1 - w) * q, tune & decided) for w in WEIGHTS])
    bt, bm, bp = w_total * mt + (1 - w_total) * vt, w_margin * mm + (1 - w_margin) * vm, w_win * pm + (1 - w_win) * q
    # Playoff games: how far the blend runs from the actual total (the regular season sets the weights).
    post_adj = 0.0
    tp = tune & post
    if tp.sum() >= 50:
        post_adj = round(float(np.mean(T[tp] - bt[tp])), 2)

    def score_err(t, m, mask):
        h, a = (t + m) / 2, (t - m) / 2
        return round(float(np.nanmean(((np.abs(h - hs) + np.abs(a - as_)) / 2)[mask])), 2)

    b, bd, bm_ = blind, blind & decided, blind & has_m
    mae = lambda x, y, m: round(float(np.mean(np.abs(x[m] - y[m]))), 2)  # noqa: E731
    acc = lambda p, m: round(float(np.mean((p[m] > 0.5) == (M[m] > 0))), 3)  # noqa: E731
    return {
        "tune_seasons": f"{int(season[tune].min())}-{tune_last}", "blind_seasons": f"{tune_last + 1}-{int(season.max())}",
        "games": {"tune": int(tune.sum()), "blind": int(b.sum())},
        "weights": {"total": w_total, "margin": w_margin, "win": w_win},
        "margin_per_logit": round(k, 3) if k is not None else None,
        "postseason_total_adj": post_adj,
        "blind": {
            "total_error": {"ratings": mae(mt, T, b), "vegas": mae(vt, T, b), "blend": mae(bt, T, b)},
            "margin_error": {"ratings": mae(mm, M, bm_), "vegas": mae(vm, M, bm_), "blend": mae(bm, M, bm_)},
            "winners": {"ratings": acc(pm, bd), "vegas": acc(q, bd), "blend": acc(bp, bd)},
            "log_loss": {"ratings": round(ll(pm, bd), 4), "vegas": round(ll(q, bd), 4), "blend": round(ll(bp, bd), 4)},
            "score_error": {"ratings": score_err(mt, mm, bm_), "blend": score_err(bt, bm, bm_)},
        },
        "postseason": {"games": int(post.sum()), "actual": round(float(T[post].mean()), 2) if post.any() else None,
                       "vegas": round(float(vt[post].mean()), 2) if post.any() else None,
                       "ratings": round(float(mt[post].mean()), 2) if post.any() else None},
    }


def run(lines_dir: Path, sports=SPORTS) -> dict:
    out = {}
    for sport in sports:
        folder = Path(lines_dir) / sport
        if not any(folder.glob("*.csv.gz")):
            continue
        res = analyze(replay(linestudies.load(folder, sport), sport), sport)
        if res:
            out[sport] = res
    return out


def report(res: dict) -> str:
    unit = {"mlb": "runs", "nhl": "goals"}
    lines = ["Prediction study: how much to trust our ratings against Vegas (blind seasons)"]
    for sport, r in res.items():
        b, w, u = r["blind"], r["weights"], unit.get(sport, "pts")
        lines.append(
            f"  {sport.upper()} ({r['games']['blind']} blind games): weight on our ratings total {w['total']:.2f}, "
            f"margin {w['margin']:.2f}, winner {w['win']:.2f}. Total missed by {b['total_error']['blend']} {u} "
            f"(ratings {b['total_error']['ratings']}, Vegas {b['total_error']['vegas']}); winners "
            f"{b['winners']['blend']:.1%} (ratings {b['winners']['ratings']:.1%}, Vegas {b['winners']['vegas']:.1%})."
        )
    return "\n".join(lines)


def blend(study: dict | None, sport: str) -> dict | None:
    """The weights a prediction uses for ``sport``, or None when there is no study for it."""
    r = (study or {}).get(sport)
    if not r:
        return None
    return {**r["weights"], "k": r.get("margin_per_logit"), "post_adj": r.get("postseason_total_adj") or 0.0,
            "blind": r["blind"], "games": r["games"]["blind"], "seasons": r["blind_seasons"]}


def logit(p: float) -> float:
    p = min(max(p, 1e-4), 1 - 1e-4)
    return math.log(p / (1 - p))


__all__ = ["replay", "analyze", "run", "report", "blend"]
