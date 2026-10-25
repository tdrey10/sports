"""Owner, Oct 25 (step 3): every new money moneyline is shopped across the US books before it posts."""

from sportsedge import shop
from sportsedge.tiers import Card
from tests.test_ledger import make_pick


class Client:
    remaining = 300.0

    def __init__(self):
        self.calls = 0

    def odds(self, cfg):
        self.calls += 1
        book = lambda key, home, away: {"key": key, "markets": [{"key": "h2h", "outcomes": [  # noqa: E731
            {"name": "Minnesota Timberwolves", "price": home}, {"name": "Toronto Raptors", "price": away}]}]}
        return [{"home_team": "Minnesota Timberwolves", "away_team": "Toronto Raptors", "bookmakers": [
            book("draftkings", -115, -105), book("fanduel", -108, -112), book("pinnacle", -102, -108)]}]


def money(units=1.0, american=-115):
    p = make_pick(market="h2h", point=None, american=american, sport="nba", win_prob=0.552)
    return Card(tier="value", units=units, headline="h", reasons=["first", "second"], sport="nba", sport_name="NBA",
                pick=p, home="Minnesota Timberwolves", away="Toronto Raptors", proven="STRONG: test")


def test_a_money_pick_moves_to_the_best_us_book_and_says_so():
    c, client = money(), Client()
    assert shop.shop([c], client=client) == 1 and client.calls == 1
    assert (c.pick.book, c.pick.american) == ("FanDuel", -108)  # Pinnacle's -102 can't be bet from the US
    assert c.reasons[1] == "💸 Best price: FanDuel -108 (the board had fanduel -115). Checked 2 books. Bet it there."
    assert abs(c.pick.ev - (0.552 * (1 + 100 / 108) - 1)) < 1e-9


def test_leans_and_already_posted_picks_cost_no_credits():
    client = Client()
    assert shop.shop([money(units=0.0), money()], is_new=lambda c: c.units == 0, client=client) == 0
    assert client.calls == 0
