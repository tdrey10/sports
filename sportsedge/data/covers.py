"""Free college football injury report from Covers.com (the page every team's injuries are on).

ESPN publishes almost no college injuries (a handful of teams, some entries years old), so
college football uses Covers' public injury page instead: every FBS team, each player with
position and status ("Out - Knee", "Questionable - Undisclosed"). Teams listed with "No
injuries" count as known and healthy; a team missing from the page stays UNKNOWN.

Covers' robots.txt allows this page. It is read at most once every CACHE_SECONDS.
"""

from __future__ import annotations

import html as html_lib
import logging
import re

import requests

log = logging.getLogger(__name__)

URL = "https://www.covers.com/sport/football/ncaaf/injuries"
CACHE_SECONDS = 2 * 3600
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; sportsedge personal picks page)"}

# Covers' name -> ESPN's name, where they spell a school differently.
ESPN_NAMES = {
    "UMass Minutemen": "Massachusetts Minutemen",
    "North Carolina State Wolfpack": "NC State Wolfpack",
    "Mississippi Rebels": "Ole Miss Rebels",
    "Appalachian State Mountaineers": "App State Mountaineers",
    "San Jose State Spartans": "San José State Spartans",
}

_TEAM = re.compile(r"covers-CoversMatchups-teamName")
_NAME = re.compile(r">\s*([^<>]+?)\s*<br\s*/?>\s*<span>\s*([^<>]+?)\s*</span>", re.S)
_ROW = re.compile(
    r"<span class=['\"]player-link['\"]>\s*(.*?)\s*</span>\s*</td>\s*<td>\s*(.*?)\s*</td>\s*<td>\s*<b>\s*(.*?)\s*</b>",
    re.S,
)


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", html_lib.unescape(re.sub(r"<[^>]+>", " ", text))).strip()


def parse(page: str) -> dict[str, list[tuple[str, str, str]]]:
    """team full name ("Michigan State Spartans") -> [(player, status, position)].

    Status is the part before " - " ("Out - Knee" -> "Out"). Teams with no injuries map to []."""
    out: dict[str, list[tuple[str, str, str]]] = {}
    starts = [m.start() for m in _TEAM.finditer(page)]
    for i, s in enumerate(starts):
        block = page[s: starts[i + 1] if i + 1 < len(starts) else len(page)]
        m = _NAME.search(block)
        if not m:
            continue
        team = f"{_clean(m.group(1))} {_clean(m.group(2))}"
        team = ESPN_NAMES.get(team, team)
        rows = []
        for player, pos, status in _ROW.findall(block):
            rows.append((_clean(player), _clean(status).split(" - ")[0].strip(), _clean(pos)))
        out[team] = rows
    return out


def college_injuries(cache=None, session: requests.Session | None = None) -> dict[str, list[tuple[str, str, str]]] | None:
    """The parsed report, or None if Covers can't be read (injuries then stay unknown)."""
    key = "covers:ncaaf:injuries"
    if cache is not None:
        hit = cache.get(key, CACHE_SECONDS)
        if hit:
            return {t: [tuple(r) for r in rows] for t, rows in hit.items()}
    try:
        resp = (session or requests).get(URL, headers=HEADERS, timeout=30)
        resp.raise_for_status()
    except requests.RequestException as exc:
        log.warning("Covers college injuries unavailable: %s", exc)
        return None
    report = parse(resp.text)
    if len(report) < 50:  # the page changed or came back partial: don't trust a thin report
        log.warning("Covers college injuries: only %d teams parsed; treating as unknown", len(report))
        return None
    if cache is not None:
        cache.set(key, report)
    return report
