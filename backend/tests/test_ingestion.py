from datetime import date, timedelta
import sqlite3

from fastapi.testclient import TestClient

from backend import database
from backend.main import app


def test_ingestion_decisions_cover_approved_rejected_review_and_invalid_requests(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "ingestion.db")
    with TestClient(app) as client:
        approved = client.post(
            "/api/decisions",
            json={"dataset_id": 1, "purpose": "research_training", "context": "research"},
        )
        assert approved.status_code == 200
        approved_result = approved.json()
        assert approved_result["decision"] == "NEEDS_REVIEW"
        assert approved_result["policy_version"] == "1.1.0"
        assert approved_result["evidence_verification_status"] == "REGISTRY_DECLARED_UNVERIFIED"
        assert "EVIDENCE_UNVERIFIED" in approved_result["reason_codes"]
        assert approved_result["audit_event_reference"] is not None

        rejected = client.post(
            "/api/decisions",
            json={"dataset_id": 1, "purpose": "commercial_training", "context": "commercial"},
        )
        assert rejected.status_code == 200
        assert rejected.json()["decision"] == "REJECTED"
        assert "PURPOSE_NOT_PERMITTED" in rejected.json()["reason_codes"]

        needs_review = client.post(
            "/api/decisions",
            json={"dataset_id": 2, "purpose": "research_training", "context": "research"},
        )
        assert needs_review.status_code == 200
        assert needs_review.json()["decision"] == "NEEDS_REVIEW"
        assert "EVIDENCE_UNVERIFIED" in needs_review.json()["reason_codes"]

        missing_dataset = client.post(
            "/api/decisions",
            json={"dataset_id": 999, "purpose": "research_training", "context": "research"},
        )
        assert missing_dataset.status_code == 404
        invalid = client.post("/api/decisions", json={"dataset_id": 1, "purpose": ""})
        assert invalid.status_code == 422

        events = client.get("/audit/events").json()
        ingestion_events = [event for event in events if event["event_type"] == "ingestion_decision"]
        assert len(ingestion_events) == 3
        assert client.get("/audit/verify").json()["intact"] is True


def test_signed_expiry_and_revocation_are_enforced_by_ingestion_endpoint(tmp_path, monkeypatch):
    import base64

    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    from backend.schemas import RightsPayload
    from backend.signatures import canonicalize_rights

    monkeypatch.setattr(database, "DB_PATH", tmp_path / "signed-ingestion.db")
    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    payload = RightsPayload(
        dataset_id=1,
        grantor="Synthetic demo grantor",
        license_name="Synthetic revoked grant",
        permitted_purposes=["research_training"],
        expires_on=date.today() - timedelta(days=1),
        revoked=True,
    )
    signature = private_key.sign(canonicalize_rights(payload))

    with TestClient(app) as client:
        signed = client.post(
            "/datasets/1/rights-records",
            json={
                "payload": payload.model_dump(mode="json"),
                "signer_id": "phase3-test-key",
                "public_key": base64.b64encode(public_key).decode(),
                "signature": base64.b64encode(signature).decode(),
            },
        )
        assert signed.status_code == 201
        result = client.post(
            "/api/decisions",
            json={"dataset_id": 1, "purpose": "research_training", "context": "research"},
        )
        assert result.status_code == 200
        assert result.json()["decision"] == "REJECTED"
        assert result.json()["rights_ledger_reference"]["record_id"] == signed.json()["id"]
        assert result.json()["audit_event_reference"] is not None
        assert "LICENSE_EXPIRED" in result.json()["reason_codes"]
        assert "LICENSE_REVOKED" in result.json()["reason_codes"]
        assert result.json()["evidence_verification_status"] == "CONFLICTING_RIGHTS_CLAIMS"
        assert result.json()["decision"] == "REJECTED"


def test_registration_cannot_assert_independent_verification_and_use_scope_terms_apply(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "rights-scope.db")
    with TestClient(app) as client:
        created = client.post(
            "/api/datasets",
            json={
                "name": "Rights scope test",
                "language": "Tamil",
                "owner": "Synthetic rights claimant",
                "license_name": "Test claim",
                "permitted_purposes": ["research_training"],
                "commercial_use": True,
                "redistribution_allowed": False,
                "derivative_work_allowed": True,
                "evidence_verified": True,
                "evidence_independently_verified": True,
                "synthetic": True,
            },
        )
        assert created.status_code == 201
        dataset_id = created.json()["id"]
        assert created.json()["evidence_independently_verified"] is False

        rejected = client.post(
            "/api/decisions",
            json={
                "dataset_id": dataset_id,
                "purpose": "research_training",
                "context": "research",
                "redistribution_requested": True,
            },
        )
        assert rejected.json()["decision"] == "REJECTED"
        assert "REDISTRIBUTION_NOT_PERMITTED" in rejected.json()["reason_codes"]

        review = client.post(
            "/api/decisions",
            json={
                "dataset_id": dataset_id,
                "purpose": "research_training",
                "context": "research",
                "derivative_work_requested": True,
            },
        )
        assert review.json()["decision"] == "NEEDS_REVIEW"
        assert "EVIDENCE_UNVERIFIED" in review.json()["reason_codes"]


def test_existing_database_migrates_additively_without_promoting_evidence(tmp_path, monkeypatch):
    db_path = tmp_path / "legacy.db"
    monkeypatch.setattr(database, "DB_PATH", db_path)
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """CREATE TABLE datasets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                language TEXT NOT NULL,
                description TEXT NOT NULL,
                license_name TEXT NOT NULL,
                permitted_purposes TEXT NOT NULL,
                commercial_use INTEGER,
                geographic_restrictions TEXT NOT NULL,
                expires_on TEXT,
                revoked INTEGER NOT NULL DEFAULT 0,
                evidence_verified INTEGER NOT NULL DEFAULT 0,
                synthetic INTEGER NOT NULL DEFAULT 1
            )"""
        )
        connection.execute(
            """INSERT INTO datasets (
                name, language, description, license_name, permitted_purposes,
                commercial_use, geographic_restrictions, revoked,
                evidence_verified, synthetic
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            ("Legacy row", "Hindi", "", "Legacy claim", '["research_training"]',
             0, "[]", 0, 1, 1),
        )

    database.initialize_database()
    with database.connect() as connection:
        row = connection.execute("SELECT * FROM datasets WHERE id = 1").fetchone()
        columns = {item["name"] for item in connection.execute("PRAGMA table_info(datasets)")}
    assert row["name"] == "Legacy row"
    assert row["owner"] == ""
    assert row["evidence_verified"] == 1
    assert row["evidence_independently_verified"] == 0
    assert row["redistribution_allowed"] is None
    assert row["derivative_work_allowed"] is None
    assert {"owner", "evidence_independently_verified", "redistribution_allowed", "derivative_work_allowed"} <= columns
    with database.connect() as connection:
        evidence_table = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'evidence_submissions'"
        ).fetchone()
        evidence_rows = connection.execute("SELECT COUNT(*) FROM evidence_submissions").fetchone()[0]
    assert evidence_table is not None
    assert evidence_rows == 0
    with TestClient(app) as client:
        datasets = client.get("/datasets")
    assert datasets.status_code == 200
    assert datasets.json()[0]["owner"] == ""
    assert datasets.json()[0]["source_name"] is None
    assert datasets.json()[0]["modalities"] is None
    assert datasets.json()[0]["provenance_status"] == "NOT_RECORDED"
