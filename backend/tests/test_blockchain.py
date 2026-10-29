import base64
import os
from datetime import date, timedelta

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient

from backend import blockchain_service, database
from backend.main import app
from backend.schemas import RightsPayload
from backend.signatures import canonicalize_rights


def _rights_payload(**overrides):
    values = {
        "dataset_id": 1,
        "grantor": "Synthetic test grantor",
        "license_name": "Local chain test grant",
        "permitted_purposes": ["research_training"],
        "expires_on": date.today() + timedelta(days=30),
    }
    values.update(overrides)
    return RightsPayload(**values)


def _rights_submission(payload):
    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    signature = private_key.sign(canonicalize_rights(payload))
    return {
        "payload": payload.model_dump(mode="json"),
        "signer_id": "local-chain-test-signer",
        "public_key": base64.b64encode(public_key).decode("ascii"),
        "signature": base64.b64encode(signature).decode("ascii"),
    }


def _create_signed_record(client, payload=None):
    return client.post(
        "/datasets/1/rights-records",
        json=_rights_submission(payload or _rights_payload()),
    )


def test_canonical_record_hash_is_stable_and_detects_record_modification():
    row = {
        "id": 4,
        "payload_json": _rights_payload().model_dump_json(),
        "signer_id": "test-signer",
        "public_key": "public-key",
        "signature": "signature-bytes",
        "signature_valid": 1,
        "verification_message": "Signature is valid.",
        "created_at": "2026-01-02T03:04:05+00:00",
    }
    digest = blockchain_service.hash_rights_record(row, "1.0.0")
    assert digest == blockchain_service.hash_rights_record(row, "1.0.0")
    changed = dict(row, verification_message="modified")
    assert digest != blockchain_service.hash_rights_record(changed, "1.0.0")
    assert digest != blockchain_service.hash_rights_record(row, "1.0.1")


def test_existing_rights_record_is_database_only_until_explicit_anchor(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "db-only.db")
    with TestClient(app) as client:
        with database.connect() as connection:
            connection.execute(
                "UPDATE datasets SET evidence_independently_verified = 1 WHERE id = 1"
            )
        created = _create_signed_record(
            client,
            _rights_payload(
                grantor="Synthetic demo contributor",
                license_name="Synthetic Research-Only License",
                commercial_use=False,
                expires_on=None,
            ),
        )
        assert created.status_code == 201
        record_id = created.json()["id"]
        ledger = client.get("/api/datasets/1/rights-ledger")
        assert ledger.status_code == 200
        item = next(value for value in ledger.json() if value["id"] == record_id)
        assert item["ledger_status"] == "DATABASE_ONLY"
        assert item["licensing_decision"] == "APPROVED"
        assert item["current_record_hash"].startswith("0x")
        assert item["transaction_hash"] is None


def test_missing_local_node_marks_failed_without_fabricating_transaction_metadata(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "offline.db")
    monkeypatch.setattr(blockchain_service, "_environment_settings", lambda: {})
    with TestClient(app) as client:
        with database.connect() as connection:
            connection.execute(
                "UPDATE datasets SET evidence_independently_verified = 1 WHERE id = 1"
            )
        created = _create_signed_record(
            client,
            _rights_payload(
                grantor="Synthetic demo contributor",
                license_name="Synthetic Research-Only License",
                commercial_use=False,
                expires_on=None,
            ),
        )
        record_id = created.json()["id"]
        result = client.post(
            f"/api/rights-records/{record_id}/anchor",
            json={"purpose": "research_training", "context": "research"},
        )
        assert result.status_code == 200
        assert result.json()["ledger_status"] == "FAILED"
        assert result.json()["transaction_hash"] is None
        assert "BLOCKCHAIN_RPC_URL" in result.json()["error_message"]
        assert client.get("/audit/verify").json()["intact"] is True


def test_licensing_rejection_prevents_any_blockchain_submission(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "denied.db")
    monkeypatch.setattr(
        "backend.main.anchor_record_on_chain",
        lambda *args, **kwargs: pytest.fail("A denied rights decision must stop before chain submission."),
    )
    with TestClient(app) as client:
        created = _create_signed_record(client, _rights_payload(permitted_purposes=["different_purpose"]))
        record_id = created.json()["id"]
        response = client.post(
            f"/api/rights-records/{record_id}/anchor",
            json={"purpose": "research_training", "context": "research"},
        )
        assert response.status_code == 409
        assert response.json()["detail"]["decision"] == "REJECTED"
        status = client.get(f"/api/rights-records/{record_id}/anchor").json()
        assert status["ledger_status"] == "DATABASE_ONLY"
        assert status["transaction_hash"] is None
        assert client.get("/audit/verify").json()["intact"] is True


def test_verification_reports_unavailable_without_claiming_a_match(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "verify-offline.db")
    monkeypatch.setattr(blockchain_service, "_environment_settings", lambda: {})
    with TestClient(app) as client:
        created = _create_signed_record(client)
        record_id = created.json()["id"]
        response = client.post(f"/api/rights-records/{record_id}/verify-anchor")
        assert response.status_code == 200
        assert response.json()["ledger_status"] == "DATABASE_ONLY"
        assert response.json()["verification_status"] == "UNAVAILABLE"
        assert response.json()["verified"] is None
        assert response.json()["transaction_hash"] is None


@pytest.mark.skipif(
    os.environ.get("BLOCKCHAIN_INTEGRATION_TEST") != "1",
    reason="Set BLOCKCHAIN_INTEGRATION_TEST=1 with a deployed loopback Hardhat contract to run this local integration test.",
)
def test_real_local_hardhat_anchor_verify_and_tamper_detection(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "hardhat-integration.db")
    payload = _rights_payload()
    with TestClient(app) as client:
        import time

        with database.connect() as connection:
            connection.execute(
                "INSERT OR REPLACE INTO sqlite_sequence (name, seq) VALUES ('rights_records', ?)",
                (int(time.time() * 10) % 10_000_000 + 100,),
            )
        created = _create_signed_record(client, payload)
        assert created.status_code == 201
        record_id = created.json()["id"]

        anchored = client.post(
            f"/api/rights-records/{record_id}/anchor",
            json={"purpose": "research_training", "context": "research"},
        )
        assert anchored.status_code == 200
        result = anchored.json()
        assert result["ledger_status"] == "ANCHORED"
        assert result["verification_status"] == "MATCH"
        assert result["verified"] is True
        assert result["transaction_hash"].startswith("0x")
        assert result["block_number"] > 0

        modified_payload = payload.model_copy(update={"license_name": "Modified after anchor"})
        with database.connect() as connection:
            connection.execute(
                "UPDATE rights_records SET payload_json = ? WHERE id = ?",
                (canonicalize_rights(modified_payload).decode("utf-8"), record_id),
            )
        verification = client.post(f"/api/rights-records/{record_id}/verify-anchor")
        assert verification.status_code == 200
        assert verification.json()["ledger_status"] == "ANCHORED"
        assert verification.json()["verification_status"] == "MISMATCH"
        assert verification.json()["verified"] is False
        assert client.get("/audit/verify").json()["intact"] is True
