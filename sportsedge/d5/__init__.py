"""The D503 engine, rebuilt from its published spec (owner, Oct 24: 'Model this. This is the formula').

data      - 10 seasons of games per sport with opening and closing moneylines
elo       - Elo ratings with a margin multiplier, off-season regression and tuned K / home edge
features  - the home-minus-away inputs (elo, form, rest, b2b, revenge, letdown, bye, short, intl,
            alt, cold, weather, travel, inj, key)
model     - the "own read" logistic model and the market blend (trust, line move, cal, home dog)
board     - the daily board: Lock / Dog / value / gap plays / leans and their unit sizes
"""
