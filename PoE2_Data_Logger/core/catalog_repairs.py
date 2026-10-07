"""Narrow repairs for verified mistakes in the original bundled references."""
from __future__ import annotations

import json


FARRUL_GRACE = "Farrul's Rune of Grace"
FARRUL_HUNT = "Farrul's Rune of the Hunt"
GENERIC_HUNT = "Rune of the Hunt"
FARRUL_SEQUENCE = [FARRUL_GRACE, FARRUL_HUNT]
_OLD_SEQUENCE = [FARRUL_GRACE, GENERIC_HUNT]
_FARRUL_RECIPES = {
    FARRUL_GRACE: (5, "Momentum + Bloodletting + Adaptive + Time + Life"),
    FARRUL_HUNT: (5, "Vision + Bloodletting + Bond + Time + Rage"),
}


def repair_farrul_hunt_family(db):
    """Repair only default Family 47 references, preserving edited data/history.

    Family 47 accidentally used the four-socket generic Rune of the Hunt in
    place of the distinct five-socket Farrul rune. Both authentic five-socket
    recipe signatures must still match before changing family or seed data.
    Calling this during initialization is idempotent and also handles a legacy
    reference pack imported since a previous launch.
    """
    family = db.execute("SELECT top_socket,valid,recipes_json FROM families WHERE id=47").fetchone()
    if not family or family["top_socket"] != 5 or family["valid"] != 1:
        return
    sequence = json.loads(family["recipes_json"])
    if sequence not in (_OLD_SEQUENCE, FARRUL_SEQUENCE):
        return
    for name, signature in _FARRUL_RECIPES.items():
        recipe = db.execute("SELECT sockets,combo FROM recipes WHERE name=?", (name,)).fetchone()
        if recipe is None or tuple(recipe) != signature:
            return
    if sequence == _OLD_SEQUENCE:
        db.execute("UPDATE families SET recipes_json=? WHERE id=47",
                   (json.dumps(FARRUL_SEQUENCE, ensure_ascii=False, separators=(",", ":")),))
    for sockets, old_rewards in ((4, [GENERIC_HUNT]), (5, _OLD_SEQUENCE)):
        seed = db.execute("SELECT seed_slot,seed_rune,rewards_json,status FROM seed_states "
                          "WHERE family=47 AND sockets=?", (sockets,)).fetchone()
        if (not seed or seed["seed_slot"] != "P2" or seed["seed_rune"] != "Unresolved" or
                seed["status"] != "calculator" or json.loads(seed["rewards_json"]) != old_rewards):
            continue
        if sockets == 4:
            db.execute("DELETE FROM seed_states WHERE family=47 AND sockets=4")
        else:
            db.execute("UPDATE seed_states SET rewards_json=? WHERE family=47 AND sockets=5",
                       (json.dumps(FARRUL_SEQUENCE, ensure_ascii=False, separators=(",", ":")),))
