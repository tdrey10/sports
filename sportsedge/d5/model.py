"""The own read (logistic model on the features) and the market blend (D503 part 2, section 5).

Own model: Newton logistic regression with an L2 pull (lambda 5) toward the prior "Elo only"
(intercept 0, 1 on elo, 0 elsewhere), on the most recent 8,000 decided games after a warm-up.

Market blend, on the last 900 games with a moneyline (logistic with offset logit(m)):
    final = sigmoid(logit(m) + trust x (logit(own) - logit(m)) + move_w x line_move + cal x logit(m) + hdog x home_dog)
prior [0.15, 0, 0, 0], lambda 8, trust clipped to [0, 1]; under 80 games: trust 0.15, the rest 0.
NHL keeps only back-to-back and rest: final = sigmoid(logit(m) + b x b2b + r x rest).
Calibration: per sport, bias = won - said over sides at 55%+ and -150 or longer, clamped -0.06..+0.02,
used with 200+ such sides: p + b x min(1, (p - 0.5)/0.07) for p > 0.5.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np

from .data import Game, load
from .features import FEATURES, Replay, logit, sigmoid, tune_elo

WARMUP = 0.25
OWN_N = 8000
BLEND_N = 900


def dec(american: float) -> float:
    return 1 + american / 100 if american > 0 else 1 + 100 / -american


def no_vig(h: float, a: float) -> float:
    ih, ia = 1 / dec(h), 1 / dec(a)
    return ih / (ih + ia)


def newton(X: np.ndarray, y: np.ndarray, prior: np.ndarray, lam: float, offset: np.ndarray | None = None,
           iters: int = 25) -> np.ndarray:
    w = prior.astype(float).copy()
    off = np.zeros(len(y)) if offset is None else offset
    for _ in range(iters):
        p = 1 / (1 + np.exp(-(X @ w + off)))
        g = X.T @ (p - y) + lam * (w - prior)
        H = (X * (p * (1 - p))[:, None]).T @ X + lam * np.eye(len(w))
        step = np.linalg.solve(H, g)
        w -= step
        if np.max(np.abs(step)) < 1e-8:
            break
    return w


@dataclass
class SportModel:
    sport: str
    k: float
    hfa: float
    w: list[float]  # intercept + one per FEATURES
    blend: list[float] = field(default_factory=lambda: [0.15, 0.0, 0.0, 0.0])  # trust, move_w, cal, hdog
    nhl: list[float] = field(default_factory=lambda: [0.0, 0.0])  # b2b, rest (NHL only)
    bias: float = 0.0
    accuracy: float | None = None
    games: int = 0
    blend_games: int = 0

    def own(self, f: dict) -> float:
        x = [1.0] + [f[k] for k in FEATURES]
        return sigmoid(sum(a * b for a, b in zip(self.w, x)))

    def final(self, own: float, m: float | None, move: float, home_dog: bool, f: dict) -> float:
        if m is None:
            return own
        if self.sport == "nhl":
            return sigmoid(logit(m) + self.nhl[0] * f["b2b"] + self.nhl[1] * f["rest"])
        t, mw, cal, hd = self.blend
        return sigmoid(logit(m) + t * (logit(own) - logit(m)) + mw * move + cal * logit(m) + hd * float(home_dog))

    def calibrate(self, p: float) -> float:
        return p + self.bias * min(1.0, (p - 0.5) / 0.07) if p > 0.5 else p


def _rows(games: list[Game], sport: str, k: float, hfa: float) -> list[dict]:
    out = []
    r = Replay(sport, k, hfa)
    for g in games:
        info = r.features(g)
        if g.done and g.hs != g.as_ and info and info["known"] >= 5:
            m = no_vig(g.ml_h, g.ml_a) if g.ml_h and g.ml_a else None
            mo = no_vig(g.open_h, g.open_a) if g.open_h and g.open_a else None
            out.append({"g": g, "f": info["f"], "y": float(g.hs > g.as_), "m": m,
                        "move": (logit(m) - logit(mo)) if m is not None and mo is not None else 0.0})
        r.update(g)
    return out


def fit(sport: str, games: list[Game] | None = None, root: Path | str = "data") -> SportModel:
    games = games if games is not None else load(sport, root)
    k, hfa = tune_elo(games, sport)
    rows = _rows(games, sport, k, hfa)
    rows = rows[int(len(rows) * WARMUP):][-OWN_N:]
    X = np.array([[1.0] + [r["f"][c] for c in FEATURES] for r in rows])
    y = np.array([r["y"] for r in rows])
    prior = np.zeros(X.shape[1])
    prior[1 + FEATURES.index("elo")] = 1.0
    w = newton(X, y, prior, 5.0)
    model = SportModel(sport, k, hfa, [float(v) for v in w], games=len(rows))
    p = 1 / (1 + np.exp(-(X @ w)))
    model.accuracy = round(float(np.mean((p > 0.5) == (y == 1))), 3)
    lined = [r for r in rows if r["m"] is not None and 0.02 < r["m"] < 0.98][-BLEND_N:]
    model.blend_games = len(lined)
    if len(lined) >= 80:
        ym = np.array([r["y"] for r in lined])
        lm = np.array([logit(r["m"]) for r in lined])
        if sport == "nhl":
            Xb = np.array([[r["f"]["b2b"], r["f"]["rest"]] for r in lined])
            model.nhl = [float(v) for v in newton(Xb, ym, np.zeros(2), 8.0, offset=lm)]
        else:
            own = np.array([logit(model.own(r["f"])) for r in lined])
            hd = np.array([float(r["m"] < 0.5 and not r["g"].neutral) for r in lined])
            Xb = np.column_stack([own - lm, [r["move"] for r in lined], lm, hd])
            b = newton(Xb, ym, np.array([0.15, 0, 0, 0.0]), 8.0, offset=lm)
            b[0] = min(max(b[0], 0.0), 1.0)
            model.blend = [float(v) for v in b]
        # Calibration: sides the posted read gives 55%+ at -150 or longer.
        said, won = [], []
        for r in lined:
            g = r["g"]
            fin = model.final(model.own(r["f"]), r["m"], 0.0, r["m"] < 0.5 and not g.neutral, r["f"])
            for p, price, y_ in ((fin, g.ml_h, r["y"]), (1 - fin, g.ml_a, 1 - r["y"])):
                if p >= 0.55 and price is not None and price >= -150:
                    said.append(p)
                    won.append(y_)
        if len(said) >= 200:
            model.bias = float(min(max(np.mean(won) - np.mean(said), -0.06), 0.02))
    return model


def fit_all(root: Path | str = "data", sports=("nfl", "ncaaf", "nba", "mlb", "nhl", "ncaab")) -> dict[str, SportModel]:
    return {s: fit(s, root=root) for s in sports}


def save(models: dict[str, SportModel], path: Path | str) -> None:
    Path(path).write_text(json.dumps({k: asdict(v) for k, v in models.items()}, indent=1))


def load_models(path: Path | str) -> dict[str, SportModel]:
    try:
        raw = json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return {}
    return {k: SportModel(**v) for k, v in raw.items()}


__all__ = ["SportModel", "fit", "fit_all", "save", "load_models", "no_vig", "dec", "newton"]
