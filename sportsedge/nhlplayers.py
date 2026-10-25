"""NHL player and roster report (owner, Oct 25: "and hockey as well... look at the entire roster.
Who's important and impactful and who's not?").

Box scores come from ESPN (only the pipeline can reach it): ``collect`` saves each finished game's
skaters and goalies to data/players/nhl/<season>.json as
    gid -> {"date", "home", "away", "rows": [[team, name, group, {label: value}], ...]}
(group: forwards / defenses / goalies; TOI kept in minutes). ``build`` turns that, this season's
results and today's injury report into data/nhl_report.json: every team's whole roster (role, ice
time, production, share of the team's, tier, the team's goal margin with and without him, form,
status), its goalies (who's the #1, save % season and last 5), team luck, and notes per game.
Research only: nothing here posts a pick or carries units.
"""

from __future__ import annotations

import csv
import json
import logging
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

log = logging.getLogger(__name__)

RECENT = 5
GOALIE_SWING = 0.015  # last-5 save % this far off his season = hot / cold


def season_of(when: datetime) -> int:
    return when.year if when.month >= 7 else when.year - 1


def _val(raw) -> float | None:
    s = str(raw or "").strip()
    if not s or s in ("--", "-"):
        return None
    if ":" in s:  # time on ice "18:32"
        m, _, sec = s.partition(":")
        try:
            return int(m) + int(sec) / 60
        except ValueError:
            return None
    try:
        return float(s.replace("+", "").replace("%", ""))
    except ValueError:
        return None


def parse(summary: dict) -> list[list]:
    """[team, name, group, {label: value}] for every skater and goalie who played."""
    out = []
    for block in (summary.get("boxscore") or {}).get("players") or []:
        team = (block.get("team") or {}).get("displayName", "")
        for group in block.get("statistics") or []:
            gname = (group.get("name") or group.get("type") or "").lower()
            labels = [str(x).upper() for x in (group.get("labels") or [])]
            for ath in group.get("athletes") or []:
                name = (ath.get("athlete") or {}).get("displayName")
                stats = {lab: v for lab, raw in zip(labels, ath.get("stats") or []) if (v := _val(raw)) is not None}
                if not name or not stats.get("TOI"):
                    continue
                out.append([team, name, "goalies" if "goal" in gname else gname or "skaters", stats])
    return out


def _path(root, season: int) -> Path:
    return Path(root) / "players" / "nhl" / f"{season}.json"


def load_season(root, season: int) -> dict:
    try:
        return json.loads(_path(root, season).read_text())
    except (OSError, ValueError):
        return {}


def _finished(root, season: int, now: datetime) -> list[tuple[datetime, dict]]:
    out = []
    for f in sorted((Path(root) / "games" / "nhl").glob("*.csv")):
        with f.open() as fh:
            for r in csv.DictReader(fh):
                try:
                    when = datetime.fromisoformat(r["date"])
                except (KeyError, ValueError):
                    continue
                if r.get("home_score") and when < now and season_of(when) == season:
                    out.append((when, r))
    return sorted(out, key=lambda x: x[0])


def collect(client, now: datetime, root="data", budget: int = 60) -> int:
    from .sports import SPORTS

    season = season_of(now)
    store = load_season(root, season)
    added = 0
    for when, r in reversed(_finished(root, season, now)):
        if added >= budget:
            break
        if r["event_id"] in store:
            continue
        try:
            rows = parse(client.summary(SPORTS["nhl"], r["event_id"]))
        except Exception as exc:  # one bad box score never stops the rest
            log.info("NHL box score %s failed: %s", r["event_id"], exc)
            continue
        if rows:
            store[r["event_id"]] = {"date": when.isoformat(), "home": r["home"], "away": r["away"], "rows": rows}
            added += 1
    if added:
        p = _path(root, season)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(store, separators=(",", ":"), sort_keys=True))
    return added


def _sv(rows: list[dict]) -> float | None:
    sa = sum(r.get("SA", 0) for r in rows)
    sv = sum(r.get("SV", r.get("SA", 0) - r.get("GA", 0)) for r in rows)
    return sv / sa if sa else None


def _team_due(root, season: int, now: datetime) -> dict[str, dict]:
    from .nbastudy import PARAMS, Team

    p = PARAMS["nhl"]
    teams: dict[str, Team] = defaultdict(lambda: Team(p))
    for _, r in _finished(root, season, now):
        hs, as_ = float(r["home_score"]), float(r["away_score"])
        teams[r["home"]].add(hs, as_, False)
        teams[r["away"]].add(as_, hs, False)
    out = {}
    for name, t in teams.items():
        d = {"w": t.w, "l": t.l, "diff": round((t.pf - t.pa) / t.games, 2) if t.games else 0.0, "close": t.close,
             "last5": round(sum(t.last5) / len(t.last5), 2) if t.last5 else None}
        notes = []
        if t.games >= p.min_games:
            d["luck"], d["slump"] = round(t.luck(), 3), round(t.slump(), 2)
            if d["luck"] <= -p.luck:
                notes.append(f"due: record {abs(d['luck']) * 100:.0f} pts under what its goal differential says")
            elif d["luck"] >= p.luck:
                notes.append(f"lucky: record {d['luck'] * 100:.0f} pts over its goal differential")
            if t.close <= -p.close_n:
                notes.append(f"{-t.close} more one-goal losses than wins")
            elif t.close >= p.close_n:
                notes.append(f"{t.close} more one-goal wins than losses")
        else:
            notes.append(f"{t.games} games in: luck reads start at {p.min_games}")
        d["notes"] = notes
        out[name] = d
    return out


def build(now: datetime, root="data", injuries: dict | None = None, games_today: list[dict] | None = None,
          study: dict | None = None) -> dict:
    from .roster import impact_line, margins, team_index, tiers, with_without

    season = season_of(now)
    store = load_season(root, season)
    prev = load_season(root, season - 1)
    games = sorted(store.items(), key=lambda kv: kv[1]["date"])
    skaters: dict[tuple[str, str], list[tuple[str, dict]]] = defaultdict(list)
    goalies: dict[tuple[str, str], list[tuple[str, dict]]] = defaultdict(list)
    started: dict[tuple[str, str], str] = {}  # (gid, team) -> the goalie with the most TOI
    pos: dict[str, Counter] = defaultdict(Counter)
    team_dates: dict[str, list[str]] = defaultdict(list)
    for gid, g in games:
        for team in {r[0] for r in g["rows"]}:
            team_dates[team].append(g["date"])
        best: dict[str, tuple[float, str]] = {}
        for team, name, group, st in g["rows"]:
            if group == "goalies":
                goalies[(team, name)].append((g["date"], st))
                if team not in best or st.get("TOI", 0) > best[team][0]:
                    best[team] = (st.get("TOI", 0), name)
            else:
                skaters[(team, name)].append((g["date"], st))
                pos[name]["D" if "def" in group else "F"] += 1
        for team, (_, name) in best.items():
            started[(gid, team)] = name

    margin = margins(root, "nhl")
    appear, dates = team_index({**prev, **store}, lambda r: r[0], lambda r: r[1])
    due = _team_due(root, season, now)
    inj = injuries or {}
    teams: dict[str, dict] = {}
    for team in sorted(set(team_dates) | set(due) | set(inj)):
        played = len(team_dates.get(team, [])) or 1
        recent = set(team_dates.get(team, [])[-RECENT:])
        t_inj = inj.get(team, {})
        out_names = {n for n, _ in t_inj.get("out", [])}
        q_names = {n for n, _ in t_inj.get("questionable", [])}
        ww = with_without(appear, dates, margin, team)
        mine = {n: rows for (t, n), rows in skaters.items() if t == team}
        tot = lambda rows, k: sum(st.get(k, 0) for _, st in rows)  # noqa: E731
        team_toi = sum(tot(r, "TOI") for r in mine.values()) or 1.0
        team_pts = sum(tot(r, "G") + tot(r, "A") for r in mine.values()) or 1.0
        value = {n: 0.5 * tot(r, "TOI") / team_toi + 0.5 * (tot(r, "G") + tot(r, "A")) / team_pts for n, r in mine.items()}
        tier = tiers(value, 0.09, 0.06, 0.035)
        roster = []
        for n, rows in sorted(mine.items(), key=lambda kv: -value[kv[0]]):
            gp = len(rows)
            pts = tot(rows, "G") + tot(rows, "A")
            last = [st for d, st in rows if d in recent]
            form = None
            if gp > RECENT and pts / gp >= 0.6 and last:
                lp = sum(st.get("G", 0) + st.get("A", 0) for st in last) / len(last)
                form = "cold" if lp <= 0.3 * pts / gp else "hot" if lp >= 1.6 * pts / gp else None
            roster.append({"name": n, "pos": pos[n].most_common(1)[0][0] if pos[n] else "", "gp": gp, "tier": tier[n],
                           "share": round(value[n], 3), "starter": False,
                           "line": f"{tot(rows, 'G'):.0f} G · {tot(rows, 'A'):.0f} A · {tot(rows, '+/-'):+.0f} · "
                                   f"{tot(rows, 'S') / gp:.1f} shots/g · {tot(rows, 'TOI') / gp:.0f} min",
                           "ww": ww.get(n), "impact": impact_line(ww.get(n)), "form": form,
                           "status": "out" if n in out_names else "questionable" if n in q_names else None})
        # Goalies: starts, save % season and last 5 starts, the #1 = most starts in the last 10.
        starts_by = [(d, started[(gid, team)]) for gid, g in games if (gid, team) in started for d in [g["date"]]]
        g1_count = Counter(n for _, n in starts_by[-10:])
        gl = []
        for (t, n), rows in goalies.items():
            if t != team:
                continue
            mine_starts = [(d, st) for d, st in rows if any(d == sd and sn == n for sd, sn in starts_by)]
            season_sv, last5 = _sv([st for _, st in rows]), _sv([st for _, st in mine_starts[-RECENT:]])
            tag = None
            if len(mine_starts) >= RECENT + 2 and season_sv and last5:
                tag = "hot" if last5 - season_sv >= GOALIE_SWING else "cold" if season_sv - last5 >= GOALIE_SWING else None
            gl.append({"name": n, "starts": len(mine_starts), "sv": season_sv and round(season_sv, 3),
                       "last5": last5 and round(last5, 3), "gaa": round(60 * tot(rows, "GA") / (tot(rows, "TOI") or 1), 2),
                       "ww": ww.get(n), "impact": impact_line(ww.get(n)), "form": tag,
                       "status": "out" if n in out_names else "questionable" if n in q_names else None})
        gl.sort(key=lambda x: (-g1_count.get(x["name"], 0), -x["starts"]))
        if gl:
            gl[0]["g1"] = True
        lost = sum(p["share"] for p in roster if p["status"] == "out")
        teams[team] = {"roster": roster, "goalies": gl, "production_out": round(lost, 3), "games": played,
                       "hurt": [{"name": n, "pos": p_, "status": st,
                                 "key": next((r["tier"] for r in roster if r["name"] == n), None)
                                 or ("G1" if gl and gl[0]["name"] == n else None)}
                                for st, lst in (("out", t_inj.get("out", [])), ("questionable", t_inj.get("questionable", [])))
                                for n, p_ in lst],
                       "due": due.get(team)}

    matchups = []
    for g in games_today or []:
        notes = []
        for name in (g["away"], g["home"]):
            t = teams.get(name, {})
            gl = t.get("goalies") or []
            if gl:
                g1 = gl[0]
                if g1.get("status"):
                    notes.append(f"{name}: #1 goalie {g1['name']} {g1['status']}" + (f", {gl[1]['name']} next" if len(gl) > 1 else ""))
                elif g1.get("form"):
                    notes.append(f"{name}: #1 goalie {g1['name']} is {g1['form']} (last 5 {g1['last5']:.3f} vs {g1['sv']:.3f})")
            if t.get("production_out", 0) >= 0.08:
                notes.append(f"{name} missing {t['production_out']:.0%} of its production")
            for p in t.get("roster", []):
                if p["status"] and p["tier"] in ("Key", "Starter"):
                    notes.append(f"{name}: {p['tier'].lower()} {p['name']} {p['status']}" + (f" ({p['impact']})" if p["impact"] else ""))
                elif p["form"] and p["tier"] in ("Key", "Starter"):
                    notes.append(f"{p['name']} ({name}) is {p['form']}")
            for n in ((t.get("due") or {}).get("notes") or [])[:2]:
                if "games in" not in n:
                    notes.append(f"{name}: {n}")
        matchups.append({**g, "notes": notes})
    return {"sport": "nhl", "generated_at": now.isoformat(), "season": season, "games_saved": len(store),
            "teams": teams, "matchups": matchups, "study": study}


__all__ = ["collect", "build", "parse", "season_of"]
