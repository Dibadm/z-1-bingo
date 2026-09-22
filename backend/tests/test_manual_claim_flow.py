# test_manual_claim_flow.py
# End-to-end check of the manual BINGO claim path (API + DB layer).
# Run with: python -m pytest test_manual_claim_flow.py
# Requires DATABASE_URL set to a real Postgres instance.
import os
import sys
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from backend import database as db
from backend import config
from backend import bingo


def _require_db():
    db_url = getattr(config, "DATABASE_URL", "") or config.DB_PATH
    if not db_url or db_url == "habesha_bet.db":
        pytest.skip("No DATABASE_URL or test DB configured")


@pytest.fixture(scope="module")
def setup_game():
    _require_db()
    db.init_db()
    uid = 9990001
    db.get_or_create_user(uid, "testuser")
    db.adjust_balance(uid, 1000.0)
    game = db.get_or_create_active_game(10)
    game_id = game["id"]

    # Buy 2 cards for the test user.
    db.purchase_cards(game_id, uid, [0, 1], 10.0)
    db.set_game_state(game_id, "running")

    # Call a few numbers and mark a winning line on card 0.
    for n in (5, 12, 23, 44, 67):
        db.add_called_number(game_id, n, n)
    # Mark the first row of card 0 (B column) — 5 numbers.
    grid = bingo.get_card(0)
    row_numbers = [grid[0][r] for r in range(5) if grid[0][r] != 0]
    db.update_marked_numbers(game_id, 0, sorted(row_numbers))

    yield game_id, uid

    # Cleanup
    conn = db.get_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM manual_bingo_claims WHERE game_id = %s", (game_id,))
    cur.execute("DELETE FROM game_cards WHERE game_id = %s", (game_id,))
    cur.execute("DELETE FROM game_players WHERE game_id = %s", (game_id,))
    cur.execute("DELETE FROM called_numbers WHERE game_id = %s", (game_id,))
    cur.execute("DELETE FROM transactions WHERE user_id = %s", (uid,))
    cur.execute("UPDATE users SET balance = 0 WHERE user_id = %s", (uid,))
    cur.execute("DELETE FROM games WHERE id = %s", (game_id,))
    conn.commit()
    db.release_connection(conn)


def test_claim_finishes_round_instantly(setup_game):
    game_id, uid = setup_game

    marked_by_card = db.get_all_marked_numbers(game_id)
    called_set = set(db.get_called_numbers(game_id))

    winners_found = {}
    claimed = db.try_finish_manual_claim(
        game_id, uid, marked_by_card, called_set, winners_found
    )

    assert claimed is True, "try_finish_manual_claim should have claimed the round"
    assert uid in winners_found
    assert len(winners_found[uid]) > 0

    # Round must be finished and the winner credited.
    game = db.get_game(game_id)
    assert game["state"] == "finished"
    assert float(db.get_balance(uid)) >= 16.0  # 80% of 20 ETB pool

    # A second claim must NOT double-pay.
    winners2 = {}
    claimed2 = db.try_finish_manual_claim(
        game_id, uid, marked_by_card, called_set, winners2
    )
    assert claimed2 is False, "second claim must be rejected (already resolved)"
    assert float(db.get_balance(uid)) == 16.0, "balance must not change on the second claim"