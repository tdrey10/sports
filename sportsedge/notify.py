"""Push live picks to your phone as comments on a private GitHub issue.

The GitHub app sends a push notification for each new comment on an issue you
are subscribed to. Uses the workflow's GITHUB_TOKEN; nothing leaves GitHub.
"""

from __future__ import annotations

import logging
import os

import requests

log = logging.getLogger(__name__)

API = "https://api.github.com"
ISSUE_TITLE = "Funky Buttlovin live bets"


class GitHubIssueNotifier:
    def __init__(self, repo: str | None = None, token: str | None = None, session: requests.Session | None = None):
        self.repo = repo or os.environ.get("GITHUB_REPOSITORY")
        self.token = token or os.environ.get("GITHUB_TOKEN")
        if not self.repo or not self.token:
            raise RuntimeError("GITHUB_REPOSITORY and GITHUB_TOKEN are needed to post live bets")
        self.session = session or requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/vnd.github+json",
        })
        self._issue: int | None = None

    def issue_number(self) -> int:
        if self._issue is None:
            resp = self.session.get(f"{API}/repos/{self.repo}/issues", params={"state": "open", "per_page": 100})
            resp.raise_for_status()
            for issue in resp.json():
                if issue.get("title") == ISSUE_TITLE and "pull_request" not in issue:
                    self._issue = issue["number"]
                    break
            else:
                resp = self.session.post(f"{API}/repos/{self.repo}/issues", json={
                    "title": ISSUE_TITLE,
                    "body": "New live bets from Funky Buttlovin are posted here as comments, so the GitHub app "
                            "notifies your phone. Keep this issue open.",
                })
                resp.raise_for_status()
                self._issue = resp.json()["number"]
        return self._issue

    def post(self, text: str) -> None:
        try:
            resp = self.session.post(
                f"{API}/repos/{self.repo}/issues/{self.issue_number()}/comments", json={"body": text}
            )
            resp.raise_for_status()
        except requests.RequestException as exc:
            log.warning("could not post live bet: %s", exc)


def format_live_card(card) -> str:
    p = card.pick
    lines = [
        f"### 🔴 {p.selection.replace(' (live)', '')} {p.american:+.0f}",
        f"**{card.sport_name}** · {p.matchup} · **{card.units:g} unit{'s' if card.units != 1 else ''}** · "
        f"+{p.ev * 100:.1f}% EV · best price at {p.book}",
        "",
        card.headline,
    ]
    lines += [f"- {r}" for r in card.reasons]
    return "\n".join(lines)
