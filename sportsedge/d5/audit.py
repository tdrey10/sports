"""Miss audit (owner, Oct 25: "we need to be catching this on every game there is").

Every run, each money pick the D503 engine posted is looked up on our board: same game, same side.
When we didn't post it, the audit names the rule that stopped us and puts our numbers beside his,
so every gap points at what to fix. Saved to data/d5_audit.json and printed in the run log.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from .board import DOG_GATE, DOG_MAX, DOG_MIN, MAX_FAV, Side, lock_ok, nhl_pricey


def his_money(picks: list[dict]) -> list[dict]:
    """His single-game moneyline picks with units, flattened."""
    out = []
    for p in picks:
        legs = p.get("legs") or []
        if not (p.get("units") or 0) > 0 or len(legs) != 1:
            continue
        leg = legs[0]
        league, _, eid = str(leg.get("game_id") or "").partition(":")
        if leg.get("market") != "ml" or not eid:
            continue
        out.append({"date": p.get("date"), "kind": p.get("kind"), "units": p.get("units"), "league": league,
                    "eid": eid, "side": "home" if leg.get("home") else "away", "team": leg.get("team"),
                    "american": p.get("american"), "p": leg.get("p"), "dog_score": leg.get("dog_score"),
                    "reasons": leg.get("reasons") or [], "result": leg.get("result") or p.get("status")})
    return out


def blockers(s: Side, kind: str, pool_ok: bool = True) -> list[str]:
    """Every rule that keeps this side off our board as his kind of pick (empty = it qualifies)."""
    why = []
    if not pool_ok:
        why.append("game not on today's board (already posted, or not today)")
    if not s.reasons and not (s.odds >= 100 and s.dog_p is not None):
        why.append("no reason (a pick needs one)")
    if s.fighting:
        why.append(f"fighting the line (own {s.own:.1%} vs price {1 / s.dec:.1%}, drift {s.drift:+.3f})")
    if s.hurt:
        why.append("injury block (hurt)")
    if s.waiting:
        why.append("waiting on a questionable key player")
    dog = s.odds >= 100
    if dog:
        if not DOG_MIN <= s.odds <= DOG_MAX:
            why.append(f"price {s.odds:+.0f} outside +100..+350")
        gate = DOG_GATE.get(s.sport)
        if kind == "play" and s.sport == "ncaaf":  # his college gap play
            if s.elo_pts < 0:
                why.append(f"gap play needs the ratings to back the dog (Elo edge {s.elo_pts:+.0f})")
            if s.raw_own - s.p_market < 0.10:
                why.append(f"gap under 10 pts (ratings {s.raw_own:.1%} vs books {s.p_market:.1%})")
            if not s.beats_price():
                why.append(f"our read {s.read_of:.1%} doesn't beat the price {1 / s.dec:.1%}")
        else:
            if s.dog_score is None:
                why.append("no dog score")
            elif gate is not None and s.dog_p is None:
                why.append(f"dog score {s.dog_score:+.1f} under the {s.sport.upper()} gate of {gate}")
            if s.edge_own <= 0:
                why.append(f"own read {s.own:.1%} doesn't beat the price {1 / s.dec:.1%}")
            if not s.beats_price():
                why.append(f"our read {s.read_of:.1%} doesn't beat the price {1 / s.dec:.1%}")
    else:
        if s.odds < MAX_FAV:
            why.append(f"price {s.odds:+.0f} past -150")
        need = 0.53 + (0.02 if s.intl else 0.0)
        if s.p < need:
            why.append(f"our read {s.p:.1%} under the {need:.0%} a favorite needs")
        if not s.own_agrees():
            why.append(f"own read {(s.w_p if s.w_p is not None else s.own):.1%} under the market {s.p_market:.1%}")
        if not s.real_value():
            why.append("no real value at the price")
        if kind == "lock":
            if not lock_ok(s):
                why.append(f"Lock needs 56%+ (ours {s.p:.1%})")
            if -119 <= s.odds <= -101:
                why.append("Lock skips -119..-101")
            if nhl_pricey(s):
                why.append("pricey hockey favorite")
    return why


def run(picks: list[dict], sides: list[Side], posted: dict[tuple[str, str], str], now: datetime,
        path: Path | str | None = None, days: tuple[str, ...] = ()) -> list[dict]:
    """Compare his money picks for ``days`` against our sides; ``posted`` maps (eid, side) -> our tier."""
    by_key = {(s.eid, s.side): s for s in sides}
    rows = []
    for h in his_money(picks):
        if days and h["date"] not in days:
            continue
        key = (h["eid"], h["side"])
        row = {**h, "checked_at": now.isoformat()}
        if key in posted:
            row["status"] = "caught"
            row["ours"] = posted[key]
        elif key not in by_key:
            row["status"] = "no line"
            row["why"] = ["the game wasn't on our board (no price on both sides, injuries unknown, or already started)"]
        else:
            s = by_key[key]
            row["status"] = "missed"
            row["why"] = blockers(s, h["kind"]) or ["passed every rule but another pick took the slot"]
            row["ours"] = {"p": round(s.p, 4), "own": round(s.own, 4), "dog_score": s.dog_score and round(s.dog_score, 2),
                           "reasons": s.reasons, "odds": s.odds}
        rows.append(row)
        if row["status"] != "caught":
            print(f"D503 miss: {h['league']} {h['team']} {h['american']:+} {h['kind']} {h['units']}u "
                  f"(his read {h['p'] or 0:.1%}, dog score {h['dog_score']}) - " + "; ".join(row["why"]))
    if path is not None:
        p = Path(path)
        try:
            old = json.loads(p.read_text())
        except (OSError, ValueError):
            old = []
        mine = {(r["eid"], r["side"], r["kind"]) for r in rows}
        keep = [r for r in old if (r["eid"], r["side"], r["kind"]) not in mine]
        merged = sorted(keep + rows, key=lambda r: r.get("date") or "")[-500:]
        p.write_text(json.dumps(merged, indent=1))
    return rows


__all__ = ["his_money", "blockers", "run"]
