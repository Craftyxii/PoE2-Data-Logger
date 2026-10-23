"""Read persisted open-chain entry timing for the explicit same-chain question.

This helper never saves or completes a chain. The UI owns the answer and invokes
the existing completion action when the user chooses to start the next chain.
"""

from dataclasses import dataclass
from datetime import datetime, timezone

from PoE2_Data_Logger.core import logger_store as logger


@dataclass(frozen=True)
class ChainGap:
    """Identify the last accepted entry and its measured age in an open chain."""

    map_id: str
    expedition_id: str
    last_commit_number: int | None = None
    recorded_at: str | None = None
    elapsed_seconds: float | None = None
    needs_prompt: bool = False


def inspect_gap(context, now=None):
    """Read a captured chain's last accepted entry, requiring a gap over 60 seconds.

    Stale session, map, expedition and discard-revision tokens are rejected by
    the same context validator used by chain writes. Only accepted ``Chain``
    commits establish entry time; corrections, settings and detonation records
    do not reset it. An imported chain with no recorded entry time is unknown.
    ``now`` may be an aware datetime for deterministic callers and tests.
    """
    if not isinstance(context, dict):
        raise ValueError("The chain capture context is invalid. Scan again.")
    current_time = datetime.now(timezone.utc) if now is None else now
    if not isinstance(current_time, datetime) or current_time.utcoffset() is None:
        raise ValueError("Chain entry timing requires a timezone-aware time.")
    with logger._connect() as db:
        db.execute("BEGIN")
        mid, _, eid = logger._chain_context(db, context)
        empty = ChainGap(mid, eid)
        if db.execute("SELECT 1 FROM chain_completions WHERE expedition_id=?", (eid,)).fetchone():
            return empty
        if not logger._saved_chain_rows(db, eid):
            return empty
        row = db.execute("SELECT number,recorded_at FROM commits WHERE kind='Chain' "
                         "AND map_id=? AND expedition_id=? ORDER BY number DESC LIMIT 1", (mid, eid)).fetchone()
        if row is None:
            return empty
        recorded_at = datetime.fromisoformat(row["recorded_at"])
        if recorded_at.utcoffset() is None:
            raise ValueError("The saved chain entry time has no timezone.")
        elapsed = (current_time.astimezone(timezone.utc) - recorded_at.astimezone(timezone.utc)).total_seconds()
        return ChainGap(mid, eid, row["number"], row["recorded_at"], elapsed, elapsed > 60)
