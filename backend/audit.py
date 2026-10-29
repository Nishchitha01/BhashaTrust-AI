import hashlib
import hmac
import json
from datetime import datetime, timezone

from .database import connect

GENESIS_HASH = "0" * 64


def canonical_json(value: dict) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def append_audit_event(connection, event_type: str, entity_type: str, entity_id: str, payload: dict) -> int:
    previous = connection.execute("SELECT event_hash FROM audit_events ORDER BY id DESC LIMIT 1").fetchone()
    previous_hash = previous["event_hash"] if previous else GENESIS_HASH
    created_at = datetime.now(timezone.utc).isoformat()
    payload_json = canonical_json(payload)
    event = {
        "event_type": event_type,
        "entity_type": entity_type,
        "entity_id": entity_id,
        "payload": json.loads(payload_json),
        "previous_hash": previous_hash,
        "created_at": created_at,
    }
    event_hash = hashlib.sha256(canonical_json(event).encode("utf-8")).hexdigest()
    cursor = connection.execute(
        """INSERT INTO audit_events
            (event_type, entity_type, entity_id, payload_json, previous_hash, event_hash, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (event_type, entity_type, entity_id, payload_json, previous_hash, event_hash, created_at),
    )
    return cursor.lastrowid


def verify_audit_chain() -> tuple[bool, int, int | None]:
    with connect() as connection:
        events = connection.execute("SELECT * FROM audit_events ORDER BY id").fetchall()

    previous_hash = GENESIS_HASH
    for row in events:
        try:
            event = {
                "event_type": row["event_type"],
                "entity_type": row["entity_type"],
                "entity_id": row["entity_id"],
                "payload": json.loads(row["payload_json"]),
                "previous_hash": previous_hash,
                "created_at": row["created_at"],
            }
            calculated_hash = hashlib.sha256(canonical_json(event).encode("utf-8")).hexdigest()
        except (TypeError, ValueError):
            return False, len(events), row["id"]
        if not hmac.compare_digest(row["previous_hash"], previous_hash) or not hmac.compare_digest(
            row["event_hash"], calculated_hash
        ):
            return False, len(events), row["id"]
        previous_hash = row["event_hash"]

    return True, len(events), None


def list_audit_events() -> list[dict]:
    with connect() as connection:
        rows = connection.execute("SELECT * FROM audit_events ORDER BY id").fetchall()
    return [
        {
            "id": row["id"],
            "event_type": row["event_type"],
            "entity_type": row["entity_type"],
            "entity_id": row["entity_id"],
            "payload": json.loads(row["payload_json"]),
            "previous_hash": row["previous_hash"],
            "event_hash": row["event_hash"],
            "created_at": row["created_at"],
        }
        for row in rows
    ]
