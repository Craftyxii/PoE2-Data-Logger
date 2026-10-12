"""Copy the editable family/recipe references into Excel's existing read snapshot.

Reference rows explain IDs and rune combinations; they are not gameplay events.
Keep membership order, shared recipe identities and retired IDs without changing
the catalog or assigning new provenance during an export.
"""

import csv
import io
import json


SHEET_NAMES = ("Family Database", "Recipe Database", "Seed Database", "Reference Aliases")
FAMILY_HEADERS = ("Family ID", "Top Socket Count", "Family Valid", "Recipe # In Family",
                  "Recipe ID", "Recipe", "Socket Count", "Exact Rune Combo", "Reference Status")
RECIPE_HEADERS = ("Recipe ID", "Recipe", "Socket Count", "Exact Rune Combo", "Level Band",
                  "Category", "Reference Source", "Reference Status")
SEED_HEADERS = ("Family ID", "Socket Count", "Visible Seed Slot", "Visible Seed Rune",
                "Rewards (JSON)", "Mapping Status")
ALIAS_HEADERS = ("Alias Position", "Alias", "Matched Recipe", "Recipe ID", "Reference Status")


def _csv(headers, rows):
    """Preserve catalog text literally; the worksheet writer emits text, never formulas."""
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(headers)
    writer.writerows(rows)
    return output.getvalue().encode("utf-8-sig")


def export_sheets(db):
    """Read all reference sheets on the caller's transaction without writes or another connection."""
    recipes = {row['name']: dict(row) for row in db.execute("SELECT * FROM recipes ORDER BY name")}
    identifiers = {row['recipe_name']: row['recipe_id']
                   for row in db.execute("SELECT recipe_name,recipe_id FROM recipe_ids")}
    family_rows = []
    for family in db.execute("SELECT id,top_socket,valid,recipes_json FROM families ORDER BY id"):
        names = json.loads(family['recipes_json'])
        if not names:
            family_rows.append((family['id'], family['top_socket'], 'Yes' if family['valid'] else 'No',
                                '', '', '', '', '', 'Empty family'))
        for ordinal, name in enumerate(names, 1):
            recipe = recipes.get(name, {})
            family_rows.append((family['id'], family['top_socket'], 'Yes' if family['valid'] else 'No',
                ordinal, identifiers.get(name, ''), name, recipe.get('sockets', ''), recipe.get('combo', ''),
                'Current catalog member' if recipe else 'Recipe missing from current catalog'))
    recipe_rows = []
    for name in sorted(set(recipes) | set(identifiers)):
        recipe = recipes.get(name, {})
        status = ('Current catalog recipe' if name in identifiers else 'Missing recipe ID') if recipe else 'Retired recipe ID'
        recipe_rows.append((identifiers.get(name, ''), name, recipe.get('sockets', ''), recipe.get('combo', ''),
                            recipe.get('level_band', ''), recipe.get('category', ''), recipe.get('source', ''), status))
    seed_rows = [tuple(row) for row in db.execute(
        "SELECT family,sockets,seed_slot,seed_rune,rewards_json,status FROM seed_states ORDER BY family,sockets")]
    alias_rows = []
    for alias in db.execute("SELECT position,alias,target FROM aliases ORDER BY position"):
        name = alias['target']
        alias_rows.append((alias['position'], alias['alias'], name, identifiers.get(name, ''),
                           'Current catalog recipe' if name in recipes else 'Target missing from current catalog'))
    return list(zip(SHEET_NAMES, (_csv(FAMILY_HEADERS, family_rows), _csv(RECIPE_HEADERS, recipe_rows),
                                 _csv(SEED_HEADERS, seed_rows), _csv(ALIAS_HEADERS, alias_rows))))
