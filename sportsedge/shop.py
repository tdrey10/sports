"""Price shopping for the money picks (owner, Oct 25: "go ahead with all four" - step 3).

The board reads one book (DraftKings, through ESPN's free feed). Before a new money pick posts, the
moneylines at every US book The Odds API carries are checked (one request per sport, from the free
plan's 500 a month; never below the credit floor), and the pick takes the best price on offer. The
book and the price it beat go on the card.

Spreads and totals too (owner, Oct 28: "scan the entire betting market... best prices on moneylines,
spreads, totals"): one more request per sport (two credits: both markets), only when a new money pick
is a spread or a total. A book wins only if it is at least as good on BOTH the line and the price
(the same number at a better price, or a better number at the same price or better), so the move
never trades half a point for a few cents.
"""

from __future__ import annotations

import logging
import os
from dataclasses import replace
from datetime import datetime, timezone

from .data.base import NameMatcher
from .odds import american_to_decimal
from .sharp import BOOK_NAMES, CREDIT_FLOOR, US_BOOKS, SharpClient, _prices, fresh
from .sports import SPORTS

log = logging.getLogger(__name__)


def best_book(ev: dict, side: str, now: datetime | None = None) -> tuple[str, float] | None:
    """(book, price) with the best moneyline for a side among the US books with fresh prices."""
    best = None
    for book, markets in _prices(ev, now).items():
        if book not in US_BOOKS:
            continue  # Pinnacle can't be bet from the US
        q = markets.get(("h2h", None), {}).get(side)
        if q and (best is None or q[0] > best[1]):
            best = (book, q[0])
    return best


def _quotes(ev: dict, market: str, side: str, now: datetime | None) -> list[tuple[str, float, float]]:
    """(book, the side's own point, price) at every fresh US book for a spread or total side."""
    want = {"home": ev.get("home_team"), "away": ev.get("away_team"), "over": "Over", "under": "Under"}[side]
    out = []
    for b in ev.get("bookmakers", []):
        if b.get("key") not in US_BOOKS or not fresh(b, now):
            continue
        for m in b.get("markets", []):
            if m.get("key") != market:
                continue
            for o in m.get("outcomes", []):
                if o.get("name") == want and o.get("point") is not None:
                    out.append((b["key"], float(o["point"]), float(o["price"])))
    return out


def best_line(ev: dict, market: str, side: str, point: float, american: float,
              now: datetime | None = None) -> tuple[str, float, float] | None:
    """(book, point, price) that beats ours on the line or the price and is no worse on the other, or None.
    More points is better on a spread and on an under; fewer on an over. A number that crosses zero is
    left alone (the card's wording can't follow it)."""
    sign = -1 if side == "over" else 1
    best = None
    for book, pt, price in _quotes(ev, market, side, now):
        if (pt > 0) != (point > 0) and pt != point:
            continue
        line_gain = sign * (pt - point)
        if line_gain < 0 or price < american or (line_gain == 0 and price == american):
            continue
        if best is None or (sign * best[1], best[2]) < (sign * pt, price):
            best = (book, pt, price)
    return best


def _fmt(point: float) -> str:
    return f"{abs(point):g}"


def _match(events: list[dict], c) -> dict | None:
    m = NameMatcher([c.home, c.away])
    return next((e for e in events if m.match(e.get("home_team", "")) == c.home
                 and m.match(e.get("away_team", "")) == c.away), None)


def _shop_moneyline(c, ev: dict, now: datetime) -> bool:
    found = best_book(ev, c.pick.side, now)
    if found is None:
        return False
    book, price = found
    name = BOOK_NAMES.get(book, book)
    n = len({b for b in _prices(ev, now) if b in US_BOOKS})
    if price > c.pick.american:
        old_book, old = c.pick.book, c.pick.american
        c.pick = replace(c.pick, american=price, book=name,
                         ev=c.pick.win_prob * american_to_decimal(price) - 1)
        c.headline = c.headline.replace(f"{old:+.0f}", f"{price:+.0f}")  # it was written at the board's price
        c.reasons.insert(1, f"💸 Best price: {name} {price:+.0f} ({BOOK_NAMES.get(old_book, old_book)} only had {old:+.0f}). "
                            f"We checked {n} sportsbooks, so bet it at {name}.")
        return True
    c.reasons.insert(1, f"💸 We checked {n} sportsbooks: {BOOK_NAMES.get(c.pick.book, c.pick.book)} {c.pick.american:+.0f} is the best price out there.")
    return False


def _shop_line(c, ev: dict, now: datetime) -> bool:
    pk = c.pick
    n = len({q[0] for q in _quotes(ev, pk.market, pk.side, now)})
    if not n:
        return False
    found = best_line(ev, pk.market, pk.side, pk.point, pk.american, now)
    if found is None:
        c.reasons.insert(1, f"💸 Checked {n} books: {pk.book} {_fmt(pk.point)} at {pk.american:+.0f} is the best number.")
        return False
    book, pt, price = found
    name = BOOK_NAMES.get(book, book)
    old_book, old_pt, old = pk.book, pk.point, pk.american
    # The win chance stays at the board's number: a better line only adds to it, so this is conservative.
    sel = pk.selection.replace(_fmt(old_pt), _fmt(pt))
    c.pick = replace(pk, point=pt, american=price, book=name, ev=pk.win_prob * american_to_decimal(price) - 1,
                     selection=sel)
    c.headline = c.headline.replace(f"{pk.selection} at {old:+.0f}", f"{sel} at {price:+.0f}")
    c.reasons.insert(1, f"💸 Best number: {name} {_fmt(pt)} at {price:+.0f} (the board had {old_book} "
                        f"{_fmt(old_pt)} at {old:+.0f}). Checked {n} books. Bet it there.")
    return True


def shop(cards, is_new=lambda c: True, client: SharpClient | None = None) -> int:
    """Move each new money pick to the best US price. Returns how many got a better number."""
    def wanted(c):
        pk = c.pick
        ok = (pk.market == "h2h" and pk.side in ("home", "away")) or \
             (pk.market in LINE_MARKETS and pk.point is not None and not pk.player)
        return c.units > 0 and ok and c.sport in SPORTS and SPORTS[c.sport].odds_key and is_new(c)

    todo = [c for c in cards if wanted(c)]
    if not todo or not (client or os.environ.get("ODDS_API_KEY")):
        return 0
    client = client or SharpClient()
    now = datetime.now(timezone.utc)
    better = 0
    groups = dict.fromkeys((c.sport, c.pick.market == "h2h") for c in todo)
    for sport, is_ml in groups:
        markets = ("h2h",) if is_ml else LINE_MARKETS
        if client.remaining is not None and client.remaining - CREDIT_FLOOR < len(markets):
            log.info("price shopping skipped: credits at the floor")
            break
        try:
            events = client.odds(SPORTS[sport], markets)
        except Exception as exc:  # shopping never stops a pick from posting
            log.warning("price shopping %s failed: %s", sport, exc)
            continue
        for c in (c for c in todo if c.sport == sport and (c.pick.market == "h2h") == is_ml):
            ev = _match(events, c)
            if ev is not None:
                better += (_shop_moneyline if is_ml else _shop_line)(c, ev, now)
    return better


LINE_MARKETS = ("spreads", "totals")


__all__ = ["best_book", "best_line", "shop"]
