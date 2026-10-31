"""Build the dashboard: a single self-contained HTML page plus data.json."""

from __future__ import annotations

import base64
import json
from datetime import datetime, timedelta
from importlib import resources
from pathlib import Path

from .brain import BrainReport
from .spots import SPOTS
from .ledger import ENGINE_MARK, OLD_ENGINE_MARK, Ledger
from .sharp import BOOK_NAMES
from .sports import SPORTS, SportConfig

KEEP_DAYS = 30  # settled picks shown in "latest results" (stats always use the full ledger)


def _whole(f):
    from .forecasts import whole_score

    return whole_score(f)


def _forecast_record(ledger):
    from .forecasts import record

    return record(getattr(ledger, "forecasts", {}))


def _teaser_record(ledger):
    from .teasers import record

    return record(getattr(ledger, "teasers", []))


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
    rival: list[dict] | None = None,
    rival_reported: dict | None = None,
    nba: dict | None = None,
    reports: dict | None = None,
    rivalry: dict | None = None,
) -> dict:
    cutoff = now - timedelta(days=KEEP_DAYS)
    entries = []
    for e in ledger.entries.values():
        if e.settled and e.start < cutoff:
            continue
        d = e.to_dict()
        d["settled"] = e.settled
        entries.append(d)
    entries = [_scrub(d) for d in entries]
    for d in entries:  # college picks posted before the fix said "goals" (insights.UNIT had no college keys)
        if d.get("sport") in ("ncaaf", "ncaab"):
            d["reasons"] = [r.replace(" goals a game", " points a game") for r in d.get("reasons") or []]
    in_ledger = {e["sport"] for e in entries}
    shown = [c for c in sports if c.key in in_ledger] or sports
    return {
        "generated_at": now.isoformat(),
        "tz": ledger.tz.key,
        "demo": demo,
        "live_note": live_note,
        "sports": [{"key": c.key, "name": c.name} for c in shown],
        # ESPN scoreboard paths, so the page can pull live scores itself between engine runs.
        "espn": {k: {"path": c.espn_path, "groups": c.espn_groups} for k, c in SPORTS.items()},
        "entries": entries,
        # A prediction for every game (not picks), and how the predictions have done.
        "forecasts": [{**f.to_dict(), "score": list(_whole(f))} for f in getattr(ledger, "forecasts", {}).values()
                      if f.status != "void" and (f.status != "final" or now - f.start < timedelta(days=2))],
        "forecast_record": _forecast_record(ledger),
        # The daily NFL 6-point teasers (proven leg kind only) and their record.
        "teasers": [t.to_dict() for t in getattr(ledger, "teasers", []) if now - datetime.fromisoformat(t.created_at) < timedelta(days=KEEP_DAYS)],
        "teaser_record": _teaser_record(ledger),
        "stats": ledger.stats(now),
        "brains": [b.to_dict() for b in brains.values()],
        "weights": weights,
        "studies": [s.to_dict() for s in (studies or {}).values()],
        "spot_labels": {k: s.label for k, s in SPOTS.items()},
        "props": props or {},
        "ufc": ufc or {},
        "clv_trust": ledger.clv_trust(now),
        "parlays": [v for v in ledger.parlay_views() if v["created_at"] >= cutoff.isoformat()],
        # Owner, Oct 28: no head to head and no "D503" anywhere on the page.
        "rival": None,
        # The NBA player report (nbaplayers.py): research, no units.
        "nba": nba,
        # The football reports (fbplayers.py): {"nfl": ..., "ncaaf": ...}. Research, no units.
        "reports": reports or {},
        # Owner, Oct 29: his picks vs our leans (rivalry.py).
        "rivalry": rivalry,
    }


def _since_2018(bb, br, tb, tr) -> str:
    bb, br, tb, tr = int(bb), float(br), int(tb), float(tr)
    n = bb + tb
    roi = (br * bb + tr * tb) / n
    return (f"The proof: from 2018 to 2026 this spot came up {n} times and {_money(f'{roi}')}. And on the {bb} games from "
            f"2022 on, which we never used to build it, it {_money(f'{br}')}, so it's not luck.")


def _money(pct: str) -> str:
    v = float(pct)
    return f"{'made' if v >= 0 else 'lost'} about ${abs(v):.0f} for every $100 bet"


# Owner, Oct 29: "too much jargon... needs to be in layman's terms". Picks posted before the plain-talk
# change carry the old wording; it's translated when the page is built.
PLAIN = [
    (r"✅ Proven spot · ([\d.]+)u: ", r"✅ Money play · \1u: "),
    (r"✅ Proven edge · ([\d.]+)u: ", r"✅ Money play · \1u: "),
    (r"Blind test \((?:PROVEN|STRONG)\): (\d+) bets on seasons it never saw, ([+-][\d.]+)%; tuning seasons (\d+) bets, ([+-][\d.]+)%\.",
     lambda m: _since_2018(m[1], m[2], m[3], m[4])),
    (r"The proof: we checked this exact spot on (\d+) past games the system had never seen, and it (made|lost) about \$(\d+) for every \$100 bet\.",
     lambda m: f"The proof: on {m[1]} games from 2022 on, which we never used to build it, this spot {m[2]} about ${m[3]} for every $100 bet."),
    (r"Only bets that beat the books in a blind test get money\. This one did\.",
     "We only put money on spots that already made money in the past. This one did."),
    (r"(?:PROVEN|STRONG) in the blind test \(([+-][\d.]+)% on (\d+) bets\)\.",
     lambda m: f"This spot {_money(m[1])} on {m[2]} games since 2022."),
    (r"💸 Best price: (.+?) ([+-]\d+) \(the board had (\S+) ([+-]\d+)\)\. Checked (\d+) books\. Bet it there\.",
     lambda m: f"💸 Best price: {m[1]} {m[2]} ({BOOK_NAMES.get(m[3], m[3])} only had {m[4]}). We checked {m[5]} sportsbooks, so bet it at {m[1]}."),
    (r"💸 Checked (\d+) books: (\S+) (.+?) is the best price\.",
     lambda m: f"💸 We checked {m[1]} sportsbooks: {BOOK_NAMES.get(m[2], m[2])} {m[3]} is the best price out there."),
    (r"Our read (\d+)% · own read \d+% · market \(no vig\) (\d+)%.*$",
     r"We give them a \1% chance to win. The betting odds give them \2%."),
    (r"Lean (.+?): (\d+)% on our numbers \(needs (\d+)% at ([+-]\d+)\)\.",
     lambda m: f"Small lean on {m[1]}: we think they win {m[2]}% of the time. At {m[4]} you need {m[3]}% to make money"
               + (", so it's close." if int(m[2]) >= int(m[3]) else ", so it's not worth a bet.")),
    (r"we got em (\d+)%, the price needs (\d+)%\.", r"we think they win \1% of the time, and this price only needs \2% to pay off."),
    (r"Line since the open: money came in \([\d.]+ pts\)\.", "Since the odds opened, the money has been coming in on them."),
    (r"Line since the open: money ran away \([\d.]+ pts\)\.", "Since the odds opened, the money has been going against them."),
    (r"No units: this kind of bet hasn't beaten the books in a blind test yet \(owner's rule: only proven bets get money\)\.",
     "No money on this one: this kind of bet hasn't proven it makes money yet. We only bet spots that already have."),
    (r"beat the close (\d+)% blind [\d-]+ \((\d+) bets, [^)]*\)", r"the odds moved our way \1% of the time (\2 past picks)"),
    (r"blind 2022-26, 107 bets, \+14\.7% \(PROVEN\)", "made $15 for every $100 over 107 past picks"),
    (r"(.+?): (?:back|fade): (.+?)\. Blind (\d+) bets ([+-][\d.]+)% \(standard error [\d.]+%\); tuning (\d+) bets [+-][\d.]+%\.",
     lambda m: f"The proof: we checked this exact spot on {m[3]} past games the system had never seen, and it {_money(m[4])}."),
    (r"Beats the closing line (\d+)% of the time \(every dog at the open: (\d+)%\);.*",
     r"After we bet, the odds moved our way \1% of the time (a random early dog: \2%). The wins and losses alone don't prove it yet."),
    (r"Early (NFL|college football) dogs at the opening line, 2023-26: (.+?), beat the close (\d+)% \(all dogs \d+%\)\.",
     r"Early \1 dogs, 2023-26: \2; the odds moved our way \3% of the time."),
    (r"the blind test's return, plus or minus one standard error", "based on how this spot did in past games"),
    (r"estimate: the model's read plus or minus \d+ points \(not validated\)", "our numbers' best guess, give or take (not proven)"),
    (r"Not validated: the model's raw edges lost money in our blind tests\.", "Not proven: our numbers alone have lost money in past tests."),
    (r"Small sample: (\d+) blind bets\.", r"Not a huge sample yet: \1 past bets."),
    (r"The win/loss record is inside its standard error: it could still be luck\.", "The win-loss record could still be luck."),
    (r"A player is questionable: if the news turns, the price will move\.", "A player is questionable. If he sits or plays, the odds will move."),
    (r"Sensitive: at the low end of the range there's no edge\.", "Thin: if we're a little off, there's no edge."),
    (r"Lines move: bet it only at the minimum price or better\.", "Odds move. Only bet it at the minimum price or better."),
    (r"None: no blind test behind this kind of bet\.", "None: this kind of bet hasn't been proven."),
    (r"A STRONG spot: it made money on the seasons used to build it AND on seasons it never saw, and it stays \+EV at the low end of its tested range\.",
     "Our best kind of bet: this spot made money on old games AND on games the system never saw, and the price is still good even if we're a little off."),
    (r"A STRONG spot, but at this price the edge is thin at the low end of its tested range\.", "A strong spot, but at this price there's not much room for error."),
    (r"A PROVEN spot: it beat the books on seasons it never saw, by more than random swing\.",
     "A proven spot: it beat the books on games the system never saw, by more than luck."),
    (r"The model sees an edge, but this kind of bet has never passed a blind test\. A lean: no units\.",
     "Our numbers like it, but this kind of bet hasn't proven it makes money. Just a lean, no money."),
    (r"No positive edge at this price\.", "Not worth it at this price."),
    # Owner, Oct 29: "the wording for everything on the page... like you're talking to one of the homies".
    (r"^Lean · no units$", "Lean · no money on it"),
    (r"^Our projected score: ", "How we see it ending: "),
    (r"^Projected score: ", "How we see it ending: "),
    (r"^Season (goals|points|runs) per game: (.+?) ([\d.]+) scored / ([\d.]+) allowed; (.+?) ([\d.]+) scored / ([\d.]+) allowed\.$",
     lambda m: f"This season: {m[2]} puts up {m[3]} {m[1]} a game and gives up {m[4]}; {m[5]} puts up {m[6]} and gives up {m[7]}."),
    (r"^Why: the stronger team", "Why: they're the better team"),
    (r"Model (\d+)% · market \(no-vig\) (\d+)% · blended (\d+)% → fair ([+-]\d+)\.",
     r"We give them about \3% to win, so a fair price would be \4."),
    (r"^Best number: ([+-]\d+) at (.+?) \(checked \d+ books?\)\.", r"Best price: \1 at \2."),
    (r"^No injury report is published for this league, so this one rides on form, scoring, head to head and the number\.",
     "This league doesn't put out injury reports, so this one's based on form, scoring, head to head and the price."),
    (r"^Dog score [+-][\d.]+: ", "Underdog boost: "),
    (r"^Pinnacle \(the sharpest book\) has (.+?) at ([+-]\d+); without its margin that's ([\d.]+)% to win\.",
     lambda m: f"The sharpest sportsbook has {m[1]} at {m[2]}, which means about a {float(m[3]):.0f}% shot."),
    (r"Today's best Lock: the strongest side on the board \((\d+)% on our numbers, [+-][\d.]+% edge\), -170 or better, injuries known\.",
     r"Today's Lock: our strongest side today (we give them \1% to win)."),
    (r"(.+?) pays ([+-]\d+): \+?([\d.]+)% expected value per bet\.",
     lambda m: f"{m[1]} pays {m[2]}: that's about ${float(m[3]):.0f} profit for every $100 over time."),
    (r"(.+?) pays ([+-]\d+)( on .+?)?; Pinnacle's fair price is ([+-]\d+) \((\d+)%\)\. That's a ([\d.]+)% edge\.",
     lambda m: f"{m[1]} pays {m[2]}{m[3] or ''}, but the sharpest sportsbook says it should be {m[4]}. That gap is in your favor."),
    (r"^No units yet: the 2012\+ test of one big book.*$",
     "No money yet: tennis picks have to win on their own first (60+ picks) before we put money on them."),
    (r"^Withdrawn before the game: only bets backed by the data are posted\.", "Pulled before the game: we only post bets the numbers back up."),
    (r"^Withdrawn before the game: ", "Pulled before the game: "),
    (r" \(score [+-][\d.]+\)", ""),
    (r"\(avg margin ([+-][\d.]+)\)", lambda m: f"({'winning' if float(m[1]) >= 0 else 'losing'} by {abs(float(m[1])):.1f} a game)"),
    (r"^Splits: ", "Home and road: "),
    (r" missing: ", " is missing "),
    (r"we got em winning (\d+)%, the price pays like (\d+)%", r"we think they win \1%, but the price acts like it's only \2%"),
    (r"(\d+)% on our numbers", r"\1% by our numbers"),
    (r"([+-][\d.]+)% edge", "an edge"),
    # Catch-alls for older wording anywhere on the page.
    (r"No units: ", "No money on this: "),
    (r"\(tracked; not proven blind yet, so it adds nothing to the read\)", "(we're watching this one, not proven yet)"),
    (r"\(proven blind: weighted into the read\)", "(proven, counts toward the pick)"),
    (r"bet before the close", "bet early"),
    (r"hasn't beaten the closing line in a blind test yet", "hasn't proven it makes money yet"),
    (r" in a blind test", ""),
    (r"[Bb]lind test", "test on past games"),
    (r"blind bets", "past bets"),
    (r"the tuning seasons", "the older seasons"),
    (r"beat(s?) the close\b", r"beat\1 the final odds"),
    (r"the closing line", "the final odds before the game"),
    (r"Pinnacle's fair line", "the sharpest sportsbook's fair price"),
    (r"by more than luck over", "by more than luck, over"),
]


def _plain(text: str) -> str:
    import re

    for pat, rep in PLAIN:
        text = re.sub(pat, rep, text)
    return text


def _scrub(obj):
    """Owner, Oct 28: "I don't want it to say D503 formula anywhere on the page" (picks posted before
    the rename still carry the old wording), and Oct 29: plain talk, no jargon."""
    if isinstance(obj, str):
        out = _plain(obj.replace(OLD_ENGINE_MARK, ENGINE_MARK).replace("D503's ", "").replace("D503 ", ""))
        return out[:1].upper() + out[1:] if out != obj else out
    if isinstance(obj, list):  # the engine line itself isn't shown at all (owner: "I don't want to see it")
        return [_scrub(x) for x in obj if x not in (ENGINE_MARK, OLD_ENGINE_MARK)]
    if isinstance(obj, dict):
        return {k: _scrub(v) for k, v in obj.items()}
    return obj


def _rival(picks, ledger, now, reported=None):
    if not picks:
        return None
    from .rival import head_to_head

    today = now.astimezone(ledger.tz).date().isoformat()
    return head_to_head(picks, [e.to_dict() for e in ledger.entries.values()], today, reported)


APP_HEAD = (
    '<meta name="apple-mobile-web-app-capable" content="yes">\n'
    '<meta name="mobile-web-app-capable" content="yes">\n'
    '<meta name="apple-mobile-web-app-title" content="Script Leaks">\n'
    '<meta name="apple-mobile-web-app-status-bar-style" content="black">\n'
    '<meta name="theme-color" content="#0b0b0c">\n'
    '<link rel="apple-touch-icon" href="apple-touch-icon.png">\n'
    '<link rel="icon" type="image/png" sizes="192x192" href="icon-192.png">\n'
    '<link rel="manifest" href="manifest.webmanifest">\n'
)
APP_MANIFEST = {
    "name": "Script Leaks",
    "short_name": "Script Leaks",
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
    """The page as its own website, ready to save to a phone's Home Screen: the Script Leaks icon,
    "Script Leaks" as the name, opening full screen like an app."""
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
    if thumb.is_file():  # the Script Leaks cover (Lillard's shot): the wide-screen cover, which is what the gallery thumbnail shows
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
