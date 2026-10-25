"""Price shopping for the money picks (owner, Oct 25: "go ahead with all four" - step 3).

The board reads one book (DraftKings, through ESPN's free feed). Before a new money pick posts, the
moneylines at every US book The Odds API carries are checked (one request per sport, from the free
plan's 500 a month; never below the credit floor), and the pick takes the best price on offer. The
book and the price it beat go on the card. Spreads and totals stay at the board's book: their
lines differ by book, and each extra market costs another request.
"""

from __future__ import annotations

import logging
import os
from dataclasses import replace

from .data.base import NameMatcher
from .odds import american_to_decimal
from .sharp import BOOK_NAMES, CREDIT_FLOOR, US_BOOKS, SharpClient, _prices
from .sports import SPORTS

log = logging.getLogger(__name__)


def best_book(ev: dict, side: str) -> tuple[str, float] | None:
    """(book, price) with the best moneyline for a side among the US books."""
    best = None
    for book, markets in _prices(ev).items():
        if book not in US_BOOKS:
            continue  # Pinnacle can't be bet from the US
        q = markets.get(("h2h", None), {}).get(side)
        if q and (best is None or q[0] > best[1]):
            best = (book, q[0])
    return best


def shop(cards, is_new=lambda c: True, client: SharpClient | None = None) -> int:
    """Move each new money moneyline to the best US price. Returns how many got a better number."""
    todo = [c for c in cards if c.units > 0 and c.pick.market == "h2h" and c.pick.side in ("home", "away")
            and c.sport in SPORTS and SPORTS[c.sport].odds_key and is_new(c)]
    if not todo or not (client or os.environ.get("ODDS_API_KEY")):
        return 0
    client = client or SharpClient()
    better = 0
    for sport in dict.fromkeys(c.sport for c in todo):
        if client.remaining is not None and client.remaining - CREDIT_FLOOR < 1:
            log.info("price shopping skipped: credits at the floor")
            break
        try:
            events = client.odds(SPORTS[sport])
        except Exception as exc:  # shopping never stops a pick from posting
            log.warning("price shopping %s failed: %s", sport, exc)
            continue
        for c in (c for c in todo if c.sport == sport):
            m = NameMatcher([c.home, c.away])
            ev = next((e for e in events if m.match(e.get("home_team", "")) == c.home
                       and m.match(e.get("away_team", "")) == c.away), None)
            found = best_book(ev, c.pick.side) if ev else None
            if found is None:
                continue
            book, price = found
            name = BOOK_NAMES.get(book, book)
            n = len({b for b in _prices(ev) if b in US_BOOKS})
            if price > c.pick.american:
                old_book, old = c.pick.book, c.pick.american
                c.pick = replace(c.pick, american=price, book=name,
                                 ev=c.pick.win_prob * american_to_decimal(price) - 1)
                c.headline = c.headline.replace(f"{old:+.0f}", f"{price:+.0f}")  # it was written at the board's price
                c.reasons.insert(1, f"💸 Best price: {name} {price:+.0f} (the board had {old_book} {old:+.0f}). "
                                    f"Checked {n} books. Bet it there.")
                better += 1
            else:
                c.reasons.insert(1, f"💸 Checked {n} books: {c.pick.book} {c.pick.american:+.0f} is the best price.")
    return better


__all__ = ["best_book", "shop"]
