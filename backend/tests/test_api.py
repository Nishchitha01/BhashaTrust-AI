import base64
from datetime import date, timedelta

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient

from backend import database
from backend.main import app
from backend.schemas import RightsPayload
from backend.signatures import canonicalize_rights


def test_api_registers_lists_and_evaluates_datasets(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "test.db")
    with TestClient(app) as client:
        assert client.get("/health").json()["status"] == "ok"
        initial = client.get("/datasets").json()
        assert len(initial) == 2
        assert all(item["synthetic"] for item in initial)

        missing_owner = client.post(
            "/datasets",
            json={
                "name": "Dataset without owner",
                "language": "Tamil",
                "license_name": "Demo permission",
            },
        )
        assert missing_owner.status_code == 422
        assert len(client.get("/datasets").json()) == 2

        created = client.post(
            "/datasets",
            json={
                "name": "Synthetic API test",
                "language": "Tamil",
                "owner": "Synthetic test submitter",
                "license_name": "Demo permission",
                "permitted_purposes": ["research_training"],
                "commercial_use": False,
                "evidence_verified": True,
                "evidence_independently_verified": True,
                "synthetic": True,
            },
        )
        assert created.status_code == 201
        dataset_id = created.json()["id"]
        assert created.json()["owner"] == "Synthetic test submitter"
        assert created.json()["evidence_verified"] is False
        assert created.json()["evidence_independently_verified"] is False
        events = client.get("/audit/events").json()
        registration = next(event for event in events if event["event_type"] == "dataset_registered")
        assert registration["entity_id"] == str(dataset_id)
        assert registration["payload"]["owner"] == "Synthetic test submitter"
        assert registration["payload"]["evidence_independently_verified"] is False
        assert client.get("/audit/verify").json()["intact"] is True
        decision = client.post(
            f"/datasets/{dataset_id}/evaluate",
            json={"purpose": "research_training", "commercial": False},
        )
        assert decision.status_code == 200
        assert decision.json()["status"] == "review_required"

        missing = client.post(
            "/datasets/9999/evaluate",
            json={"purpose": "research_training", "commercial": False},
        )
        assert missing.status_code == 404


def test_signed_expired_and_revoked_rights_are_verified_audited_and_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "signed.db")
    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    payload = RightsPayload(
        dataset_id=1,
        grantor="Synthetic demo contributor",
        license_name="Synthetic expired grant",
        permitted_purposes=["research_training"],
        commercial_use=False,
        expires_on=date.today() - timedelta(days=1),
        revoked=True,
    )
    signature = private_key.sign(canonicalize_rights(payload))

    with TestClient(app) as client:
        submitted = client.post(
            "/datasets/1/rights-records",
            json={
                "payload": payload.model_dump(mode="json"),
                "signer_id": "demo-key-1",
                "public_key": base64.b64encode(public_key).decode(),
                "signature": base64.b64encode(signature).decode(),
            },
        )
        assert submitted.status_code == 201
        assert submitted.json()["signature_valid"] is True
        assert submitted.json()["ownership_proven"] is False

        decision = client.post(
            "/datasets/1/evaluate",
            json={"purpose": "research_training", "commercial": False},
        )
        assert decision.status_code == 200
        assert decision.json()["status"] == "rejected"
        assert any("expired" in reason.lower() for reason in decision.json()["reasons"])
        assert any("revoked" in reason.lower() for reason in decision.json()["reasons"])

        audit_events = client.get("/audit/events").json()
        assert [event["event_type"] for event in audit_events] == [
            "rights_claim_changed",
            "rights_signature_verified",
            "license_decision",
        ]
        assert client.get("/audit/verify").json()["intact"] is True


def test_invalid_rights_signature_is_recorded_but_requires_review(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "invalid.db")
    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    payload = RightsPayload(
        dataset_id=1,
        grantor="Synthetic demo contributor",
        license_name="Synthetic permission",
        permitted_purposes=["research_training"],
    )
    signature = private_key.sign(canonicalize_rights(payload.model_copy(update={"commercial_use": True})))

    with TestClient(app) as client:
        submitted = client.post(
            "/datasets/1/rights-records",
            json={
                "payload": payload.model_dump(mode="json"),
                "signer_id": "demo-key-2",
                "public_key": base64.b64encode(public_key).decode(),
                "signature": base64.b64encode(signature).decode(),
            },
        )
        assert submitted.status_code == 201
        assert submitted.json()["signature_valid"] is False

        decision = client.post(
            "/datasets/1/evaluate",
            json={"purpose": "research_training", "commercial": False},
        )
        assert decision.json()["status"] == "review_required"
        assert client.get("/audit/verify").json()["intact"] is True


def test_valid_signature_is_not_independent_legal_verification(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "signed-claim.db")
    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    payload = RightsPayload(
        dataset_id=1,
        grantor="Synthetic demo contributor",
        license_name="Signed research claim",
        permitted_purposes=["research_training"],
        commercial_use=False,
        redistribution_allowed=True,
        derivative_work_allowed=True,
    )
    signature = private_key.sign(canonicalize_rights(payload))

    with TestClient(app) as client:
        submitted = client.post(
            "/datasets/1/rights-records",
            json={
                "payload": payload.model_dump(mode="json"),
                "signer_id": "demo-key-3",
                "public_key": base64.b64encode(public_key).decode(),
                "signature": base64.b64encode(signature).decode(),
            },
        )
        assert submitted.status_code == 201
        assert submitted.json()["signature_valid"] is True

        decision = client.post(
            "/api/decisions",
            json={
                "dataset_id": 1,
                "purpose": "research_training",
                "context": "research",
            },
        )
        assert decision.status_code == 200
        assert decision.json()["decision"] == "NEEDS_REVIEW"
        assert decision.json()["evidence_verification_status"] == "USER_SIGNED_CLAIM_SIGNATURE_VALID"
        assert "EVIDENCE_UNVERIFIED" in decision.json()["reason_codes"]


def test_tampered_signed_rights_payload_is_not_applied(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tampered-rights.db")
    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    payload = RightsPayload(
        dataset_id=1,
        grantor="Synthetic demo contributor",
        license_name="Signed research-only claim",
        permitted_purposes=["research_training"],
        commercial_use=False,
    )
    signature = private_key.sign(canonicalize_rights(payload))

    with TestClient(app) as client:
        submitted = client.post(
            "/datasets/1/rights-records",
            json={
                "payload": payload.model_dump(mode="json"),
                "signer_id": "demo-key-tamper",
                "public_key": base64.b64encode(public_key).decode(),
                "signature": base64.b64encode(signature).decode(),
            },
        )
        assert submitted.status_code == 201
        record_id = submitted.json()["id"]

        tampered_payload = payload.model_copy(
            update={
                "license_name": "Commercial training permission",
                "permitted_purposes": ["commercial_training"],
                "commercial_use": True,
            }
        )
        with database.connect() as connection:
            connection.execute(
                "UPDATE rights_records SET payload_json = ? WHERE id = ?",
                (canonicalize_rights(tampered_payload).decode("utf-8"), record_id),
            )

        listed = client.get("/datasets/1/rights-records")
        assert listed.status_code == 200
        assert listed.json()[0]["signature_valid"] is False

        decision = client.post(
            "/api/decisions",
            json={
                "dataset_id": 1,
                "purpose": "commercial_training",
                "context": "commercial",
            },
        )
        assert decision.status_code == 200
        assert decision.json()["decision"] == "REJECTED"
        assert decision.json()["evidence_verification_status"] == "USER_SIGNED_CLAIM_SIGNATURE_INVALID"
        assert "PURPOSE_NOT_PERMITTED" in decision.json()["reason_codes"]


def test_conflicting_rights_assertions_require_review(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "conflicting-claim.db")
    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    payload = RightsPayload(
        dataset_id=1,
        grantor="Synthetic demo contributor",
        license_name="Conflicting research claim",
        permitted_purposes=["research_training"],
        commercial_use=True,
    )
    signature = private_key.sign(canonicalize_rights(payload))

    with TestClient(app) as client:
        with database.connect() as connection:
            connection.execute(
                "UPDATE datasets SET evidence_independently_verified = 1 WHERE id = 1"
            )
        submitted = client.post(
            "/datasets/1/rights-records",
            json={
                "payload": payload.model_dump(mode="json"),
                "signer_id": "demo-key-conflict",
                "public_key": base64.b64encode(public_key).decode(),
                "signature": base64.b64encode(signature).decode(),
            },
        )
        assert submitted.status_code == 201
        decision = client.post(
            "/api/decisions",
            json={
                "dataset_id": 1,
                "purpose": "research_training",
                "context": "research",
            },
        )
        assert decision.status_code == 200
        assert decision.json()["decision"] == "NEEDS_REVIEW"
        assert decision.json()["evidence_verification_status"] == "CONFLICTING_RIGHTS_CLAIMS"
        assert "EVIDENCE_CONFLICT" in decision.json()["reason_codes"]
