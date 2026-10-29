import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend import cost_allocation, database, main
from backend.cost_data import DemoCost
from backend.main import app


PURPOSE = "commercial_speech_training"
REVIEWER_ID = "isolated-synthetic-test-reviewer"
REVIEWER_TOKEN = "isolated-synthetic-review-token-0123456789"


def _register_fixture(client, name, **overrides):
    values = {
        "name": name,
        "language": "Kannada",
        "language_variety": "Fictional Karnataka regional variety",
        "owner": f"Fictional rights-holder claim for {name}",
        "data_contributor_claim": "Fictional volunteer contributor group",
        "license_name": "Fictional demonstration license",
        "license_version": "test-version-1",
        "permitted_purposes": [PURPOSE],
        "commercial_use": True,
        "redistribution_allowed": True,
        "derivative_work_allowed": True,
        "source_name": "Fictional demonstration source",
        "source_url": f"https://example.org/bhashatrust-fixtures/{name.split()[-1].lower()}",
        "source_dataset_id": f"fictional-{name.split()[-1].lower()}",
        "source_version": "fixture-v1",
        "modalities": ["speech_audio", "transcription"],
        "collection_method": "Fictional volunteered recordings; no audio included",
        "intended_use_notes": "Synthetic test for commercial speech training eligibility.",
        "known_restrictions": "None asserted in this fictional fixture.",
        "collection_period": "Fictional period; no real collection occurred",
        "provenance_notes": "SYNTHETIC / DEMONSTRATION ONLY; not a real source or evidence.",
        "synthetic": True,
    }
    values.update(overrides)
    response = client.post("/api/datasets", json=values)
    assert response.status_code == 201, response.text
    dataset_id = response.json()["id"]
    with database.connect() as connection:
        connection.execute(
            "UPDATE datasets SET synthetic_test_fixture = 1 WHERE id = ?",
            (dataset_id,),
        )
    return dataset_id


def _submit_and_review_fixture(client, dataset_id, decision):
    evidence = client.post(
        f"/api/datasets/{dataset_id}/evidence",
        json={
            "evidence_type": "license_document",
            "description": "SYNTHETIC test-only evidence; fictional and not a real rights document.",
            "reference": "https://example.org/bhashatrust-fixtures/synthetic-license",
            "submitter_id": "isolated-test-fixture-factory",
        },
    )
    assert evidence.status_code == 201, evidence.text
    if decision is None:
        return evidence.json()["id"]

    reviewed = client.post(
        f"/api/evidence/{evidence.json()['id']}/review",
        json={"decision": decision, "reason": "Synthetic test fixture decision in isolated database."},
        headers={"Authorization": f"Bearer {REVIEWER_TOKEN}"},
    )
    assert reviewed.status_code == 200, reviewed.text
    return evidence.json()["id"]


@pytest.fixture
def isolated_fixture_environment(tmp_path, monkeypatch):
    db_path = tmp_path / "isolated-provenance.db"
    monkeypatch.setattr(database, "DB_PATH", db_path)
    monkeypatch.setattr(main, "DEMO_DATASETS", [])
    monkeypatch.setattr(main, "REVIEWER_ENV_FILES", (tmp_path / "no-reviewer-config.env",))
    monkeypatch.setattr(main.corpus_manifests, "MANIFEST_ROOT", tmp_path / "manifests")
    monkeypatch.setenv("BHASHATRUST_REVIEWER_ID", REVIEWER_ID)
    monkeypatch.setenv("BHASHATRUST_REVIEWER_TOKEN", REVIEWER_TOKEN)
    yield db_path


def test_provenance_migration_preserves_legacy_rows_as_unknown(tmp_path, monkeypatch):
    db_path = tmp_path / "legacy-provenance.db"
    monkeypatch.setattr(database, "DB_PATH", db_path)
    with database.connect() as connection:
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
                commercial_use, geographic_restrictions, expires_on, revoked,
                evidence_verified, synthetic
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                "Legacy Kannada record",
                "Kannada",
                "",
                "Legacy claim",
                '["research_training"]',
                None,
                "[]",
                None,
                0,
                0,
                1,
            ),
        )

    database.initialize_database()
    with database.connect() as connection:
        row = connection.execute("SELECT * FROM datasets WHERE id = 1").fetchone()
        columns = {item["name"] for item in connection.execute("PRAGMA table_info(datasets)")}

    assert row["name"] == "Legacy Kannada record"
    assert row["source_name"] is None
    assert row["source_url"] is None
    assert row["modalities_json"] is None
    assert row["provenance_status"] == "NOT_RECORDED"
    assert row["metadata_claimed_at"] is None
    assert row["metadata_origin"] is None
    assert row["synthetic_test_fixture"] == 0
    assert {"source_name", "source_url", "modalities_json", "provenance_status"} <= columns


@pytest.mark.parametrize(
    "source_url",
    [
        "file:///private/dataset-card",
        "javascript:alert(1)",
        "https://user:pass@example.org/card",
        "https://example.org/card?token=private",
        "https://example.org/card#private",
        "https://example.org:99999/card",
    ],
)
def test_registration_rejects_unsafe_provenance_urls(tmp_path, monkeypatch, source_url):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "invalid-source-url.db")
    with TestClient(app) as client:
        response = client.post(
            "/api/datasets",
            json={
                "name": "Synthetic URL validation fixture",
                "language": "Kannada",
                "owner": "Fictional claimant",
                "license_name": "Fictional license",
                "source_url": source_url,
            },
        )
    assert response.status_code == 422


def test_isolated_kannada_abc_rights_gate_end_to_end(
    isolated_fixture_environment, monkeypatch
):
    db_path = isolated_fixture_environment
    monkeypatch.setattr(
        cost_allocation,
        "DEMO_LICENSE_COSTS",
        {
            **cost_allocation.DEMO_LICENSE_COSTS,
            "Synthetic Kannada Fixture A": DemoCost("Synthetic Kannada Fixture A", 10000, "USD", 10),
            "Synthetic Kannada Fixture B": DemoCost("Synthetic Kannada Fixture B", 10000, "USD", 10),
            "Synthetic Kannada Fixture C": DemoCost("Synthetic Kannada Fixture C", 10000, "USD", 10),
        },
    )
    with TestClient(app) as client:
        accepted_id = _register_fixture(client, "Synthetic Kannada Fixture A")
        rejected_id = _register_fixture(
            client,
            "Synthetic Kannada Fixture B",
            commercial_use=False,
            known_restrictions="Fictional license prohibits commercial training.",
        )
        review_id = _register_fixture(
            client,
            "Synthetic Kannada Fixture C",
            commercial_use=None,
            source_dataset_id=None,
            source_version=None,
            license_version=None,
            known_restrictions="Rights evidence is incomplete in this fictional fixture.",
        )

        with database.connect() as connection:
            fixture_ids = [
                row["id"]
                for row in connection.execute(
                    "SELECT id FROM datasets WHERE synthetic_test_fixture = 1 ORDER BY id"
                )
            ]
        assert fixture_ids == [accepted_id, rejected_id, review_id]

        for dataset_id, review_decision in (
            (accepted_id, "APPROVED"),
            (rejected_id, "APPROVED"),
            (review_id, None),
        ):
            _submit_and_review_fixture(client, dataset_id, review_decision)

        accepted_metadata = client.get(f"/api/datasets/{accepted_id}").json()
        assert accepted_metadata["language"] == "Kannada"
        assert accepted_metadata["language_variety"] == "Fictional Karnataka regional variety"
        assert accepted_metadata["source_url"].startswith("https://example.org/")
        assert accepted_metadata["modalities"] == ["speech_audio", "transcription"]
        assert accepted_metadata["owner"].startswith("Fictional rights-holder claim")
        assert accepted_metadata["provenance_status"] == "REGISTRANT_SUPPLIED"
        assert accepted_metadata["metadata_origin"] == "REGISTRANT"
        assert accepted_metadata["metadata_claimed_at"]
        assert accepted_metadata["evidence_independently_verified"] is True
        assert accepted_metadata["synthetic_test_fixture"] is True
        registry_record = next(
            item
            for item in client.get("/api/datasets").json()
            if item["id"] == accepted_id
        )
        assert registry_record["source_dataset_id"] == "fictional-a"
        assert registry_record["metadata_origin"] == "REGISTRANT"

        detail = client.get(f"/api/datasets/{review_id}").json()
        assert detail["provenance_status"] == "REGISTRANT_SUPPLIED"
        assert detail["evidence_independently_verified"] is False
        evidence_status = client.get(
            f"/api/datasets/{accepted_id}/evidence/status"
        ).json()
        assert evidence_status["submissions"][0]["review_status"] == "APPROVED"
        assert "reference" not in evidence_status["submissions"][0]

        decisions = {}
        for dataset_id in (accepted_id, rejected_id, review_id):
            response = client.post(
                "/api/decisions",
                json={
                    "dataset_id": dataset_id,
                    "purpose": PURPOSE,
                    "context": "commercial",
                    "geography": "IN",
                    "redistribution_requested": False,
                    "derivative_work_requested": False,
                },
            )
            assert response.status_code == 200, response.text
            decisions[dataset_id] = response.json()

        assert decisions[accepted_id]["decision"] == "APPROVED"
        assert decisions[accepted_id]["reason_codes"] == ["PERMISSIONS_SUPPORT_REQUEST"]
        assert decisions[rejected_id]["decision"] == "REJECTED"
        assert decisions[rejected_id]["reason_codes"] == ["COMMERCIAL_USE_NOT_PERMITTED"]
        assert decisions[review_id]["decision"] == "NEEDS_REVIEW"
        assert decisions[review_id]["reason_codes"] == [
            "COMMERCIAL_PERMISSION_UNSPECIFIED",
            "EVIDENCE_UNVERIFIED",
        ]

        optimization = client.post(
            "/api/optimizations",
            json={
                "purpose": PURPOSE,
                "context": "commercial",
                "geography": "IN",
                "redistribution_requested": False,
                "derivative_work_requested": False,
                "dataset_ids": [accepted_id, rejected_id, review_id],
                "budget": "200.00",
            },
        )
        assert optimization.status_code == 200, optimization.text
        assert optimization.json()["selected_dataset_ids"] == [accepted_id]
        assert {
            (item["dataset_id"], tuple(item["reason_codes"]))
            for item in optimization.json()["excluded_datasets"]
        } == {
            (rejected_id, ("COMMERCIAL_USE_NOT_PERMITTED",)),
            (
                review_id,
                ("COMMERCIAL_PERMISSION_UNSPECIFIED", "EVIDENCE_UNVERIFIED"),
            ),
        }

        manifest_response = client.post(
            "/api/manifests/generate",
            json={
                "purpose": PURPOSE,
                "context": "commercial",
                "geography": "IN",
                "redistribution_requested": False,
                "derivative_work_requested": False,
            },
        )
        assert manifest_response.status_code == 200, manifest_response.text
        manifest_result = manifest_response.json()
        assert manifest_result["counts"]["accepted"] == 1
        assert manifest_result["counts"]["rejected"] == 1
        assert manifest_result["counts"]["needs_review"] == 1

        accepted_manifest = json.loads(
            Path(manifest_result["files"]["accepted"]).read_text(encoding="utf-8")
        )
        rejected_manifest = json.loads(
            Path(manifest_result["files"]["rejected"]).read_text(encoding="utf-8")
        )
        review_manifest = json.loads(
            Path(manifest_result["files"]["needs_review"]).read_text(encoding="utf-8")
        )
        assert accepted_manifest["synthetic_test_output"] is True
        assert accepted_manifest["entries"][0]["dataset_id"] == accepted_id
        assert accepted_manifest["entries"][0]["synthetic_test_fixture"] is True
        assert rejected_manifest["entries"][0]["dataset_id"] == rejected_id
        assert review_manifest["entries"][0]["dataset_id"] == review_id

        accepted_cost = client.post(
            "/api/costs/allocate",
            json={
                "dataset_ids": [accepted_id],
                "purpose": PURPOSE,
                "context": "commercial",
                "geography": "IN",
                "redistribution_requested": False,
                "derivative_work_requested": False,
                "budget": "200.00",
            },
        )
        assert accepted_cost.status_code == 200, accepted_cost.text

        rejected_cost = client.post(
            "/api/costs/allocate",
            json={
                "dataset_ids": [rejected_id],
                "purpose": PURPOSE,
                "context": "commercial",
                "geography": "IN",
                "redistribution_requested": False,
                "derivative_work_requested": False,
                "budget": "200.00",
            },
        )
        review_cost = client.post(
            "/api/costs/allocate",
            json={
                "dataset_ids": [review_id],
                "purpose": PURPOSE,
                "context": "commercial",
                "geography": "IN",
                "redistribution_requested": False,
                "derivative_work_requested": False,
                "budget": "200.00",
            },
        )

        assert rejected_cost.status_code == 409
        assert rejected_cost.json()["detail"]["excluded_datasets"][0]["reason_codes"] == [
            "COMMERCIAL_USE_NOT_PERMITTED"
        ]
        assert review_cost.status_code == 409
        assert review_cost.json()["detail"]["excluded_datasets"][0]["reason_codes"] == [
            "COMMERCIAL_PERMISSION_UNSPECIFIED",
            "EVIDENCE_UNVERIFIED",
        ]

        cost_events = [
            event
            for event in client.get("/audit/events").json()
            if event["event_type"] == "cost_allocation"
        ]
        assert len(cost_events) == 1
        assert cost_events[0]["payload"]["eligible_dataset_ids"] == [accepted_id]
        assert client.get("/audit/verify").json()["intact"] is True

    assert db_path.parent == isolated_fixture_environment.parent
    assert not (database.PROJECT_ROOT / "data" / "isolated-provenance.db").exists()
