"""Owner, Oct 25 (step 3): every new money moneyline is shopped across the US books before it posts."""

from sportsedge import shop
from sportsedge.tiers import Card
from tests.test_ledger import make_pick


class Client:
    remaining = 300.0

    def __init__(self):
        self.calls = 0

    def odds(self, cfg, markets=("h2h",)):
        self.calls += 1
        book = lambda key, home, away: {"key": key, "markets": [{"key": "h2h", "outcomes": [  # noqa: E731
            {"name": "Minnesota Timberwolves", "price": home}, {"name": "Toronto Raptors", "price": away}]}]}
        return [{"home_team": "Minnesota Timberwolves", "away_team": "Toronto Raptors", "bookmakers": [
            book("draftkings", -115, -105), book("fanduel", -108, -112), book("pinnacle", -102, -108)]}]


def money(units=1.0, american=-115):
    p = make_pick(market="h2h", point=None, american=american, sport="nba", win_prob=0.552)
    return Card(tier="value", units=units, headline=f"Minnesota Timberwolves ML at {american:+.0f}: off a blowout win.", reasons=["first", "second"], sport="nba", sport_name="NBA",
                pick=p, home="Minnesota Timberwolves", away="Toronto Raptors", proven="STRONG: test")


def test_a_money_pick_moves_to_the_best_us_book_and_says_so():
    c, client = money(), Client()
    assert shop.shop([c], client=client) == 1 and client.calls == 1
    assert (c.pick.book, c.pick.american) == ("FanDuel", -108)
    assert c.headline == "Minnesota Timberwolves ML at -108: off a blowout win."  # Pinnacle's -102 can't be bet from the US
    assert c.reasons[1] == "💸 Best price: FanDuel -108 (FanDuel only had -115). We checked 2 sportsbooks, so bet it at FanDuel."
    assert abs(c.pick.ev - (0.552 * (1 + 100 / 108) - 1)) < 1e-9


def test_leans_and_already_posted_picks_cost_no_credits():
    client = Client()
    assert shop.shop([money(units=0.0), money()], is_new=lambda c: c.units == 0, client=client) == 0
    assert client.calls == 0


def test_a_book_that_has_not_updated_in_30_minutes_is_skipped():
    """Owner's +EV spec: reject stale prices. A stale book can show a 'best price' that isn't really there."""
    from datetime import datetime, timedelta, timezone

    now = datetime(2026, 10, 28, 20, tzinfo=timezone.utc)
    stamp = lambda m: (now - timedelta(minutes=m)).isoformat().replace("+00:00", "Z")  # noqa: E731
    book = lambda key, home, mins: {"key": key, "last_update": stamp(mins), "markets": [{"key": "h2h", "outcomes": [  # noqa: E731
        {"name": "Minnesota Timberwolves", "price": home}, {"name": "Toronto Raptors", "price": -105}]}]}
    ev = {"home_team": "Minnesota Timberwolves", "away_team": "Toronto Raptors",
          "bookmakers": [book("draftkings", -115, 5), book("fanduel", -102, 95), book("betmgm", -110, 10)]}
    assert shop.best_book(ev, "home", now) == ("betmgm", -110)  # FanDuel's -102 is 95 minutes old
    assert shop.best_book(ev, "home") == ("fanduel", -102)  # no clock given: nothing is called stale


class LineClient:
    remaining = 300.0

    def __init__(self):
        self.asked = []

    def odds(self, cfg, markets=("h2h",)):
        self.asked.append(markets)
        def book(key, total, over, under, spread, price):
            return {"key": key, "markets": [
                {"key": "totals", "outcomes": [{"name": "Over", "point": total, "price": over},
                                               {"name": "Under", "point": total, "price": under}]},
                {"key": "spreads", "outcomes": [{"name": "Minnesota Timberwolves", "point": spread, "price": price},
                                                {"name": "Toronto Raptors", "point": -spread, "price": -110}]}]}
        return [{"home_team": "Minnesota Timberwolves", "away_team": "Toronto Raptors", "bookmakers": [
            book("draftkings", 220.5, -110, -110, -3.5, -110), book("fanduel", 221.5, -108, -112, -3.5, -105),
            book("betmgm", 219.5, -125, +100, -2.5, -115), book("pinnacle", 218.5, -100, -100, -1.5, +100)]}]


def line_card(market, side, point, selection, american=-110):
    p = make_pick(market=market, side=side, point=point, american=american, sport="nba", win_prob=0.55)
    p = __import__("dataclasses").replace(p, selection=selection, book="DraftKings")
    return Card(tier="value", units=1.0, headline=f"{selection} at {american:+.0f}: a spot (+13.5% blind).",
                reasons=["first", "second"], sport="nba", sport_name="NBA", pick=p,
                home="Minnesota Timberwolves", away="Toronto Raptors", proven="STRONG: test")


def test_an_over_takes_the_lower_total_at_the_same_price_or_better():
    """Owner, Oct 28: best prices on spreads and totals too. An over at 220.5 -110: FanDuel's 221.5 is a worse
    number, BetMGM's 219.5 is a better number but costs -125, so only a book better on both would move it."""
    c, client = line_card("totals", "over", 220.5, "Over 220.5"), LineClient()
    assert shop.shop([c], client=client) == 0 and client.asked == [("spreads", "totals")]
    assert c.reasons[1] == "💸 Checked 3 books: DraftKings 220.5 at -110 is the best number."


def test_an_under_never_trades_price_for_half_a_point():
    """FanDuel's under is 221.5 (a better number) but at -112 (a worse price): not strictly better, so it stays."""
    c = line_card("totals", "under", 220.5, "Under 220.5")
    assert shop.shop([c], client=LineClient()) == 0 and c.pick.point == 220.5


def test_a_spread_moves_to_the_same_number_at_a_better_price():
    c = line_card("spreads", "home", -3.5, "Minnesota Timberwolves -3.5")
    assert shop.shop([c], client=LineClient()) == 1
    assert (c.pick.book, c.pick.point, c.pick.american) == ("FanDuel", -3.5, -105)
    assert c.headline == "Minnesota Timberwolves -3.5 at -105: a spot (+13.5% blind)."
    assert c.reasons[1].startswith("💸 Best number: FanDuel 3.5 at -105 (the board had DraftKings 3.5 at -110)")


def test_best_line_takes_a_better_number_only_at_an_equal_or_better_price():
    ev = LineClient().odds(None)[0]
    assert shop.best_line(ev, "spreads", "home", -3.5, -120) == ("betmgm", -2.5, -115)  # better number AND price
    assert shop.best_line(ev, "totals", "under", 220.5, -112) == ("fanduel", 221.5, -112)
    assert shop.best_line(ev, "totals", "under", 220.5, -110) is None
