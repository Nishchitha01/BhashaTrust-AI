from backend import database
from backend.audit import append_audit_event, verify_audit_chain


def test_audit_chain_detects_record_tampering(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "audit.db")
    database.initialize_database()
    with database.connect() as connection:
        append_audit_event(connection, "test_event", "dataset", "1", {"status": "accepted"})
        append_audit_event(connection, "test_event", "dataset", "2", {"status": "rejected"})

    assert verify_audit_chain() == (True, 2, None)
    with database.connect() as connection:
        connection.execute("UPDATE audit_events SET payload_json = 'not-json' WHERE id = 1")
    intact, count, first_invalid = verify_audit_chain()
    assert not intact
    assert count == 2
    assert first_invalid == 1
