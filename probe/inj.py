import requests
for path, n in [("football/nfl", 32), ("basketball/nba", 30), ("hockey/nhl", 32), ("baseball/mlb", 30)]:
    d = requests.get(f"https://site.api.espn.com/apis/site/v2/sports/{path}/injuries", timeout=30).json()
    blocks = d.get("injuries", []) or []
    empty = sum(1 for b in blocks if not b.get("injuries"))
    print(path, "teams listed", len(blocks), "of", n, "| listed with nobody hurt:", empty, "| keys:", list(d.keys())[:6])
