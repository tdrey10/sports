"""Build the dashboard: a single self-contained HTML page plus data.json."""

from __future__ import annotations

import base64
import json
from datetime import datetime, timedelta
from importlib import resources
from pathlib import Path

from .brain import BrainReport
from .spots import SPOTS
from .ledger import Ledger
from .sports import SportConfig

KEEP_DAYS = 30  # settled picks shown in "latest results" (stats always use the full ledger)


def build_data(
    ledger: Ledger,
    brains: dict[str, BrainReport],
    weights: dict[str, float],
    sports: list[SportConfig],
    now: datetime,
    demo: bool = False,
    live_note: str = "",
    studies: dict | None = None,
    props: dict | None = None,
) -> dict:
    cutoff = now - timedelta(days=KEEP_DAYS)
    entries = []
    for e in ledger.entries.values():
        if e.settled and e.start < cutoff:
            continue
        d = e.to_dict()
        d["settled"] = e.settled
        entries.append(d)
    in_ledger = {e["sport"] for e in entries}
    shown = [c for c in sports if c.key in in_ledger] or sports
    return {
        "generated_at": now.isoformat(),
        "tz": ledger.tz.key,
        "demo": demo,
        "live_note": live_note,
        "sports": [{"key": c.key, "name": c.name} for c in shown],
        "entries": entries,
        "stats": ledger.stats(now),
        "brains": [b.to_dict() for b in brains.values()],
        "weights": weights,
        "studies": [s.to_dict() for s in (studies or {}).values()],
        "spot_labels": {k: s.label for k, s in SPOTS.items()},
        "props": props or {},
    }


def render_html(data: dict, standalone: bool = True) -> str:
    web = resources.files("sportsedge").joinpath("web")
    template = web.joinpath("board.html").read_text()
    payload = json.dumps(data, default=str).replace("</", "<\\/")
    body = template.replace("__DATA__", payload)
    mascot = web.joinpath("mascot.jpg")
    if mascot.is_file():  # Dog of the Day background photo, embedded so the page stays one file
        uri = "data:image/jpeg;base64," + base64.b64encode(mascot.read_bytes()).decode()
        body = body.replace("__MASCOT__", uri)
    thumb = web.joinpath("thumb.jpg")
    if thumb.is_file():  # the duck: the wide-screen cover, which is what the gallery thumbnail shows
        uri = "data:image/jpeg;base64," + base64.b64encode(thumb.read_bytes()).decode()
        body = body.replace("__THUMB__", uri)
    if not standalone:
        return body
    head, _, rest = body.partition("<main")
    return (
        '<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">\n'
        + head + "</head>\n<body>\n<main" + rest + "\n</body>\n</html>\n"
    )


def write_site(out_dir: str | Path, data: dict) -> Path:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "data.json").write_text(json.dumps(data, default=str))
    (out / "index.html").write_text(render_html(data))
    return out / "index.html"
