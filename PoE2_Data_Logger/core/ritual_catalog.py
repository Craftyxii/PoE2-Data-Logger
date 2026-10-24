"""Bundled Omen names used to initialize the editable Ritual name database.

Initialization filters retired defaults; this tuple is seed input rather than
the current display catalog or a record of previously logged rewards. Names a
Ritual altar never offers come from the sourced ``ritual_item_assets`` catalog.
"""

import json
from pathlib import Path

OMEN_NAMES = (
    "Omen of Refreshment",
    "Omen of Resurgence",
    "Omen of Amelioration",
    "Omen of Whittling",
    "Omen of Sinistral Erasure",
    "Omen of Dextral Erasure",
    "Omen of Sinistral Alchemy",
    "Omen of Dextral Alchemy",
    "Omen of Sinistral Coronation",
    "Omen of Dextral Coronation",
    "Omen of Corruption",
    "Omen of Greater Exaltation",
    "Omen of Sinistral Exaltation",
    "Omen of Dextral Exaltation",
    "Omen of Greater Annulment",
    "Omen of Sinistral Annulment",
    "Omen of Dextral Annulment",
    "Omen of Answered Prayers",
    "Omen of Secret Compartments",
    "Omen of the Hunt",
    "Omen of Reinforcements",
    "Omen of Homogenising Exaltation",
    "Omen of Homogenising Coronation",
    "Omen of Gambling",
    "Omen of Bartering",
    "Omen of Recombination",
    "Omen of the Blessed",
    "Omen of Chaotic Rarity",
    "Omen of Chaotic Quantity",
    "Omen of Chaotic Monsters",
    "Omen of Chaotic Effectiveness",
    "Omen of Chance",
    "Omen of the Ancients",
    "Omen of Sanctification",
    "Omen of Dextral Crystallisation",
    "Omen of Sinistral Crystallisation",
    "Omen of Catalysing Exaltation",
    "Omen of Abyssal Echoes",
    "Omen of the Sovereign",
    "Omen of the Liege",
    "Omen of the Blackblooded",
    "Omen of Putrefaction",
    "Omen of Light",
    "Omen of Sinistral Necromancy",
    "Omen of Dextral Necromancy",
)

CATALOG = Path(__file__).resolve().parents[1] / "ritual_item_assets" / "catalog.json"
# Reported by players and not yet in the sourced catalog: Origin Spark and
# Delirium's Distilled Emotions, every Liquid at every tier.
_EMOTIONS = {"Diluted ": ("Ire", "Guilt", "Greed"), "": ("Paranoia", "Envy", "Disgust", "Despair"),
             "Concentrated ": ("Fear", "Suffering", "Isolation"),
             "Potent ": ("Contempt", "Ferocity", "Melancholy")}
PLAYER_CONFIRMED_EXCLUSIONS = frozenset({"Origin Spark"} | {
    f"{ancient}{tier}Liquid {emotion}" for tier, emotions in _EMOTIONS.items()
    for emotion in emotions for ancient in ("", "Ancient ")})


def _not_ritual_rewards():
    """Read the sourced exclusions; a Foretold Bounty name always stays possible."""
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    possible = {row["name"] for row in catalog["guaranteed_rewards"]}
    return frozenset({row["name"] for row in catalog["exclusions"]} - possible) | PLAYER_CONFIRMED_EXCLUSIONS


# Rewards a Ritual altar never offers. Their artwork can still be the closest
# match for another reward, so a Ritual scan holds such a match for review
# instead of naming it, and a reviewed Ritual row cannot use the name. Every
# other name keeps its existing recognition.
NOT_RITUAL_REWARDS = _not_ritual_rewards()
