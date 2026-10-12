"""Plain-English reasons for a pick, built from the same data the model used."""

from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace

from .edge import Pick
from .engine import EventContext, SportScan
from .models.ratings import GameResult
from .odds import implied_prob

SEASON_GAP_DAYS = 60


def season_games(results: list[GameResult], team: str, before: datetime) -> list[GameResult]:
    """The team's completed games this season (stops at the last long break)."""
    games = sorted(
        (g for g in results if g.completed and team in (g.home, g.away) and g.date < before),
        key=lambda g: g.date,
    )
    season: list[GameResult] = []
    for g in reversed(games):
        if season and (season[-1].date - g.date) > timedelta(days=SEASON_GAP_DAYS):
            break
        season.append(g)
    return list(reversed(season))


def record(games: list[GameResult], team: str) -> tuple[int, int, int]:
    w = l = t = 0
    for g in games:
        mine, theirs = (g.home_score, g.away_score) if g.home == team else (g.away_score, g.home_score)
        if mine > theirs:
            w += 1
        elif mine < theirs:
            l += 1
        else:
            t += 1
    return w, l, t


def fmt_record(r: tuple[int, int, int]) -> str:
    return f"{r[0]}-{r[1]}" + (f"-{r[2]}" if r[2] else "")


def last_game_line(games: list[GameResult], team: str, short: str) -> str | None:
    if not games:
        return None
    g = games[-1]
    home = g.home == team
    mine, theirs = (g.home_score, g.away_score) if home else (g.away_score, g.home_score)
    opp = g.away if home else g.home
    when = g.date.strftime("%a %-m/%-d")
    score = f"{mine:.0f}-{theirs:.0f}"
    if mine > theirs:
        verb = "blowout of" if _blowout(mine, theirs) else "win over"
        return f"{short} fresh off a {score} {verb} {opp} ({when})."
    if mine < theirs:
        return f"{short} coming off a {score} loss to {opp} ({when})."
    return f"{short} drew {score} with {opp} last time out ({when})."


def _blowout(a: float, b: float) -> bool:
    return a - b >= max(14, 0.25 * (a + b)) if a + b > 12 else a - b >= 3


def _split_top(text: str) -> list[str]:
    """Split on '; ' outside parentheses."""
    out, depth, cur = [], 0, ""
    i = 0
    while i < len(text):
        ch = text[i]
        depth += ch == "("
        depth -= ch == ")"
        if depth == 0 and text.startswith("; ", i):
            out.append(cur.strip())
            cur, i = "", i + 2
            continue
        cur += ch
        i += 1
    return [x for x in out + [cur.strip()] if x]


def pick_team(pick: Pick, ctx: EventContext) -> str | None:
    return {"home": ctx.home, "away": ctx.away}.get(pick.side)


def reasons(pick: Pick, scan: SportScan) -> tuple[str, list[str]]:
    """(headline, supporting bullets) for a pick."""
    ctx = scan.contexts.get(pick.event_id)
    pred = scan.predictions.get(pick.event_id)
    implied = implied_prob(pick.american)
    our = pick.win_prob
    bullets: list[str] = []
    odds = f"{pick.american:+.0f}"

    if pick.is_prop:
        headline = (
            f"We make this {our:.0%} to cash; {odds} only needs {implied:.0%}." if pick.ev > 0
            else f"No edge: we make this {our:.0%} to cash and {odds} needs {implied:.0%}."
        )
        if pick.projection:
            parts = _split_top(pick.projection)
            bullets.append(f"Projection: {parts[0]}.")
            bullets += [x[0].upper() + x[1:] + "." for x in parts[1:] if x]
        if ctx:
            bullets += matchup_facts(scan.results, ctx.home, ctx.away, ctx.event.commence_time, scan.sport.key,
                                     scan.ratings, None, ctx.neutral)[:5]
    else:
        team = pick_team(pick, ctx) if ctx else None
        if pick.market == "h2h" and team and pick.ev <= 0:
            headline = f"No edge at {odds}: {team} wins {our:.0%} of the time on our numbers, and the price needs {implied:.0%}."
        elif pick.market == "h2h" and team:
            if pick.american > 0:
                headline = f"{odds} undersells {team}: our numbers say they win {our:.0%} of the time, the price pays like {implied:.0%}."
            else:
                headline = f"{team} wins {our:.0%} of the time on our numbers; {odds} only asks for {implied:.0%}."
        elif pick.market == "h2h":
            headline = f"Draw comes in {our:.0%} on our numbers vs {implied:.0%} priced."
        elif pick.market == "spreads" and team and pred:
            margin = pred.exp_margin if pick.side == "home" else -pred.exp_margin
            outcome = f"to win by {margin:.1f}" if margin >= 0 else f"to lose by {-margin:.1f}"
            line = f"laying {-pick.point:g}" if pick.point < 0 else f"getting {pick.point:g}"
            headline = f"We project {team} {outcome}; {line} at {odds} covers {our:.0%} of the time."
        elif pick.market == "totals" and pred:
            headline = f"We project {pred.exp_total:.1f} total; {pick.selection} hits {our:.0%} of the time."
        else:
            headline = f"Our number {our:.0%} vs {implied:.0%} implied by {odds}."

        if ctx and pred:
            bullets.append(
                f"Projected score: {ctx.away} {pred.exp_away:.1f} - {ctx.home} {pred.exp_home:.1f}."
            )
            before = ctx.event.commence_time
            hg = season_games(scan.results, ctx.home, before)
            ag = season_games(scan.results, ctx.away, before)
            if hg or ag:
                bullets.append(
                    f"{ctx.away} ({fmt_record(record(ag, ctx.away))}) @ {ctx.home} ({fmt_record(record(hg, ctx.home))})."
                )
            order = [ctx.home, ctx.away] if pick.side == "home" else [ctx.away, ctx.home]
            for t in order:
                line = last_game_line(hg if t == ctx.home else ag, t, t)
                if line:
                    bullets.append(line)
                    break
            rest = _rest_line(ctx)
            if rest:
                bullets.append(rest)
            if ctx.home_pitcher or ctx.away_pitcher:
                def sp(name, era, sv):
                    if era is not None:
                        return f"{name} ({era:.2f} ERA)"
                    if sv:
                        return f"{name} ({sv[0]:.3f} career save %, {sv[1]:,.0f} shots)"
                    return name or "TBD"
                label = "Starting goalies" if scan.sport.key == "nhl" else "Starters"
                bullets.append(
                    f"{label}: {sp(ctx.away_pitcher, ctx.away_pitcher_era, ctx.away_goalie_sv)} vs "
                    f"{sp(ctx.home_pitcher, ctx.home_pitcher_era, ctx.home_goalie_sv)}."
                )

        if ctx and pred:
            bullets.extend(stat_lines(pick, scan, ctx, pred))

    if pick.market_prob is not None:
        bullets.append(
            f"Model {pick.model_prob:.0%} · market (no-vig) {pick.market_prob:.0%} · blended {pick.win_prob:.0%} → fair {pick.fair_american:+.0f}."
        )
    bullets.append(f"Best price {odds} at {pretty_book(pick.book)} ({pick.books} books compared).")
    if ctx:
        for team, players in ctx.injuries.items():
            if players:
                more = f" +{len(players) - 4} more" if len(players) > 4 else ""
                bullets.append(f"{team} missing: {', '.join(players[:4])}{more}.")
    return headline, bullets


FORM_GAMES = 5
UNIT = {"nfl": "points", "nba": "points", "mlb": "runs", "nhl": "goals"}
MAX_FACTS = 10


def streak(games: list[GameResult], team: str) -> tuple[str, int] | None:
    """The team's current run: ("won", 4) or ("lost", 2)."""
    out, n = None, 0
    for g in reversed(games):
        mine, theirs = (g.home_score, g.away_score) if g.home == team else (g.away_score, g.home_score)
        r = "won" if mine > theirs else "lost" if mine < theirs else "tied"
        if out is None:
            out = r
        if r != out:
            break
        n += 1
    return (out, n) if out else None


def matchup_facts(results: list[GameResult], home: str, away: str, before: datetime, sport: str,
                  ratings=None, ctx: EventContext | None = None, neutral: bool = False) -> list[str]:
    """Facts about a game from the season's results (and the ESPN context when there is one):
    records, streaks, last games, scoring, form, splits, head to head, power rank, starters,
    rest and injuries. For picks whose own reasons are about the price (sharp value, live)."""
    out: list[str] = []
    unit = _unit(sport)
    hg, ag = season_games(results, home, before), season_games(results, away, before)
    if hg or ag:
        out.append(f"{away} ({fmt_record(record(ag, away))}) @ {home} ({fmt_record(record(hg, home))}).")
    runs = []
    for team, games in ((away, ag), (home, hg)):
        s = streak(games, team)
        if s and s[1] >= 2 and s[0] != "tied":
            runs.append(f"{team} has {s[0]} {s[1]} straight")
    if runs:
        out.append("Streaks: " + "; ".join(runs) + ".")
    for team, games in ((away, ag), (home, hg)):
        line = last_game_line(games, team, team)
        if line:
            out.append(line)
    sides = []
    for team, games in ((away, ag), (home, hg)):
        pg = _per_game(games, team)
        if pg:
            sides.append(f"{team} {pg[0]:.1f} scored / {pg[1]:.1f} allowed")
    if sides:
        out.append(f"Season {unit} per game: " + "; ".join(sides) + ".")
    forms = [f for f in (_form(ag, away), _form(hg, home)) if f]
    if forms:
        out.append(f"Last {FORM_GAMES}: " + "; ".join(forms) + ".")
    if not neutral:
        hh, ar = [g for g in hg if g.home == home], [g for g in ag if g.away == away]
        if len(hh) >= 2 and len(ar) >= 2:
            out.append(f"Splits: {home} {fmt_record(record(hh, home))} at home; {away} {fmt_record(record(ar, away))} on the road.")
    h2h = [g for g in hg if away in (g.home, g.away)]
    if h2h:
        g = h2h[-1]
        out.append(f"Head to head this season: {home} {fmt_record(record(h2h, home))} "
                   f"(last: {g.away} {g.away_score:.0f} - {g.home} {g.home_score:.0f}, {g.date:%-m/%-d}).")
    if ratings is not None:
        ranks = power_ranks(SimpleNamespace(ratings=ratings))
        if home in ranks and away in ranks:
            out.append(f"Power rank: {away} #{ranks[away]} vs {home} #{ranks[home]} (of {len(ranks)}).")
    if ctx is not None:
        rest = _rest_line(ctx)
        if rest:
            out.append(rest)
        if ctx.home_pitcher or ctx.away_pitcher:
            out.append(_starters(ctx, sport))
        for team, players in ctx.injuries.items():
            if players:
                more = f" +{len(players) - 4} more" if len(players) > 4 else ""
                out.append(f"{team} missing: {', '.join(players[:4])}{more}.")
    return out


def _starters(ctx: EventContext, sport: str) -> str:
    def sp(name, era, sv):
        if era is not None:
            return f"{name} ({era:.2f} ERA)"
        if sv:
            return f"{name} ({sv[0]:.3f} career save %, {sv[1]:,.0f} shots)"
        return name or "TBD"
    label = "Starting goalies" if sport == "nhl" else "Starters"
    return (f"{label}: {sp(ctx.away_pitcher, ctx.away_pitcher_era, ctx.away_goalie_sv)} vs "
            f"{sp(ctx.home_pitcher, ctx.home_pitcher_era, ctx.home_goalie_sv)}.")


def find_context(scan: SportScan, home: str, away: str, when: datetime) -> tuple[str, EventContext] | None:
    for eid, c in scan.contexts.items():
        if c.home == home and c.away == away and abs((c.event.commence_time - when).total_seconds()) < 12 * 3600:
            return eid, c
    return None


def add_facts(card, scans: list[SportScan], keep_first: int = 2) -> None:
    """Give a price-only card (sharp value) the game facts: up to MAX_FACTS lines in all, the
    card's own price lines first and its closing note last."""
    scan = next((s for s in scans if s.sport.key == card.sport), None)
    if scan is None or not card.home:
        return
    found = find_context(scan, card.home, card.away, card.pick.commence_time)
    ctx = found[1] if found else None
    facts = []
    if found and found[0] in scan.predictions:
        pred = scan.predictions[found[0]]
        facts.append(f"Our projected score: {card.away} {pred.exp_away:.1f} - {card.home} {pred.exp_home:.1f}.")
    facts += matchup_facts(scan.results, card.home, card.away, card.pick.commence_time, scan.sport.key,
                           scan.ratings, ctx, ctx.neutral if ctx else False)
    room = max(MAX_FACTS - len(card.reasons), 0)
    card.reasons = card.reasons[:keep_first] + facts[:room] + card.reasons[keep_first:]


def _unit(sport: str) -> str:
    return UNIT.get(sport, "goals")  # every other key is a soccer league


def _per_game(games: list[GameResult], team: str) -> tuple[float, float] | None:
    if not games:
        return None
    pf = sum(g.home_score if g.home == team else g.away_score for g in games) / len(games)
    pa = sum(g.away_score if g.home == team else g.home_score for g in games) / len(games)
    return pf, pa


def _form(games: list[GameResult], team: str) -> str | None:
    last = games[-FORM_GAMES:]
    if len(last) < 3:
        return None
    pf, pa = _per_game(last, team)
    return f"{team} {fmt_record(record(last, team))} (avg margin {pf - pa:+.1f})"


def power_ranks(scan: SportScan) -> dict[str, int]:
    r = scan.ratings
    if r is None:
        return {}
    teams = [t for t in r.off if r.games_played.get(t, 0) >= 3]
    net = sorted(teams, key=lambda t: r.off[t] + r.deff.get(t, 0.0), reverse=True)
    return {t: i + 1 for i, t in enumerate(net)}


def track_record(scan: SportScan, home_prob: float) -> tuple[float, float, int] | None:
    """The backtest bucket nearest this win probability: (predicted, actual, games)."""
    cal = [c for c in (scan.brain.calibration if scan.brain else []) if c[2] >= 15]
    if not cal:
        return None
    return min(cal, key=lambda c: abs(c[0] - home_prob))


def stat_lines(pick: Pick, scan: SportScan, ctx: EventContext, pred) -> list[str]:
    """The numbers behind a game pick: scoring, form, venue splits, head-to-head,
    power rank, the chance of an upset and how the model has done at this confidence."""
    out: list[str] = []
    before = ctx.event.commence_time
    unit = _unit(scan.sport.key)
    hg = season_games(scan.results, ctx.home, before)
    ag = season_games(scan.results, ctx.away, before)

    sides = []
    for team, games in ((ctx.away, ag), (ctx.home, hg)):
        pg = _per_game(games, team)
        if pg:
            sides.append(f"{team} {pg[0]:.1f} scored / {pg[1]:.1f} allowed")
    if sides:
        out.append(f"Season {unit} per game: " + "; ".join(sides) + ".")

    forms = [f for f in (_form(ag, ctx.away), _form(hg, ctx.home)) if f]
    if forms:
        out.append(f"Last {FORM_GAMES}: " + "; ".join(forms) + ".")

    if not ctx.neutral:
        home_at_home = [g for g in hg if g.home == ctx.home]
        away_on_road = [g for g in ag if g.away == ctx.away]
        if len(home_at_home) >= 2 and len(away_on_road) >= 2:
            out.append(
                f"Splits: {ctx.home} {fmt_record(record(home_at_home, ctx.home))} at home; "
                f"{ctx.away} {fmt_record(record(away_on_road, ctx.away))} on the road."
            )

    h2h = [g for g in hg if ctx.away in (g.home, g.away)]
    if h2h:
        g = h2h[-1]
        out.append(
            f"Head to head this season: {ctx.home} {fmt_record(record(h2h, ctx.home))}"
            f" (last: {g.away} {g.away_score:.0f} - {g.home} {g.home_score:.0f}, {g.date:%-m/%-d})."
        )

    ranks = power_ranks(scan)
    if ctx.home in ranks and ctx.away in ranks:
        n = len(ranks)
        out.append(f"Power rank: {ctx.away} #{ranks[ctx.away]} vs {ctx.home} #{ranks[ctx.home]} (of {n}).")

    team = pick_team(pick, ctx)
    if team and pick.market in ("h2h", "spreads"):
        p_win = pred.p_home if team == ctx.home else pred.p_away
        p_fav = max(pred.p_home, pred.p_away)
        if p_win >= p_fav:
            out.append(f"Upset risk: {1 - p_win - pred.p_draw:.0%} chance {ctx.away if team == ctx.home else ctx.home} wins outright.")
        else:
            out.append(f"Upset chance: {team} wins outright {p_win:.0%} of the time on our numbers.")
        if pick.american > 0 and p_win < p_fav:
            case = upset_case(team, ctx, hg, ag, ranks)
            if case:
                out.append("Upset case: " + "; ".join(case) + ".")
        home_prob = pred.p_home
        tr = track_record(scan, home_prob)
        if tr:
            pred_p, actual, n = tr
            if team == ctx.away:
                pred_p, actual = 1 - pred_p, 1 - actual
            out.append(
                f"Track record: teams we gave a {pred_p:.0%} chance to win outright won {actual:.0%} of the time "
                f"({n} recent games tested)."
            )
    return out


def upset_case(team: str, ctx: EventContext, hg, ag, ranks: dict[str, int]) -> list[str]:
    """Where the underdog is actually the better side on paper."""
    opp = ctx.away if team == ctx.home else ctx.home
    mine, theirs = (hg, ag) if team == ctx.home else (ag, hg)
    case = []
    a, b = mine[-FORM_GAMES:], theirs[-FORM_GAMES:]
    if len(a) >= 3 and len(b) >= 3:
        ra, rb = record(a, team), record(b, opp)
        if ra[0] / len(a) > rb[0] / len(b):
            case.append(f"hotter lately ({fmt_record(ra)} vs {fmt_record(rb)} in the last {FORM_GAMES})")
    pa, pb = _per_game(mine, team), _per_game(theirs, opp)
    if pa and pb and pa[1] < pb[1]:
        case.append(f"better defense ({pa[1]:.1f} allowed vs {pb[1]:.1f})")
    if team in ranks and opp in ranks and ranks[team] < ranks[opp]:
        case.append(f"higher power rank (#{ranks[team]} vs #{ranks[opp]})")
    rest_mine = ctx.home_rest if team == ctx.home else ctx.away_rest
    rest_opp = ctx.away_rest if team == ctx.home else ctx.home_rest
    if rest_mine and rest_opp and rest_mine - rest_opp >= 2 and rest_mine < 20:
        case.append(f"more rest ({rest_mine:.0f} days vs {rest_opp:.0f})")
    if team == ctx.home and not ctx.neutral:
        case.append("home field")
    return case


def _rest_line(ctx: EventContext) -> str | None:
    if ctx.home_b2b != ctx.away_b2b:
        tired = ctx.home if ctx.home_b2b else ctx.away
        return f"{tired} on the second night of a back-to-back."
    if ctx.home_rest and ctx.away_rest and abs(ctx.home_rest - ctx.away_rest) >= 3 and max(ctx.home_rest, ctx.away_rest) < 20:
        fresher = ctx.home if ctx.home_rest > ctx.away_rest else ctx.away
        return f"Rest edge: {fresher} ({max(ctx.home_rest, ctx.away_rest):.0f} days vs {min(ctx.home_rest, ctx.away_rest):.0f})."
    return None


BOOK_NAMES = {
    "draftkings": "DraftKings", "fanduel": "FanDuel", "betmgm": "BetMGM", "caesars": "Caesars",
    "williamhill_us": "Caesars", "pinnacle": "Pinnacle", "betrivers": "BetRivers", "espnbet": "ESPN BET",
    "fanatics": "Fanatics", "bovada": "Bovada", "betonlineag": "BetOnline", "mybookieag": "MyBookie",
    "hardrockbet": "Hard Rock", "ballybet": "Bally Bet", "lowvig": "LowVig", "betus": "BetUS",
    "bet365": "bet365", "draftkings_est": "DraftKings (price not shown by ESPN, assumed -115; take it at -115 or better)",
}


def pretty_book(key: str) -> str:
    return BOOK_NAMES.get(key, key.replace("_", " ").title())
