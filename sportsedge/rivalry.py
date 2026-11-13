"""The Doctor vs Grim (owner, Oct 29: "You come out with your leans. And I'll give you my picks. We can see
whose record is better." Then: "Also track my parlay I give you. Can you call it The Doctor vs Grim" -
the owner is The Doctor, Script Leaks is Grim).

The owner's picks live in their own ledger (data/my_picks.json) and are graded exactly like ours, on
their own game. The scoreboard compares them with our leans (the no-money picks) posted since the
contest started, one unit flat on every pick on both sides. The owner's parlays are tracked the same way
(their legs in data/my_parlay_legs.json, graded on their own games) against our parlays.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from .data.base import NameMatcher
from .edge import Pick
from .ledger import Ledger
from .tiers import Card

START = datetime(2026, 10, 29, 7, tzinfo=timezone.utc)  # midnight Pacific, Oct 29
LEAN_TIERS = ("lean", "early", "value", "lock", "dog")
BEST_PICK_RANGE_FROM = "2026-11-13"  # Grim's daily no-money pick skips long shots from this day on
FILE = "my_picks.json"
LEGS = "my_parlay_legs.json"
GRIM_LEGS = "grim_parlay_legs.json"  # Grim's own contest parlays (owner, Oct 31: "log that as Grim's parlay")
GRIM_INBOX = "grim_parlays_inbox.csv"
# Owner, Nov 10: teasers count too, in their own record. Their legs sit at the teased number, so they live in
# their own file (a teased Spurs -5 is not the Spurs -9 leg of a parlay). The teaser's price rides in its id:
# "doc-teaser+400-20261109230002".
TEASER_LEGS = "my_teaser_legs.json"


def path(root) -> Path:
    return Path(root) / FILE


def _game(root, sport: str, team: str, now: datetime):
    """(event_id, start, home, away) of the next game for this team, from our saved line snapshots."""
    import csv

    rows = []
    for f in sorted((Path(root) / "odds" / sport).glob("*.csv"))[-2:]:
        with f.open() as fh:
            rows.extend(csv.DictReader(fh))
    games = {}
    for r in rows:
        start = datetime.fromisoformat(r["start"])
        if start > now - timedelta(hours=1):
            games[r["event_id"]] = (start, r["home"], r["away"])
    m = NameMatcher({t for _, h, a in games.values() for t in (h, a)})
    name = m.match(team)
    hits = sorted((v[0], eid, v[1], v[2]) for eid, v in games.items() if name in (v[1], v[2]))
    if not hits:
        return None
    start, eid, home, away = hits[0]
    return eid, start, home, away, name


TENNIS_SRC = ("_src", "tennis", "matches.csv")


def _tennis_rows(root) -> list[dict]:
    """The ATP/WTA match list the tennis board uses (fetched here if this run hasn't yet)."""
    from . import tennis

    f = Path(root).joinpath(*TENNIS_SRC)
    if not f.exists():
        try:
            tennis.fetch(f.parent)
        except Exception:  # no tennis list: a tennis pick just waits in the inbox
            return []
    return tennis.read_matches(f)


def _tennis_match(root, player: str, now: datetime):
    """(match id, start, player 1, player 2, the player's full name, tour) of this player's next match."""
    rows = [r for r in _tennis_rows(root) if r.get("start")]
    near = []
    for r in rows:
        start = datetime.fromisoformat(r["start"].replace("Z", "+00:00"))
        if start > now - timedelta(hours=1):
            near.append((start, r))
    m = NameMatcher({n for _, r in near for n in (r["p1_name"], r["p2_name"])})
    name = m.match(player)
    hits = sorted(((st, r) for st, r in near if name in (r["p1_name"], r["p2_name"])), key=lambda x: x[0])
    if not name or not hits:
        return None
    start, r = hits[0]
    return r["id"], start, r["p1_name"], r["p2_name"], name, r["tour"]


def _card(root, sport, team, odds, now, market, point, side) -> Card:
    # Owner, Nov 10 ("the contest is all sports, add everything"): tennis moneylines, graded on the match result.
    if sport.startswith("tennis"):
        if market != "h2h":
            raise ValueError("tennis picks are moneylines for now")
        found = _tennis_match(root, team, now)
        if found is None:
            raise ValueError(f"no upcoming tennis match found for {team}")
        eid, start, home, away, name, tour = found
        sport = f"tennis_{tour}"
    else:
        found = _game(root, sport, team, now)
        if found is None:
            raise ValueError(f"no upcoming {sport.upper()} game found for {team}")
        eid, start, home, away, name = found
    # Owner, Nov 5: bets on part of a game ("2nd half over 108.5"): market "totals_2h" and so on.
    from .ledger import PARTS, split_market

    # Owner, Nov 8: player props ride in the market as "player_points@LeBron James" (the inbox has no player
    # column); touchdown scorer bets are over 0.5.
    player = None
    if "@" in market:
        market, player = market.split("@", 1)
    base, part = split_market(market)
    if player is None and base not in ("h2h", "spreads", "totals"):
        raise ValueError(f"unknown market {market}")
    tag = f"{part.upper()} " if part else ""
    if player is not None:
        label = PROP_LABELS.get(market, market.replace("player_", "").replace("_", " ").title())
        if market in ("player_anytime_td", "player_first_td"):
            sel_side, point, selection = "over", 0.5, f"{player} {label}"
        else:
            sel_side, selection = side, f"{player} {side.title()} {point:g} {label}"
    elif base == "totals":
        sel_side, selection = side, f"{tag}{side.title()} {point:g}"
    else:
        sel_side = "home" if name == home else "away"
        selection = f"{tag}{name} ML" if base == "h2h" else f"{tag}{name} {point:+g}"
    label = PROP_LABELS.get(market, "Player prop") if player is not None else \
        {"h2h": "Moneyline", "spreads": "Spread", "totals": "Total"}[base]
    pick = Pick(sport=sport, event_id=eid, commence_time=start, matchup=f"{away} @ {home}", market=market,
                market_label=f"{PARTS[part].capitalize()} {label.lower()}" if part else label, selection=selection,
                side=sel_side, point=point, player=player, book="", american=float(odds), model_prob=0.5,
                market_prob=None, win_prob=0.5, push_prob=0.0, fair_american=0.0, ev=0.0, kelly=0.0, stake=None,
                projection="", books=0)
    return Card(tier="lean", units=0.0, headline=f"Your pick: {selection} at {odds:+.0f}.", reasons=["Your pick."],
                sport=sport, sport_name=sport.upper(), pick=pick, home=home, away=away)


def add(root, sport: str, team: str, odds: float, now: datetime, market: str = "h2h",
        point: float | None = None, side: str | None = None) -> str:
    """Record one of the owner's picks: a team's moneyline (or spread with point, or a total with side
    over/under and point). Returns what was saved, in words."""
    card = _card(root, sport, team, odds, now, market, point, side)
    pick, selection, start = card.pick, card.pick.selection, card.pick.commence_time
    home, away = card.home, card.away
    ledger = Ledger.load(path(root))
    added = ledger.publish([card], now)
    ledger.save(path(root))
    if not added:
        return f"Already have it: {selection} ({away} @ {home})."
    return f"Saved: {selection} at {odds:+.0f}, {away} @ {home}, {start.astimezone().strftime('%a %b %d %H:%M UTC')}."


INBOX = "my_picks_inbox.csv"  # picks as sent in chat; the engine files them (the inbox is never rewritten by a run)
INBOX_FIELDS = ["added_at", "sport", "team", "odds", "market", "point", "side"]


def send(root, sport: str, team: str, odds: float, now: datetime, market: str = "h2h",
         point: float | None = None, side: str | None = None) -> str:
    """Put a pick in the inbox (checked against our saved games now, filed on the next run)."""
    import csv

    card = _card(root, sport, team, odds, now, market, point, side)  # raises when there's no game coming up
    f = Path(root) / INBOX
    new = not f.exists()
    with f.open("a", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=INBOX_FIELDS)
        if new:
            w.writeheader()
        w.writerow({"added_at": now.isoformat(), "sport": sport, "team": team, "odds": odds, "market": market,
                    "point": "" if point is None else point, "side": side or ""})
    return f"In: {card.pick.selection} at {odds:+.0f} ({card.away} @ {card.home})."


def cancel(root, sport: str, team: str, now: datetime, market: str = "h2h") -> str:
    """Take back one of the owner's picks before its game (owner, Oct 29: "I'm gonna change my pick")."""
    import csv

    found = _game(root, sport, team, now)
    if found is None:
        raise ValueError(f"no upcoming {sport.upper()} game found for {team}")
    f = Path(root) / INBOX
    with f.open("a", newline="") as fh:
        csv.DictWriter(fh, fieldnames=INBOX_FIELDS).writerow(
            {"added_at": now.isoformat(), "sport": sport, "team": team, "odds": 0, "market": f"cancel:{market}",
             "point": "", "side": ""})
    return f"Out: {found[4]} {market} ({found[3]} @ {found[2]})."


def _cancel(root, r) -> bool:
    when = datetime.fromisoformat(r["added_at"])
    found = _game(root, r["sport"], r["team"], when)
    if found is None:
        return False
    eid, _, _, _, name = found
    ledger = Ledger.load(path(root))
    hit = False
    for e in ledger.entries.values():
        if e.event_id == eid and e.market == r["market"].split(":", 1)[1] and name in e.selection \
                and e.status == "pending" and e.start > when:
            e.status, e.profit, e.graded_at = "void", 0.0, when.isoformat()
            e.reasons = ["Changed pick: taken back before the game."] + list(e.reasons)
            hit = True
    if hit:
        ledger.save(path(root))
    return hit


def _file_inbox(root) -> int:
    import csv

    f = Path(root) / INBOX
    if not f.exists():
        return 0
    n = 0
    with f.open() as fh:
        for r in csv.DictReader(fh):
            if r["market"].startswith("cancel:"):
                _cancel(root, r)
                continue
            try:
                msg = add(root, r["sport"], r["team"], float(r["odds"]), datetime.fromisoformat(r["added_at"]),
                          r["market"] or "h2h", float(r["point"]) if r["point"] else None, r["side"] or None)
            except ValueError:
                continue
            n += msg.startswith("Saved")
    return n


PARLAY_INBOX = "my_parlays_inbox.csv"
PARLAY_FIELDS = ["added_at", "parlay"] + INBOX_FIELDS[1:]


PROP_LABELS = {"player_points": "Points", "player_rebounds": "Rebounds", "player_assists": "Assists",
               "player_threes": "Threes", "player_points_rebounds_assists": "Pts+Reb+Ast", "player_pass_yds": "Pass Yds",
               "player_pass_tds": "Pass TDs", "player_rush_yds": "Rush Yds", "player_reception_yds": "Rec Yds",
               "player_receptions": "Catches", "player_goals": "Goals", "player_shots_on_goal": "Shots on Goal",
               "player_anytime_td": "anytime TD", "player_first_td": "first TD", "batter_hits": "Hits",
               "batter_home_runs": "Home Runs", "pitcher_strikeouts": "Strikeouts"}
PROP_ALIASES = {"pts": "points", "reb": "rebounds", "ast": "assists", "3s": "threes", "pra": "points_rebounds_assists",
                "td": "anytime_td", "anytime": "anytime_td", "first": "first_td", "ftd": "first_td",
                "passing_yards": "pass_yds", "rushing_yards": "rush_yds", "receiving_yards": "reception_yds",
                "rec_yds": "reception_yds", "catches": "receptions", "sog": "shots_on_goal", "shots": "shots_on_goal",
                "hits": "hits", "hr": "home_runs", "ks": "strikeouts", "strikeouts": "strikeouts"}


def prop_market(sport: str, name: str) -> str:
    """'points' -> 'player_points', 'first_td' -> 'player_first_td', 'hits' (MLB) -> 'batter_hits'."""
    key = PROP_ALIASES.get(name.lower(), name.lower())
    if sport == "mlb":
        return f"pitcher_{key}" if key == "strikeouts" else f"batter_{key}"
    return f"player_{key}"


def parse_leg(spec: str) -> tuple:
    """'nba|Lakers|-120', 'nfl|Bills|-110|spread -3.5', 'nba|Lakers|-110|over 221.5' -> leg fields.
    A last field '1h', '2h', 'q1'..'q4' or 'p1'..'p3' makes it a bet on that part of the game
    ('nba|Magic|-110|over 108.5|2h', 'nba|Lakers|-115|ml|1h')."""
    from .ledger import PARTS

    parts = [x.strip() for x in spec.split("|")]
    part = parts.pop().lower() if len(parts) > 3 and parts[-1].lower() in PARTS else None
    sport, team, odds = parts[0].lower(), parts[1], float(parts[2])
    market, point, side = "h2h", None, None
    if len(parts) > 3 and parts[3].lower().startswith("player "):
        # 'nba|Lakers|-115|player LeBron James points over 25.5', 'nfl|Panthers|1100|player Jalen Coker first_td'
        import re

        m = re.match(r"^player (.+?) (\w+)(?: (over|under) ([\d.]+))?$", parts[3], re.I)
        if not m:
            raise ValueError(f"can't read the prop in {spec!r}")
        name, stat, ou, num = m.groups()
        market = prop_market(sport, stat)
        point = float(num) if num else 0.5
        return sport, team, odds, f"{market}@{name}", point, (ou or "over").lower()
    if len(parts) > 3 and parts[3].lower() not in ("ml", "moneyline"):
        kind, num = parts[3].split()
        if kind.lower() == "spread":
            market, point = "spreads", float(num)
        else:
            market, side, point = "totals", kind.lower(), float(num)
    return sport, team, odds, f"{market}_{part}" if part else market, point, side


def send_parlay(root, legs: list[tuple], now: datetime, grim: bool = False, teaser: float | None = None) -> str:
    """Put a parlay in the inbox: every leg must be on an upcoming game we have. With `teaser` (its price),
    the legs are at the teased numbers and it keeps its own record."""
    import csv

    if len(legs) < 2:
        raise ValueError("a parlay needs 2+ legs")
    names = [f"{_card(root, *leg[:3], now, leg[3], leg[4], leg[5]).pick.selection}" for leg in legs]
    pid = ("grim-" if grim else "doc-") + (f"teaser{teaser:+.0f}-" if teaser is not None else "") + now.strftime("%Y%m%d%H%M%S")
    f = Path(root) / (GRIM_INBOX if grim else PARLAY_INBOX)
    new = not f.exists()
    with f.open("a", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=PARLAY_FIELDS)
        if new:
            w.writeheader()
        for sport, team, odds, market, point, side in legs:
            w.writerow({"added_at": now.isoformat(), "parlay": pid, "sport": sport, "team": team, "odds": odds,
                        "market": market, "point": "" if point is None else point, "side": side or ""})
    kind = f"{len(legs)}-team teaser at {teaser:+.0f}" if teaser is not None else f"{len(legs)}-leg parlay"
    return f"In: {kind}: " + " + ".join(names) + "."


def teaser_price(pid: str) -> float | None:
    import re

    m = re.search(r"teaser([+-]\d+)", pid)
    return float(m.group(1)) if m else None


def _file_parlays(root, inbox: str = PARLAY_INBOX, legs_file: str = LEGS, teasers: bool = False) -> int:
    import csv
    from collections import defaultdict

    from .ledger import entry_id
    from .odds import american_to_decimal
    from .parlays import Parlay

    f = Path(root) / inbox
    if not f.exists():
        return 0
    groups = defaultdict(list)
    with f.open() as fh:
        for r in csv.DictReader(fh):
            groups[r["parlay"]].append(r)
    lp = Path(root) / legs_file
    ledger = Ledger.load(lp)
    have = {p.id for p in ledger.parlays}
    n = 0
    for pid, rows in groups.items():
        if pid in have or (teaser_price(pid) is not None) != teasers:
            continue
        now = datetime.fromisoformat(rows[0]["added_at"])
        try:
            cards = [_card(root, r["sport"], r["team"], float(r["odds"]), now, r["market"] or "h2h",
                           float(r["point"]) if r["point"] else None, r["side"] or None) for r in rows]
        except ValueError:
            continue
        ledger.publish(cards, now)
        ids = [entry_id(c.pick) for c in cards]
        if not all(i in ledger.entries for i in ids):
            continue
        if teasers:
            # Every leg carries an even share of the teaser's price, so a pushed leg drops it like a smaller teaser.
            dec = american_to_decimal(teaser_price(pid))
            each = dec ** (1 / len(cards))
            leg_odds = [round((each - 1) * 100, 2) if each >= 2 else round(-100 / (each - 1), 2)] * len(cards)
        else:
            dec = 1.0
            for c in cards:
                dec *= american_to_decimal(c.pick.american)
            leg_odds = [c.pick.american for c in cards]
        ledger.parlays.append(Parlay(id=pid, day=ledger.day_of(now), created_at=now.isoformat(), size=len(cards), legs=ids,
                                     leg_odds=leg_odds, units=0.0, decimal=round(dec, 4),
                                     kind="teaser" if teasers else "doctor"))
        n += 1
    ledger.save(lp)
    return n


def refresh(provider, root, now: datetime) -> int:
    """File new picks and parlays from the inboxes, then grade them the same way ours are graded."""
    from .runner import refresh_scores

    _file_inbox(root)
    _file_parlays(root)
    _file_parlays(root, GRIM_INBOX, GRIM_LEGS)
    _file_parlays(root, PARLAY_INBOX, TEASER_LEGS, teasers=True)
    graded = 0
    tennis_rows = None
    for p in (path(root), Path(root) / LEGS, Path(root) / GRIM_LEGS, Path(root) / TEASER_LEGS):
        if p.exists():
            ledger = Ledger.load(p)
            graded += refresh_scores(provider, ledger, now)
            if any(e.sport.startswith("tennis_") and not e.settled for e in ledger.entries.values()):
                from . import tennis

                tennis_rows = _tennis_rows(root) if tennis_rows is None else tennis_rows
                graded += tennis.grade_entries(ledger, tennis_rows, now)
            ledger.save(p)
    return graded


def _parlay_record(parlays, entries) -> tuple[dict, list[dict]]:
    from .parlays import settle

    rows = []
    for p in parlays:
        s = settle(p, entries)
        if s["status"] != "void":
            rows.append((p, s))
    won = sum(s["status"] == "won" for _, s in rows)
    lost = sum(s["status"] == "lost" for _, s in rows)
    push = sum(s["status"] == "push" for _, s in rows)
    units = round(sum(s["profit"] or 0 for _, s in rows if s["profit"] is not None), 2)
    rec = {"won": won, "lost": lost, "push": push, "units": units,
           "pending": sum(s["status"] in ("pending", "live") for _, s in rows)}
    view = [{"odds": p.american, "status": s["status"],
             "legs": [f"{entries[i].selection} {a:+.0f}" + (f" ({entries[i].status})" if entries[i].settled else "")
                      for i, a in zip(p.legs, p.leg_odds) if i in entries]}
            for p, s in sorted(rows, key=lambda x: x[0].created_at, reverse=True)[:5]]
    return rec, view


def _record(entries) -> dict:
    won = sum(e.status in ("won", "half_won") for e in entries)
    lost = sum(e.status in ("lost", "half_lost") for e in entries)
    push = sum(e.status == "push" for e in entries)
    units = round(sum(e.profit or 0 for e in entries if e.settled), 2)
    return {"won": won, "lost": lost, "push": push, "units": units,
            "pending": sum(not e.settled for e in entries)}


def grim_picks(ours: Ledger) -> list:
    """Grim's side (owner, Oct 31: "Have Grim post only the money plays but there has to be picks every day"):
    every money play from the contest start, and on a day with no money play on that day's games, Grim's
    one best lean (where our number beats the market's by the most), so Grim always has a pick."""
    from collections import defaultdict

    games = [e for e in ours.entries.values() if e.start >= START and e.status != "void" and not e.player
             and (e.units > 0 or e.tier in LEAN_TIERS)]
    by_day = defaultdict(list)
    for e in games:
        by_day[ours.day_of(e.start)].append(e)
    out = []
    for day, es in by_day.items():
        money = [e for e in es if e.units > 0]
        if money:
            out += money
        else:
            # Owner, Nov 12 ("ULM +310... just seems like a terrible pick"): from Nov 13 on, Grim's no-money pick is
            # a favorite to -150 or a dog to +200 when there is one; long shots show the biggest paper gaps.
            if day >= BEST_PICK_RANGE_FROM:
                es = [e for e in es if -150 <= e.odds <= 200] or es
            out.append(max(es, key=lambda e: ((e.win_prob - (e.market_prob or e.win_prob)), e.win_prob)))
    return out


def _flat(entries) -> dict:
    """Record at one unit flat on every pick (a money play's own stake is set aside for the contest)."""
    from .odds import american_to_decimal

    won = sum(e.status in ("won", "half_won") for e in entries)
    lost = sum(e.status in ("lost", "half_lost") for e in entries)
    push = sum(e.status == "push" for e in entries)
    units = sum((american_to_decimal(e.odds) - 1) if e.status == "won" else -1.0 if e.status == "lost"
                else (american_to_decimal(e.odds) - 1) / 2 if e.status == "half_won" else -0.5 if e.status == "half_lost"
                else 0.0 for e in entries if e.settled)
    return {"won": won, "lost": lost, "push": push, "units": round(units, 2), "pending": sum(not e.settled for e in entries)}


def scoreboard(ours: Ledger, root) -> dict | None:
    """Both records since the contest started, one unit flat each."""
    p = path(root)
    mine = Ledger.load(p) if p.exists() else None
    grim = grim_picks(ours)
    every = [e for e in (mine.entries.values() if mine else []) if e.status != "void"]
    # Owner, Nov 10: "Prop picks shouldn't count against my record. It should have its own like parlays does."
    yours = [e for e in every if not e.player]
    your_props = [e for e in every if e.player]
    grim_props = [e for e in ours.entries.values() if e.player and e.units > 0 and e.start >= START and e.status != "void"]
    recent = sorted(yours, key=lambda e: e.start, reverse=True)[:10]
    recent_props = sorted(your_props, key=lambda e: e.start, reverse=True)[:10]
    legs = Ledger.load(Path(root) / LEGS)
    you_p, your_parlays = _parlay_record(legs.parlays, legs.entries)
    tl = Ledger.load(Path(root) / TEASER_LEGS)
    you_t, your_teasers = _parlay_record(tl.parlays, tl.entries)
    gl = Ledger.load(Path(root) / GRIM_LEGS)
    entries = {**ours.entries, **gl.entries}
    grim_p, grim_parlays = _parlay_record([p for p in ours.parlays if p.created_at >= START.isoformat()] + gl.parlays, entries)
    return {"since": START.isoformat(), "names": {"you": "The Doctor", "us": "Grim"},
            "leans": _flat(grim), "you": _record(yours),
            "grim_picks": [{"selection": e.selection, "matchup": e.matchup, "odds": e.odds, "start": e.start.isoformat(),
                            "status": e.status, "final": e.final, "money": e.units > 0}
                           for e in sorted(grim, key=lambda e: e.start, reverse=True)[:10]],
            "your_picks": [{"selection": e.selection, "matchup": e.matchup, "odds": e.odds, "start": e.start.isoformat(),
                            "status": e.status, "final": e.final} for e in recent],
            "props": {"you": _record(your_props), "us": _flat(grim_props)},
            "your_props": [{"selection": e.selection, "matchup": e.matchup, "odds": e.odds, "start": e.start.isoformat(),
                            "status": e.status, "final": e.final} for e in recent_props],
            "teasers": {"you": you_t, "us": _parlay_record([], {})[0]}, "your_teasers": your_teasers,
            "parlays": {"you": you_p, "us": grim_p}, "your_parlays": your_parlays, "grim_parlays": grim_parlays}


__all__ = ["add", "cancel", "grim_picks", "parse_leg", "refresh", "scoreboard", "send", "send_parlay"]
