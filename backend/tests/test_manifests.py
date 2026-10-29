import json
from datetime import date, timedelta
from pathlib import Path

from fastapi.testclient import TestClient

from backend import database, manifests
from backend.main import app


def test_manifests_are_generated_from_engine_decisions_and_audited(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "manifest.db")
    manifest_root = tmp_path / "project" / "manifests"
    monkeypatch.setattr(manifests, "MANIFEST_ROOT", manifest_root)

    with TestClient(app) as client:
        with database.connect() as connection:
            connection.execute(
                "UPDATE datasets SET evidence_independently_verified = 1 WHERE id = 1"
            )
        registry = client.get("/api/datasets")
        assert registry.status_code == 200
        assert client.get("/api/datasets/1").status_code == 200
        assert client.get("/api/datasets/999").status_code == 404

        registered = client.post(
            "/api/datasets",
            json={
                "name": "Synthetic revoked dataset",
                "language": "Kannada",
                "owner": "Synthetic demo contributor",
                "license_name": "Synthetic test license",
                "permitted_purposes": ["research_training"],
                "commercial_use": False,
                "expires_on": (date.today() + timedelta(days=30)).isoformat(),
                "revoked": True,
                "evidence_verified": True,
                "synthetic": True,
            },
        )
        assert registered.status_code == 201

        generated = client.post(
            "/api/manifests/generate",
            json={"purpose": "research_training", "context": "research"},
        )
        assert generated.status_code == 200
        result = generated.json()
        assert result["counts"] == {"accepted": 1, "rejected": 1, "needs_review": 1}
        assert result["audit_event_reference"] is not None

        loaded = {}
        for category, relative_path in result["files"].items():
            path = manifests.PROJECT_ROOT / relative_path
            assert path.is_file()
            loaded[category] = json.loads(path.read_text(encoding="utf-8"))
            assert loaded[category]["manifest_type"] == category
            assert loaded[category]["policy_version"] == "1.1.0"
            assert loaded[category]["redistribution_requested"] is False
            assert loaded[category]["derivative_work_requested"] is False
            for entry in loaded[category]["entries"]:
                assert {
                    "dataset_id",
                    "decision",
                    "reason_codes",
                    "policy_version",
                    "timestamp",
                    "evidence_verification_status",
                    "evidence_references",
                } <= entry.keys()

        accepted_ids = {entry["dataset_id"] for entry in loaded["accepted"]["entries"]}
        rejected_ids = {entry["dataset_id"] for entry in loaded["rejected"]["entries"]}
        review_ids = {entry["dataset_id"] for entry in loaded["needs_review"]["entries"]}
        assert accepted_ids == {1}
        assert rejected_ids == {3}
        assert review_ids == {2}
        assert not (accepted_ids & rejected_ids or accepted_ids & review_ids or rejected_ids & review_ids)
        assert accepted_ids | rejected_ids | review_ids == {1, 2, 3}

        audit_types = [event["event_type"] for event in client.get("/audit/events").json()]
        assert audit_types.count("manifest_decision") == 3
        assert audit_types.count("manifest_generation") == 1
        audit_events = client.get("/audit/events").json()
        manifest_decision_ids = {
            event["id"] for event in audit_events if event["event_type"] == "manifest_decision"
        }
        assert all(
            entry["audit_event_reference"] in manifest_decision_ids
            for manifest in loaded.values()
            for entry in manifest["entries"]
        )
        assert client.get("/audit/verify").json()["intact"] is True
