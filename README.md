# SportsEdge

A stats-driven engine that prices every bet on the board and ranks the ones
where the price is better than the true odds:

- **Moneylines** (2-way, and 3-way with the draw for soccer)
- **Spreads**: point spreads, run lines, puck lines and Asian handicaps (quarter lines too)
- **Totals** (over/under)
- **Player props** (over/under), e.g. points, rebounds, passing yards, strikeouts, shots on goal

Sports: **NFL, College Football, NBA, College Basketball, MLB, NHL**, and soccer
(**Premier League, La Liga, Bundesliga, Serie A, Ligue 1, MLS, Champions League**).

```
=== NBA — 8 upcoming events ===

Best game-line bet per game (EV >= 2%):
Time       Matchup                          Bet                Book        Odds  Fair  Model  Market      EV  Stake
Thu 07:00  Boston Foxes @ Phoenix Owls      Boston Foxes ML    pinnacle    +160  +137  47.5%   37.1%   +9.9%   1.5%
Thu 10:00  Mobile Storm @ Houston Titans    Under 236          fanduel     -101  -115  57.1%   49.6%   +6.1%   1.6%
...
```
(Output above is from `--demo` mode, which uses made-up teams.)

## Quick start

```bash
pip install -e .                    # Python 3.10+, depends on numpy + requests

sportsedge scan nba --demo --props  # try it offline with synthetic data

export ODDS_API_KEY=your_key        # free tier at https://the-odds-api.com
sportsedge scan nba nfl             # live game lines
sportsedge scan nba --props --bankroll 1000 --hours 12
sportsedge scan soccer --books draftkings,fanduel,betmgm
sportsedge scan all --json picks.json
```

Other commands (these need no odds key; they only use ESPN):

```bash
sportsedge ratings ncaaf --top 25   # fitted power ratings
sportsedge backtest nba --days 90   # walk-forward accuracy and calibration
sportsedge sports                   # list sport keys and aliases
```

Sport keys: `nfl ncaaf nba ncaab mlb nhl epl laliga bundesliga seriea ligue1 mls ucl`.
Aliases: `soccer`, `football`, `basketball`, `college`, `cfb`, `cbb`, `hockey`, `baseball`, `all`.

### Useful `scan` options

| Option | Default | Meaning |
|---|---|---|
| `--props` | off | Also price player props. Each event costs extra Odds API credits. |
| `--prop-markets` | all | e.g. `player_points,player_rebounds` |
| `--min-ev` | 0.02 | Only show bets with at least +2% expected return. |
| `--market-weight` | 0.5 | How much to trust the no-vig market vs. the model (0 = model only). |
| `--kelly` | 0.25 | Fraction of full Kelly used for stake sizing. |
| `--max-stake` | 0.03 | Never stake more than 3% of bankroll on one bet. |
| `--max-ev` | 0.30 | Hide "too good to be true" edges (usually stale lines or injury news). |
| `--show-flagged` | off | Include picks where model and market differ by over 12 points. |
| `--books` / `--regions` | `us` | Restrict which sportsbooks are compared. |
| `--hours` | all | Only events starting within N hours. |
| `--all` | off | Show every qualifying game-line edge, not just the best one per game. |
| `--json` / `--csv` | | Export qualifying picks. |

## How it works

```
ESPN results ──► team ratings ──► score distribution ──┐
ESPN box scores ──► player projections ────────────────┼──► win/push/loss ──► EV vs. best price ──► ranked picks
The Odds API prices ──► no-vig consensus ──────────────┘         ▲
                                                     blend model + market
```

1. **Team ratings** (`sportsedge/models/ratings.py`). Every team gets an offense
   and a defense rating plus a league-wide home advantage. They're fitted from
   the last season or so of final scores.
   - Football and basketball use ridge regression on points scored.
   - Baseball, hockey and soccer use ridge Poisson regression on runs/goals.
   - Recent games weigh more (exponential decay, sport-specific half-life).
   - The ridge penalty pulls teams with few games toward average.
   - Unrated college teams (e.g. FCS opponents) are treated as low-percentile teams.
2. **Score distributions** (`sportsedge/models/game.py`).
   - Football and basketball: margin and total are discretized normals, so
     whole-number lines get proper push probabilities (key numbers like NFL 3 and 7).
   - Baseball: negative binomial (runs are overdispersed). Hockey: Poisson.
   - Soccer: Poisson with a Dixon-Coles correction for low-scoring draws.
   - Ties resolve as overtime/extra innings, or as a draw for soccer.
3. **Player props** (`sportsedge/models/props.py`).
   - Each player's stat comes from their recent ESPN box scores, weighted
     toward recent games.
   - The projection is adjusted for how many points the team is expected to
     score in this matchup (opponent defense and pace).
   - Count stats use Poisson/negative binomial; yardage and points use a normal.
4. **Market consensus** (`sportsedge/edge.py`). Each book's vig is removed
   (power method). Sharp books (Pinnacle, Circa, etc.) are used when available,
   otherwise the average across books.
5. **Blend, then price.** The model's probability is blended with the market's
   (`--market-weight`). EV is computed at the **best available price** across
   books, with pushes handled exactly. Stakes use fractional Kelly with a cap.
6. **Sanity filters.** Some edges are almost always data problems rather than
   real value: huge EVs, or big model-vs-market disagreements (injuries,
   suspensions, a starter resting, a goalie or pitcher change). These are
   hidden by default and counted in the output.

## Tuning per sport

All per-sport knobs are in `sportsedge/sports.py`: rating half-life, ridge
strength, lookback window, margin/total standard deviations, negative-binomial
dispersion, soccer draw correction, and the box-score fields behind each prop
market.

Use `sportsedge backtest <sport>` while tuning. It refits weekly on past data
only and reports log loss vs. a baseline, Brier score, margin/total MAE and a
calibration table. If predicted 70% favorites win 60% of the time, the model
is overconfident: raise `margin_sd` (or the ridge) for that sport.

## Data sources and costs

- **ESPN public site API**: schedules, scores and box scores. No key needed.
  Responses are cached in `~/.cache/sportsedge`, or in `$SPORTSEDGE_CACHE`.
  Completed box scores are cached forever. The first college basketball run
  downloads a full season of scoreboards, so it is slower.
- **The Odds API**: prices. A game-lines scan costs about 3 credits per sport.
  Props are fetched per event (`--props`) and cost more, so narrow them with
  `--hours` and `--prop-markets`. Remaining credits are printed after each scan.
- Soccer player props aren't supported yet: ESPN soccer box scores don't
  include per-player shot stats.

## Read this before betting real money

- Betting markets, especially closing lines at sharp books, are very
  efficient. A ratings model built only on scores is a **baseline**. Treat
  "+EV" here as "worth a closer look", not a guaranteed edge.
- The model does not know about injuries, lineups, rest, travel, weather,
  starting pitchers or starting goalies. Check the news on every pick,
  especially MLB (starting pitcher) and NHL (goalie).
- Track your results against the **closing line** (CLV). Beating the close
  over hundreds of bets is the best evidence of a real edge.
- Bet responsibly, and only where sports betting is legal for you.

## Development

```bash
pip install -e '.[dev]'
pytest
```

Tests run offline. They use synthetic data (`sportsedge/data/sample.py`) and
recorded-format ESPN / Odds API payloads.

Project layout:

```
sportsedge/
  sports.py          sport configs + prop market definitions
  odds.py            odds conversion, devig, EV, Kelly
  distributions.py   discrete normal / Poisson / NB pmfs, line pricing (push + quarter lines)
  models/ratings.py  team rating fits (ridge LS / ridge Poisson)
  models/game.py     game score distributions -> ML / spread / total probs
  models/props.py    player stat projections
  edge.py            market consensus, blending, EV, ranking
  engine.py          scan pipeline
  backtest.py        walk-forward model evaluation
  data/espn.py       ESPN client + box-score parser
  data/oddsapi.py    The Odds API client
  data/sample.py     synthetic provider for demo/tests
  report.py, cli.py  terminal output and CLI
```
