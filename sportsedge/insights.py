"""Plain-English reasons for a pick, built from the same data the model used."""

from __future__ import annotations

from datetime import datetime, timedelta

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
            f"We make this {our:.0%} to cash; {odds} only needs {implied:.0%}."
        )
        if pick.projection:
            bullets.append(f"Projection: {pick.projection}.")
    else:
        team = pick_team(pick, ctx) if ctx else None
        if pick.market == "h2h" and team:
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
                def sp(name, era):
                    return f"{name} ({era:.2f} ERA)" if era is not None else (name or "TBD")
                bullets.append(
                    f"Starters: {sp(ctx.away_pitcher, ctx.away_pitcher_era)} vs {sp(ctx.home_pitcher, ctx.home_pitcher_era)}."
                )

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
}


def pretty_book(key: str) -> str:
    return BOOK_NAMES.get(key, key.replace("_", " ").title())
