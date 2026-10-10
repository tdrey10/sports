from sportsedge.data import covers
from sportsedge.data.base import NameMatcher, is_out


def _team(school, mascot, rows):
    head = f'''<div class="covers-CoversMatchups-teamName">
        <a class="covers-CoversMatchups-imgLink" href="/x">
            {school}<br><span>{mascot}</span>
        </a></div>'''
    if not rows:
        return head + "<div>No injuries</div>"
    body = "".join(f'''<tr><td>
        <span class='player-link'>
{p.split()[0]}                                        {p.split()[1]}
            </span>
        </td>
        <td>{pos}</td>
        <td><b>{status}</b><br>(
Sat, Oct 3)</td><td></td></tr>''' for p, pos, status in rows)
    return head + f'<table class="table"><thead><tr><th>Player</th></tr></thead><tbody>{body}</tbody></table>'


PAGE = (
    _team("Michigan State", "Spartans", [("N. Burt", "CB", "Out - Knee"), ("C. Edwards", "RB", "Questionable - Ankle")])
    + _team("Michigan", "Wolverines", [("B. Underwood", "QB", "Out For Season - Shoulder")])
    + _team("Purdue", "Boilermakers", [])
)


def test_parse_teams_players_status_and_position():
    report = covers.parse(PAGE)
    assert set(report) == {"Michigan State Spartans", "Michigan Wolverines", "Purdue Boilermakers"}
    assert report["Michigan State Spartans"] == [("N. Burt", "Out", "CB"), ("C. Edwards", "Questionable", "RB")]
    assert report["Purdue Boilermakers"] == []  # listed with no injuries: known and healthy
    out = [p for p, s, _ in report["Michigan Wolverines"] if is_out(s)]
    assert out == ["B. Underwood"]  # "Out For Season" counts as out


def test_full_names_match_espn_teams_without_mixing_up_michigan_and_michigan_state():
    m = NameMatcher(covers.parse(PAGE))
    assert m.match("Michigan State Spartans") == "Michigan State Spartans"
    assert m.match("Michigan Wolverines") == "Michigan Wolverines"
    assert m.match("Purdue Boilermakers") == "Purdue Boilermakers"


def test_a_thin_or_failed_page_means_unknown_not_healthy(monkeypatch):
    class Resp:
        text = PAGE  # only 3 teams: the real page lists about 140

        def raise_for_status(self):
            pass

    class Session:
        def get(self, *a, **k):
            return Resp()

    assert covers.college_injuries(session=Session()) is None


def test_schools_covers_spells_differently_get_espn_names():
    page = _team("Mississippi", "Rebels", []) + _team("North Carolina State", "Wolfpack", []) + _team("UMass", "Minutemen", [])
    assert set(covers.parse(page)) == {"Ole Miss Rebels", "NC State Wolfpack", "Massachusetts Minutemen"}
