"""UFC: fighter ratings from every past result, DraftKings lines, and a track record built from scratch.

ESPN keeps every UFC result for years but drops the odds once a fight is over, so
UFC can't be tested against two seasons of past closing lines like the other
sports. Instead:

- fighter ratings (Elo) are replayed over every UFC fight since 2019, predicting
  each fight before it happened (no peeking), which measures how well the
  ratings pick winners;
- every scan saves each upcoming fight's DraftKings line; the last line before
  the fight is its close. Once fights are graded, the same blind test as the
  other sports runs on them: the blend of our number and the market's, at
  -170 or better, 2%+ edge, judged on ROI over 60+ bets.

Nothing is posted as a UFC bet until that test is passed. Until then the Model
tab shows the research: each fight's market and model numbers, and the record.
"""

from __future__ import annotations

import json
import logging
import math
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

from .data.espn import BASE, EspnClient, parse_date
from .odds import american_to_decimal, implied_prob

log = logging.getLogger(__name__)

SITE = f"{BASE}/mma/ufc"
CORE = "https://sports.core.api.espn.com/v2/sports/mma/leagues/ufc"
DK = "100"
HISTORY_START = datetime(2019, 1, 1, tzinfo=timezone.utc)
START_ELO = 1500.0
K_NEW, K_SETTLED, NEW_FIGHTS = 40.0, 28.0, 3
MARKET_WEIGHT = 0.75  # same trust in the market as the other sports start with
MIN_EV = 0.02
MAX_FAVORITE = -170
PROVEN_BETS = 60


@dataclass
class Fight:
    card_id: str
    fight_id: str
    card: str
    date: str  # ISO
    weight: str
    a_id: str
    a_name: str
    a_record: str
    b_id: str
    b_name: str
    b_record: str
    state: str = ""  # pre / in / post
    winner: str | None = None  # a_id, b_id, or None (upcoming, draw, no contest)

    @property
    def when(self) -> datetime:
        return parse_date(self.date)


def parse_card(ev: dict) -> list[Fight]:
    out = []
    for comp in ev.get("competitions", []) or []:
        cs = [c for c in comp.get("competitors", []) if c.get("type", "athlete") == "athlete"]
        if len(cs) != 2:
            continue
        cs.sort(key=lambda c: c.get("order", 0))
        a, b = cs
        status = (comp.get("status") or {}).get("type") or {}
        done = bool(status.get("completed"))
        winner = next((str(c["id"]) for c in cs if c.get("winner")), None) if done else None

        def rec(c):
            return next((r.get("summary", "") for r in c.get("records", []) if r.get("type") == "total"), "")
        out.append(Fight(
            card_id=str(ev.get("id")), fight_id=str(comp.get("id")), card=ev.get("name", ""),
            date=comp.get("date") or ev.get("date"), weight=(comp.get("type") or {}).get("abbreviation", ""),
            a_id=str(a["id"]), a_name=a["athlete"]["displayName"], a_record=rec(a),
            b_id=str(b["id"]), b_name=b["athlete"]["displayName"], b_record=rec(b),
            state="post" if done else status.get("state", ""), winner=winner,
        ))
    return out


def _american(v) -> float | None:
    try:
        return float(str(v).replace("+", "")) if v not in (None, "", "OFF", "EVEN") else (100.0 if v == "EVEN" else None)
    except ValueError:
        return None


def parse_fight_odds(o: dict, fight: Fight) -> dict | None:
    """{fighter id: {"ml": current, "open": opening}} from a DraftKings odds object."""
    out = {}
    for key in ("awayAthleteOdds", "homeAthleteOdds"):
        side = o.get(key) or {}
        ref = ((side.get("athlete") or {}).get("$ref") or "").split("?")[0]
        aid = ref.rstrip("/").rsplit("/", 1)[-1] if ref else ""
        ml = _american(side.get("moneyLine"))
        opened = _american((((side.get("open") or {}).get("moneyLine")) or {}).get("american"))
        if aid in (fight.a_id, fight.b_id) and ml is not None:
            out[aid] = {"ml": ml, "open": opened}
    return out if len(out) == 2 else None


class UfcData:
    def __init__(self, espn: EspnClient | None = None):
        self.espn = espn or EspnClient()

    def fights(self, start: datetime, end: datetime, now: datetime) -> list[Fight]:
        out: dict[str, Fight] = {}
        month = datetime(start.year, start.month, 1, tzinfo=timezone.utc)
        while month <= end:
            nxt = datetime(month.year + month.month // 12, month.month % 12 + 1, 1, tzinfo=timezone.utc)
            final = nxt < now - timedelta(days=3)  # finished months never change: cached for good
            try:
                data = self.espn._get(f"{SITE}/scoreboard", {"dates": f"{month:%Y%m}", "limit": 500},
                                      ttl=None if final else 600)
            except requests.RequestException as exc:
                log.warning("ESPN UFC %s failed: %s", f"{month:%Y%m}", exc)
                data = {}
            for ev in data.get("events", []) or []:
                for f in parse_card(ev):
                    out[f.fight_id] = f
            month = nxt
        return sorted(out.values(), key=lambda f: (f.date, f.fight_id))

    def odds(self, fight: Fight) -> dict | None:
        url = f"{CORE}/events/{fight.card_id}/competitions/{fight.fight_id}/odds"
        try:
            listing = self.espn._get(url, {}, ttl=600)
        except requests.RequestException as exc:
            log.info("UFC odds %s unavailable: %s", fight.fight_id, exc)
            return None
        for item in listing.get("items", []) or []:
            pid = str((item.get("provider") or {}).get("id") or "")
            if pid != DK:
                continue
            o = item
            if "awayAthleteOdds" not in item and item.get("$ref"):
                try:
                    o = self.espn._get(item["$ref"].replace("http://", "https://"), {}, ttl=600)
                except requests.RequestException:
                    return None
            return parse_fight_odds(o, fight)
        return None


# ---------------------------------------------------------------------- ratings
def elo_p(ra: float, rb: float) -> float:
    return 1 / (1 + 10 ** ((rb - ra) / 400))


@dataclass
class Ratings:
    elo: dict[str, float] = field(default_factory=dict)
    fights: dict[str, int] = field(default_factory=dict)

    def get(self, fid: str) -> float:
        return self.elo.get(fid, START_ELO)

    def p(self, f: Fight) -> float:
        return elo_p(self.get(f.a_id), self.get(f.b_id))

    def update(self, f: Fight) -> None:
        if f.winner not in (f.a_id, f.b_id):
            return  # draws and no contests teach nothing about who is better
        p = self.p(f)
        y = 1.0 if f.winner == f.a_id else 0.0
        for fid, sign in ((f.a_id, 1), (f.b_id, -1)):
            k = K_NEW if self.fights.get(fid, 0) < NEW_FIGHTS else K_SETTLED
            self.elo[fid] = self.get(fid) + sign * k * (y - p)
            self.fights[fid] = self.fights.get(fid, 0) + 1


def no_vig(a: float, b: float) -> float:
    pa, pb = implied_prob(a), implied_prob(b)
    return pa / (pa + pb)


# ---------------------------------------------------------------------- closing lines
class Lines:
    """data/history/ufc/<YYYY-MM>.json: fight id -> {fighter id: {"ml", "open"}, "seen": iso}.
    Overwritten on every scan until the fight starts, so the last line kept is the close."""

    def __init__(self, folder: Path):
        self.folder = Path(folder)

    def _path(self, when: datetime) -> Path:
        return self.folder / f"{when:%Y-%m}.json"

    def load_all(self) -> dict[str, dict]:
        out = {}
        for p in sorted(self.folder.glob("*.json")) if self.folder.exists() else []:
            out.update(json.loads(p.read_text()))
        return out

    def save(self, fight: Fight, odds: dict, now: datetime) -> None:
        path = self._path(fight.when)
        data = json.loads(path.read_text()) if path.exists() else {}
        prev = data.get(fight.fight_id, {})
        for fid, o in odds.items():  # keep the first opening line we ever saw
            if o.get("open") is None and (prev.get(fid) or {}).get("open") is not None:
                o["open"] = prev[fid]["open"]
        data[fight.fight_id] = {**odds, "seen": now.isoformat()}
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=1, sort_keys=True))


# ---------------------------------------------------------------------- study
def _summary(bets: list[tuple[float, bool]]) -> dict:
    """(american odds, won) per 1-unit bet."""
    if not bets:
        return {"bets": 0, "won": 0, "lost": 0, "units": 0.0, "roi": None, "se": None}
    profits = [(american_to_decimal(a) - 1) if w else -1.0 for a, w in bets]
    roi = sum(profits) / len(profits)
    se = math.sqrt(sum((x - roi) ** 2 for x in profits) / (len(profits) - 1) / len(profits)) if len(profits) > 1 else None
    won = sum(1 for _, w in bets if w)
    return {"bets": len(bets), "won": won, "lost": len(bets) - won, "units": round(sum(profits), 2),
            "roi": roi, "se": se}


def study(fights: list[Fight], lines: dict[str, dict], now: datetime) -> dict:
    """Replay every fight in order, predicting each one before updating the ratings."""
    ratings = Ratings()
    n = right = called = 0
    ll = 0.0
    buckets: dict[int, list[tuple[float, float]]] = {}
    bets: list[tuple[float, bool]] = []
    upcoming = []
    for f in fights:
        p = ratings.p(f)
        line = lines.get(f.fight_id)
        if f.state == "post" and f.winner in (f.a_id, f.b_id):
            y = 1.0 if f.winner == f.a_id else 0.0
            n += 1
            if p != 0.5:  # two debutants: no call to make
                called += 1
                right += (p > 0.5) == (y == 1.0)
            q = min(max(p, 1e-3), 1 - 1e-3)
            ll -= y * math.log(q) + (1 - y) * math.log(1 - q)
            fav, won = (p, y == 1.0) if p >= 0.5 else (1 - p, y == 0.0)
            buckets.setdefault(min(int(fav * 10), 9), []).append((fav, float(won)))
            if line and f.a_id in line and f.b_id in line:
                bet = _pick(f, p, line)
                if bet:
                    side, price = bet
                    bets.append((price, f.winner == side))
            ratings.update(f)
        elif f.state in ("", "pre") and f.when > now:
            row = {"fight_id": f.fight_id, "card": f.card, "date": f.date, "weight": f.weight,
                   "a": f.a_name, "a_record": f.a_record, "b": f.b_name, "b_record": f.b_record,
                   "model_a": round(p, 3), "a_fights": ratings.fights.get(f.a_id, 0),
                   "b_fights": ratings.fights.get(f.b_id, 0)}
            if line and f.a_id in line and f.b_id in line:
                a_ml, b_ml = line[f.a_id]["ml"], line[f.b_id]["ml"]
                row.update(a_ml=a_ml, b_ml=b_ml, market_a=round(no_vig(a_ml, b_ml), 3))
            upcoming.append(row)
    market = _summary(bets)
    market["proven"] = market["bets"] >= PROVEN_BETS and market["roi"] is not None \
        and market["roi"] - (market["se"] or 0) > 0
    return {
        "ran_at": now.isoformat(),
        "fights": n,
        "called": called,
        "accuracy": right / called if called else None,
        "log_loss": ll / n if n else None,  # a coin flip scores 0.693
        "calibration": [(sum(a for a, _ in v) / len(v), sum(b for _, b in v) / len(v), len(v))
                        for _, v in sorted(buckets.items()) if len(v) >= 20],
        "market": market,
        "lines_saved": len(lines),
        "upcoming": upcoming,
        "note": ("Not proven: UFC bets post only after the blend beats DraftKings' closing lines over "
                 f"{PROVEN_BETS}+ graded fights. Lines have been saved since this was added; ESPN drops "
                 "UFC odds after each fight, so there is no older history to test on."),
    }


def _pick(f: Fight, p: float, line: dict) -> tuple[str, float] | None:
    """The bet the blind test makes: blended edge of 2%+ at -170 or better."""
    a_ml, b_ml = line[f.a_id]["ml"], line[f.b_id]["ml"]
    m = no_vig(a_ml, b_ml)
    q = (1 - MARKET_WEIGHT) * p + MARKET_WEIGHT * m
    best = None
    for side, prob, price in ((f.a_id, q, a_ml), (f.b_id, 1 - q, b_ml)):
        ev = prob * american_to_decimal(price) - 1
        if price >= MAX_FAVORITE and ev >= MIN_EV and (best is None or ev > best[0]):
            best = (ev, side, price)
    return (best[1], best[2]) if best else None


def run(data_dir: Path, now: datetime | None = None, source: UfcData | None = None) -> dict:
    """Refresh fights and lines, rerun the study, save data/ufc.json."""
    now = now or datetime.now(timezone.utc)
    source = source or UfcData()
    fights = source.fights(HISTORY_START, now + timedelta(days=21), now)
    lines = Lines(Path(data_dir) / "history" / "ufc")
    for f in fights:
        if f.state in ("", "pre") and now < f.when <= now + timedelta(days=14):
            odds = source.odds(f)
            if odds:
                lines.save(f, odds, now)
    out = study(fights, lines.load_all(), now)
    Path(data_dir).mkdir(parents=True, exist_ok=True)
    (Path(data_dir) / "ufc.json").write_text(json.dumps(out, indent=1))
    return out


def load(data_dir: Path) -> dict:
    try:
        return json.loads((Path(data_dir) / "ufc.json").read_text())
    except (OSError, ValueError):
        return {}
