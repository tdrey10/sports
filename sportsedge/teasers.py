"""The best NFL teasers: 6-point, two and three legs, built only from the proven leg kind.

The 20-season test (nflhist.teaser_legs, rerun every morning) found one kind of teaser leg
that beats the price in both the tuning seasons and the blind ones: a small dog, +1.5 to
+2.5, teased up to +7.5/+8.5 so it crosses both 3 and 7, the most common NFL margins
("Wong" dogs; 77% blind, 79% with a total of 49 or less). A 2-team teaser at -120 needs
73.9% per leg, a 3-team at +160 needs 72.7%. Favorites teased through 7 and 3 stopped
working in the recent seasons, so they are left out.

Each day the best legs on the board (low total first, then our model's chance to cover the
teased line) make one 2-team and, with three legs, one 3-team teaser. They carry units only
while the leg kind stays proven; they are graded leg by leg (a pushed leg drops out).
"""

from __future__ import annotations

import statistics
from dataclasses import asdict, dataclass, fields
from datetime import datetime, timedelta
from types import SimpleNamespace

from .nflhist import PROVEN_BETS, TEASE, TEASER_BREAK_EVEN, TEASER_LEGS, TEASER_LOW_TOTAL, teaser_kind
from .odds import american_to_decimal

PRICES = {2: -120.0, 3: 160.0, 6: 550.0}  # 6-point teaser prices: standard 2/3-team, the owner's book's 6-team
UNITS = {2: 1.0, 3: 0.5, 6: 0.25}
REDUCED = {2: -120.0, 3: 160.0, 4: 250.0, 5: 400.0}  # a pushed leg drops a teaser to the next size down
BIG = 6  # the 6-team teaser: the proven dogs first, the next-best leg kinds by their 20-season record after
LOOKAHEAD_DAYS = 7
KIND = "wong_dog"


@dataclass
class Teaser:
    id: str
    day: str
    size: int
    odds: float
    units: float
    legs: list[dict]
    created_at: str
    leg_rate: float | None = None  # the study's blind cover rate for this kind of leg
    proven: bool = False
    hit: float | None = None  # chance every leg covers, from each leg kind's record
    status: str = "pending"  # pending, live, won, lost, push
    profit: float | None = None  # units (no-unit teasers as if 1 unit)
    graded_at: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Teaser":
        names = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in names})

    @property
    def hit_chance(self) -> float | None:
        if self.hit is not None:
            return self.hit
        return self.leg_rate ** self.size if self.leg_rate else None


def _median(xs):
    return statistics.median(xs) if xs else None


def leg_rates(study: dict | None) -> dict[str, tuple[float, bool]]:
    """Leg kind -> (conservative cover rate, proven). The lower of the tuning and blind rates,
    so a kind that only looked good in one era is not trusted on that era."""
    out = {}
    for kind, d in ((study or {}).get("teasers") or {}).items():
        t, b = d.get("in_sample") or {}, d.get("blind") or {}
        if (b.get("legs") or 0) < PROVEN_BETS or t.get("rate") is None or b.get("rate") is None:
            continue
        out[kind] = (min(t["rate"], b["rate"]), bool(d.get("proven")))
    return out


def candidate_legs(scans, now: datetime, rates: dict[str, tuple[float, bool]] | None = None) -> list[dict]:
    """Every NFL side on the board with a known teaser leg kind, best record first."""
    rates = rates or {}
    out = []
    for scan in scans:
        if scan.sport.key != "nfl":
            continue
        for ev_id, pred in scan.predictions.items():
            ctx = scan.contexts.get(ev_id)
            if ctx is None or not (now < ctx.event.commence_time <= now + timedelta(days=LOOKAHEAD_DAYS)):
                continue
            if ctx.injuries_unknown:
                continue  # the blueprint: injuries unknown, no bet
            quotes = ctx.event.quotes
            total = _median([q.point for q in quotes if q.market == "totals" and q.side == "over" and q.point is not None])
            for side, team in (("home", ctx.home), ("away", ctx.away)):
                line = _median([q.point for q in quotes if q.market == "spreads" and q.side == side
                                and q.point is not None])
                kinds = teaser_kind(line, total) if line is not None else []
                if not kinds:
                    continue
                base = kinds[0]
                best = max((rates[k] for k in kinds if k in rates), default=None, key=lambda r: r[0])
                if base == KIND and best is None:
                    best = (None, False)
                if best is None:
                    continue
                teased = line + TEASE
                cover, push, _ = pred.spread(side, teased)
                out.append({
                    "event_id": ev_id, "team": team, "side": side, "home": ctx.home, "away": ctx.away,
                    "commence_time": ctx.event.commence_time.isoformat(), "point": line, "teased": teased,
                    "total": total, "low_total": total is not None and total <= TEASER_LOW_TOTAL,
                    "kind": base, "rate": best[0], "proven": best[1],
                    "model_cover": round(cover / max(1 - push, 1e-9), 4), "status": "pending", "final": None,
                })
    out.sort(key=lambda leg: (-(leg["rate"] or 0), not leg["low_total"], -leg["model_cover"]))
    return out


def build(scans, stored: list[Teaser], study: dict | None, now: datetime, day: str) -> list[Teaser]:
    """Today's teasers, each size posted once a day, never reusing a leg already in an open one:
    a 2-team and a 3-team from the proven dogs only, and a 6-team at +550 (the owner's book)
    that leads with the proven dogs and fills with the next-best leg kinds, posted only when the
    legs' records together beat the +550 price."""
    kind = ((study or {}).get("teasers") or {}).get(KIND) or {}
    if not kind:
        return []  # no study yet: no teaser
    rates = leg_rates(study)
    proven = bool(kind.get("proven"))
    rate = (kind.get("blind") or {}).get("rate")
    open_legs = {(leg["event_id"], leg["side"]) for t in stored if t.status in ("pending", "live") for leg in t.legs}
    have = {t.size for t in stored if t.day == day}
    legs = [leg for leg in candidate_legs(scans, now, rates) if (leg["event_id"], leg["side"]) not in open_legs]
    seen, unique = set(), []  # one leg per game
    for leg in legs:
        if leg["event_id"] not in seen:
            seen.add(leg["event_id"])
            unique.append(leg)
    dogs = [leg for leg in unique if leg["kind"] == KIND]
    out = []
    for size in (2, 3):
        if size in have or len(dogs) < size:
            continue
        if rate is not None and rate <= TEASER_BREAK_EVEN[size]:
            continue  # the price needs more than this kind of leg has been hitting
        pick = [dict(leg) for leg in dogs[:size]]
        out.append(Teaser(
            id=f"teaser:{day}:{size}", day=day, size=size, odds=PRICES[size],
            units=UNITS[size] if proven else 0.0, legs=pick, created_at=now.isoformat(),
            leg_rate=rate, proven=proven, hit=rate ** size if rate else None,
        ))
    if BIG not in have and len(unique) >= BIG:
        pick = [dict(leg) for leg in unique[:BIG]]
        if all(leg["rate"] for leg in pick):
            hit = 1.0
            for leg in pick:
                hit *= leg["rate"]
            if hit * american_to_decimal(PRICES[BIG]) > 1:  # the legs' records beat the +550 price
                all_proven = all(leg["proven"] for leg in pick)
                out.append(Teaser(
                    id=f"teaser:{day}:{BIG}", day=day, size=BIG, odds=PRICES[BIG],
                    units=UNITS[BIG] if all_proven else 0.0, legs=pick, created_at=now.isoformat(),
                    leg_rate=round(sum(leg["rate"] for leg in pick) / BIG, 4), proven=all_proven, hit=round(hit, 4),
                ))
    return out


def _leg_result(leg: dict, game) -> str:
    margin = game.home_score - game.away_score if leg["side"] == "home" else game.away_score - game.home_score
    res = margin + leg["teased"]
    return "won" if res > 0 else "push" if res == 0 else "lost"


def grade(stored: list[Teaser], results_for, now: datetime) -> int:
    from .ledger import find_game

    open_ = [t for t in stored if t.status in ("pending", "live")]
    if not open_:
        return 0
    starts = [datetime.fromisoformat(leg["commence_time"]) for t in open_ for leg in t.legs]
    started = [s for s in starts if s <= now]
    if not started:
        return 0
    try:
        results = results_for("nfl", min(started) - timedelta(days=1), now + timedelta(days=1))
    except Exception:  # scores unavailable this run
        return 0
    graded = 0
    for t in open_:
        for leg in t.legs:
            if leg["status"] in ("won", "lost", "push"):
                continue
            start = datetime.fromisoformat(leg["commence_time"])
            if start > now:
                continue
            game = find_game(results, SimpleNamespace(start=start, home=leg["home"], away=leg["away"]))
            if game is None:
                if now - start > timedelta(days=4):
                    leg["status"] = "push"  # never played: the leg drops out
                continue
            if game.completed:
                leg["status"] = _leg_result(leg, game)
                leg["final"] = f"{leg['away']} {game.away_score:.0f} @ {leg['home']} {game.home_score:.0f}"
            else:
                leg["status"] = "live"
        statuses = [leg["status"] for leg in t.legs]
        stake = t.units if t.units > 0 else 1.0
        if "lost" in statuses:
            t.status, t.profit = "lost", -stake
        elif all(s in ("won", "push") for s in statuses):
            alive = statuses.count("won")
            if alive < 2:
                t.status, t.profit = "push", 0.0
            else:
                price = t.odds if alive == t.size else REDUCED.get(alive, PRICES.get(alive, t.odds))
                t.status, t.profit = "won", round(stake * (american_to_decimal(price) - 1), 4)
        else:
            t.status = "live" if "live" in statuses or any(s != "pending" for s in statuses) else "pending"
            continue
        t.graded_at = now.isoformat()
        graded += 1
    return graded


def record(stored: list[Teaser]) -> dict:
    out = {}
    for size in (2, 3, BIG):
        done = [t for t in stored if t.size == size and t.status in ("won", "lost")]
        money = [t for t in done if t.units > 0]
        out[str(size)] = {
            "won": sum(t.status == "won" for t in done), "lost": sum(t.status == "lost" for t in done),
            "units": round(sum(t.profit or 0 for t in money), 2),
        }
    return out


__all__ = ["Teaser", "build", "grade", "record", "candidate_legs", "PRICES"]
