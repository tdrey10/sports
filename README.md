# The Great Hambino

**100% free to run:** stats and betting lines come from ESPN (DraftKings lines, plus bet365 for soccer, live in-game lines, and player prop lines), and it runs on GitHub's free plan. No API keys or paid plans.

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

## The dashboard

`hambino run` (or `funky run`, or `sportsedge run`, same thing) turns each scan into a phone-friendly dashboard (`site/index.html`):

- **Best bets**: the day's top 5 across spreads, over/unders, moneylines and props. They're ranked by expected bankroll growth (Kelly), which weighs edge against risk, so a solid -110 edge outranks a +450 longshot with a slightly bigger EV.
- **Filter by bet type**: Spreads, Over/Under, Props, Moneyline, plus a record and closing-line value for each type.
- **Today's best**: Lock of the Day, Dog of the Day (best edge on a +100 to +220 dog) and Prop of the Day, on the page by 8 AM Pacific every day. The Lock is the most likely winner on the day's board at -150 or better, with injuries known and our model and the market agreeing. It carries units only when it also passes the strict edge checks (model 62%+, market 55%+, 60%+ to cash after calibration, edge score 50+, 8+ games per team); otherwise it is posted with no units. Every daily Lock has its own record on the Results tab. The Dog posts every day (a lean with no units when none clears the bar).
- **The board**: game-line value plays, player props, early value (games more than 24 hours out) and leans (small edges, no units, still tracked).
- **Every pick explains itself**: projected score, team records, last result, back-to-backs and rest, injuries, model vs. market odds, and the best book.
- **Line tracking**: "Got it at +215 → now +150 · we beat the line" for every open pick.
- **Live scores** on open picks, and automatic grading from final scores and box scores.
- **Parlay slip**: add open picks from different games to see the combined price, the chance it hits and its EV.
- **The record**: bankroll curve, record, units, ROI and closing-line value, broken down by tier and by sport.
- **The brain**: accuracy on recent games the model never saw, refreshed every run.

### What makes it sharper than a typical picks app

| | Typical picks app | SportsEdge |
|---|---|---|
| Bet size | Fixed ½ or 1 unit | Fractional Kelly from the measured edge (½–3 units) |
| Who to trust | The model's opinion | A blend of model and no-vig market, **re-learned from graded picks** for each sport |
| Proof of edge | Win/loss record | Win/loss **plus closing-line value**, which shows skill much sooner than a W-L record |
| Probabilities | Point estimates | Re-calibrated every run from out-of-sample errors |
| Prices | One book | Best price across every book in the feed |
| Bad edges | Posted anyway | Hidden when the model and market disagree by 12+ points (usually injury or lineup news) |

## Quick start

```bash
pip install -e .                    # Python 3.10+, depends on numpy + requests

sportsedge scan nba --demo --props  # try it offline with synthetic data

sportsedge scan nba nfl             # real lines from ESPN, free
sportsedge scan nba --props --bankroll 1000 --hours 12
sportsedge scan soccer --books draftkings,fanduel,betmgm
sportsedge scan all --json picks.json
```

Run the full engine and build the dashboard:

```bash
hambino run --demo --props             # demo: 14 days of simulated history, then today's board
open site/index.html

sportsedge run nba nfl ncaaf --props # live: picks go into data/ledger.json
sportsedge run all --scores-only     # live scores + grading only (ESPN, no odds credits)
sportsedge serve nba nfl             # keep running: scores every 2 min, odds every hour, http://127.0.0.1:8000
```

### Live bets

Live lines move in seconds, so live betting runs as a session you start before games: in the GitHub app open **Actions → Live session → Run workflow**, pick the sports, hours (up to 5) and minutes between checks.

- Every check reads the score and clock from ESPN (free). Only sports with games in progress use odds credits: 3 credits per check.
- In-play games are priced from the current score, the time left and the pregame model. The model doesn't know possession, power plays or pitch counts, and the books' live models do. So live picks lean harder on the market (85%), need at least +3% EV, and are skipped when the model and market disagree by more than 8 points or in the last 5% of a game.
- Each new live bet (one per game) is posted as a comment on the **The Great Hambino live bets** issue, and the GitHub app sends your phone a notification. Live bets are graded and tracked like every other pick.
- Locally: `hambino live nfl nba --hours 3 --every 4`.

### Run it on a schedule with GitHub Actions

`.github/workflows/sportsedge.yml` runs a full cycle every 30 minutes from 8 AM to 8 PM Pacific and hourly from 4 AM to 8 AM and 8 PM to midnight (fresh odds, new picks, live bets, scores and grading; the 6:15 AM run posts the Lock and Dog), inside the free 2,000 Actions minutes a month for private repos, and NFL + NBA player props once a day at 8:47 AM Pacific for games in the next 30 hours. It commits the ledger and saves the dashboard as a private download on each run (Actions tab → the run → **Artifacts**).

1. Merge to the default branch. No secrets are needed.
2. Run it once by hand from the **Actions** tab, using `props` if you want props.

**Publishing to GitHub Pages is off by default.** Project pages inherit the account's Pages custom domain. If this GitHub account also hosts another site, such as a business site on its own domain, the dashboard would appear under that domain. Only turn it on in a repo under an account with no other Pages site: add the repository variable `PUBLISH_PAGES` = `true` and set **Settings → Pages** to **GitHub Actions**.

The free Odds API tier has 500 credits a month, and a full cycle costs about 3 credits per in-season sport. Out-of-season sports are skipped automatically. At 8 cycles a day with 4 sports in season that's about 2,900 credits a month, so either pick a paid tier or slow the schedule in the workflow file.

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
| `--market-weight` | 0.75 | How much to trust the no-vig market vs. the model for game lines (0 = model only). |
| `--prop-market-weight` | 0.8 | Same, for player props. |
| `--kelly` | 0.25 | Fraction of full Kelly used for stake sizing. |
| `--max-stake` | 0.03 | Never stake more than 3% of bankroll on one bet. |
| `--max-ev` | 0.20 | Hide "too good to be true" edges (usually stale lines or injury news). |
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
6. **Key numbers.** Football margins land on 3, 7 and 10 far more often than a bell curve says, and totals have favorite numbers too. Each sport learns this lumpiness from its own final scores, so -3 vs -3.5 and the push chance on whole-number lines are priced correctly.
7. **MLB starting pitchers.** ESPN's probable starters and their ERA adjust the opponent's expected runs (shrunk halfway to league average, applied to the ~60% of the game a starter pitches). This affects totals, run lines and moneylines.
8. **Prop matchups.** Each stat is adjusted for how much of it this opponent has allowed lately vs. the average team (rebounds allowed, strikeouts by opposing pitchers, receiving yards allowed…), measured from box scores. Scoring stats follow the game model's expected score instead.
9. **Teammates out.** When a recent regular is ruled out, half of their production is redistributed to healthy teammates in proportion to their share.
10. **Rest and injuries.** NBA/NHL back-to-back effects are fitted from the data. ESPN's injury report is shown on each pick, and props for players listed out are skipped.
11. **Self-tuning.** Every run re-tests the model on recent games it never saw ("the brain"). It re-tunes each sport's score spread so a 60% call really wins 60%. Once a sport has 60 graded picks, it also re-learns how much to trust the model vs. the market.
12. **Sanity filters.** Some edges are almost always data problems rather than
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
- **ESPN odds (default, free)**: DraftKings pregame and live prices for game lines, bet365 for soccer moneylines, and player prop lines. ESPN shows prop lines without their prices, so props are priced at an assumed -115 each way. Only take those at -115 or better.
- **The Odds API (optional, `--odds-source oddsapi`)**: many books for line shopping. A game-lines scan costs 3 credits per in-season sport (about 36 for everything in October), and props cost 1 credit per market per game. The free plan's 500 credits a month covers only a couple of full scans a day for about a week, so pick a paid plan for daily use. Every run prints the credits left, and scans are skipped instead of running the balance below `--credit-floor` (default 60).
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
- Track your results against the **closing line** (CLV). The dashboard does
  this for you. Beating the close over hundreds of bets is the best evidence
  of a real edge, and it shows up much sooner than a win/loss record. A 19-10
  record over 29 bets can easily be luck.
- In simulation, trusting the market more (0.75 instead of 0.5) turned a
  losing strategy into a winning one when the books were sharper than the
  model. That's why the defaults lean on the market and the weights are
  re-learned from results.
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
  brain.py           walk-forward retraining + calibration
  tiers.py           Lock / Dog / Prop of the Day, value, early, leans, unit sizing
  insights.py        the written reasons on each pick
  ledger.py          published picks, line movement, CLV, grading, bankroll stats
  runner.py          one engine cycle; demo history simulation
  site.py, web/      the dashboard
  backtest.py        walk-forward model evaluation
  data/espn.py       ESPN client + box-score parser
  data/oddsapi.py    The Odds API client
  data/sample.py     synthetic provider for demo/tests
  report.py, cli.py  terminal output and CLI
```

## Money rules (always on)

- Never bet a price worse than **-150**, live bets included. Big favorites kill ROI.
- **Straight bets only.** No parlays are posted or built.
- **Unknown injuries = no bet.** A team with no injury report (feed down, or no feed, as in soccer) is unknown, not healthy. Its games show as leans only.
- **Injuries are weighed, not a hard rule.** NFL: QB out is about -8 points of win chance, RB or WR -3. College QB is -3.
- **No forced picks.** If nothing beats the price, there is no bet that day.
