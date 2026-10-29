"""The scorecard and grade on every pick (owner, Oct 28: the +EV spec - "required output for every
candidate", graded A+ / A / B / C / NO BET, "prefer NO BET over a weak or unsupported recommendation").

Grades come from validation evidence, not the model's confidence (owner's choice, Oct 28): our blind tests
showed the raw model's edges don't hold up on unseen seasons, so a bet earns its grade by what passed.
- A+: a STRONG blind-tested spot (tuning and blind both beat zero by a standard error), and its EV stays
  positive at the low end of the tested range.
- A: a PROVEN spot (blind beats zero by a standard error, with a tuning profit), or A+ that fails the
  sensitivity check.
- B: evidence short of a full blind pass (beats the closing line well above chance; a record of its own).
- C: the model sees an edge, but it has never been validated: a lean, no units.
- NO BET: no positive edge at this price.
Only A+, A and B carry units (tiers.gate already holds the rest at 0u).
"""

from __future__ import annotations

from datetime import datetime

from .odds import american_to_decimal, decimal_to_american

UNVALIDATED_ERR = 0.05  # +/- win-chance points assumed for a model read nothing has validated (an estimate)
SMALL_SAMPLE = 150


def _odds(p: float) -> float | None:
    return round(decimal_to_american(1 / p)) if 0 < p < 1 else None


def _range(card, dec: float) -> tuple[float, float, str]:
    """(low, high) win chance and how it was set."""
    ev = card.evidence or {}
    if ev.get("blind_roi") is not None and ev.get("blind_se") is not None:
        lo = (1 + ev["blind_roi"] - ev["blind_se"]) / dec
        hi = (1 + ev["blind_roi"] + ev["blind_se"]) / dec
        return max(lo, 0.01), min(hi, 0.99), "based on how this spot did in past games"
    p = card.pick.win_prob
    return (max(p - UNVALIDATED_ERR, 0.01), min(p + UNVALIDATED_ERR, 0.99),
            "our numbers' best guess, give or take (not proven)")


def grade(card) -> tuple[str, str]:
    p, dec = card.pick.win_prob, american_to_decimal(card.pick.american)
    lo, _, _ = _range(card, dec)
    ev = card.evidence or {}
    kind = ev.get("kind")
    if card.units > 0 and kind == "STRONG":
        if lo * dec - 1 > 0:
            return "A+", "Our best kind of bet: this spot made money on old games AND on games the system never saw, and the price is still good even if we're a little off."
        return "A", "A strong spot, but at this price there's not much room for error."
    if card.units > 0 and kind == "PROVEN":
        return "A", "A proven spot: it beat the books on games the system never saw, by more than luck."
    if card.units > 0 and kind in ("CLV", "RECORD"):
        return "B", ev.get("why") or "Good signs but not fully proven yet, so a small bet."
    if card.units > 0:
        return "B", "A bet you approved; see the proof below."
    if p * dec - 1 > 0:
        return "C", "Our numbers like it, but this kind of bet hasn't proven it makes money. Just a lean, no money."
    return "NO BET", "Not worth it at this price."


def score(card, g: str, ev_pct: float, edge_pts: float | None, lo: float, dec: float, risks: list[str]) -> dict:
    """The 100-point readout (owner, Oct 28): projection accuracy 30, EV 30, market edge 15, data quality 15,
    risk 10. Design weights, not proven ones: the letter grade stays on the evidence, and a bet with no
    positive EV is NO BET whatever this says."""
    ev = card.evidence or {}
    proj = {"STRONG": 30, "PROVEN": 24, "CLV": 15, "RECORD": 15}.get(ev.get("kind"), 5)
    val = round(max(min(ev_pct / 10, 1), 0) * 30)
    mkt = 0 if edge_pts is None else round(max(min(edge_pts / 5, 1), 0) * 15)
    data = 15 - (5 if card.pick.market_prob is None else 0) - (5 if any("questionable" in r.lower() for r in card.reasons) else 0)
    risk = 0 if lo * dec - 1 <= 0 else 5 if any(r.startswith("Small sample") for r in risks) else 10
    parts = {"projection": proj, "ev": val, "market": mkt, "data": data, "risk": risk}
    return {"total": sum(parts.values()) if g != "NO BET" else 0, **parts}


def build(card, now: datetime) -> dict:
    """Every field of the spec's required output that we can fill honestly; missing ones say so."""
    pk = card.pick
    dec = american_to_decimal(pk.american)
    lo, hi, how = _range(card, dec)
    g, why = grade(card)
    ev = card.evidence or {}
    mp = pk.market_prob
    factors = [r for r in card.reasons if not r.startswith(("✅", "💸", "No units", "Lean ·", "Lock of", "Dog of"))][:4]
    risks = []
    if not ev:
        risks.append("Not proven: our numbers alone have lost money in past tests.")
    if ev.get("blind_bets") is not None and ev["blind_bets"] < SMALL_SAMPLE:
        risks.append(f"Not a huge sample yet: {ev['blind_bets']} past bets.")
    if ev.get("blind_roi") is not None and ev.get("blind_se") is not None and ev["blind_roi"] - ev["blind_se"] <= 0:
        risks.append("The win-loss record could still be luck.")
    if ev.get("risk"):
        risks.append(ev["risk"])
    if any("questionable" in r.lower() for r in card.reasons):
        risks.append("A player is questionable. If he sits or plays, the odds will move.")
    if lo * dec - 1 <= 0 < pk.win_prob * dec - 1:
        risks.append("Thin: if we're a little off, there's no edge.")
    if not risks:
        risks.append("Odds move. Only bet it at the minimum price or better.")
    ev_pct = (pk.win_prob * dec - 1) * 100
    edge_pts = None if mp is None else (pk.win_prob - mp) * 100
    return {
        "grade": g, "why": why,
        "score": score(card, g, ev_pct, edge_pts, lo, dec, risks),
        "checked_at": now.isoformat(), "book": pk.book, "odds": pk.american,
        "model_prob": round(pk.win_prob, 4),
        "engine_prob": round(pk.model_prob, 4),
        "implied_prob": round(1 / dec, 4),
        "fair_market_prob": None if mp is None else round(mp, 4),
        "fair_odds": _odds(pk.win_prob),
        "edge_pts": None if mp is None else round((pk.win_prob - mp) * 100, 1),
        "ev_pct": round((pk.win_prob * dec - 1) * 100, 1),
        "range": [round(lo, 4), round(hi, 4)], "range_how": how,
        "min_odds": _odds(pk.win_prob),
        "validation": ev.get("text") or "None: this kind of bet hasn't been proven.",
        "factors": factors, "risks": risks,
    }


def attach(card, now: datetime):
    card.scorecard = build(card, now)
    card.grade = card.scorecard["grade"]
    return card


__all__ = ["attach", "build", "grade"]
