"""NBA player report (owner, Oct 25): by position, who's healthy and who isn't, who's playing badly
("shitting the bed") or hot, which teams are due, and what that means for today's games.

Player box scores come from ESPN (only the pipeline can reach it): ``collect`` saves each finished
game's players to data/players/nba/<season>.json as
    gid -> {"date", "home", "away", "players": [[team, name, pos, starter, min, pts, reb, ast, pm,
                                                  fgm, fga, tpm, tpa, ftm, fta], ...]}
``build`` turns that, this season's results and today's injury report into data/nba_report.json,
which the site's NBA tab shows. Research only: nothing here posts a pick or carries units until a
blind study proves it (nbastudy.py tests the team "due" angles).
"""

from __future__ import annotations

import csv
import json
import logging
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

log = logging.getLogger(__name__)

POSITIONS = ("PG", "SG", "SF", "PF", "C")
REGULAR_MPG = 20.0  # a rotation regular
COLD_TS = 0.06  # true shooting 6+ points under his baseline...
COLD_SCORING = 0.85  # ...and scoring per 36 at 85% of it or less
FORM_GAMES = 5
MIN_BASE_GAMES = 10
DUE_GAMES = 10  # team luck needs 10 games
COLS = ("team", "name", "pos", "starter", "min", "pts", "reb", "ast", "pm", "fgm", "fga", "tpm", "tpa", "ftm", "fta")


def season_of(when: datetime) -> int:
    """The year the season started (Oct 2026 - Jun 2027 -> 2026)."""
    return when.year if when.month >= 7 else when.year - 1


def _num(raw) -> tuple[float, float | None] | None:
    s = str(raw or "").strip()
    if not s or s in ("--", "-"):
        return None
    if "-" in s[1:] and not s.startswith("-"):  # made-attempted, e.g. "7-15" (a plus-minus is "-12")
        a, _, b = s.partition("-")
        try:
            return float(a), float(b)
        except ValueError:
            return None
    try:
        return float(s.replace("+", "")), None
    except ValueError:
        return None


def parse(summary: dict) -> list[list]:
    """Rows in COLS order for every player who played, from an ESPN NBA summary."""
    out = []
    for block in (summary.get("boxscore") or {}).get("players") or []:
        team = (block.get("team") or {}).get("displayName", "")
        for group in block.get("statistics") or []:
            labels = [str(x).upper() for x in (group.get("labels") or group.get("names") or [])]
            for ath in group.get("athletes") or []:
                if ath.get("didNotPlay"):
                    continue
                a = ath.get("athlete") or {}
                name = a.get("displayName")
                stats = ath.get("stats") or []
                if not name or not stats:
                    continue
                v = {lab: _num(raw) for lab, raw in zip(labels, stats)}
                one = lambda k: (v.get(k) or (0.0, None))[0]  # noqa: E731
                pair = lambda k: v.get(k) or (0.0, 0.0)  # noqa: E731
                mins = one("MIN")
                if mins <= 0:
                    continue
                fg, tp, ft = pair("FG"), pair("3PT"), pair("FT")
                pos = ((a.get("position") or {}).get("abbreviation") or "").upper()
                out.append([team, name, pos, bool(ath.get("starter")), mins, one("PTS"), one("REB"), one("AST"),
                            one("+/-"), fg[0], fg[1] or 0.0, tp[0], tp[1] or 0.0, ft[0], ft[1] or 0.0])
    return out


def _path(root: Path | str, season: int) -> Path:
    return Path(root) / "players" / "nba" / f"{season}.json"


def load_season(root: Path | str, season: int) -> dict:
    try:
        return json.loads(_path(root, season).read_text())
    except (OSError, ValueError):
        return {}


def _finished(root: Path | str, season: int, now: datetime) -> list[tuple[datetime, dict]]:
    out = []
    for f in sorted((Path(root) / "games" / "nba").glob("*.csv")):
        with f.open() as fh:
            for r in csv.DictReader(fh):
                try:
                    when = datetime.fromisoformat(r["date"])
                except (KeyError, ValueError):
                    continue
                if r.get("home_score") and when < now and season_of(when) == season:
                    out.append((when, r))
    return sorted(out, key=lambda x: x[0])


def collect(client, now: datetime, root: Path | str = "data", budget: int = 60, seasons: int = 2) -> int:
    """Save player box scores for finished games not yet saved, this season first (newest first),
    then last season. Returns how many games were added."""
    from .sports import SPORTS

    added = 0
    for season in range(season_of(now), season_of(now) - seasons, -1):
        store = load_season(root, season)
        todo = [(w, r) for w, r in _finished(root, season, now) if r["event_id"] not in store]
        new = 0
        for when, r in reversed(todo):
            if added >= budget:
                break
            try:
                rows = parse(client.summary(SPORTS["nba"], r["event_id"]))
            except Exception as exc:  # one bad box score never stops the rest
                log.info("NBA box score %s failed: %s", r["event_id"], exc)
                continue
            if rows:
                store[r["event_id"]] = {"date": when.isoformat(), "home": r["home"], "away": r["away"], "players": rows}
                added += 1
                new += 1
        if new:
            p = _path(root, season)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps(store, separators=(",", ":"), sort_keys=True))
    return added


# --------------------------------------------------------------------- report

class Line:
    """A player's totals over some games."""

    def __init__(self) -> None:
        self.g = 0
        self.t = Counter()

    def add(self, row: dict) -> None:
        self.g += 1
        self.t.update({k: row[k] for k in COLS[4:]})

    @property
    def mpg(self) -> float:
        return self.t["min"] / self.g if self.g else 0.0

    def per36(self, k: str) -> float:
        return 36 * self.t[k] / self.t["min"] if self.t["min"] else 0.0

    def per_game(self, k: str) -> float:
        return self.t[k] / self.g if self.g else 0.0

    @property
    def ts(self) -> float | None:
        shots = 2 * (self.t["fga"] + 0.44 * self.t["fta"])
        return self.t["pts"] / shots if shots else None


def _rows(store: dict) -> list[tuple[datetime, str, dict]]:
    out = []
    for gid, g in store.items():
        when = datetime.fromisoformat(g["date"])
        for r in g["players"]:
            out.append((when, gid, dict(zip(COLS, r))))
    return sorted(out, key=lambda x: (x[0], x[1]))


def _team_due(root: Path | str, season: int, now: datetime) -> dict[str, dict]:
    from .nbastudy import Team

    teams: dict[str, Team] = defaultdict(Team)
    for _, r in _finished(root, season, now):
        hs, as_ = float(r["home_score"]), float(r["away_score"])
        teams[r["home"]].add(hs, as_, False)
        teams[r["away"]].add(as_, hs, False)
    out = {}
    for name, t in teams.items():
        d = {"w": t.w, "l": t.l, "diff": round((t.pf - t.pa) / t.games, 1) if t.games else 0.0,
             "close": t.close, "last5": round(sum(t.last5) / len(t.last5), 1) if t.last5 else None}
        if t.games >= DUE_GAMES:
            d["luck"] = round(t.luck(), 3)
            d["slump"] = round(t.slump(), 1)
            notes = []
            if d["luck"] <= -0.06:
                notes.append(f"due: record {abs(d['luck']) * 100:.0f} pts under what its point differential says")
            elif d["luck"] >= 0.06:
                notes.append(f"lucky: record {d['luck'] * 100:.0f} pts over its point differential")
            if t.close <= -3:
                notes.append(f"{-t.close} more close losses than wins")
            elif t.close >= 3:
                notes.append(f"{t.close} more close wins than losses")
            if d["slump"] <= -8:
                notes.append(f"slumping: last 5 games {abs(d['slump']):.0f} pts a game under its season")
            elif d["slump"] >= 8:
                notes.append(f"hot: last 5 games {d['slump']:.0f} pts a game over its season")
            d["notes"] = notes
        else:
            d["notes"] = [f"{t.games} games in: luck reads start at {DUE_GAMES}"]
        out[name] = d
    return out


def _broad(pos: str) -> str:
    """ESPN lists NBA players as G, F or C (sometimes PG/SG/SF/PF)."""
    return {"PG": "G", "SG": "G", "SF": "F", "PF": "F"}.get(pos, pos)


def _lineup(team: str, starts: list, this: dict, pos_of) -> dict[str, str]:
    """PG/SG/SF/PF/C from the team's 5 most frequent starters over its last 5 games. ESPN only says
    G, F or C, so the five are ordered guard -> big (G, F, C; within one, more assists and fewer
    rebounds = the smaller spot) and handed PG, SG, SF, PF, C in that order."""
    recent = sorted(starts)[-25:]
    five = [n for n, _ in Counter(n for _, n, _ in recent).most_common(5)]
    rank = {"G": 0, "F": 1, "C": 2}

    def size(n: str) -> tuple:
        line = this.get((team, n))
        reb = line.per_game("reb") if line else 0.0
        ast = line.per_game("ast") if line else 0.0
        return rank.get(_broad(pos_of(n)), 1), reb - ast

    return dict(zip(POSITIONS, sorted(five, key=size)))


def game_score(line: "Line") -> float:
    """Per game: Hollinger's game score from what the box score keeps (no steals, blocks, turnovers):
    PTS + 0.4 FGM - 0.7 FGA - 0.4 (FTA - FTM) + 0.5 REB + 0.7 AST."""
    t, g = line.t, line.g or 1
    return (t["pts"] + 0.4 * t["fgm"] - 0.7 * t["fga"] - 0.4 * (t["fta"] - t["ftm"]) + 0.5 * t["reb"] + 0.7 * t["ast"]) / g


def _roster(team: str, this: dict, pos_of, ww: dict, by_name: dict, out_names: set, q_names: set,
            starters: set) -> list[dict]:
    """Every player who has played for the team this season: role, production, share of the team's
    production, tier, the team's margin with and without him, form and today's status."""
    from .roster import impact_line, tiers

    mine = {n: line for (t, n), line in this.items() if t == team}
    played = max((line.g for line in mine.values()), default=1)
    value = {n: max(game_score(line), 0.0) * line.g / played for n, line in mine.items()}
    tier = tiers(value, 0.17, 0.10, 0.04)
    total = sum(value.values()) or 1.0
    out = []
    for n, line in sorted(mine.items(), key=lambda kv: -value[kv[0]]):
        p = by_name.get((team, n)) or {}
        out.append({"name": n, "pos": pos_of(n), "gp": line.g, "tier": tier[n], "share": round(value[n] / total, 3),
                    "starter": n in starters,
                    "line": f"{line.per_game('pts'):.1f} pts · {line.per_game('reb'):.1f} reb · {line.per_game('ast'):.1f} ast · "
                            f"{line.mpg:.0f} min" + (f" · {line.ts:.0%} TS" if line.ts else ""),
                    "gmsc": round(game_score(line), 1), "ww": ww.get(n), "impact": impact_line(ww.get(n)),
                    "form": (p.get("form") or {}).get("tag"),
                    "status": "out" if n in out_names else "questionable" if n in q_names else None})
    return out


def build(now: datetime, root: Path | str = "data", injuries: dict | None = None,
          games_today: list[dict] | None = None, study: dict | None = None) -> dict:
    """The report the NBA tab shows. ``injuries``: team -> {"out": [(player, pos)], "questionable": [...]};
    ``games_today``: [{"home", "away", "start", "home_ml", "away_ml", "spread"}]."""
    season = season_of(now)
    cur, prev = load_season(root, season), load_season(root, season - 1)
    base: dict[tuple[str, str], Line] = defaultdict(Line)  # last season
    this: dict[tuple[str, str], Line] = defaultdict(Line)
    recent: dict[tuple[str, str], list[dict]] = defaultdict(list)
    pos_count: dict[tuple[str, str], Counter] = defaultdict(Counter)
    starts: dict[str, list[tuple[datetime, str, str]]] = defaultdict(list)  # team -> (date, player, pos)
    team_of: dict[str, str] = {}
    for _, _, r in _rows(prev):
        base[("", r["name"])].add(r)
        if r["pos"]:
            pos_count[("", r["name"])][r["pos"]] += 1
    for when, _, r in _rows(cur):
        key = (r["team"], r["name"])
        this[key].add(r)
        recent[key].append(r)
        team_of[r["name"]] = r["team"]
        if r["pos"]:
            pos_count[("", r["name"])][r["pos"]] += 1
        if r["starter"]:
            starts[r["team"]].append((when, r["name"], r["pos"]))

    def pos_of(name: str) -> str:
        c = pos_count.get(("", name))
        return c.most_common(1)[0][0] if c else ""

    players = []
    for (team, name), line in this.items():
        b = base.get(("", name))
        if b is not None and b.g < MIN_BASE_GAMES:
            b = None
        last = Line()
        for r in recent[(team, name)][-FORM_GAMES:]:
            last.add(r)
        mpg = line.mpg
        if mpg < 15 and (b is None or b.mpg < REGULAR_MPG):
            continue
        ref = b if b is not None else line  # early season: last season is the baseline
        cmp_ = last if b is not None or line.g > FORM_GAMES else None
        p = {"team": team, "name": name, "pos": pos_of(name), "g": line.g, "mpg": round(mpg, 1),
             "ppg": round(line.per_game("pts"), 1), "rpg": round(line.per_game("reb"), 1),
             "apg": round(line.per_game("ast"), 1), "pm": round(line.per_game("pm"), 1),
             "ts": line.ts and round(line.ts, 3),
             "base": None if b is None else {"g": b.g, "mpg": round(b.mpg, 1), "ppg": round(b.per_game("pts"), 1),
                                             "ts": b.ts and round(b.ts, 3)},
             "form": None}
        if cmp_ is not None and cmp_.g >= 3 and ref.ts and cmp_.ts:
            dts = cmp_.ts - ref.ts
            ratio = cmp_.per36("pts") / ref.per36("pts") if ref.per36("pts") else 1.0
            mins = cmp_.mpg / ref.mpg if ref.mpg else 1.0
            tag = None
            if dts <= -COLD_TS and ratio <= COLD_SCORING:
                tag = "cold"
            elif dts >= COLD_TS and ratio >= 2 - COLD_SCORING:
                tag = "hot"
            elif mins <= 0.75 and ref.mpg >= REGULAR_MPG:
                tag = "losing minutes"
            p["form"] = {"tag": tag, "games": cmp_.g, "ppg": round(cmp_.per_game("pts"), 1),
                         "ts": round(cmp_.ts, 3), "d_ts": round(dts, 3), "scoring": round(ratio, 2),
                         "mpg": round(cmp_.mpg, 1), "pm": round(cmp_.per_game("pm"), 1),
                         "vs": "last season" if b is not None else "his season"}
        players.append(p)
    by_name = {(p["team"], p["name"]): p for p in players}

    # With/without for every player: this season and last, his team's margin with him and without.
    from .roster import margins, team_index, with_without

    margin = margins(root, "nba")
    appear, dates = team_index({**prev, **cur}, lambda r: r[0], lambda r: r[1])
    ww_by_team: dict[str, dict] = {}

    # Usual starters by position (last 5 starts each team), and today's health.
    teams: dict[str, dict] = {}
    due = _team_due(root, season, now)
    inj = injuries or {}
    for team in sorted(set(t for t, _ in this) | set(due) | set(inj)):
        lineup = _lineup(team, starts.get(team, []), this, pos_of)
        t_inj = inj.get(team, {})
        out_names = {n for n, _ in t_inj.get("out", [])}
        q_names = {n for n, _ in t_inj.get("questionable", [])}
        mins_out = pts_out = 0.0
        tot_min = sum(p["mpg"] for p in players if p["team"] == team) or 240.0
        tot_pts = sum(p["ppg"] for p in players if p["team"] == team) or 1.0
        hurt = []
        for n, pos in t_inj.get("out", []) + t_inj.get("questionable", []):
            p = by_name.get((team, n))
            status = "out" if n in out_names else "questionable"
            if p and status == "out":
                mins_out += p["mpg"]
                pts_out += p["ppg"]
            hurt.append({"name": n, "pos": pos or (p or {}).get("pos", ""), "status": status,
                         "mpg": p and p["mpg"], "ppg": p and p["ppg"],
                         "starter": n in lineup.values()})
        holes = []
        for slot in POSITIONS:
            n = lineup.get(slot)
            if n and (n in out_names or n in q_names):
                broad = _broad(by_name.get((team, n), {}).get("pos", ""))
                subs = sorted((p for p in players if p["team"] == team and _broad(p["pos"]) == broad
                               and p["name"] not in lineup.values() and p["name"] not in out_names), key=lambda p: -p["mpg"])
                holes.append({"slot": slot, "starter": n, "status": "out" if n in out_names else "questionable",
                              "next": subs[0]["name"] if subs else None})
        roster = _roster(team, this, pos_of, ww_by_team.setdefault(team, with_without(appear, dates, margin, team)),
                         by_name, out_names, q_names, set(lineup.values()))
        lost = sum(p["share"] for p in roster if p["status"] == "out")
        teams[team] = {"lineup": lineup, "hurt": hurt, "holes": holes, "roster": roster, "production_out": round(lost, 3),
                       "minutes_out": round(mins_out / tot_min, 3), "points_out": round(pts_out / tot_pts, 3),
                       "cold": [p["name"] for p in players if p["team"] == team and (p["form"] or {}).get("tag") == "cold"],
                       "hot": [p["name"] for p in players if p["team"] == team and (p["form"] or {}).get("tag") == "hot"],
                       "due": due.get(team)}

    matchups = []
    for g in games_today or []:
        h, a = teams.get(g["home"], {}), teams.get(g["away"], {})
        notes = []
        for side, t, name in (("home", h, g["home"]), ("away", a, g["away"])):
            if t.get("minutes_out", 0) >= 0.10:
                notes.append(f"{name} missing {t['minutes_out']:.0%} of its minutes ({t['points_out']:.0%} of its scoring)")
            for hole in t.get("holes", []):
                notes.append(f"{name} {hole['slot']} {hole['starter']} {hole['status']}"
                             + (f", {hole['next']} next up" if hole["next"] else ""))
            for n in t.get("cold", [])[:2]:
                notes.append(f"{n} ({name}) is cold")
            for n in t.get("hot", [])[:2]:
                notes.append(f"{n} ({name}) is hot")
            for n in ((t.get("due") or {}).get("notes") or [])[:2]:
                if "games in" not in n:
                    notes.append(f"{name}: {n}")
        lh, la = (h.get("due") or {}).get("luck"), (a.get("due") or {}).get("luck")
        angle = None
        if lh is not None and la is not None:
            if lh <= -0.06 and la >= 0.06:
                angle = g["home"]
            elif la <= -0.06 and lh >= 0.06:
                angle = g["away"]
        matchups.append({**g, "notes": notes, "angle": angle})

    return {"generated_at": now.isoformat(), "season": season, "games_saved": len(cur), "last_season_games": len(prev),
            "players": sorted(players, key=lambda p: (-p["mpg"])), "teams": teams, "matchups": matchups,
            "study": study}


__all__ = ["collect", "build", "parse", "season_of", "POSITIONS"]
