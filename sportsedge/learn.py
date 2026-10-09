"""A learned model of where the market's price is wrong, tested blind against the closing line.

Every side of every past closing line becomes one row. Its features are what was
known before the game: the market's no-vig chance, our ratings' disagreement
with it, rest and back-to-backs, time-zone travel, recent form (margin, points
for and against over the last 10 games), pace, MLB starter ERAs and sample
size. A gradient-boosting classifier learns from the older games how those
features turn into a true win/cover chance; the newer games stay blind.

It only matters if it beats the market: on the blind games the learned chance
must have a lower log loss than the market's own no-vig chance, and betting
its 2%+ edges at -150 or better must make money beyond luck over 60+ bets.
"""

from __future__ import annotations

import bisect
import logging
import math
from collections import defaultdict
from datetime import datetime
from typing import TYPE_CHECKING

from .models.ratings import GameResult, is_b2b
from .odds import american_to_decimal

if TYPE_CHECKING:  # pragma: no cover
    from .study import Side

log = logging.getLogger(__name__)

FEATURES = (
    "market", "model_gap", "is_h2h", "is_spread", "is_total", "point", "is_home",
    "rest_diff", "own_b2b", "opp_b2b", "form_margin", "form_for", "form_against", "pace",
    "travel", "sample", "era_diff", "era_known",
)
MIN_ROWS = 600
MIN_EV, MAX_EV, MAX_FAVORITE = 0.02, 0.20, -150
PROVEN_BETS = 60


def _logit(p: float | None) -> float:
    p = min(max(p if p is not None else 0.5, 1e-3), 1 - 1e-3)
    return math.log(p / (1 - p))


class Form:
    """Rolling team form from final scores only: what was known before each game."""

    def __init__(self, results: list[GameResult], window: int = 10):
        self.window = window
        self.games: dict[str, list[tuple[datetime, float, float]]] = defaultdict(list)
        for g in sorted((g for g in results if g.completed), key=lambda g: g.date):
            self.games[g.home].append((g.date, g.home_score, g.away_score))
            self.games[g.away].append((g.date, g.away_score, g.home_score))
        self.dates = {t: [d for d, _, _ in rows] for t, rows in self.games.items()}

    def before(self, team: str, when: datetime) -> tuple[float, float, float, int]:
        """(avg margin, avg points for, avg points against, games so far) over the last ``window``."""
        rows = self.games.get(team, [])
        i = bisect.bisect_left(self.dates.get(team, []), when)
        last = rows[max(0, i - self.window):i]
        if not last:
            return 0.0, 0.0, 0.0, 0
        n = len(last)
        pf = sum(r[1] for r in last) / n
        pa = sum(r[2] for r in last) / n
        return pf - pa, pf, pa, i


def game_context(cfg, g: GameResult, form: Form, rest: dict) -> dict:
    """Home-perspective context for one game."""
    from .spots import ZONE_HOURS, zone

    hm, hf, ha, hn = form.before(g.home, g.date)
    am, af, aa, an = form.before(g.away, g.date)
    rh, ra = rest.get((g.event_id, g.home)), rest.get((g.event_id, g.away))
    hz, az = zone(cfg.key, g.home), zone(cfg.key, g.away)
    era_known = g.home_pitcher_era is not None and g.away_pitcher_era is not None
    return {
        "rest_diff": max(min((rh or 3) - (ra or 3), 7), -7),
        "home_b2b": float(bool(cfg.rest_effects and is_b2b(rh))),
        "away_b2b": float(bool(cfg.rest_effects and is_b2b(ra))),
        "form_margin": hm - am, "form_for": hf - af, "form_against": ha - aa,
        "pace": (hf + ha + af + aa) / 2 if hn and an else 0.0,
        "travel": float(ZONE_HOURS[az] - ZONE_HOURS[hz]) if hz and az and not g.neutral else 0.0,
        "sample": float(min(hn, an)),
        "era_diff": (g.away_pitcher_era - g.home_pitcher_era) if era_known else 0.0,  # + = home starter better
        "era_known": float(era_known),
    }


def side_features(ctx: dict, market: str, side: str, point: float | None,
                  model_prob: float, market_prob: float | None) -> list[float]:
    """One row, oriented to the side being bet."""
    total = market == "totals"
    sign = 0.0 if total else (1.0 if side == "home" else -1.0)
    pace_sign = (1.0 if side == "over" else -1.0) if total else 0.0
    return [
        _logit(market_prob), _logit(model_prob) - _logit(market_prob),
        float(market == "h2h"), float(market == "spreads"), float(total), float(point or 0.0),
        float(side in ("home", "over")),
        sign * ctx["rest_diff"],
        ctx["home_b2b"] if side == "home" else ctx["away_b2b"] if side == "away" else 0.0,
        ctx["away_b2b"] if side == "home" else ctx["home_b2b"] if side == "away" else 0.0,
        sign * ctx["form_margin"], sign * ctx["form_for"], sign * ctx["form_against"],
        pace_sign * ctx["pace"], sign * ctx["travel"], ctx["sample"],
        sign * ctx["era_diff"], ctx["era_known"],
    ]


def _rows(sides: list["Side"]):
    keep = [s for s in sides if s.features is not None and s.market_prob is not None and s.win + s.loss > 0]
    X = [s.features for s in keep]
    y = [1 if s.win > s.loss else 0 for s in keep]
    return keep, X, y


def _paired(keep: list["Side"], probs) -> list[float]:
    """Make the two sides of each line add up to 1 (the classifier scores them separately)."""
    idx = defaultdict(list)
    for i, s in enumerate(keep):
        idx[(s.event_id, s.market)].append(i)
    out = list(map(float, probs))
    for group in idx.values():
        if len(group) == 2:
            a, b = group
            tot = out[a] + out[b]
            if tot > 0:
                out[a], out[b] = out[a] / tot, out[b] / tot
    return out


def _log_loss(ps: list[float], ys: list[int]) -> float:
    return -sum(y * math.log(min(max(p, 1e-4), 1 - 1e-4)) + (1 - y) * math.log(1 - min(max(p, 1e-4), 1 - 1e-4))
                for p, y in zip(ps, ys)) / max(len(ys), 1)


def evaluate(tune: list["Side"], blind: list["Side"]) -> dict:
    """Train on the tuning games, judge on the blind ones."""
    try:
        from sklearn.ensemble import HistGradientBoostingClassifier
    except ImportError:  # pragma: no cover
        return {"note": "scikit-learn is not installed"}
    t_keep, tX, ty = _rows(tune)
    b_keep, bX, by = _rows(blind)
    if len(tX) < MIN_ROWS or not bX:
        return {"note": f"Only {len(tX)} past lines with full features to learn from (need {MIN_ROWS}).",
                "tune_rows": len(tX), "blind_rows": len(bX)}
    clf = HistGradientBoostingClassifier(
        max_depth=3, learning_rate=0.04, max_iter=300, min_samples_leaf=80, l2_regularization=1.0,
        early_stopping=True, validation_fraction=0.2, random_state=7,
    )
    clf.fit(tX, ty)
    learned = _paired(b_keep, clf.predict_proba(bX)[:, 1])
    market = [s.market_prob for s in b_keep]
    ll_market, ll_learned = _log_loss(market, by), _log_loss(learned, by)

    # The bets it would have made: best 2%+ edge per game, -150 or better, at the closing price.
    best: dict[str, tuple[float, "Side"]] = {}
    for s, q in zip(b_keep, learned):
        if s.american < MAX_FAVORITE:
            continue
        ev = q * (1 - s.push_prob) * (american_to_decimal(s.american) - 1) - (1 - q) * (1 - s.push_prob)
        if MIN_EV <= ev <= MAX_EV and (s.event_id not in best or ev > best[s.event_id][0]):
            best[s.event_id] = (ev, s)
    profits = [s.profit for _, s in best.values()]
    n = len(profits)
    roi = sum(profits) / n if n else None
    se = math.sqrt(sum((p - roi) ** 2 for p in profits) / (n - 1) / n) if n > 1 else None
    won = sum(s.win for _, s in best.values())
    lost = sum(s.loss for _, s in best.values())
    proven = n >= PROVEN_BETS and roi is not None and roi - (se or 0) > 0 and ll_learned < ll_market
    return {
        "tune_rows": len(tX), "blind_rows": len(bX),
        "log_loss_market": round(ll_market, 5), "log_loss_learned": round(ll_learned, 5),
        "beats_market": ll_learned < ll_market,
        "bets": n, "won": won, "lost": lost, "units": round(sum(profits), 2),
        "roi": roi, "se": se, "proven": proven,
    }
