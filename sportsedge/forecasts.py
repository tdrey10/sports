"""Predictions for every game on the board: a projected score and who we think wins.

Not picks: no price has to be beaten. Each forecast is our own educated read of the game,
not the betting line repeated back:

- projected score and win chances: our team ratings (points scored and allowed, home
  field, rest, injuries, starting pitchers and goalies), calibrated by the studies;
- the Vegas spread, total and win chance beside it, with where we disagree;
- the facts behind it: records, form, splits, power rank, rest, starters, weather, injuries.

A forecast keeps updating until the game starts, then it is frozen and graded against the
final score: did we pick the winner, and how far off was the score.
"""

from __future__ import annotations

import statistics
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime, timedelta

from .edge import Settings
from .engine import SportScan
from .insights import reasons
from .odds import implied_prob, no_vig

KEEP_DAYS = 14
DROP = ("Best price", "Model ", "Projected score")  # shown on the card in their own way


@dataclass
class Forecast:
    id: str
    sport: str
    sport_name: str
    event_id: str
    commence_time: str
    home: str
    away: str
    home_score: float  # projected
    away_score: float
    p_home: float
    p_away: float
    p_draw: float = 0.0
    winner: str = "home"  # "home" / "away" / "draw"
    model_home: float | None = None  # our ratings alone, before the market blend
    market_home: float | None = None  # the market's no-vig chance, when there is a price
    facts: list[str] = field(default_factory=list)
    updated_at: str = ""
    status: str = "pending"  # pending, live, final, void
    final_home: float | None = None
    final_away: float | None = None
    live: dict | None = None
    correct: bool | None = None
    graded_at: str | None = None

    @property
    def start(self) -> datetime:
        return datetime.fromisoformat(self.commence_time)

    @property
    def pick_prob(self) -> float:
        return {"home": self.p_home, "away": self.p_away, "draw": self.p_draw}[self.winner]

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Forecast":
        names = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in names})


def _median_point(quotes, market: str, side: str) -> float | None:
    pts = [q.point for q in quotes if q.market == market and q.side == side and q.point is not None]
    return statistics.median(pts) if pts else None


def _consensus_ml(quotes, sides) -> dict[str, float] | None:
    """Median no-vig chance per side across books."""
    by_book: dict[str, dict[str, float]] = {}
    for q in quotes:
        if q.market == "h2h" and q.side in sides:
            by_book.setdefault(q.book, {})[q.side] = q.american
    fairs = [no_vig(p) for p in by_book.values() if len(p) == len(sides)]
    if not fairs:
        return None
    med = {s: statistics.median(f[s] for f in fairs) for s in sides}
    total = sum(med.values())
    return {s: v / total for s, v in med.items()}


def _whole(home: float, away: float, winner: str) -> tuple[int, int]:
    """Whole-number score that names the same winner as the win chances."""
    h, a = round(home), round(away)
    if winner == "home" and h <= a:
        h = a + 1
    elif winner == "away" and a <= h:
        a = h + 1
    elif winner == "draw":
        h = a = round((home + away) / 2)
    return h, a


def _vegas_line(f_home: str, f_away: str, home_line: float | None, total_line: float | None) -> str | None:
    parts = []
    if home_line is not None:
        fav, pts = (f_home, -home_line) if home_line < 0 else (f_away, home_line)
        parts.append(f"{fav} -{pts:g}" if pts else "pick'em")
    if total_line is not None:
        parts.append(f"O/U {total_line:g}")
    return " · ".join(parts) if parts else None


def build(scans: list[SportScan], settings: Settings, now: datetime) -> list[Forecast]:
    """Our own read of every game: the ratings' projected score and win chances (scoring,
    defense, home field, rest, injuries, starting pitchers and goalies), not the betting
    line. The market's spread and total are shown beside it, so the disagreements stand out."""
    out = []
    for scan in scans:
        cfg = scan.sport
        for ev_id, pred in scan.predictions.items():
            ctx = scan.contexts.get(ev_id)
            if ctx is None or ctx.event.commence_time <= now:
                continue
            quotes = ctx.event.quotes
            sides = ("home", "away", "draw") if cfg.draws else ("home", "away")
            p = {"home": pred.p_home, "away": pred.p_away, "draw": pred.p_draw}
            # The studies' calibration of our win chances, when the engine applied it to the picks.
            for side in sides:
                pk = next((x for x in scan.picks if x.event_id == ev_id and x.market == "h2h" and x.side == side
                           and not x.is_prop), None)
                if pk is not None:
                    p[side] = pk.model_prob
            norm = sum(p[s] for s in sides) or 1.0
            p = {s: p[s] / norm for s in sides}
            winner = max(p, key=p.get)
            market = _consensus_ml(quotes, sides)

            margin, total = pred.exp_margin, pred.exp_total
            if winner == "home" and margin <= 0:
                margin = 0.5
            elif winner == "away" and margin >= 0:
                margin = -0.5
            home_score, away_score = max((total + margin) / 2, 0.0), max((total - margin) / 2, 0.0)

            facts: list[str] = []
            home_line = _median_point(quotes, "spreads", "home")
            total_line = _median_point(quotes, "totals", "over")
            vegas = _vegas_line(ctx.home, ctx.away, home_line, total_line)
            if vegas:
                diff = []
                if home_line is not None:
                    d = pred.exp_margin - (-home_line)
                    if abs(d) >= 1:
                        diff.append(f"we have {ctx.home if d > 0 else ctx.away} {abs(d):.1f} better than the spread")
                if total_line is not None:
                    d = pred.exp_total - total_line
                    if abs(d) >= 1:
                        diff.append(f"{abs(d):.1f} {'more' if d > 0 else 'fewer'} total than the line")
                facts.append(f"Vegas: {vegas}" + (f" ({'; '.join(diff)})." if diff else " (we agree)."))
            if market:
                facts.append(f"Market win chance: {ctx.away} {market['away']:.0%} · {ctx.home} {market['home']:.0%}"
                             + (f" · draw {market['draw']:.0%}" if 'draw' in market else "") + ".")
            if ctx.wind_mph is not None:
                facts.append(f"Wind forecast {ctx.wind_mph:.0f} mph at an outdoor stadium"
                             + (": windy games score less (the NFL windy under is proven over 20 seasons)."
                                if ctx.wind_mph >= 15 else "."))
            side = winner if winner != "draw" else "home"
            pick = next((x for x in scan.picks if x.event_id == ev_id and x.market == "h2h" and x.side == side
                         and not x.is_prop), None)
            if pick is not None:
                _, bullets = reasons(pick, scan)
                facts += [b for b in bullets if not b.startswith(DROP)]
            out.append(Forecast(
                id=f"{cfg.key}:{ev_id}", sport=cfg.key, sport_name=cfg.name, event_id=ev_id,
                commence_time=ctx.event.commence_time.isoformat(), home=ctx.home, away=ctx.away,
                home_score=round(home_score, 1), away_score=round(away_score, 1),
                p_home=round(p["home"], 4), p_away=round(p["away"], 4), p_draw=round(p.get("draw", 0.0), 4),
                winner=winner, model_home=round(pred.p_home, 4),
                market_home=round(market["home"], 4) if market else None,
                facts=facts[:10], updated_at=now.isoformat(),
            ))
    return out


def merge(stored: dict[str, Forecast], fresh: list[Forecast], now: datetime) -> None:
    """Update forecasts for games not yet started; a started game's forecast is frozen."""
    for f in fresh:
        old = stored.get(f.id)
        if old is not None and (old.start <= now or old.status != "pending"):
            continue
        stored[f.id] = f
    cutoff = now - timedelta(days=KEEP_DAYS)
    for k in [k for k, f in stored.items() if f.start < cutoff]:
        del stored[k]


def grade(stored: dict[str, Forecast], results_for, now: datetime) -> int:
    """Live scores and final grading against ESPN results. Returns forecasts graded."""
    from .ledger import find_game

    open_ = [f for f in stored.values() if f.status in ("pending", "live") and f.start <= now]
    by_sport: dict[str, list[Forecast]] = {}
    for f in open_:
        by_sport.setdefault(f.sport, []).append(f)
    graded = 0
    for sport, items in by_sport.items():
        start = min(f.start for f in items) - timedelta(days=1)
        try:
            results = results_for(sport, start, now + timedelta(days=1))
        except Exception:  # scores unavailable this run: try again next run
            continue
        for f in items:
            game = find_game(results, f)
            if game is None:
                if now - f.start > timedelta(days=4):
                    f.status, f.graded_at = "void", now.isoformat()
                continue
            if game.completed:
                f.final_home, f.final_away = game.home_score, game.away_score
                actual = "home" if game.home_score > game.away_score else "away" if game.away_score > game.home_score else "draw"
                f.correct = actual == f.winner
                f.status, f.live, f.graded_at = "final", None, now.isoformat()
                f.facts = []  # the record keeps the result; the reasons were for before the game
                graded += 1
            elif game.state == "in" or game.live_home is not None:
                f.status = "live"
                f.live = {"home": game.live_home, "away": game.live_away, "detail": game.detail}
    return graded


def record(stored: dict[str, Forecast]) -> dict:
    """How the predictions have done: winners picked, and how close the scores came."""
    done = [f for f in stored.values() if f.status == "final" and f.correct is not None]

    def summary(items):
        right = sum(1 for f in items if f.correct)
        err = [abs(f.home_score - f.final_home) + abs(f.away_score - f.final_away) for f in items]
        return {"right": right, "wrong": len(items) - right, "pct": right / len(items) if items else None,
                "score_error": sum(err) / len(err) / 2 if err else None}

    by_sport = {}
    for s in sorted({f.sport for f in done}):
        mine = [f for f in done if f.sport == s]
        by_sport[s] = {"name": mine[0].sport_name, **summary(mine)}
    return {"overall": summary(done), "by_sport": by_sport}


def whole_score(f: Forecast) -> tuple[int, int]:
    return _whole(f.home_score, f.away_score, f.winner)


def fair_odds(p: float) -> str:
    """A win chance as American odds, for the card."""
    if p <= 0 or p >= 1:
        return "–"
    return f"{-100 * p / (1 - p):+.0f}" if p >= 0.5 else f"{100 * (1 - p) / p:+.0f}"


__all__ = ["Forecast", "build", "merge", "grade", "record", "whole_score", "fair_odds", "implied_prob"]
