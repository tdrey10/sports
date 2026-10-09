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
    ufc: dict | None = None,
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
        "ufc": ufc or {},
        "parlays": [v for v in ledger.parlay_views() if v["created_at"] >= cutoff.isoformat()],
    }


APP_HEAD = (
    '<meta name="apple-mobile-web-app-capable" content="yes">\n'
    '<meta name="mobile-web-app-capable" content="yes">\n'
    '<meta name="apple-mobile-web-app-title" content="Hambino">\n'
    '<meta name="apple-mobile-web-app-status-bar-style" content="black">\n'
    '<meta name="theme-color" content="#0b0b0c">\n'
    '<link rel="apple-touch-icon" href="apple-touch-icon.png">\n'
    '<link rel="icon" type="image/png" sizes="192x192" href="icon-192.png">\n'
    '<link rel="manifest" href="manifest.webmanifest">\n'
)
APP_MANIFEST = {
    "name": "The Great Hambino",
    "short_name": "Hambino",
    "start_url": "./",
    "scope": "./",
    "display": "standalone",
    "background_color": "#0b0b0c",
    "theme_color": "#0b0b0c",
    "icons": [
        {"src": "icon-192.png", "sizes": "192x192", "type": "image/png"},
        {"src": "icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any maskable"},
    ],
}


def write_app(out_dir: str | Path, data: dict) -> Path:
    """The page as its own website, ready to save to a phone's Home Screen: the duck as the icon,
    "Hambino" as the name, opening full screen like an app."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "index.html").write_text(render_html(data, standalone=True, app=True))
    (out / "data.json").write_text(json.dumps(data, default=str))  # an open page re-reads this every minute
    app = resources.files("sportsedge").joinpath("web", "app")
    for name in ("apple-touch-icon.png", "icon-192.png", "icon-512.png"):
        (out / name).write_bytes(app.joinpath(name).read_bytes())
    (out / "favicon.ico").write_bytes(app.joinpath("icon-192.png").read_bytes())
    (out / "manifest.webmanifest").write_text(json.dumps(APP_MANIFEST, indent=2))
    # Always fetch the newest picks: the page changes every 30 minutes.
    (out / "_headers").write_text("/\n  Cache-Control: no-cache\n/index.html\n  Cache-Control: no-cache\n"
                                  "/data.json\n  Cache-Control: no-cache\n")
    return out / "index.html"


def render_html(data: dict, standalone: bool = True, app: bool = False) -> str:
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
        + (APP_HEAD if app else "") + head + "</head>\n<body>\n<main" + rest + "\n</body>\n</html>\n"
    )


def write_site(out_dir: str | Path, data: dict) -> Path:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "data.json").write_text(json.dumps(data, default=str))
    (out / "index.html").write_text(render_html(data))
    return out / "index.html"
