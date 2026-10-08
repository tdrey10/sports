"""The pick ledger: every published pick, its line movement, live status and
final grade. It is what turns "trust me" into a verifiable record.

Stored as a small JSON file so it can be committed by a scheduled job.
"""

from __future__ import annotations

import hashlib
import math
import json
import os
from collections import defaultdict
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Iterable
from zoneinfo import ZoneInfo

from .data.base import NameMatcher
from .distributions import line_probs
from .edge import Pick
from .engine import SportScan
from .models.props import PlayerGame, stat_value
from .models.ratings import GameResult
from .odds import american_to_decimal
from .sports import SPORTS
from .tiers import Card

DAY_TIERS = ("lock", "dog", "prop")
GRADE_AFTER = timedelta(hours=2)  # earliest a game can be final


@dataclass
class Entry:
    id: str
    created_at: str
    day: str
    tier: str
    units: float
    sport: str
    sport_name: str
    event_id: str
    commence_time: str
    home: str
    away: str
    matchup: str
    market: str
    market_label: str
    side: str
    point: float | None
    player: str | None
    selection: str
    headline: str
    reasons: list[str]
    book: str
    odds: float
    model_prob: float
    win_prob: float
    ev: float
    market_prob: float | None = None
    best_rank: int | None = None
    current_odds: float | None = None
    current_point: float | None = None
    current_book: str | None = None
    history: list[list] = field(default_factory=list)  # [iso time, best odds, point]
    close_odds: float | None = None
    close_fair: float | None = None  # consensus no-vig prob of our side at last look before start
    status: str = "pending"  # pending, live, won, lost, push, half_won, half_lost, void
    live: dict | None = None
    final: str | None = None
    result_value: float | None = None
    profit: float | None = None  # in units (leans: as if 1 unit)
    graded_at: str | None = None

    @property
    def start(self) -> datetime:
        return datetime.fromisoformat(self.commence_time)

    @property
    def is_unit_play(self) -> bool:
        return self.units > 0

    @property
    def settled(self) -> bool:
        return self.status not in ("pending", "live")

    @property
    def clv(self) -> float | None:
        """Expected value of our price against the closing no-vig line."""
        if self.close_fair is None:
            return None
        return self.close_fair * american_to_decimal(self.odds) - 1

    def to_dict(self) -> dict:
        d = asdict(self)
        d["clv"] = self.clv
        return d


def entry_id(pick: Pick) -> str:
    raw = "|".join(map(str, (pick.sport, pick.event_id, pick.market, pick.side, pick.player)))
    return hashlib.sha1(raw.encode()).hexdigest()[:16]


class Ledger:
    def __init__(self, entries: Iterable[Entry] = (), tz: str = "America/Los_Angeles", start_bankroll: float = 1000.0):
        self.entries: dict[str, Entry] = {e.id: e for e in entries}
        self.tz = ZoneInfo(tz)
        self.start_bankroll = start_bankroll

    # ------------------------------------------------------------- storage
    @classmethod
    def load(cls, path: str | os.PathLike, **kw) -> "Ledger":
        path = Path(path)
        if not path.exists():
            return cls(**kw)
        data = json.loads(path.read_text())
        names = {f.name for f in fields(Entry)}
        entries = [Entry(**{k: v for k, v in e.items() if k in names}) for e in data.get("entries", [])]
        kw.setdefault("start_bankroll", data.get("start_bankroll", 1000.0))
        return cls(entries, **kw)

    def save(self, path: str | os.PathLike) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "start_bankroll": self.start_bankroll,
            "entries": [asdict(e) for e in sorted(self.entries.values(), key=lambda e: (e.commence_time, e.id))],
        }
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=1))
        tmp.replace(path)

    # ------------------------------------------------------------- helpers
    def day_of(self, when: datetime) -> str:
        return when.astimezone(self.tz).date().isoformat()

    def best_taken(self, now: datetime) -> int:
        today = self.day_of(now)
        return sum(1 for e in self.entries.values() if e.day == today and e.best_rank)

    def taken_today(self, now: datetime) -> set[str]:
        today = self.day_of(now)
        return {e.tier for e in self.entries.values() if e.day == today and e.tier in DAY_TIERS}

    # ------------------------------------------------------------- publish
    def publish(self, cards: list[Card], now: datetime) -> list[Entry]:
        """Add new cards. A pick already in the ledger keeps its original price,
        and each game gets at most one game-line unit play."""
        added = []
        unit_events = {e.event_id for e in self.entries.values() if e.is_unit_play and e.player is None}
        today = self.day_of(now)
        ranked_today = [e for e in self.entries.values() if e.day == today and e.best_rank]
        next_rank = max((e.best_rank for e in ranked_today), default=0) + 1
        for card in sorted(cards, key=lambda c: c.best_rank or 99):
            p = card.pick
            eid = entry_id(p)
            if eid in self.entries:
                existing = self.entries[eid]
                # Promote an open unit play that is now one of today's best bets.
                if card.best_rank and not existing.best_rank and existing.is_unit_play \
                        and existing.status == "pending" and existing.start > now and next_rank <= 5:
                    existing.best_rank, existing.day = next_rank, today
                    next_rank += 1
                continue
            if p.player is None and card.units > 0:
                if p.event_id in unit_events:
                    continue
                unit_events.add(p.event_id)
            e = Entry(
                id=eid,
                created_at=now.isoformat(),
                day=self.day_of(now),
                tier=card.tier,
                units=card.units,
                sport=p.sport,
                sport_name=card.sport_name,
                event_id=p.event_id,
                commence_time=p.commence_time.isoformat(),
                home=card.home,
                away=card.away,
                matchup=p.matchup,
                market=p.market,
                market_label=p.market_label,
                side=p.side,
                point=p.point,
                player=p.player,
                selection=p.selection,
                headline=card.headline,
                reasons=card.reasons,
                book=p.book,
                odds=p.american,
                model_prob=p.model_prob,
                market_prob=p.market_prob,
                best_rank=None,
                win_prob=p.win_prob,
                ev=p.ev,
                current_odds=p.american,
                current_point=p.point,
                current_book=p.book,
                history=[[now.isoformat(), p.american, p.point]],
                close_odds=p.american,
                close_fair=p.market_prob,
            )
            if card.best_rank and card.units > 0 and next_rank <= 5:
                e.best_rank = next_rank
                next_rank += 1
            self.entries[eid] = e
            added.append(e)
        return added

    # -------------------------------------------------------- line movement
    def update_prices(self, scans: list[SportScan], now: datetime) -> None:
        """Track the best current price on each pending pick until it starts."""
        index: dict[tuple, list[Pick]] = defaultdict(list)
        for scan in scans:
            for p in scan.picks:
                index[(p.sport, p.event_id, p.market, p.side, p.player)].append(p)
        for e in self.entries.values():
            if e.settled or e.start <= now:
                continue
            matches = index.get((e.sport, e.event_id, e.market, e.side, e.player))
            if not matches:
                continue
            same = [p for p in matches if p.point == e.point]
            pick = same[0] if same else min(matches, key=lambda p: abs((p.point or 0) - (e.point or 0)))
            e.current_odds, e.current_point, e.current_book = pick.american, pick.point, pick.book
            if same:
                e.close_odds, e.close_fair = pick.american, pick.market_prob
            last = e.history[-1] if e.history else None
            if not last or last[1] != pick.american or last[2] != pick.point:
                e.history.append([now.isoformat(), pick.american, pick.point])
                e.history = e.history[-48:]

    # -------------------------------------------------------------- grading
    def refresh(
        self,
        results_for: Callable[[str, datetime, datetime], list[GameResult]],
        box_score_for: Callable[[str, GameResult], list[PlayerGame]],
        now: datetime,
    ) -> int:
        """Update live scores and grade finished picks. Returns picks graded."""
        open_entries = [e for e in self.entries.values() if not e.settled and e.start <= now]
        by_sport: dict[str, list[Entry]] = defaultdict(list)
        for e in open_entries:
            by_sport[e.sport].append(e)
        graded = 0
        for sport, entries in by_sport.items():
            start = min(e.start for e in entries) - timedelta(days=1)
            results = results_for(sport, start, now + timedelta(days=1))
            boxes: dict[str, list[PlayerGame]] = {}
            for e in entries:
                game = find_game(results, e)
                if game is None:
                    if now - e.start > timedelta(days=4):
                        e.status, e.graded_at = "void", now.isoformat()  # postponed / never played
                        e.profit = 0.0
                    continue
                if game.completed and now - e.start >= GRADE_AFTER:
                    box = None
                    if e.player is not None:
                        if game.event_id not in boxes:
                            boxes[game.event_id] = box_score_for(sport, game)
                        box = boxes[game.event_id]
                    grade(e, game, box, now)
                    graded += 1
                elif game.state == "in" or game.live_home is not None:
                    e.status = "live"
                    e.live = {
                        "home": game.live_home, "away": game.live_away, "detail": game.detail,
                    }
        return graded

    # ------------------------------------------------------------- learning
    def learn_weights(self, min_picks: int = 60) -> dict[str, float]:
        """Learn how much to trust the market vs. the model from graded picks.

        For each sport and market group (game lines / props) with enough
        graded picks, pick the blend weight whose probabilities would have
        had the lowest log loss on what actually happened. Leans count too,
        so the engine learns from its whole slate, not just unit plays.
        """
        groups: dict[str, list[tuple[float, float, float]]] = defaultdict(list)
        for e in self.entries.values():
            if e.status not in ("won", "lost") or e.market_prob is None:
                continue
            key = f"{e.sport}:{'prop' if e.player else 'game'}"
            groups[key].append((e.model_prob, e.market_prob, 1.0 if e.status == "won" else 0.0))
        learned = {}
        for key, rows in groups.items():
            if len(rows) < min_picks:
                continue
            best_w, best_loss = None, float("inf")
            for i in range(21):
                w = i / 20
                loss = 0.0
                for m, k, y in rows:
                    q = min(max((1 - w) * m + w * k, 1e-4), 1 - 1e-4)
                    loss -= y * math.log(q) + (1 - y) * math.log(1 - q)
                if loss < best_loss - 1e-12:
                    best_w, best_loss = w, loss
            # Never fully ignore either source on a finite sample.
            learned[key] = min(max(best_w, 0.2), 0.9)
        return learned

    # ---------------------------------------------------------------- stats
    def stats(self, now: datetime) -> dict:
        unit = self.start_bankroll / 100.0
        settled = [e for e in self.entries.values() if e.settled and e.status != "void"]
        plays = [e for e in settled if e.is_unit_play]
        leans = [e for e in settled if not e.is_unit_play]

        def summary(items: list[Entry]) -> dict:
            won = sum(1 for e in items if e.status in ("won", "half_won"))
            lost = sum(1 for e in items if e.status in ("lost", "half_lost"))
            push = sum(1 for e in items if e.status == "push")
            risked = sum(e.units for e in items)
            profit = sum(e.profit or 0 for e in items)
            clvs = [e.clv for e in items if e.clv is not None]
            return {
                "won": won, "lost": lost, "push": push,
                "win_pct": won / (won + lost) if won + lost else None,
                "units": round(profit, 2),
                "risked": round(risked, 2),
                "roi": profit / risked if risked else None,
                "dollars": round(profit * unit, 2),
                "clv": sum(clvs) / len(clvs) if clvs else None,
                "beat_close": sum(1 for c in clvs if c > 0) / len(clvs) if clvs else None,
            }

        week_ago = now - timedelta(days=7)
        by_tier = {t: summary([e for e in plays if e.tier == t]) for t in ("lock", "dog", "prop", "value", "early")}
        by_sport = {}
        for sport in sorted({e.sport for e in plays}):
            by_sport[sport] = {"name": SPORTS[sport].name if sport in SPORTS else sport,
                               **summary([e for e in plays if e.sport == sport])}

        curve, bank = [], self.start_bankroll
        for e in sorted(plays, key=lambda e: e.graded_at or ""):
            bank += (e.profit or 0) * unit
            curve.append([e.graded_at, round(bank, 2)])

        lean_won = sum(1 for e in leans if e.status in ("won", "half_won"))
        lean_lost = sum(1 for e in leans if e.status in ("lost", "half_lost"))
        return {
            "unit_value": unit,
            "start_bankroll": self.start_bankroll,
            "bankroll": round(self.start_bankroll + sum(e.profit or 0 for e in plays) * unit, 2),
            "overall": summary(plays),
            "last7": summary([e for e in plays if e.start >= week_ago]),
            "by_tier": by_tier,
            "best": summary([e for e in plays if e.best_rank]),
            "by_market": {
                m: summary([e for e in plays if _market_group(e) == m])
                for m in ("spreads", "totals", "props", "moneyline")
            },
            "by_sport": by_sport,
            "leans": {"won": lean_won, "lost": lean_lost,
                      "win_pct": lean_won / (lean_won + lean_lost) if lean_won + lean_lost else None},
            "curve": curve,
        }


def _market_group(e: "Entry") -> str:
    if e.player:
        return "props"
    return {"spreads": "spreads", "totals": "totals"}.get(e.market, "moneyline")


def find_game(results: list[GameResult], e: Entry) -> GameResult | None:
    start = e.start
    near = [g for g in results if abs((g.date - start).total_seconds()) < 36 * 3600]
    for g in near:
        if g.home == e.home and g.away == e.away:
            return g
    # Fall back to fuzzy names (e.g. the ledger was written by a different source).
    matcher = NameMatcher({g.home for g in near} | {g.away for g in near})
    home, away = matcher.match(e.home), matcher.match(e.away)
    for g in near:
        if g.home == home and g.away == away:
            return g
    return None


def grade(e: Entry, game: GameResult, box: list[PlayerGame] | None, now: datetime) -> None:
    hs, as_ = game.home_score, game.away_score
    e.final = f"{e.away} {as_:.0f} @ {e.home} {hs:.0f}"
    e.live = None
    e.graded_at = now.isoformat()
    if e.player is not None:
        value = _prop_value(e, box or [])
        if value is None:
            e.status, e.profit = "void", 0.0  # did not play
            return
        e.result_value = value
        outcome = _settle(value, -e.point) if e.side == "over" else _settle(-value, e.point)
    elif e.market == "h2h":
        winner = "home" if hs > as_ else "away" if as_ > hs else "draw"
        outcome = (1.0, 0.0, 0.0) if winner == e.side else (0.0, 0.0, 1.0)
    elif e.market == "spreads":
        margin = hs - as_ if e.side == "home" else as_ - hs
        outcome = _settle(margin, e.point)
    elif e.market == "totals":
        total = hs + as_
        outcome = _settle(total, -e.point) if e.side == "over" else _settle(-total, e.point)
    else:
        e.status, e.profit = "void", 0.0
        return
    win, push, loss = outcome
    stake = e.units if e.units > 0 else 1.0
    e.profit = round(stake * (win * (american_to_decimal(e.odds) - 1) - loss), 4)
    if win == 1:
        e.status = "won"
    elif loss == 1:
        e.status = "lost"
    elif push == 1:
        e.status = "push"
    elif win > 0:
        e.status = "half_won"
    else:
        e.status = "half_lost"


def _settle(x: float, line: float) -> tuple[float, float, float]:
    """(win, push, loss) shares for an exact result; quarter lines split in half."""
    return line_probs({0: 1.0}, x + line)


def _prop_value(e: Entry, box: list[PlayerGame]) -> float | None:
    spec = SPORTS[e.sport].props.get(e.market) if e.sport in SPORTS else None
    if spec is None:
        return None
    players = {pg.player: pg for pg in box}
    name = NameMatcher(players, threshold=0.88).match(e.player or "")
    if name is None:
        return None
    return stat_value(spec, players[name].stats)
