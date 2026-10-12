"""Predictions for every game on the board: a projected score and who we think wins.

Not picks: no price has to be beaten. Each forecast is the best read the data supports:

- projected score and win chances: the Vegas total, spread and moneyline, moved toward our
  team ratings (points scored and allowed, home field, rest, injuries, starting pitchers and
  goalies) by the weight our ratings earned in the blind prediction study (forecaststudy:
  2018+ games, tuned through 2021, tested after). In every sport tested the closing line
  beat our ratings alone, so the ratings move it only as far as they proved they should;
- our ratings' own read beside it, so the disagreements stand out;
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
from .forecaststudy import COUNT, blend, logit
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


def _default_blend(study: dict | None) -> dict | None:
    """For a league with no history test (soccer): the average weights of the tested sports."""
    tested = [blend(study, s) for s in (study or {})]
    tested = [b for b in tested if b]
    if not tested:
        return None
    avg = lambda k: round(sum(b[k] for b in tested) / len(tested), 2)  # noqa: E731
    return {"total": avg("total"), "margin": avg("margin"), "win": avg("win"), "k": None, "post_adj": 0.0,
            "blind": None, "games": 0, "seasons": ""}


def _unit(key: str) -> str:
    return {"mlb": "runs", "nhl": "goals"}.get(key, "goals" if key not in ("nfl", "ncaaf", "nba", "ncaab") else "points")


def build(scans: list[SportScan], settings: Settings, now: datetime, study: dict | None = None) -> list[Forecast]:
    """The best read the data supports for every game: the Vegas line, moved toward our ratings
    (scoring, defense, home field, rest, injuries, starting pitchers and goalies) by the weight
    they earned in the blind prediction study. Our ratings' own read is shown beside it."""
    out = []
    fallback = _default_blend(study)
    for scan in scans:
        cfg = scan.sport
        wts = blend(study, cfg.key) or fallback
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
            market = _consensus_ml(quotes, sides)
            home_line = _median_point(quotes, "spreads", "home")
            total_line = _median_point(quotes, "totals", "over")
            ratings_p = dict(p)
            margin, total = pred.exp_margin, pred.exp_total
            if wts and market:
                w = wts["win"]
                p = {s: w * p[s] + (1 - w) * market[s] for s in sides}
                if total_line is not None:
                    total = wts["total"] * total + (1 - wts["total"]) * total_line
                mkt_margin = None
                if cfg.key in COUNT and wts.get("k"):
                    mkt_margin = wts["k"] * logit(market["home"] / (market["home"] + market["away"]))
                elif home_line is not None and cfg.key not in COUNT:
                    mkt_margin = -home_line
                if mkt_margin is not None:
                    margin = wts["margin"] * margin + (1 - wts["margin"]) * mkt_margin
            winner = max(p, key=p.get)

            if winner == "home" and margin <= 0:
                margin = 0.5
            elif winner == "away" and margin >= 0:
                margin = -0.5
            home_score, away_score = max((total + margin) / 2, 0.0), max((total - margin) / 2, 0.0)

            facts: list[str] = []
            vegas = _vegas_line(ctx.home, ctx.away, home_line, total_line)
            if vegas:
                diff = []
                if home_line is not None:
                    d = pred.exp_margin - (-home_line)
                    if abs(d) >= 1:
                        diff.append(f"our ratings have {ctx.home if d > 0 else ctx.away} {abs(d):.1f} better than the spread")
                if total_line is not None:
                    d = pred.exp_total - total_line
                    if abs(d) >= 1:
                        diff.append(f"our ratings have {abs(d):.1f} {'more' if d > 0 else 'fewer'} {_unit(cfg.key)} than the total")
                facts.append(f"Vegas: {vegas}" + (f" ({'; '.join(diff)})." if diff else " (our ratings agree)."))
            if market:
                facts.append(f"Market win chance: {ctx.away} {market['away']:.0%} · {ctx.home} {market['home']:.0%}"
                             + (f" · draw {market['draw']:.0%}" if 'draw' in market else "") + ".")
            if ctx.wind_mph is not None:
                facts.append(f"Wind forecast {ctx.wind_mph:.0f} mph at an outdoor stadium"
                             + (": windy games score less (the NFL windy under is proven over 20 seasons)."
                                if ctx.wind_mph >= 15 else "."))
            if wts and market:
                rh, ra = max((pred.exp_total + pred.exp_margin) / 2, 0), max((pred.exp_total - pred.exp_margin) / 2, 0)
                facts.append(f"Our ratings alone: {ctx.away} {ra:.1f} @ {ctx.home} {rh:.1f}, "
                             f"{ctx.home} {ratings_p['home']:.0%} to win. The prediction gives them "
                             f"{wts['total']:.0%} of the say on the total and {wts['win']:.0%} on the winner, "
                             "the weight they earned in testing.")
                b = wts.get("blind")
                if b:
                    facts.append(f"Backed by {wts['games']:,} blind-tested games ({wts['seasons']}): this method picked "
                                 f"{b['winners']['blend']:.1%} of winners (Vegas alone {b['winners']['vegas']:.1%}, our "
                                 f"ratings alone {b['winners']['ratings']:.1%}) and missed the total by "
                                 f"{b['total_error']['blend']} {_unit(cfg.key)} on average (ratings alone "
                                 f"{b['total_error']['ratings']}).")
                else:
                    facts.append("No history test for this league yet: it uses the average weights of the tested sports.")
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
                facts=facts[:12], updated_at=now.isoformat(),
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

    open_ = [f for f in stored.values() if f.status in ("pending", "live") and f.start <= now
             and not f.sport.startswith("tennis_")]  # tennis is graded from its own results (tennis.grade)
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
