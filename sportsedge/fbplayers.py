"""Football player report, NFL and college (owner, Oct 25: "do we have this for nfl, college
football...?"): the NBA report's football twin.

Box scores come from ESPN (only the pipeline can reach it): ``collect`` saves each finished game's
passing, rushing and receiving lines to data/players/<sport>/<season>.json as
    gid -> {"date", "home", "away", "rows": [[team, name, group, a, b, c, d, e], ...]}
    passing [cmp, att, yds, td, int] · rushing [car, yds, td, 0, 0] · receiving [rec, yds, td, tgts, 0]
``build`` turns that, this season's results and today's injury report into data/<sport>_report.json:
each team's QB (and whether he's new), lead back and top receivers with their last 3 games against
their season, who's hurt (flagged when it's one of those), team luck, and notes for each game.
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

FB_SPORTS = ("nfl", "ncaaf")
GROUPS = {"passing": ("C/ATT", "YDS", "TD", "INT"), "rushing": ("CAR", "YDS", "TD"), "receiving": ("REC", "YDS", "TD", "TGTS"),
          "defensive": ("TOT", "SACKS", "TFL", "PD", "QB HTS"), "interceptions": ("INT", "YDS", "TD")}
RECENT = 3
QB_SWING = 1.5  # adjusted yards/attempt this far off his season = hot / cold
RB_SWING = 1.2  # yards per carry
WR_SHARE = 0.6  # receiving yards at 60% of his season pace or less = cold (140%+ = hot)


def season_of(when: datetime) -> int:
    return when.year if when.month >= 7 else when.year - 1


def _num(raw) -> float:
    try:
        return float(str(raw).replace(",", ""))
    except (TypeError, ValueError):
        return 0.0


def parse(summary: dict) -> list[list]:
    """[team, name, group, a, b, c, d, e] rows from an ESPN football summary."""
    out = []
    for block in (summary.get("boxscore") or {}).get("players") or []:
        team = (block.get("team") or {}).get("displayName", "")
        for group in block.get("statistics") or []:
            gname = (group.get("name") or "").lower()
            if gname not in GROUPS:
                continue
            labels = [str(x).upper() for x in (group.get("labels") or [])]
            for ath in group.get("athletes") or []:
                name = (ath.get("athlete") or {}).get("displayName")
                stats = dict(zip(labels, ath.get("stats") or []))
                if not name or not stats:
                    continue
                if gname == "passing":
                    c, _, a = str(stats.get("C/ATT", "0/0")).partition("/")
                    row = [_num(c), _num(a), _num(stats.get("YDS")), _num(stats.get("TD")), _num(stats.get("INT"))]
                    if not row[1]:
                        continue
                elif gname == "rushing":
                    row = [_num(stats.get("CAR")), _num(stats.get("YDS")), _num(stats.get("TD")), 0.0, 0.0]
                elif gname == "defensive":
                    row = [_num(stats.get("TOT")), _num(stats.get("SACKS")), _num(stats.get("TFL")), _num(stats.get("PD")),
                           _num(stats.get("QB HTS"))]
                elif gname == "interceptions":
                    row = [_num(stats.get("INT")), _num(stats.get("YDS")), _num(stats.get("TD")), 0.0, 0.0]
                else:
                    row = [_num(stats.get("REC")), _num(stats.get("YDS")), _num(stats.get("TD")), _num(stats.get("TGTS")), 0.0]
                out.append([team, name, gname] + row)
    return out


def _path(root, sport: str, season: int) -> Path:
    return Path(root) / "players" / sport / f"{season}.json"


def load_season(root, sport: str, season: int) -> dict:
    try:
        return json.loads(_path(root, sport, season).read_text())
    except (OSError, ValueError):
        return {}


def _finished(root, sport: str, season: int, now: datetime) -> list[tuple[datetime, dict]]:
    out = []
    for f in sorted((Path(root) / "games" / sport).glob("*.csv")):
        with f.open() as fh:
            for r in csv.DictReader(fh):
                try:
                    when = datetime.fromisoformat(r["date"])
                except (KeyError, ValueError):
                    continue
                if r.get("home_score") and when < now and season_of(when) == season:
                    out.append((when, r))
    return sorted(out, key=lambda x: x[0])


def collect(client, sport: str, now: datetime, root="data", budget: int = 60) -> int:
    """Save this season's finished games not yet saved, newest first. Returns games added."""
    from .sports import SPORTS

    season = season_of(now)
    store = load_season(root, sport, season)
    added = 0
    for when, r in reversed(_finished(root, sport, season, now)):
        if added >= budget:
            break
        if r["event_id"] in store:
            continue
        try:
            rows = parse(client.summary(SPORTS[sport], r["event_id"]))
        except Exception as exc:  # one bad box score never stops the rest
            log.info("%s box score %s failed: %s", sport, r["event_id"], exc)
            continue
        if rows:
            store[r["event_id"]] = {"date": when.isoformat(), "home": r["home"], "away": r["away"], "rows": rows}
            added += 1
    if added:
        p = _path(root, sport, season)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(store, separators=(",", ":"), sort_keys=True))
    return added


def _adj_ypa(lines: list[list]) -> float | None:
    att = sum(r[1] for r in lines)
    if not att:
        return None
    return (sum(r[2] for r in lines) + 20 * sum(r[3] for r in lines) - 45 * sum(r[4] for r in lines)) / att


def _team_due(root, sport: str, season: int, now: datetime) -> dict[str, dict]:
    from .nbastudy import PARAMS, Team

    p = PARAMS[sport]
    teams: dict[str, Team] = defaultdict(lambda: Team(p))
    for _, r in _finished(root, sport, season, now):
        hs, as_ = float(r["home_score"]), float(r["away_score"])
        teams[r["home"]].add(hs, as_, False)
        teams[r["away"]].add(as_, hs, False)
    out = {}
    for name, t in teams.items():
        d = {"w": t.w, "l": t.l, "diff": round((t.pf - t.pa) / t.games, 1) if t.games else 0.0, "close": t.close,
             "last5": round(sum(t.last5) / len(t.last5), 1) if t.last5 else None}
        notes = []
        if t.games >= p.min_games:
            d["luck"], d["slump"] = round(t.luck(), 3), round(t.slump(), 1)
            if d["luck"] <= -p.luck:
                notes.append(f"due: their record's {abs(d['luck']) * 100:.0f} pts worse than their point differential says, they've been unlucky")
            elif d["luck"] >= p.luck:
                notes.append(f"lucky: their record's {d['luck'] * 100:.0f} pts better than their point differential says, the bill's coming")
            if t.close <= -p.close_n:
                notes.append(f"lost {-t.close} more one-score games than they won")
            elif t.close >= p.close_n:
                notes.append(f"won {t.close} more one-score games than they lost")
            if d["slump"] <= -p.slump:
                notes.append(f"slumping: {abs(d['slump']):.0f} pts a game worse the last {p.recent} than their season")
            elif d["slump"] >= p.slump:
                notes.append(f"rollin': {d['slump']:.0f} pts a game better the last {p.recent} than their season")
        else:
            notes.append(f"only {t.games} games in, luck reads kick in at {p.min_games}")
        d["notes"] = notes
        out[name] = d
    return out


def _roster(team: str, mine: dict, games: int, qb: str | None, ww: dict, out_names: set, q_names: set) -> list[dict]:
    """Every player in the team's box scores this season, offense and defense: production, share of
    his unit's production, tier, the team's margin with and without him, status."""
    from .roster import impact_line, tiers

    people: dict[str, dict[str, list]] = defaultdict(lambda: defaultdict(list))
    for (_, n, grp), rows in mine.items():
        people[n][grp] = [r for _, r in rows]
    s = lambda rows, i: sum(r[i] for r in rows)  # noqa: E731
    off, dfn, info = {}, {}, {}
    for n, g in people.items():
        p, ru, re, de, it = (g.get(k, []) for k in ("passing", "rushing", "receiving", "defensive", "interceptions"))
        o = 0.5 * (s(p, 2) + 20 * s(p, 3) - 45 * s(p, 4)) + s(ru, 1) + 20 * s(ru, 2) + s(re, 1) + 20 * s(re, 2)
        d = s(de, 0) + 4 * s(de, 1) + 2 * s(de, 2) + 2 * s(de, 3) + 2 * s(de, 4) + 6 * s(it, 0)
        gp = max(len(x) for x in (p, ru, re, de, it))
        if o >= d:
            off[n] = max(o, 0.0)
            role = "QB" if p and s(p, 1) >= 10 else "RB" if s(ru, 0) > s(re, 0) else "WR/TE"
            bits = []
            if p and s(p, 1):
                bits.append(f"{s(p, 2) / len(p):.0f} pass yds/g, {s(p, 3):.0f} TD-{s(p, 4):.0f} INT")
            if ru and s(ru, 0) >= 5:
                bits.append(f"{s(ru, 1) / len(ru):.0f} rush yds/g ({s(ru, 1) / s(ru, 0):.1f} a carry), {s(ru, 2):.0f} TD")
            if re and s(re, 0):
                bits.append(f"{s(re, 0) / len(re):.1f} rec, {s(re, 1) / len(re):.0f} yds/g, {s(re, 2):.0f} TD")
            info[n] = ("offense", role, "; ".join(bits), gp)
        else:
            dfn[n] = d
            info[n] = ("defense", "DEF", f"{s(de, 0):.0f} tkl, {s(de, 1):g} sacks, {s(de, 2):g} TFL, {s(de, 3):g} PD, "
                                         f"{s(it, 0):.0f} INT", gp)
    tier = {**tiers(off, 0.20, 0.08, 0.03), **tiers(dfn, 0.10, 0.06, 0.03)}
    if qb in tier:
        tier[qb] = "Key"
    tot = {"offense": sum(off.values()) or 1.0, "defense": sum(dfn.values()) or 1.0}
    out = []
    for n, (unit, role, line, gp) in info.items():
        v = off.get(n, dfn.get(n, 0.0))
        out.append({"name": n, "pos": role, "unit": unit, "gp": gp, "tier": tier[n], "share": round(v / tot[unit], 3),
                    "line": line, "ww": ww.get(n), "impact": impact_line(ww.get(n)), "form": None,
                    "status": "out" if n in out_names else "questionable" if n in q_names else None})
    rank = {"Key": 0, "Starter": 1, "Rotation": 2, "Bench": 3}
    return sorted(out, key=lambda p: (p["unit"] != "offense", rank[p["tier"]], -p["share"]))


def build(sport: str, now: datetime, root="data", injuries: dict | None = None,
          games_today: list[dict] | None = None, study: dict | None = None) -> dict:
    """``injuries``: team -> {"out": [(player, pos)], "questionable": [...]}; ``games_today``:
    [{"home", "away", "start", "home_ml", "away_ml", "spread"}]."""
    season = season_of(now)
    store = load_season(root, sport, season)
    games = sorted(store.values(), key=lambda g: g["date"])
    by: dict[tuple[str, str, str], list[tuple[str, list]]] = defaultdict(list)  # (team, name, group) -> [(date, row)]
    for g in games:
        for r in g["rows"]:
            by[(r[0], r[1], r[2])].append((g["date"], r[3:]))
    team_games: dict[str, list[str]] = defaultdict(list)
    for g in games:
        for t in {r[0] for r in g["rows"]}:
            team_games[t].append(g["date"])

    from .roster import margins, team_index, with_without

    margin = margins(root, sport)
    appear, gdates = team_index(store, lambda r: r[0], lambda r: r[1])
    ww_by_team: dict[str, dict] = {}
    teams: dict[str, dict] = {}
    due = _team_due(root, sport, season, now)
    inj = injuries or {}
    names = sorted(set(team_games) | set(inj) | set(due))
    for team in names:
        dates = team_games.get(team, [])
        recent = set(dates[-RECENT:])
        mine = {k: v for k, v in by.items() if k[0] == team}
        # The starter each game: the most pass attempts. The QB now: the latest game's starter.
        starter_by_day: dict[str, tuple[float, str]] = {}
        for k, v in mine.items():
            if k[2] != "passing":
                continue
            for d, r in v:
                if d not in starter_by_day or r[1] > starter_by_day[d][0]:
                    starter_by_day[d] = (r[1], k[1])
        starts = Counter(n for _, n in starter_by_day.values())
        last = dates[-1] if dates else None
        qb = starter_by_day[last][1] if last in starter_by_day else None
        qb_line = None
        if qb:
            rows = [r for _, r in mine[(team, qb, "passing")]]
            season_ypa, last3 = _adj_ypa(rows), _adj_ypa([r for d, r in mine[(team, qb, "passing")] if d in recent])
            tag = None
            if len(rows) >= RECENT + 1 and season_ypa is not None and last3 is not None:
                tag = "hot" if last3 - season_ypa >= QB_SWING else "cold" if season_ypa - last3 >= QB_SWING else None
            qb_line = {"name": qb, "starts": starts.get(qb, 0), "games": len(dates), "new": starts.get(qb, 0) <= 2 < len(dates),
                       "ypa": season_ypa and round(season_ypa, 1), "last3": last3 and round(last3, 1), "tag": tag,
                       "yds_g": round(sum(r[2] for r in rows) / len(rows), 1), "td": int(sum(r[3] for r in rows)),
                       "int": int(sum(r[4] for r in rows))}
        # Lead back: most carries over the last 3 games.
        rb = None
        backs = sorted(((sum(r[0] for d, r in v if d in recent), k[1]) for k, v in mine.items() if k[2] == "rushing"
                        and k[1] != qb), reverse=True)
        if backs and backs[0][0]:
            n = backs[0][1]
            rows = mine[(team, n, "rushing")]
            car, yds = sum(r[0] for _, r in rows), sum(r[1] for _, r in rows)
            rc, ry = sum(r[0] for d, r in rows if d in recent), sum(r[1] for d, r in rows if d in recent)
            ypc, rypc = (yds / car if car else None), (ry / rc if rc else None)
            tag = None
            if len(rows) >= RECENT + 1 and ypc is not None and rypc is not None and rc >= 20:
                tag = "hot" if rypc - ypc >= RB_SWING else "cold" if ypc - rypc >= RB_SWING else None
            rb = {"name": n, "car_g": round(car / len(rows), 1), "ypc": ypc and round(ypc, 1),
                  "last3_ypc": rypc and round(rypc, 1), "yds_g": round(yds / len(rows), 1), "tag": tag}
        # Top 3 receivers: targets (or catches) over the last 3 games.
        catchers = sorted(((sum((r[3] or r[0]) for d, r in v if d in recent), k[1]) for k, v in mine.items()
                           if k[2] == "receiving"), reverse=True)[:3]
        wrs = []
        for vol, n in catchers:
            if not vol:
                continue
            rows = mine[(team, n, "receiving")]
            g_all = len(rows)
            per = sum(r[1] for _, r in rows) / g_all
            rec3 = [r for d, r in rows if d in recent]
            per3 = sum(r[1] for r in rec3) / len(rec3) if rec3 else None
            tag = None
            if g_all >= RECENT + 1 and per3 is not None and per >= 30:
                tag = "hot" if per3 >= per * (2 - WR_SHARE) else "cold" if per3 <= per * WR_SHARE else None
            wrs.append({"name": n, "yds_g": round(per, 1), "last3_yds": per3 and round(per3, 1),
                        "rec_g": round(sum(r[0] for _, r in rows) / g_all, 1), "td": int(sum(r[2] for _, r in rows)), "tag": tag})
        key = {x["name"]: role for x, role in ((qb_line, "QB1"), (rb, "RB1")) if x} | {w["name"]: "top target" for w in wrs}
        t_inj = inj.get(team, {})
        out_names = {n for n, _ in t_inj.get("out", [])}
        q_names = {n for n, _ in t_inj.get("questionable", [])}
        roster = _roster(team, mine, len(dates), qb, ww_by_team.setdefault(team, with_without(appear, gdates, margin, team)),
                         out_names, q_names)
        for p in roster:  # the whole roster's Key players count as key on the injury list too
            if p["tier"] == "Key":
                key.setdefault(p["name"], f"key {p['unit']}")
        hurt = [{"name": n, "pos": pos, "status": st, "key": key.get(n)}
                for st, lst in (("out", t_inj.get("out", [])), ("questionable", t_inj.get("questionable", [])))
                for n, pos in lst]
        lost = {u: round(sum(p["share"] for p in roster if p["unit"] == u and p["status"] == "out"), 3) for u in ("offense", "defense")}
        teams[team] = {"qb": qb_line, "rb": rb, "wr": wrs, "hurt": hurt, "roster": roster, "production_out": lost,
                       "due": due.get(team)}

    matchups = []
    for g in games_today or []:
        notes = []
        for name in (g["away"], g["home"]):
            t = teams.get(name, {})
            for h in t.get("hurt", []):
                if h["key"] or (h["status"] == "out" and h["pos"] in ("QB",)):
                    notes.append(f"{name}: {h['key'] or h['pos']} {h['name']} {h['status']}")
            q = t.get("qb")
            if q and q.get("new"):
                notes.append(f"{name}: QB {q['name']} only has {q['starts']} starts this year, new guy under center")
            for who in ([q] if q else []) + ([t["rb"]] if t.get("rb") else []) + t.get("wr", []):
                if who and who.get("tag"):
                    notes.append(f"{who['name']} ({name}) is " + {"cold": "ice cold 🥶", "hot": "cookin' 🔥"}.get(who["tag"], who["tag"]))
            for n in ((t.get("due") or {}).get("notes") or [])[:2]:
                if "games in" not in n:
                    notes.append(f"{name}: {n}")
        matchups.append({**g, "notes": notes})
    return {"sport": sport, "generated_at": now.isoformat(), "season": season, "games_saved": len(store),
            "teams": teams, "matchups": matchups, "study": study}


__all__ = ["FB_SPORTS", "collect", "build", "parse", "season_of"]
