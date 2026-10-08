import json

import pytest

from sportsedge.cli import main
from sportsedge.data.sample import SampleProvider
from sportsedge.edge import Settings, qualifying
from sportsedge.engine import Engine
from sportsedge.sports import SPORTS, resolve


@pytest.mark.parametrize("key", ["nfl", "ncaaf", "nba", "ncaab", "mlb", "nhl", "epl"])
def test_demo_scan_every_sport(key):
    provider = SampleProvider(seed=3)
    engine = Engine(provider, Settings(), now=provider.now)
    scan = engine.scan(SPORTS[key], props=True)
    assert scan.events > 0
    assert len(scan.predictions) == scan.events
    markets = {p.market for p in scan.picks}
    assert {"h2h", "totals"} <= markets
    if SPORTS[key].props:
        assert any(p.is_prop for p in scan.picks)
    for p in scan.picks:
        assert 0 < p.win_prob < 1
        assert 0 <= p.kelly <= engine.settings.max_stake
    assert not [w for w in scan.warnings if "no rating" in w]


def test_soccer_has_three_way_moneyline():
    provider = SampleProvider()
    scan = Engine(provider, now=provider.now).scan(SPORTS["epl"])
    assert any(p.side == "draw" for p in scan.picks)


def test_resolve_aliases():
    assert [c.key for c in resolve("soccer")][:2] == ["epl", "laliga"]
    assert [c.key for c in resolve(["college", "nba"])] == ["ncaaf", "ncaab", "nba"]
    with pytest.raises(KeyError):
        resolve("cricket")


def test_cli_demo_scan(tmp_path, capsys):
    out = tmp_path / "picks.json"
    assert main(["scan", "nba", "--demo", "--props", "--json", str(out), "--bankroll", "500"]) == 0
    text = capsys.readouterr().out
    assert "DEMO MODE" in text and "NBA" in text
    picks = json.loads(out.read_text())
    assert picks and all(p["ev"] >= 0.02 for p in picks)


def test_cli_backtest_and_ratings(capsys):
    assert main(["backtest", "nhl", "--demo", "--days", "40"]) == 0
    assert main(["ratings", "mlb", "--demo", "--top", "3"]) == 0
    text = capsys.readouterr().out
    assert "log loss" in text and "power ratings" in text


def test_qualifying_respects_min_ev():
    provider = SampleProvider()
    engine = Engine(provider, Settings(min_ev=0.05), now=provider.now)
    scan = engine.scan(SPORTS["nba"])
    assert all(p.ev >= 0.05 for p in qualifying(scan.picks, engine.settings))
