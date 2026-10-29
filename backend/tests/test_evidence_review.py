import base64
from datetime import date

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient

from backend import database, main
from backend.main import app
from backend.schemas import RightsPayload
from backend.signatures import canonicalize_rights


REVIEWER_TOKEN = "test-only-reviewer-token-with-enough-length-123456"
REVIEWER_ID = "test-reviewer"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "evidence-review.db")
    monkeypatch.setattr(main, "REVIEWER_ENV_FILES", (tmp_path / "missing-reviewer.env",))
    monkeypatch.delenv("BHASHATRUST_REVIEWER_ID", raising=False)
    monkeypatch.delenv("BHASHATRUST_REVIEWER_TOKEN", raising=False)
    with TestClient(app) as test_client:
        yield test_client


def register_dataset(client, **overrides):
    values = {
        "name": "Synthetic evidence-review dataset",
        "language": "Kannada",
        "owner": "Synthetic submitter claim",
        "license_name": "Synthetic research permission",
        "permitted_purposes": ["research_training"],
        "commercial_use": False,
        "redistribution_allowed": True,
        "derivative_work_allowed": True,
        "synthetic": True,
    }
    values.update(overrides)
    response = client.post("/api/datasets", json=values)
    assert response.status_code == 201
    return response.json()["id"]


def submit_evidence(client, dataset_id, **overrides):
    values = {
        "evidence_type": "license_document",
        "description": "Synthetic public reference for workflow testing.",
        "reference": "https://example.org/synthetic-license.pdf",
        "submitter_id": "synthetic-submit-actor",
    }
    values.update(overrides)
    return client.post(f"/api/datasets/{dataset_id}/evidence", json=values)


def authorize_reviewer(monkeypatch):
    monkeypatch.setenv("BHASHATRUST_REVIEWER_ID", REVIEWER_ID)
    monkeypatch.setenv("BHASHATRUST_REVIEWER_TOKEN", REVIEWER_TOKEN)


def review(client, evidence_id, decision, reason="Reviewed synthetic evidence."):
    return client.post(
        f"/api/evidence/{evidence_id}/review",
        json={"decision": decision, "reason": reason},
        headers={"Authorization": f"Bearer {REVIEWER_TOKEN}"},
    )


def test_reviewer_is_fail_closed_when_missing_or_malformed(client, monkeypatch):
    assert client.get("/api/reviewer/status").json() == {"configured": False}
    assert client.get("/api/review/queue").status_code == 503

    monkeypatch.setenv("BHASHATRUST_REVIEWER_ID", REVIEWER_ID)
    monkeypatch.setenv("BHASHATRUST_REVIEWER_TOKEN", "too-short")
    assert client.get("/api/reviewer/status").json() == {"configured": False}
    assert client.get("/api/review/queue").status_code == 503


def test_non_ascii_invalid_reviewer_token_fails_closed(client, monkeypatch):
    authorize_reviewer(monkeypatch)
    response = client.get(
        "/api/review/queue",
        headers=[(b"Authorization", b"Bearer invalid-token-\xc3\xa9")],
    )
    assert response.status_code == 401


def test_legacy_verification_flag_without_approved_review_is_demoted_on_migration(client):
    dataset_id = register_dataset(client)
    with database.connect() as connection:
        connection.execute(
            """UPDATE datasets
               SET evidence_verified = 1, evidence_independently_verified = 1
               WHERE id = ?""",
            (dataset_id,),
        )

    database.initialize_database()
    migrated = client.get(f"/api/datasets/{dataset_id}").json()
    assert migrated["evidence_verified"] is False
    assert migrated["evidence_independently_verified"] is False


@pytest.mark.parametrize(
    "reference",
    [
        r"C:\private\license.pdf",
        "http://example.org/license.pdf",
        "https://user:password@example.org/license.pdf",
        "https://example.org/license.pdf?access_token=secret",
        "https://example.org/license.pdf#private-fragment",
    ],
)
def test_evidence_reference_rejects_unsafe_shapes(client, reference):
    dataset_id = register_dataset(client)
    response = submit_evidence(client, dataset_id, reference=reference)
    assert response.status_code == 422


def test_evidence_submission_is_pending_audited_and_not_auto_approved(client):
    dataset_id = register_dataset(client)
    submitted = submit_evidence(client, dataset_id)
    assert submitted.status_code == 201
    evidence = submitted.json()
    assert evidence["review_status"] == "PENDING"
    assert evidence["reviewer_id"] is None

    decision = client.post(
        "/api/decisions",
        json={"dataset_id": dataset_id, "purpose": "research_training", "context": "research"},
    ).json()
    assert decision["decision"] == "NEEDS_REVIEW"
    assert "EVIDENCE_UNVERIFIED" in decision["reason_codes"]

    events = client.get("/audit/events").json()
    evidence_event = next(event for event in events if event["event_type"] == "evidence_submitted")
    assert evidence_event["payload"]["dataset_id"] == dataset_id
    assert evidence_event["payload"]["submitter_id"] == "synthetic-submit-actor"
    assert "reference" not in evidence_event["payload"]
    assert client.get("/audit/verify").json()["intact"] is True


def test_reviewer_queue_and_decisions_are_authenticated_and_auditable(client, monkeypatch):
    dataset_id = register_dataset(client)
    evidence = submit_evidence(client, dataset_id).json()
    evidence_id = evidence["id"]

    unauthorized = client.get(
        "/api/review/queue",
        headers={"Authorization": "Bearer wrong-token"},
    )
    assert unauthorized.status_code == 503
    assert client.post(
        f"/api/evidence/{evidence_id}/review",
        json={"decision": "APPROVED", "reason": "Not authorized."},
        headers={"Authorization": f"Bearer {REVIEWER_TOKEN}"},
    ).status_code == 503

    authorize_reviewer(monkeypatch)
    queue = client.get(
        "/api/review/queue",
        headers={"Authorization": f"Bearer {REVIEWER_TOKEN}"},
    )
    assert queue.status_code == 200
    item = next(item for item in queue.json()["items"] if item["evidence"]["id"] == evidence_id)
    assert item["owner_claim"] == "Synthetic submitter claim"
    assert item["evidence"]["reference"] == "https://example.org/synthetic-license.pdf"
    assert item["current_decision"] == "NEEDS_REVIEW"

    rejected_token = client.get(
        "/api/review/queue",
        headers={"Authorization": "Bearer invalid-reviewer-token-0123456789"},
    )
    assert rejected_token.status_code == 401

    approved = review(client, evidence_id, "APPROVED")
    assert approved.status_code == 200
    assert approved.json()["dataset_evidence_approved"] is True
    assert approved.json()["evidence"]["reviewer_id"] == REVIEWER_ID
    assert approved.json()["evidence"]["reviewed_at"]

    research = client.post(
        "/api/decisions",
        json={"dataset_id": dataset_id, "purpose": "research_training", "context": "research"},
    ).json()
    commercial = client.post(
        "/api/decisions",
        json={"dataset_id": dataset_id, "purpose": "research_training", "context": "commercial"},
    ).json()
    assert research["decision"] == "APPROVED"
    assert commercial["decision"] == "REJECTED"
    assert "COMMERCIAL_USE_NOT_PERMITTED" in commercial["reason_codes"]

    event_types = [event["event_type"] for event in client.get("/audit/events").json()]
    assert "evidence_approved" in event_types
    assert client.get("/audit/verify").json()["intact"] is True


@pytest.mark.parametrize(
    ("decision", "expected_status", "expected_reason"),
    [
        ("REJECTED", "EVIDENCE_REVIEW_REJECTED", "EVIDENCE_REVIEW_REJECTED"),
        ("MORE_EVIDENCE_REQUESTED", "ADDITIONAL_EVIDENCE_REQUESTED", "EVIDENCE_MORE_REQUESTED"),
    ],
)
def test_rejected_or_insufficient_evidence_stays_ineligible(
    client, monkeypatch, decision, expected_status, expected_reason
):
    authorize_reviewer(monkeypatch)
    dataset_id = register_dataset(client)
    evidence_id = submit_evidence(client, dataset_id).json()["id"]
    result = review(client, evidence_id, decision, "The synthetic evidence is insufficient.")
    assert result.status_code == 200
    assert result.json()["dataset_evidence_approved"] is False

    current = client.post(
        "/api/decisions",
        json={"dataset_id": dataset_id, "purpose": "research_training", "context": "research"},
    ).json()
    assert current["decision"] == "NEEDS_REVIEW"
    assert current["evidence_verification_status"] == expected_status
    assert expected_reason in current["reason_codes"]


def test_synthetic_demo_reference_cannot_be_approved_or_authorize_training(
    client, monkeypatch, tmp_path
):
    from backend import manifests

    monkeypatch.setattr(manifests, "MANIFEST_ROOT", tmp_path / "manifests")
    authorize_reviewer(monkeypatch)
    dataset_id = register_dataset(client)
    evidence_id = submit_evidence(
        client,
        dataset_id,
        evidence_type="Synthetic demo license reference",
    ).json()["id"]

    approval = review(client, evidence_id, "APPROVED")
    assert approval.status_code == 409
    assert "cannot be approved" in approval.json()["detail"]

    current = client.post(
        "/api/decisions",
        json={"dataset_id": dataset_id, "purpose": "research_training", "context": "research"},
    ).json()
    assert current["decision"] == "NEEDS_REVIEW"
    assert "EVIDENCE_UNVERIFIED" in current["reason_codes"]

    optimization = client.post(
        "/api/optimizations",
        json={
            "purpose": "research_training",
            "context": "research",
            "dataset_ids": [dataset_id],
            "budget": "250.00",
            "currency": "USD",
        },
    ).json()
    assert dataset_id not in optimization["selected_dataset_ids"]
    assert any(item["dataset_id"] == dataset_id for item in optimization["excluded_datasets"])

    manifest = client.post(
        "/api/manifests/generate",
        json={"purpose": "research_training", "context": "research"},
    ).json()
    assert manifest["counts"]["accepted"] == 0
    event_types = [event["event_type"] for event in client.get("/audit/events").json()]
    assert "evidence_submitted" in event_types
    assert "evidence_approved" not in event_types
    assert client.get("/audit/verify").json()["intact"] is True


def test_new_evidence_reopens_previous_approval_and_rechecks_all_items(client, monkeypatch):
    authorize_reviewer(monkeypatch)
    dataset_id = register_dataset(client)
    first = submit_evidence(client, dataset_id).json()
    assert review(client, first["id"], "APPROVED").json()["dataset_evidence_approved"] is True

    second = submit_evidence(
        client,
        dataset_id,
        reference="https://example.org/synthetic-additional-evidence.pdf",
    ).json()
    assert second["review_status"] == "PENDING"
    assert client.get(f"/api/datasets/{dataset_id}").json()["evidence_independently_verified"] is False
    queue = client.get(
        "/api/review/queue",
        headers={"Authorization": f"Bearer {REVIEWER_TOKEN}"},
    ).json()
    dataset_items = [item for item in queue["items"] if item["evidence"]["dataset_id"] == dataset_id]
    assert [item["evidence"]["review_status"] for item in dataset_items] == ["PENDING", "PENDING"]

    decision = client.post(
        "/api/decisions",
        json={"dataset_id": dataset_id, "purpose": "research_training", "context": "research"},
    ).json()
    assert decision["decision"] == "NEEDS_REVIEW"
    event_types = [event["event_type"] for event in client.get("/audit/events").json()]
    assert "evidence_review_reopened" in event_types
    assert client.get("/audit/verify").json()["intact"] is True


def test_new_signed_rights_claim_reopens_evidence_review(client, monkeypatch):
    authorize_reviewer(monkeypatch)
    dataset_id = register_dataset(client)
    evidence_id = submit_evidence(client, dataset_id).json()["id"]
    assert review(client, evidence_id, "APPROVED").json()["dataset_evidence_approved"] is True

    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    payload = RightsPayload(
        dataset_id=dataset_id,
        grantor="Synthetic signed claimant",
        license_name="Updated synthetic research permission",
        permitted_purposes=["research_training"],
        commercial_use=False,
        redistribution_allowed=True,
        derivative_work_allowed=True,
    )
    signature = private_key.sign(canonicalize_rights(payload))
    response = client.post(
        f"/datasets/{dataset_id}/rights-records",
        json={
            "payload": payload.model_dump(mode="json"),
            "signer_id": "synthetic-key",
            "public_key": base64.b64encode(public_key).decode(),
            "signature": base64.b64encode(signature).decode(),
        },
    )
    assert response.status_code == 201
    assert response.json()["signature_valid"] is True
    assert client.get(f"/api/datasets/{dataset_id}").json()["evidence_independently_verified"] is False
    assert client.get("/audit/verify").json()["intact"] is True


def test_pending_and_rejected_evidence_cannot_enter_optimizer_or_accepted_manifest(
    client, monkeypatch, tmp_path
):
    from pathlib import Path

    from backend import manifests

    monkeypatch.setattr(manifests, "MANIFEST_ROOT", tmp_path / "manifests")
    authorize_reviewer(monkeypatch)
    submit_evidence(client, 1)
    evidence_row = client.get("/api/review/queue", headers={
        "Authorization": f"Bearer {REVIEWER_TOKEN}",
    })
    assert evidence_row.status_code == 200
    evidence_id = next(
        item["evidence"]["id"]
        for item in evidence_row.json()["items"]
        if item["evidence"]["dataset_id"] == 1
    )

    with database.connect() as connection:
        connection.execute(
            "UPDATE datasets SET evidence_independently_verified = 1 WHERE id = 1"
        )
    pending = client.post(
        "/api/optimizations",
        json={
            "purpose": "research_training",
            "context": "research",
            "dataset_ids": [1],
            "budget": "250.00",
            "currency": "USD",
        },
    ).json()
    assert pending["selected_dataset_ids"] == []
    assert any(item["dataset_id"] == 1 for item in pending["excluded_datasets"])

    assert review(client, evidence_id, "REJECTED", "Synthetic evidence rejected.").status_code == 200
    manifest = client.post(
        "/api/manifests/generate",
        json={"purpose": "research_training", "context": "research"},
    ).json()
    accepted_path = Path(manifest["files"]["accepted"])
    accepted = accepted_path.read_text(encoding="utf-8")
    assert f'"dataset_id": {1}' not in accepted
    assert manifest["counts"]["accepted"] == 0
    assert client.get("/audit/verify").json()["intact"] is True


def test_review_reasons_reject_obvious_credential_material(client, monkeypatch):
    authorize_reviewer(monkeypatch)
    dataset_id = register_dataset(client)
    evidence_id = submit_evidence(client, dataset_id).json()["id"]
    response = review(client, evidence_id, "REJECTED", "token=do-not-store-this")
    assert response.status_code == 422
