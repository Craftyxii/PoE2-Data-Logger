"""Read first and final saved Ritual counters without adding observed totals."""

import json


COUNTER_FIELDS = ("tribute_available", "rerolls_remaining")


def summary(db, map_id):
    """Reduce this map's approved Ritual history using the caller's read snapshot.

    Page numbers identify reward pages, so correcting an earlier page must not
    change which reading was first or final. Commit numbers preserve that order.
    Each boundary owns both of its counter fields, including absent values.
    """
    result = {"reading_count": 0,
              "first_commit_number": None, "final_commit_number": None,
              **{f"{boundary}_{field}": None
                 for boundary in ("first", "final") for field in COUNTER_FIELDS}}
    first = last = None
    for row in db.execute("SELECT number,details_json FROM commits "
                          "WHERE kind='Ritual' AND map_id=? ORDER BY number", (map_id,)):
        if first is None:
            first = row
        last = row
        result["reading_count"] += 1
    for boundary, row in (("first", first), ("final", last)):
        if row is None:
            continue
        result[f"{boundary}_commit_number"] = row[0]
        details = json.loads(row[1])
        for field in COUNTER_FIELDS:
            result[f"{boundary}_{field}"] = details.get(field)
    return result
