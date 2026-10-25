"""The D503 daily board (spec sections 7-13): reads per side, filters, Lock / Dog / value plays /
gap plays / leans, and their unit sizes. Covers the six sports his engine covers (NFL, college
football, NBA, MLB, NHL, college hoops); every other sport keeps the old board.

What our data can't support yet is left out rather than faked: NBA roster prior and star lists,
NHL goalie save % / PDO / shot share, MLB best-bats, 4th-down rates, news drama tags, coaching
changes, public splits. Those dog-score items are simply not added (they are capped +/-6 anyway).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from ..edge import Pick
from ..tiers import Card
from .data import Game, load
from .features import FEATURES, Replay, logit, sigmoid
from .model import SportModel, dec, fit, no_vig
from .qb import QB_SPORTS, REASON_KEY, QBIndex, load_store

log = logging.getLogger(__name__)

D5_SPORTS = ("nfl", "ncaaf", "nba", "mlb", "nhl", "ncaab")
PT = ZoneInfo("America/Los_Angeles")
ET = ZoneInfo("America/New_York")
MAX_FAV = -150
DOG_MIN, DOG_MAX = 100, 350
THIN_EDGE = 0.03
STUDY_CAP = 6.0
FAV_CAP = 0.04
DOG_GATE = {"nfl": 8, "nhl": 6, "ncaaf": 4, "nba": 4, "ncaab": 4}  # MLB: no gate (no MLB dogs without a proven angle)
BOARD_TARGET = 5
LEANS_PER_SPORT = 2
INJURY_PTS = {  # owner-spec injury penalties on the own read, win-chance points
    "nfl": {"QB": 0.08, "one": 0.03, "two": 0.10},
    "ncaaf": {"QB": 0.03, "one": 0.0, "two": 0.05},
}
SKILL = {"QB": "QB", "RB": "RB", "WR": "WR", "TE": "WR"}

_cache: dict[str, tuple[list[Game], Replay]] = {}


@dataclass
class Side:
    sport: str
    eid: str
    home: str
    away: str
    side: str  # "home" / "away"
    team: str
    opp: str
    start: datetime
    pick: Pick
    odds: float
    p_market: float
    p: float
    own: float
    raw_own: float
    elo_pts: float  # rating edge from this side's view
    drift: float
    f: dict
    s: int  # +1 home, -1 away
    reasons: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    hurt: bool = False
    waiting: bool = False
    fighting: bool = False
    w_p: float | None = None
    dog_score: float | None = None
    dog_items: float = 0.0
    dog_p: float | None = None
    team_games: int = 0
    intl: bool = False
    neutral: bool = False
    rest: float = 10.0  # this side's days of rest (capped 10)
    rest_opp: float = 10.0
    last_margin: float | None = None
    streak: int = 0
    record: tuple[int, int] = (0, 0)
    venue_tz: float | None = None
    team_tz: float | None = None
    qb_out: bool = False

    @property
    def dec(self) -> float:
        return dec(self.odds)

    @property
    def edge(self) -> float:
        return self.p * self.dec - 1

    @property
    def edge_own(self) -> float:
        return self.own * self.dec - 1

    @property
    def read_of(self) -> float:
        if self.dog_p is not None and self.odds >= 100:
            return self.dog_p
        return self.w_p if self.w_p is not None else self.own

    def beats_price(self) -> bool:
        return self.read_of * self.dec > 1

    def real_value(self) -> bool:
        if self.dog_p is not None:
            return self.dog_p * self.dec > 1
        return self.w_p * self.dec > 1 if self.w_p is not None else self.edge_own > 0

    def own_beats(self) -> bool:
        return self.w_p * self.dec > 1 if self.w_p is not None else self.edge_own > 0

    def own_agrees(self) -> bool:
        return (self.w_p if self.w_p is not None else self.own) >= self.p_market

    def thin(self) -> bool:
        return min(self.edge_own, self.read_of * self.dec - 1) < THIN_EDGE


def history(sport: str, now: datetime, root="data") -> tuple[list[Game], Replay]:
    """All finals before now, replayed into team states (cached per run)."""
    key = f"{sport}:{now.date()}"
    if key not in _cache:
        games = [g for g in load(sport, root) if g.start < now]
        _cache[key] = (games, None)
    return _cache[key]


def _state(sport: str, model: SportModel, games: list[Game]) -> Replay:
    r = Replay(sport, model.k, model.hfa)
    for g in games:
        r.features(g)  # applies off-season pulls in order
        r.update(g)
    return r


def _injury_pen(sport: str, out: list[tuple[str, str]]) -> tuple[float, bool]:
    """(penalty in win-chance points, QB out) from the owner's injury table."""
    table = INJURY_PTS.get(sport)
    if not table:
        return 0.0, False
    kinds = {SKILL[pos.upper()] for _, pos in out if pos and pos.upper() in SKILL}
    qb = "QB" in kinds
    if len(kinds) >= 2:
        return table["two"], qb
    if qb:
        return table["QB"], True
    return (table["one"], False) if kinds else (0.0, False)


def build_sides(scan, model: SportModel, now: datetime, root="data") -> list[Side]:
    sport = scan.sport.key
    games, _ = history(sport, now, root)
    r = _state(sport, model, games)
    qbs = QBIndex(load_store(sport, root)) if sport in QB_SPORTS else None
    by_event: dict[str, dict[str, Pick]] = {}
    for p in scan.picks:
        if p.market == "h2h" and p.side in ("home", "away") and p.market_prob is not None:
            cur = by_event.setdefault(p.event_id, {}).get(p.side)
            if cur is None or p.american > cur.american:
                by_event[p.event_id][p.side] = p
    out: list[Side] = []
    for eid, ctx in scan.contexts.items():
        picks = by_event.get(eid, {})
        if set(picks) != {"home", "away"} or ctx.injuries_unknown:
            continue  # no verified price on both sides, or injuries unknown: no pick
        ph_pick = picks["home"]
        start = ph_pick.commence_time
        if start <= now + timedelta(minutes=20):
            continue
        m = ph_pick.market_prob
        if not 0.02 < m < 0.98:
            continue
        o = ctx.open_prices
        mo = no_vig(o["home"], o["away"]) if o.get("home") and o.get("away") else None
        move = logit(m) - logit(mo) if mo is not None else 0.0
        hs, as_ = r.teams[ctx.home], r.teams[ctx.away]
        g = Game(sport, eid, start, ctx.home, ctx.away, neutral=ctx.neutral,
                 tzo=hs.home_tz, elev=hs.home_elev)
        inj_h, inj_a = ctx.injured.get(ctx.home, []), ctx.injured.get(ctx.away, [])
        key = qbs.key_edge(ctx.home, ctx.away, start) if qbs is not None else 0.0
        info = r.features(g, inj_out=(len(inj_h), len(inj_a)), key=key)
        if info is None or info["known"] < 5:
            continue
        f = info["f"]
        p_model = model.own(f)
        home_dog = m < 0.5 and not ctx.neutral
        pen_h, qb_h = _injury_pen(sport, inj_h)
        pen_a, qb_a = _injury_pen(sport, inj_a)
        key_out = qb_h or qb_a or (sport == "nhl" and any(pos.upper() == "G" for _, pos in inj_h + inj_a))
        if key_out:
            ph = sigmoid(logit(m) + model.blend[1] * move) if sport != "nhl" else m
            ph_own = m
        else:
            ph = model.final(p_model, m, move, home_dog, f)
            ph_own = model.final(p_model, m, 0.0, home_dog, f)
        raw = p_model
        if sport in ("nba", "ncaab"):
            w = min(1.0, min(hs.season_w + hs.season_l, as_.season_w + as_.season_l) / 10)
            shrink = lambda p: sigmoid(logit(m) + w * (logit(p) - logit(m)))  # noqa: E731
            ph, ph_own, raw = shrink(ph), shrink(ph_own), shrink(raw)
        # Injury penalties on the own read only (the QB part is skipped when the QB is the key-out).
        pen_h_own = 0.0 if qb_h and key_out else pen_h
        pen_a_own = 0.0 if qb_a and key_out else pen_a
        ph_own = min(max(ph_own - pen_h_own + pen_a_own, 0.01), 0.99)
        raw = min(max(raw - pen_h + pen_a, 0.01), 0.99)
        p = ph
        if not key_out:
            cal = model.calibrate(ph)
            p = m if (cal < m <= ph) else cal
        for side, s in (("home", 1), ("away", -1)):
            pk = picks[side]
            team, opp = (ctx.home, ctx.away) if s == 1 else (ctx.away, ctx.home)
            p_mkt = m if s == 1 else 1 - m
            drift = ((mo if s == 1 else 1 - mo) - p_mkt) if mo is not None else 0.0
            ts = hs if s == 1 else as_
            sd = Side(sport, eid, ctx.home, ctx.away, side, team, opp, start, pk, pk.american, p_mkt,
                      p if s == 1 else 1 - p, ph_own if s == 1 else 1 - ph_own, raw if s == 1 else 1 - raw,
                      info["elo_pts"] * s, drift, f, s, team_games=ts.season_w + ts.season_l, intl=g.intl,
                      neutral=ctx.neutral,
                      rest=info["rest_h"] if s == 1 else info["rest_a"], rest_opp=info["rest_a"] if s == 1 else info["rest_h"],
                      last_margin=ts.last_margin, streak=ts.streak, record=(ts.season_w, ts.season_l),
                      venue_tz=hs.home_tz, team_tz=ts.home_tz, qb_out=qb_h if s == 1 else qb_a)
            _reasons(sd, model, move)
            _hurt(sd, ctx)
            sd.fighting = sd.own < 1 / sd.dec - 0.03 or (sd.drift >= 0.03 and sport not in ("nfl", "ncaaf"))
            if (qb_h if s == 1 else qb_a) or any(pos.upper() in ("QB", "G")
                                                  for _, pos in ctx.questionable.get(team, [])):
                sd.waiting = any(pos.upper() in ("QB", "G") for _, pos in ctx.questionable.get(team, []))
            out.append(sd)
    _dog_scores(out, r)
    _weighed_favorites(out, r)
    return out


def _reasons(sd: Side, model: SportModel, move: float) -> None:
    w = dict(zip(("w0",) + FEATURES, model.w))
    f, s = sd.f, sd.s
    R = sd.reasons
    if sd.elo_pts >= 15:
        R.append("the stronger team")
    if f["form"] * s >= 0.08 and w["form"] > 0:
        R.append("hotter recent form")
    if f["rest"] * 7 * s >= 2 and w["rest"] > 0:
        R.append("better rested")
    if f["b2b"] * s > 0 and w["b2b"] > 0:
        R.append("opponent on a back-to-back")
    if f["inj"] * 5 * s >= 1 and w["inj"] > 0:
        R.append("opponent missing key players")
    if f["key"] * s >= REASON_KEY and w["key"] > 0:
        R.append("better QB play lately")
    if move * s >= 0.08:
        R.append("sharp money moving this way")
    for k, label in (("revenge", "revenge spot: lost the last meeting"), ("letdown", "opponent's letdown spot"),
                     ("bye", "coming off a bye"), ("short", "opponent on a short week")):
        if f[k] * s > 0 and w[k] >= 0.10:
            R.append(label)
    for k, label in (("alt", "visitors playing in thin air"), ("cold", "visitors from somewhere warm, in the cold"),
                     ("weather", "weather spot"), ("travel", "the body clock: opponent crossed time zones")):
        if f[k] * s > 0.05 and w[k] >= 0.10:
            R.append(label)
    if w["letdown"] <= -0.10 and f["letdown"] * s < 0:
        R.append("rolling off a blowout win")


def _hurt(sd: Side, ctx) -> None:
    """Hockey / hoops / baseball: 2+ out or 4+ questionable, or 2+ more out than the opponent = no units."""
    out = len(ctx.injured.get(sd.team, []))
    opp_out = len(ctx.injured.get(sd.opp, []))
    q = len(ctx.questionable.get(sd.team, []))
    if sd.sport in ("nhl", "nba", "ncaab", "mlb"):
        sd.hurt = out >= 2 or q >= 4 or out - opp_out >= 2
    if out:
        names = ", ".join(p for p, _ in ctx.injured[sd.team][:4])
        sd.notes.append(f"🚑 {sd.team} without {names}.")


def _dog_scores(sides: list[Side], r: Replay) -> None:
    by_game: dict[str, list[Side]] = {}
    for s in sides:
        by_game.setdefault(s.eid, []).append(s)
    for pair in by_game.values():
        if len(pair) != 2:
            continue
        for dog in pair:
            fav = pair[1] if pair[0] is dog else pair[0]
            if not DOG_MIN <= dog.odds <= DOG_MAX:
                continue
            sp = dog.sport
            base = (dog.own - dog.p_market) * 100
            if base > 12:
                base = 0.0 if sp in ("nfl", "nba") else 12.0
            td, tf = r.teams[dog.team], r.teams[fav.team]
            days_d = 99.0 if td.last is None else (dog.start - td.last).total_seconds() / 86400
            days_f = 99.0 if tf.last is None else (fav.start - tf.last).total_seconds() / 86400
            rest_d, rest_f = min(10.0, days_d), min(10.0, days_f)
            football = sp in ("nfl", "ncaaf")
            # A football dog off a blowout loss (NFL 21+, college 30+): the market overreacts.
            overreact = football and td.last_margin is not None and td.last_margin <= (-21 if sp == "nfl" else -30) \
                and days_d <= 21
            items: list[tuple[float, str]] = []
            add = lambda pts, why: items.append((pts, why))  # noqa: E731
            if sp in ("nhl", "nfl", "ncaaf", "nba") and dog.drift >= 0.02:
                add(-4, "money ran away from the dog")
            if sp in ("nba", "nhl") and rest_d > 1.2 and rest_f <= 1.2:
                add(3, "rested, and the favorite played yesterday")
            if sp in ("nfl", "ncaaf", "ncaab"):
                if tf.last_won is False:
                    add(2, "the favorite lost its last game")
                elif td.last_won is False and tf.last_won is True and not overreact:
                    add(-3, "we lost our last, they won theirs")
            if overreact:
                add(3, "off a blowout loss: the market overreacts")
            if sp in ("nfl", "ncaaf", "mlb") and td.last_won and td.last_ml is not None and td.last_ml >= 200:
                add(-3, "hangover: a dog again right after winning as a +200 dog")
            if sp == "nba" and td.last_won is True and tf.last_won is False:
                add(-2, "we won our last, they lost theirs")
            if sp == "nhl":
                if dog.odds >= 220:
                    add(-3, "+220 or longer")
                if dog.odds >= 200:
                    add(-2, "+200 or longer")
                if rest_d <= 1.2 < rest_f:
                    add(-3, "dog played last night, the favorite didn't")
                if td.last_won is False and tf.last_won is False:
                    add(3.5, "both teams lost their last game")
                if dog.drift <= -0.02:
                    add(2, "money came in on the dog")
            if sp == "mlb" and 200 <= dog.odds <= 249:
                add(-4, "+200 to +249 (a trap band)")
            if sp == "nfl":
                if td.home_tz is not None and td.home_tz >= -5 and dog.side == "away" and tf.home_tz is not None \
                        and tf.home_tz <= -7:
                    add(3, "East Coast team as a road dog out West")
                if dog.start.astimezone(ET).weekday() == 0:
                    add(1, "Monday night dog")
                if td.streak >= 3:
                    add(1, "3+ game win streak")
                if td.last_pts is not None and td.last_pts <= 10 and days_d <= 21:
                    add(1, "scored 10 or fewer last game")
                if td.season_w == 0 and td.season_l == 2:
                    add(3, "an 0-2 team")
                if dog.side == "home" and td.home_tz is not None and td.home_tz >= -5 and tf.home_tz is not None \
                        and tf.home_tz <= -7:
                    add(-2, "Eastern home dog vs a West Coast road favorite")
            if sp in ("nfl", "ncaaf"):
                if td.last_margin is not None and td.last_margin >= 17 and rest_d <= 21:
                    add(2, "off a 17+ point win")
                if 13 <= days_d <= 30 and days_f <= 8:  # (the old check read rest capped at 10: never fired)
                    add(3 if sp == "nfl" else 2, "off a bye vs a team that played")
                if dog.neutral and dog.own > dog.p_market:
                    add(1.5, "neutral site and our read is over the price")
            if sp == "ncaaf":
                n = td.season_w + td.season_l
                if n >= 4 and td.season_w / n >= 0.7:
                    add(1.5, ".700+ record")
                if td.streak <= -3:
                    add(-3, "3+ game losing streak")
            if football:
                ms = td.margins
                cold = len(ms) >= 5 and sum(ms[-3:]) / 3 <= sum(ms) / len(ms) - 7
                if cold:
                    add(-3, "ice cold: last 3 games 7+ points worse than its season")
                    if sp == "ncaaf" and td.streak <= -3:
                        add(2, "ice cold and a losing streak together (net -4)")
            if sp == "nba":
                if td.streak >= 3:
                    add(-2, "dog on a 3+ win streak")
                if td.last_margin is not None and td.last_margin >= 15:
                    add(-2, "dog off a 15+ win")
                if tf.last_margin is not None and tf.last_margin >= 20:
                    add(-2, "favorite off a 20+ win")
                if rest_f - rest_d >= 2:
                    add(-2, "favorite has 2+ more days rest")
                if dog.elo_pts >= -30 and dog.odds >= 200:
                    add(-2, "near-even ratings at +200")
                g_no = dog.team_games + 1
                if dog.side == "home" and 100 <= dog.odds <= 200 and 6 <= g_no <= 20:
                    add(4 if g_no <= 10 else 2, f"home dog in its game {g_no}")
            study = sum(p for p, _ in items)
            study = max(-STUDY_CAP, min(STUDY_CAP, study))
            dog.dog_items = study
            dog.dog_score = base + study
            for p, why in items:
                dog.notes.append(f"Dog score {p:+g}: {why}.")
            gate = DOG_GATE.get(sp)
            if gate is not None and not dog.fighting and dog.edge_own > 0 and dog.dog_score >= gate:
                dog.dog_p = min(0.95, dog.p_market + dog.dog_score / 100)


def _weighed_favorites(sides: list[Side], r: Replay) -> None:
    by_game: dict[str, list[Side]] = {}
    for s in sides:
        by_game.setdefault(s.eid, []).append(s)
    for pair in by_game.values():
        if len(pair) != 2:
            continue
        a, b = pair
        fav, dog = (a, b) if a.p_market >= b.p_market else (b, a)
        if fav.odds >= 100:
            continue
        clamp = lambda x: max(-FAV_CAP, min(FAV_CAP, x))  # noqa: E731
        if fav.sport == "nhl" and dog.dog_score is not None and DOG_MIN <= dog.odds <= DOG_MAX:
            fav.w_p = min(0.95, max(0.05, fav.p_market + clamp(-0.005 * dog.dog_score)))
        elif fav.sport == "nba":
            tf, td = r.teams[fav.team], r.teams[dog.team]
            rest = lambda t: 10.0 if t.last is None else min(10.0, (fav.start - t.last).total_seconds() / 86400)  # noqa
            tot = 0.0
            if rest(tf) - rest(td) >= 2:
                tot -= 0.02
            if tf.last_margin is not None and tf.last_margin >= 20:
                tot += 0.02
            if tot:
                fav.w_p = fav.p_market + clamp(tot)
        elif dog.dog_items:
            fav.w_p = fav.own + clamp(-0.005 * dog.dog_items)
        if fav.sport == "ncaaf" and -150 <= fav.odds <= -101:
            tf, td = r.teams[fav.team], r.teams[dog.team]
            if tf.last_won is False:
                fav.w_p = max(fav.own - 0.04, fav.p_market - 0.03)
            elif tf.last_won is True and td.last_won is False and fav.elo_pts > 0:
                fav.w_p = min(fav.own + 0.04, fav.p_market + 0.03)
                fav.reasons.append("Rule A: off a win vs a team off a loss")


def good(sd: Side) -> bool:
    if not sd.reasons or sd.fighting:
        return False
    if sd.sport == "nhl" and sd.odds < 0 and sd.w_p is not None and sd.w_p * sd.dec <= 1:
        return False
    if sd.odds >= 100:
        return sd.odds <= DOG_MAX and sd.dog_p is not None
    return sd.p >= 0.53 + (0.02 if sd.intl else 0.0)


def lock_ok(sd: Side) -> bool:
    return sd.odds <= 125 and sd.p >= 0.56 and not (sd.sport == "nba" and sd.team_games < 5)


def nhl_pricey(sd: Side) -> bool:
    return sd.sport == "nhl" and -150 <= sd.odds <= -130 and sd.w_p is None and sd.edge_own < 0.03


def lock_units(sd: Side) -> float:
    if sd.thin():
        return 0.5
    if sd.w_p is not None and sd.w_p * sd.dec > 1:
        return 3.0 if sd.edge_own > 0 else 2.0
    return 1.0


def dog_units(sd: Side) -> float:
    if sd.thin() or sd.sport in ("nba", "ncaab"):
        return 0.5
    return 2.0 if sd.edge_own > 0 else 1.0


def _pt_day(d: datetime) -> str:
    return d.astimezone(PT).date().isoformat()


def choose(sides: list[Side], now: datetime, taken: set[str] | frozenset = frozenset(),
           posted_events: set[str] | frozenset = frozenset()) -> list[tuple[str, float, Side, str]]:
    """(tier, units, side, label) for today's board."""
    today = _pt_day(now)
    pool = [s for s in sides if _pt_day(s.start) == today and s.eid not in posted_events and s.odds >= MAX_FAV]
    used: set[str] = set()
    out: list[tuple[str, float, Side, str]] = []
    if "lock" not in taken:
        locks = [s for s in pool if good(s) and MAX_FAV <= s.odds <= 125 and lock_ok(s)
                 and not -119 <= s.odds <= -101 and s.own_agrees() and s.real_value() and not nhl_pricey(s)
                 and not s.hurt and not s.waiting]
        if not locks:  # backup Lock
            locks = [s for s in pool if s.reasons and MAX_FAV <= s.odds <= 125 and not s.fighting and not s.hurt
                     and not s.waiting and not nhl_pricey(s) and not (s.sport == "nba" and s.team_games < 5)
                     and s.own >= 0.56 and s.edge_own > 0 and s.real_value()]
        if locks:
            lk = max(locks, key=lambda s: (round(s.p, 3), s.read_of * s.dec - 1))
            out.append(("lock", lock_units(lk), lk, "Lock of the Day"))
            used.add(lk.eid)
    if "dog" not in taken:
        dogs = [s for s in pool if s.eid not in used and good(s) and DOG_MIN <= s.odds <= DOG_MAX and s.beats_price()
                and (s.dog_score or 0) > 0 and not s.hurt and not s.waiting]
        regular = [s for s in dogs if s.odds < 200]
        pick = max(regular, key=lambda s: (s.dog_score, s.edge)) if regular else None
        big = [s for s in dogs if s.odds >= 200 and s.p >= 0.22 and (pick is None or s.edge >= pick.edge + 0.05)]
        if big:
            pick = max(big, key=lambda s: (s.dog_score, s.edge))
        if pick is None:
            nhl = [s for s in pool if s.sport == "nhl" and s.eid not in used and DOG_MIN <= s.odds <= DOG_MAX
                   and not s.fighting and not s.waiting and not s.hurt and s.edge_own > 0 and (s.dog_score or 0) > 0
                   and (s.p_market + s.dog_score / 100) * s.dec - 1 >= 0.01]
            pick = max(nhl, key=lambda s: s.dog_score) if nhl else None
        if pick:
            out.append(("dog", dog_units(pick), pick, "Dog of the Day"))
            used.add(pick.eid)
    plays = [s for s in pool if s.eid not in used and good(s) and MAX_FAV <= s.odds <= -101 and lock_ok(s)
             and s.own_agrees() and s.real_value() and s.own_beats() and not s.hurt and not s.waiting]
    for s in sorted(plays, key=lambda s: (s.p, s.edge), reverse=True):
        if s.eid not in used:
            out.append(("value", 0.5, s, "Value play"))
            used.add(s.eid)
    gaps = [s for s in pool if s.sport == "ncaaf" and s.eid not in used and DOG_MIN <= s.odds <= DOG_MAX
            and s.elo_pts >= 0 and s.raw_own - s.p_market >= 0.10 and s.beats_price()]
    for s in sorted(gaps, key=lambda s: s.raw_own - s.p_market, reverse=True):
        if s.eid not in used:
            s.notes.insert(0, f"🔍 Gap play: our ratings have {s.team} at {s.raw_own:.0%}, the books {s.p_market:.0%}.")
            out.append(("value", 1.0, s, "🔍 Gap play"))
            used.add(s.eid)
    # Leans: fill the board to BOARD_TARGET straight picks, 2 per sport, 0 units.
    need = BOARD_TARGET - len(out)
    per: dict[str, int] = {}
    leans = []
    for s in pool:
        if s.eid in used or s.odds < MAX_FAV or (s.sport == "ncaaf" and -150 <= s.odds <= -130) or nhl_pricey(s):
            continue
        if s.fighting and not s.own >= 0.55:
            continue
        if s.p >= 0.50 or (DOG_MIN <= s.odds <= DOG_MAX and s.edge_own >= 0.02):
            leans.append(s)
    for s in sorted(leans, key=lambda s: s.read_of, reverse=True):
        if need <= 0:
            break
        if s.eid in used or per.get(s.sport, 0) >= LEANS_PER_SPORT:
            continue
        out.append(("lean", 0.0, s, "Lean"))
        used.add(s.eid)
        per[s.sport] = per.get(s.sport, 0) + 1
        need -= 1
    return out


SPOT_WEIGHT = {"bye": 0.04, "mnf": 0.015, "eastwest": 0.03, "blowout": 0.02, "hammered": 0.02}
SPOT_UNITS = {"bye": 1.0, "mnf": 0.5, "eastwest": 0.5, "blowout": 0.5, "hammered": 0.5}
SPOT_LABEL = {"bye": "off a bye vs a team that played", "mnf": "Monday night dog",
              "eastwest": "East Coast team flying West", "blowout": "blew somebody out last week",
              "hammered": "hammered early: the price came in 4+ pts"}


def early_spots(s: Side) -> tuple[list[str], float]:
    """(spots hit, total weight) for an early value play (spec section 14)."""
    if s.sport not in ("nfl", "ncaaf") or not DOG_MIN <= s.odds <= DOG_MAX or s.qb_out or s.waiting:
        return [], 0.0
    if s.own < s.p_market - 0.01:
        return [], 0.0  # the engine is fighting the side
    hit = []
    if s.rest >= 13 and s.rest_opp <= 8:
        hit.append("bye")
    if s.sport == "nfl" and s.start.astimezone(ET).weekday() == 0:
        hit.append("mnf")
    if s.sport == "nfl" and s.side == "away" and not s.neutral and s.team_tz is not None and s.team_tz >= -5 \
            and s.venue_tz is not None and s.venue_tz <= -7:
        hit.append("eastwest")
    if s.sport == "ncaaf" and s.last_margin is not None and s.last_margin >= 17 and s.rest <= 21:
        hit.append("blowout")
    if s.drift <= -0.04:
        hit.append("hammered")
    if not hit:
        return [], 0.0
    gap = s.own - s.p_market
    gap = min(gap, 0.05) if s.sport == "ncaaf" else (0.0 if gap > 0.12 else gap)
    total = gap + sum(SPOT_WEIGHT[h] for h in hit)
    if s.sport == "ncaaf" and s.streak <= -3:
        total -= 0.04  # losing-streak fade
    w, l = s.record
    if s.sport == "ncaaf" and w + l >= 4 and w / (w + l) >= 0.7:
        total += 0.015
    if s.neutral and s.own > s.p_market:
        total += 0.015
    if s.sport == "nfl" and s.streak >= 3:
        total += 0.01
    return hit, total


def choose_early(sides: list[Side], now: datetime, posted_events: set[str] | frozenset = frozenset()):
    """Early value plays: NFL / college dogs before game day (never on the game's own date), within 8 days."""
    today = _pt_day(now)
    found: dict[str, list[tuple[Side, list[str], float]]] = {}
    for s in sides:
        if _pt_day(s.start) <= today or s.start > now + timedelta(days=8) or s.eid in posted_events:
            continue
        hit, total = early_spots(s)
        if hit and total >= 0.05:
            found.setdefault(s.eid, []).append((s, hit, total))
    out = []
    for eid, rows in found.items():
        if len(rows) != 1:
            continue  # both sides of one game hit: neither posts
        s, hit, total = rows[0]
        units = max(SPOT_UNITS[h] for h in hit)
        s.notes.insert(0, "⏰ Early value: " + ", ".join(SPOT_LABEL[h] for h in hit) + f" (score {total * 100:+.1f}).")
        out.append(("early", units, s, "⏰ Early value play"))
    return sorted(out, key=lambda t: -t[1])


def night_picks(sides: list[Side], now: datetime, chosen: list, posted_events) -> list:
    """Monday / Thursday (PT): every NFL game that night gets a pick (a lean is fine)."""
    today = _pt_day(now)
    if now.astimezone(PT).weekday() not in (0, 3):
        return []
    have = {s.eid for _, _, s, _ in chosen} | set(posted_events)
    games: dict[str, list[Side]] = {}
    for s in sides:
        if s.sport == "nfl" and _pt_day(s.start) == today and s.eid not in have and s.odds >= MAX_FAV:
            games.setdefault(s.eid, []).append(s)
    out = []
    for eid, pair in games.items():
        units_ok = [s for s in pair if good(s) and s.real_value() and not s.hurt and not s.waiting]
        if units_ok:
            s = max(units_ok, key=lambda s: (round(s.p * 50), s.edge))
            k = max(0.0, (s.read_of * s.dec - 1) / (s.dec - 1))
            units = 0.5 if s.thin() else min(5.0, max(0.5, round(0.25 * k * 100 * 2) / 2))
            out.append(("value", units, s, "🏈 Night game pick"))
        else:
            calm = [s for s in pair if not s.fighting] or pair
            s = max(calm, key=lambda s: s.p)
            out.append(("lean", 0.0, s, "🏈 Night game lean"))
    return out


def _card(tier: str, units: float, s: Side, label: str, sport_name: str) -> Card:
    read = s.read_of
    be = 1 / s.dec
    pk = replace(s.pick, win_prob=read, model_prob=s.own, ev=read * s.dec - 1,
                 kelly=max(0.0, (read * s.dec - 1) / (s.dec - 1)), fair_american=_fair(read))
    head = (f"{s.team} at {s.odds:+.0f}: we got em {read:.0%}, the price needs {be:.0%}."
            if units > 0 else f"Lean {s.team}: {s.p:.0%} on our numbers (needs {be:.0%} at {s.odds:+.0f}).")
    why = [f"{label}{f' · {units:g}u' if units else ' · no units'}" + (" (thin edge)" if units == 0.5 and tier in ("lock", "dog") else ""),
           f"Our read {s.p:.0%} · own read {s.own:.0%} · market (no vig) {s.p_market:.0%}"
           + (f" · weighed read {s.w_p:.0%}" if s.w_p is not None else "")
           + (f" · dog score {s.dog_score:+.1f}" if s.dog_score is not None else "") + "."]
    if s.reasons:
        why.append("Why: " + "; ".join(s.reasons[:3]) + ".")
    if s.drift:
        why.append(f"Line since the open: {'money ran away' if s.drift > 0 else 'money came in'} "
                   f"({abs(s.drift) * 100:.1f} pts).")
    why += s.notes[:6]
    why.append("Engine: D503 formula.")
    return Card(tier=tier, units=units, headline=head, reasons=why, sport=s.sport, sport_name=sport_name, pick=pk,
                home=s.home, away=s.away)


def _fair(p: float) -> float:
    p = min(max(p, 0.01), 0.99)
    return round(-100 * p / (1 - p)) if p >= 0.5 else round(100 * (1 - p) / p)


def build(scans, now: datetime, models: dict[str, SportModel], taken: set[str] | frozenset = frozenset(),
          posted_events: set[str] | frozenset = frozenset(), root="data") -> list[Card]:
    sides: list[Side] = []
    names: dict[str, str] = {}
    for scan in scans:
        sport = scan.sport.key
        if sport not in D5_SPORTS or not scan.contexts:
            continue
        model = models.get(sport)
        if model is None:
            try:
                model = models[sport] = fit(sport, root=root)
            except Exception as exc:  # no history for a sport: skip it, never guess
                log.warning("D503 model for %s unavailable: %s", sport, exc)
                continue
        names[sport] = scan.sport.name
        try:
            sides += build_sides(scan, model, now, root)
        except Exception as exc:
            log.warning("D503 board for %s failed: %s", sport, exc, exc_info=True)
    chosen = choose(sides, now, taken, posted_events)
    chosen += night_picks(sides, now, chosen, posted_events)
    chosen += choose_early(sides, now, posted_events)
    print(f"D503 board: {len(sides)} sides, " + ", ".join(f"{t} {s.team} {s.odds:+.0f} {u:g}u" for t, u, s, _ in chosen))
    return [_card(t, u, s, label, names.get(s.sport, s.sport)) for t, u, s, label in chosen]


__all__ = ["build", "build_sides", "choose", "Side", "D5_SPORTS"]
