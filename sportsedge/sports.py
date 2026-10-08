"""Per-sport configuration: data sources, scoring model and prop markets."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class PropSpec:
    """How to compute a player-prop stat from ESPN box-score stats.

    ``terms`` is summed; each term is a list of candidate box-score keys and
    the first one present in a player's line is used (ESPN labels vary).
    ``dist`` is "count" (Poisson / negative binomial) for low-count stats and
    "normal" for high-volume stats such as points or yards.
    ``scales_with_team`` adjusts the projection by how many points the
    player's team is expected to score in this matchup vs. its baseline.
    """

    label: str
    terms: tuple[tuple[str, ...], ...]
    dist: str = "count"
    scales_with_team: bool = False


@dataclass(frozen=True)
class SportConfig:
    key: str
    name: str
    espn_path: str
    odds_key: str
    scoring: str  # "normal" (margin/total ~ normal) or "count" (team scores ~ Poisson/NB)
    link: str  # rating model link: "identity" or "log"
    half_life_days: float
    ridge: float
    lookback_days: int
    margin_sd: float = 0.0
    total_sd: float = 0.0
    dispersion: float | None = None  # NB dispersion for count sports (None = Poisson)
    max_score: int = 15
    draws: bool = False  # soccer: 3-way moneyline
    dixon_coles_rho: float = 0.0
    espn_groups: str | None = None
    unknown_percentile: float = 50.0
    prop_games: int = 12
    rest_effects: bool = False  # learn a back-to-back penalty (NBA / NHL)
    margin_shape: dict[int, float] = field(default_factory=dict)  # learned key numbers, by |margin|
    total_shape: dict[int, float] = field(default_factory=dict)  # learned key numbers for totals
    has_injury_feed: bool = False  # ESPN publishes a league injury report
    carryover: float = 1.0  # weight of last season's games (below 1 = regress to the mean); set by the studies
    props: dict[str, PropSpec] = field(default_factory=dict)


def _p(label, *terms, dist="count", team=False) -> PropSpec:
    return PropSpec(label, tuple(tuple(t) if isinstance(t, (list, tuple)) else (t,) for t in terms), dist, team)


BASKETBALL_PROPS = {
    "player_points": _p("Points", "PTS", dist="normal", team=True),
    "player_rebounds": _p("Rebounds", "REB"),
    "player_assists": _p("Assists", "AST", team=True),
    "player_threes": _p("3PT Made", ("3PT", "3PM")),
    "player_blocks": _p("Blocks", "BLK"),
    "player_steals": _p("Steals", "STL"),
    "player_turnovers": _p("Turnovers", ("TO", "TOV")),
    "player_points_rebounds_assists": _p("Pts+Reb+Ast", "PTS", "REB", "AST", dist="normal", team=True),
    "player_points_rebounds": _p("Pts+Reb", "PTS", "REB", dist="normal", team=True),
    "player_points_assists": _p("Pts+Ast", "PTS", "AST", dist="normal", team=True),
    "player_rebounds_assists": _p("Reb+Ast", "REB", "AST"),
}

FOOTBALL_PROPS = {
    "player_pass_yds": _p("Pass Yds", "passing.YDS", dist="normal", team=True),
    "player_pass_tds": _p("Pass TDs", "passing.TD", team=True),
    "player_pass_completions": _p("Completions", "passing.C/ATT"),
    "player_pass_attempts": _p("Pass Attempts", "passing.C/ATT_A"),
    "player_pass_interceptions": _p("Interceptions", "passing.INT"),
    "player_rush_yds": _p("Rush Yds", "rushing.YDS", dist="normal", team=True),
    "player_rush_attempts": _p("Rush Attempts", "rushing.CAR"),
    "player_reception_yds": _p("Rec Yds", "receiving.YDS", dist="normal", team=True),
    "player_receptions": _p("Receptions", "receiving.REC"),
}

MLB_PROPS = {
    "batter_hits": _p("Hits", "batting.H"),
    "batter_home_runs": _p("Home Runs", "batting.HR"),
    "batter_rbis": _p("RBIs", "batting.RBI", team=True),
    "batter_runs_scored": _p("Runs", "batting.R", team=True),
    "batter_walks": _p("Walks", "batting.BB"),
    "batter_strikeouts": _p("Strikeouts (B)", ("batting.K", "batting.SO")),
    "batter_hits_runs_rbis": _p("H+R+RBI", "batting.H", "batting.R", "batting.RBI", team=True),
    "pitcher_strikeouts": _p("Strikeouts (P)", ("pitching.K", "pitching.SO")),
    "pitcher_hits_allowed": _p("Hits Allowed", "pitching.H"),
    "pitcher_walks": _p("Walks Allowed", "pitching.BB"),
    "pitcher_earned_runs": _p("Earned Runs", "pitching.ER"),
    "pitcher_outs": _p("Outs Recorded", "pitching.IP_OUTS"),
}

NHL_PROPS = {
    "player_points": _p("Points", "G", "A"),
    "player_goals": _p("Goals", "G"),
    "player_assists": _p("Assists", "A"),
    "player_shots_on_goal": _p("Shots on Goal", ("S", "SOG", "shotsTotal")),
    "player_blocked_shots": _p("Blocked Shots", ("BS", "blockedShots")),
    "player_total_saves": _p("Saves", ("SV", "saves")),
}

_SOCCER_COMMON = dict(
    scoring="count",
    link="log",
    half_life_days=180,
    ridge=6.0,
    lookback_days=540,
    max_score=10,
    draws=True,
    dixon_coles_rho=-0.08,
    dispersion=None,
)

SPORTS: dict[str, SportConfig] = {
    "nfl": SportConfig(
        key="nfl", name="NFL", espn_path="football/nfl", odds_key="americanfootball_nfl",
        scoring="normal", link="identity", half_life_days=150, ridge=3.0, lookback_days=500,
        margin_sd=13.5, total_sd=13.5, prop_games=10, has_injury_feed=True, props=FOOTBALL_PROPS,
    ),
    "ncaaf": SportConfig(
        key="ncaaf", name="College Football", espn_path="football/college-football",
        odds_key="americanfootball_ncaaf", scoring="normal", link="identity",
        half_life_days=120, ridge=3.0, lookback_days=450, margin_sd=16.5, total_sd=16.0,
        espn_groups="80", unknown_percentile=10.0, prop_games=8, props=FOOTBALL_PROPS,
    ),
    "nba": SportConfig(
        key="nba", name="NBA", espn_path="basketball/nba", odds_key="basketball_nba",
        scoring="normal", link="identity", half_life_days=60, ridge=6.0, lookback_days=300,
        margin_sd=12.5, total_sd=18.0, rest_effects=True, has_injury_feed=True, props=BASKETBALL_PROPS,
    ),
    "ncaab": SportConfig(
        key="ncaab", name="College Basketball", espn_path="basketball/mens-college-basketball",
        odds_key="basketball_ncaab", scoring="normal", link="identity", half_life_days=60,
        ridge=4.0, lookback_days=300, margin_sd=11.0, total_sd=15.5, espn_groups="50",
        unknown_percentile=10.0, props=BASKETBALL_PROPS,
    ),
    "mlb": SportConfig(
        key="mlb", name="MLB", espn_path="baseball/mlb", odds_key="baseball_mlb",
        scoring="count", link="log", half_life_days=90, ridge=25.0, lookback_days=400,
        dispersion=4.0, max_score=30, prop_games=25, has_injury_feed=True, props=MLB_PROPS,
    ),
    "nhl": SportConfig(
        key="nhl", name="NHL", espn_path="hockey/nhl", odds_key="icehockey_nhl",
        scoring="count", link="log", half_life_days=90, ridge=15.0, lookback_days=400,
        dispersion=None, max_score=15, rest_effects=True, has_injury_feed=True, props=NHL_PROPS,
    ),
    "epl": SportConfig(key="epl", name="Premier League", espn_path="soccer/eng.1",
                       odds_key="soccer_epl", **_SOCCER_COMMON),
    "laliga": SportConfig(key="laliga", name="La Liga", espn_path="soccer/esp.1",
                          odds_key="soccer_spain_la_liga", **_SOCCER_COMMON),
    "bundesliga": SportConfig(key="bundesliga", name="Bundesliga", espn_path="soccer/ger.1",
                              odds_key="soccer_germany_bundesliga", **_SOCCER_COMMON),
    "seriea": SportConfig(key="seriea", name="Serie A", espn_path="soccer/ita.1",
                          odds_key="soccer_italy_serie_a", **_SOCCER_COMMON),
    "ligue1": SportConfig(key="ligue1", name="Ligue 1", espn_path="soccer/fra.1",
                          odds_key="soccer_france_ligue_one", **_SOCCER_COMMON),
    "mls": SportConfig(key="mls", name="MLS", espn_path="soccer/usa.1",
                       odds_key="soccer_usa_mls", **_SOCCER_COMMON),
    "ucl": SportConfig(key="ucl", name="Champions League", espn_path="soccer/uefa.champions",
                       odds_key="soccer_uefa_champs_league", **_SOCCER_COMMON),
}

ALIASES: dict[str, list[str]] = {
    "soccer": ["epl", "laliga", "bundesliga", "seriea", "ligue1", "mls", "ucl"],
    "football": ["nfl", "ncaaf"],
    "basketball": ["nba", "ncaab"],
    "college": ["ncaaf", "ncaab"],
    "cfb": ["ncaaf"],
    "cbb": ["ncaab"],
    "hockey": ["nhl"],
    "baseball": ["mlb"],
    "all": list(SPORTS),
}


def resolve(names: list[str] | str) -> list[SportConfig]:
    if isinstance(names, str):
        names = [names]
    keys: list[str] = []
    for name in names:
        for part in name.lower().split(","):
            part = part.strip()
            if not part:
                continue
            if part in ALIASES:
                keys.extend(ALIASES[part])
            elif part in SPORTS:
                keys.append(part)
            else:
                raise KeyError(f"unknown sport {part!r}; choose from {sorted(SPORTS) + sorted(ALIASES)}")
    seen: dict[str, None] = dict.fromkeys(keys)
    return [SPORTS[k] for k in seen]
