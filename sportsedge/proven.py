"""Proven spots (owner, Oct 25: "go ahead with all four"): only bets that passed a blind test get money.

Every spot that beat the closing line on seasons it never saw (STRONG: tuning and blind both beat zero by
a standard error; PROVEN: blind by a standard error and a tuning profit) is looked for on today's games:
- the game-factor study (factorstudy.py), except a team's own split (too few games per team);
- the "who's due" study (nbastudy.py): luck and slumps;
- the line studies: the windy NFL under, and the college hoops favorite the spread says is cheap;
- injury timing (injurytiming.py): the other team when a player is just ruled out, once that passes.
A game in one posts at the spot's price window with units: STRONG 1u, PROVEN 1/2u. Everything else
posts at 0u (tiers.gate). The owner's -150 cap on favorites holds.
"""

from __future__ import annotations

import json
import logging
import math
from dataclasses import replace
from datetime import datetime
from pathlib import Path

from .edge import Pick
from .odds import american_to_decimal, no_vig
from .tiers import Card

log = logging.getLogger(__name__)

UNITS = {"STRONG": 1.0, "PROVEN": 0.5}
MAX_FAV = -150  # owner's rule (the studies tested to -170)
DOG = (100, 300)
SPORTS = ("nfl", "ncaaf", "nba", "ncaab", "nhl", "mlb")
WIND_MPH = 15


def _load(path: Path) -> dict:
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return {}


def passed(root="data") -> list[dict]:
    """Every STRONG / PROVEN row from the studies the live board can spot."""
    root = Path(root)
    out = []
    for sport, res in _load(root / "factor_study.json").items():
        for r in res.get("rows", []):
            if r.get("verdict") and "its own history" not in r["variant"]:
                out.append({**r, "kind": "factor", "sport": sport})
    for sport, res in _load(root / "due_study.json").items():
        for r in res.get("rows", []):
            if r.get("verdict"):
                out.append({**r, "kind": "due", "sport": sport})
    for r in _load(root / "line_studies.json").get("rows", []):
        key = (r.get("study"), r.get("variant"))
        if r.get("verdict") and key in LINE_SPOTS:
            out.append({**r, "kind": "line", "market": LINE_SPOTS[key]})
    return out


LINE_SPOTS = {
    ("06 weather totals", "under, wind 15+ mph"): "under",
    ("02 spread-vs-ML value", "fav, spread says 4%+ more"): "moneyline favorite",
}


def _prices(scan) -> dict[str, dict]:
    """event -> {("h2h", side): Pick, ("spreads", side): Pick, ("totals", "under"): Pick}, best price each."""
    out: dict[str, dict] = {}
    for p in scan.picks:
        if p.player or p.market not in ("h2h", "spreads", "totals"):
            continue
        key = (p.market, p.side)
        cur = out.setdefault(p.event_id, {}).get(key)
        if cur is None or p.american > cur.american:
            out[p.event_id][key] = p
    return out


def _fits(market: str, pick: Pick | None) -> bool:
    if pick is None:
        return False
    if market == "moneyline favorite":
        return MAX_FAV <= pick.american <= -101
    if market == "moneyline dog":
        return DOG[0] <= pick.american <= DOG[1]
    return market in ("spread", "under", "over")


def _card(sport_name: str, ctx, pick: Pick, row: dict, label: str) -> Card:
    """A proven-spot card: its win chance is the price's break-even plus the edge the blind test kept
    after one standard error (so the ledger's break-even rule reads the test, not the old model)."""
    b, t = row["blind"], row["in_sample"]
    keep = max((b.get("flat_roi") or 0) - (b.get("se") or 0), 0.01)
    dec = american_to_decimal(pick.american)
    p = min((1 + keep) / dec, 0.97)
    pick = replace(pick, win_prob=p, model_prob=p, ev=p * dec - 1)
    units = UNITS[row["verdict"]]
    word = "STRONG" if row["verdict"] == "STRONG" else "PROVEN"
    reasons = [
        f"✅ Proven spot · {units:g}u: {label}.",
        (f"Blind test ({word}): {b['bets']} bets on seasons it never saw, {b['flat_roi']:+.1%}; "
         f"tuning seasons {t['bets']} bets, {t['flat_roi']:+.1%}."),
        "Only bets that beat the books in a blind test get money. This one did.",
    ]
    head = f"{pick.selection} at {pick.american:+.0f}: {label}. {word} in the blind test ({b['flat_roi']:+.1%} on {b['bets']} bets)."
    return Card(tier="value", units=units, headline=head, reasons=reasons, sport=pick.sport, sport_name=sport_name,
                pick=pick, home=ctx.home, away=ctx.away, proven=f"{word}: {row.get('study', '')} · {row['variant']}",
                evidence={"kind": word, "blind_bets": b["bets"], "blind_roi": b["flat_roi"], "blind_se": b.get("se"),
                          "tune_bets": t["bets"], "tune_roi": t["flat_roi"],
                          "text": f"{row.get('study', '')}: {row['variant']}. Blind {b['bets']} bets {b['flat_roi']:+.1%} "
                                  f"(standard error {b.get('se') or 0:.1%}); tuning {t['bets']} bets {t['flat_roi']:+.1%}."})


def _phi(z: float) -> float:
    return 0.5 * (1 + math.erf(z / math.sqrt(2)))


def find(scans, now: datetime, root="data") -> list[Card]:
    """Today's games in a proven spot, one card per game (the strongest spot)."""
    from . import factorstudy, nbastudy

    rows = passed(root)
    out: dict[str, Card] = {}
    for scan in scans:
        sport = scan.sport.key
        mine = [r for r in rows if r["sport"] == sport]
        timing = _load(Path(root) / "injury_timing.json").get(sport)
        if sport not in SPORTS or not (mine or timing) or not scan.contexts:
            continue
        prices = _prices(scan)
        games = {}
        for eid, ctx in scan.contexts.items():
            if ctx.event.commence_time <= now:
                continue
            pr = prices.get(eid, {})
            h, a = pr.get(("h2h", "home")), pr.get(("h2h", "away"))
            sp = pr.get(("spreads", "home"))
            games[eid] = {"home": ctx.home, "away": ctx.away, "start": ctx.event.commence_time.isoformat(),
                          "home_ml": h.american if h else None, "away_ml": a.american if a else None,
                          "spread": sp.point if sp else None, "neutral": ctx.neutral}
        found: list[tuple[str, Card]] = []

        def add(eid, market, side, row, label, scan=scan, prices=prices, found=found):
            ctx = scan.contexts[eid]
            key = {"moneyline favorite": "h2h", "moneyline dog": "h2h", "spread": "spreads", "under": "totals",
                   "over": "totals"}[market]
            pick = prices.get(eid, {}).get((key, side))
            if _fits(market, pick) and not ctx.injuries_unknown:
                found.append((eid, _card(scan.sport.name, ctx, pick, row, label)))

        # 1. Game factors.
        facts = [r for r in mine if r["kind"] == "factor"]
        if facts:
            try:
                study = factorstudy.game_study(sport, list(games.values()), root,
                                               results={sport: {(r["market"], r["variant"]): r for r in facts}}, now=now)
            except Exception as exc:  # a study failing never stops the board
                log.warning("proven factors %s: %s", sport, exc)
                study = {}
            for eid, g in games.items():
                for r in study.get(f"{g['away']} @ {g['home']}", []):
                    for x in r["markets"]:
                        if x["tag"] not in ("edge", "lean"):
                            continue
                        team_side = r["side"]
                        side = team_side if x["bet"] == "back" else ("away" if team_side == "home" else "home")
                        row = next(f for f in facts if (f["market"], f["variant"]) == (x["market"], f"{x['bet']}: {r['factor']}"))
                        who = g[side]
                        label = (f"{who}: {r['team']} {r['factor']}" if x["bet"] == "fade"
                                 else f"{who} {r['factor']}")
                        add(eid, x["market"], side, row, label)
                # Over/unders: the game's spots, by home or road team (as factorstudy tested them).
                spots = {(r["factor"] if r["factor"] in factorstudy.GAME_WIDE
                          else f"{'home team' if r['side'] == 'home' else 'road team'} {r['factor']}")
                         for r in study.get(f"{g['away']} @ {g['home']}", [])}
                for f in facts:
                    if f["market"] in ("over", "under") and f["variant"] in spots:
                        add(eid, f["market"], f["market"], f, f"{g['away']} @ {g['home']}: {f['variant']}")
        # 2. Who's due.
        due = [r for r in mine if r["kind"] == "due"]
        if due and sport in nbastudy.PARAMS:
            p = nbastudy.PARAMS[sport]
            hist, names = factorstudy.history(sport, root)
            season = hist[-1].season if hist else None
            teams: dict[str, nbastudy.Team] = {}
            for g in hist:
                if g.season == season:
                    for t, pf, pa in ((g.home, g.hs, g.as_), (g.away, g.as_, g.hs)):
                        teams.setdefault(t, nbastudy.Team(p)).add(pf, pa, False)
            rules = nbastudy.rules(p)
            for eid, g in games.items():
                ids = {s: names.get(g[s]) for s in ("home", "away")}
                ts = {s: teams.get(i) for s, i in ids.items()}
                if any(t is None or t.games < p.min_games for t in ts.values()):
                    continue
                f = {s: {"luck": t.luck(), "close": t.close, "slump": t.slump()} for s, t in ts.items()}
                for side in ("home", "away"):
                    other = "away" if side == "home" else "home"
                    for r in due:
                        test = rules.get(r["variant"])
                        if test and test(f[side], f[other]):
                            add(eid, r["market"], side, r, f"{g[side]}: {r['variant']}")
        # 3. Line studies.
        for r in (x for x in mine if x["kind"] == "line"):
            for eid, g in games.items():
                ctx = scan.contexts[eid]
                if r["market"] == "under" and sport == "nfl" and (ctx.wind_mph or 0) >= WIND_MPH:
                    add(eid, "under", "under", r, f"wind {ctx.wind_mph:.0f} mph at kickoff: under")
                if r["market"] == "moneyline favorite" and sport == "ncaab" and g["spread"] is not None \
                        and g["home_ml"] and g["away_ml"]:
                    fair = no_vig({"home": g["home_ml"], "away": g["away_ml"]})
                    p_home = _phi(-g["spread"] / NCAAB_SD)
                    for side, ps in (("home", p_home), ("away", 1 - p_home)):
                        if ps - fair[side] >= 0.04:
                            add(eid, "moneyline favorite", side, r,
                                f"{g[side]} moneyline is cheaper than the spread says ({ps:.0%} vs {fair[side]:.0%})")
        # 4. Injury timing: a player just ruled out, if betting the other team on fresh news passed blind.
        if timing:
            from . import injurytiming

            for news in injurytiming.recent(sport, now, root):
                eid, hit = news["event_id"], injurytiming.spot(timing, news)
                if eid not in games or hit is None:
                    continue
                side = "away" if news["side"] == "home" else "home"
                pick = prices.get(eid, {}).get(("h2h", side))
                market = "moneyline favorite" if pick and pick.american < 0 else "moneyline dog"
                add(eid, market, side, hit[0], f"{games[eid][side]}: {news['team']} just ruled out {news['player']}")
        for eid, card in found:
            cur = out.get(eid)
            if cur is None or (card.units, card.pick.ev) > (cur.units, cur.pick.ev):
                out[eid] = card
    return sorted(out.values(), key=lambda c: (c.units, c.pick.ev), reverse=True)


NCAAB_SD = 11.2  # college hoops margin around the closing spread (linestudies.margin_sd on 2018-2021)

__all__ = ["UNITS", "find", "passed"]
