"""Plain-text tables for the terminal plus JSON / CSV export."""

from __future__ import annotations

import csv
import json
from typing import Iterable, Sequence

from .backtest import BacktestReport
from .edge import Pick, Settings, best_per_event, qualifying
from .engine import SportScan
from .models.ratings import TeamRatings


def table(headers: Sequence[str], rows: Iterable[Sequence[object]], align: str | None = None) -> str:
    rows = [[str(c) for c in r] for r in rows]
    widths = [len(h) for h in headers]
    for r in rows:
        widths = [max(w, len(c)) for w, c in zip(widths, r)]
    align = align or "l" * len(headers)

    def fmt(cells):
        return "  ".join(c.rjust(w) if a == "r" else c.ljust(w) for c, w, a in zip(cells, widths, align))

    lines = [fmt(headers), "  ".join("-" * w for w in widths)]
    lines += [fmt(r) for r in rows]
    return "\n".join(lines)


def _odds(a: float) -> str:
    return f"{a:+.0f}"


def _pct(p: float | None) -> str:
    return "-" if p is None else f"{p * 100:.1f}%"


def pick_rows(picks: list[Pick], settings: Settings) -> list[list[object]]:
    rows = []
    for p in picks:
        rows.append([
            p.commence_time.astimezone().strftime("%a %H:%M"),
            p.matchup,
            p.selection + (" (!)" if p.warnings else ""),
            p.book,
            _odds(p.american),
            _odds(p.fair_american),
            _pct(p.model_prob),
            _pct(p.market_prob),
            f"{p.ev * 100:+.1f}%",
            f"{p.kelly * 100:.1f}%" + (f" ${p.stake:,.0f}" if p.stake else ""),
        ])
    return rows


HEADERS = ["Time", "Matchup", "Bet", "Book", "Odds", "Fair", "Model", "Market", "EV", "Stake"]
ALIGN = "lllllrrrrr"


def render_scan(scan: SportScan, settings: Settings, top: int = 15, all_picks: bool = False) -> str:
    out = [f"\n=== {scan.sport.name} — {scan.events} upcoming events ==="]
    game = [p for p in scan.picks if not p.is_prop]
    props = [p for p in scan.picks if p.is_prop]

    if all_picks:
        sections = [("All game-line edges", qualifying(game, settings)), ("All prop edges", qualifying(props, settings)[:top])]
    else:
        sections = [
            ("Best game-line bet per game", best_per_event(game, settings)),
            ("Top player props", qualifying(props, settings)[:top]),
        ]
    for title, picks in sections:
        if title.endswith("props") and not props:
            continue
        out.append(f"\n{title} (EV >= {settings.min_ev:.0%}):")
        if picks:
            out.append(table(HEADERS, pick_rows(picks, settings), ALIGN))
        else:
            out.append("  none")

    capped = [p for p in scan.picks if p.ev > settings.max_ev]
    if capped:
        out.append(
            f"\n{len(capped)} picks above the {settings.max_ev:.0%} EV sanity cap were hidden "
            "(usually stale lines, injuries or lineup news the model can't see)."
        )
    if settings.hide_flagged:
        hidden = [p for p in scan.picks if p.warnings and settings.min_ev <= p.ev <= settings.max_ev]
        if hidden:
            out.append(
                f"{len(hidden)} picks hidden where model and market differ by more than "
                f"{settings.disagreement_warning * 100:.0f} percentage points (see them with --show-flagged)."
            )
    elif any(p.warnings for p in qualifying(scan.picks, settings)):
        out.append("(!) = model and market disagree a lot; check injuries/lineups before betting.")
    for w in dict.fromkeys(scan.warnings):
        out.append(f"warning: {w}")
    return "\n".join(out)


def render_ratings(ratings: TeamRatings, top: int) -> str:
    rows = []
    for i, (team, net, o, d) in enumerate(ratings.power_ranking()[:top], 1):
        rows.append([i, team, f"{net:+.2f}", f"{o:+.3f}", f"{d:+.3f}"])
    hfa = f"{ratings.hfa:+.2f}" if ratings.link == "identity" else f"x{2.718281828 ** ratings.hfa:.3f}"
    head = f"Home advantage: {hfa} | games weighted: {sum(ratings.games_played.values()) / 2:.0f}"
    return head + "\n" + table(["#", "Team", "Net", "Off", "Def"], rows, "rlrrr")


def render_backtest(r: BacktestReport) -> str:
    if not r.games:
        return f"{r.sport}: not enough games to backtest"
    lines = [
        f"{r.sport}: {r.games} games",
        f"  moneyline log loss {r.log_loss:.4f} (home-rate baseline {r.baseline_log_loss:.4f})",
        f"  Brier {r.brier:.4f} | straight-up accuracy {r.accuracy:.1%}",
        f"  margin MAE {r.margin_mae:.2f} | total MAE {r.total_mae:.2f}",
        "  calibration (predicted home win -> actual):",
    ]
    lines.append(
        "\n".join(
            "    " + line
            for line in table(
                ["Pred", "Actual", "Games"],
                [[f"{p:.1%}", f"{a:.1%}", n] for p, a, n in r.calibration],
                "rrr",
            ).splitlines()
        )
    )
    return "\n".join(lines)


def write_json(path: str, picks: list[Pick]) -> None:
    with open(path, "w") as f:
        json.dump([p.to_dict() for p in picks], f, indent=2)


def write_csv(path: str, picks: list[Pick]) -> None:
    if not picks:
        open(path, "w").close()
        return
    rows = [p.to_dict() for p in picks]
    for r in rows:
        r["warnings"] = "; ".join(r["warnings"])
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
