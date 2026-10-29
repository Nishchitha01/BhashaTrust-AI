from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from backend import database
from backend.cost_allocation import calculate_allocation
from backend.main import app
from backend.optimizer import Candidate, solve_exact, validate_optimizer_selection
from backend.schemas import CostLine, EligibilityResult, IngestionDecisionStatus


def test_cost_allocation_is_deterministic_and_handles_zero_budget():
    items = [
        CostLine(
            dataset_id=1,
            dataset_name="Synthetic Hindi News Sample",
            cost=Decimal("125.00"),
            currency="USD",
            utility_score=90,
            synthetic_demo_cost=True,
            cost_source="synthetic_demo_data",
        )
    ]
    result = calculate_allocation(items, Decimal("0.00"), "USD")
    assert result.total_cost == Decimal("125.00")
    assert result.remaining_budget == Decimal("-125.00")
    assert result.budget_status == "OVER_BUDGET"
    assert result.items[0].synthetic_demo_cost is True
    assert calculate_allocation([], Decimal("0.00"), "USD").budget_status == "WITHIN_BUDGET"


def test_exact_optimizer_finds_best_feasible_subset():
    candidates = [
        Candidate(dataset_id=1, cost_minor_units=12500, utility_score=90),
        Candidate(dataset_id=2, cost_minor_units=8000, utility_score=70),
        Candidate(dataset_id=3, cost_minor_units=5000, utility_score=45),
    ]
    selected, utility = solve_exact(candidates, 13000)
    assert selected == [2, 3]
    assert utility == 115


def test_optimizer_result_validator_rejects_ineligible_and_over_budget_outputs():
    eligibility = [
        EligibilityResult(
            dataset_id=1,
            decision=IngestionDecisionStatus.APPROVED,
            eligible=True,
            reason_codes=[],
            explanation="Approved",
            evidence_verification_status="USER_SIGNED_CLAIM_SIGNATURE_VALID",
            rights_ledger_reference={"record_id": 7},
        ),
        EligibilityResult(
            dataset_id=2,
            decision=IngestionDecisionStatus.NEEDS_REVIEW,
            eligible=False,
            reason_codes=["EVIDENCE_UNVERIFIED"],
            explanation="Evidence needs review",
            evidence_verification_status="UNVERIFIED",
            rights_ledger_reference=None,
        ),
    ]
    assert validate_optimizer_selection([2], eligibility, {2: 500}, 100) == [
        "Dataset 2 is not currently approved.",
        "Selected dataset costs exceed the configured budget.",
    ]


def test_optimizer_api_filters_review_datasets_persists_and_audits_result(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "optimizer.db")
    with TestClient(app) as client:
        with database.connect() as connection:
            connection.execute(
                "UPDATE datasets SET evidence_independently_verified = 1 WHERE id = 1"
            )
        extra = client.post(
            "/api/datasets",
            json={
                "name": "Synthetic Marathi Speech Sample",
                "language": "Marathi",
                "owner": "Synthetic demo contributor",
                "license_name": "Synthetic research-only license",
                "permitted_purposes": ["research_training"],
                "commercial_use": False,
                "evidence_verified": True,
                "synthetic": True,
            },
        )
        assert extra.status_code == 201
        dataset3 = extra.json()["id"]
        with database.connect() as connection:
            connection.execute(
                "UPDATE datasets SET evidence_independently_verified = 1 WHERE id = ?",
                (dataset3,),
            )

        allocation = client.post(
            "/api/costs/allocate",
            json={
                "dataset_ids": [1, dataset3],
                "purpose": "research_training",
                "context": "research",
                "budget": "200.00",
                "currency": "USD",
            },
        )
        assert allocation.status_code == 200
        assert allocation.json()["total_cost"] == "205.00"
        assert allocation.json()["remaining_budget"] == "-5.00"
        assert allocation.json()["budget_status"] == "OVER_BUDGET"
        assert all(item["synthetic_demo_cost"] for item in allocation.json()["items"])

        eligible = client.post(
            "/api/datasets/eligible",
            json={
                "purpose": "research_training",
                "context": "research",
                "dataset_ids": [1, 2, dataset3],
            },
        )
        assert eligible.status_code == 200
        assert {item["dataset_id"] for item in eligible.json()["eligible_datasets"]} == {1, dataset3}
        assert {item["dataset_id"] for item in eligible.json()["excluded_datasets"]} == {2}

        optimized = client.post(
            "/api/optimizations",
            json={
                "purpose": "research_training",
                "context": "research",
                "dataset_ids": [1, 2, dataset3],
                "budget": "125.00",
                "currency": "USD",
            },
        )
        assert optimized.status_code == 200
        result = optimized.json()
        assert result["selected_dataset_ids"] == [1]
        assert result["total_utility"] == 90
        assert result["total_licensing_cost"] == "125.00"
        assert result["remaining_budget"] == "0.00"
        assert result["validation_passed"] is True
        assert result["algorithm"] == "exhaustive_subset_search"
        assert result["optimality_guaranteed"] is True
        assert result["optimization_id"] > 0
        assert client.get(f"/api/optimizations/{result['optimization_id']}").json() == result

        events = client.get("/audit/events").json()
        assert any(event["event_type"] == "cost_allocation" for event in events)
        optimization_event = next(event for event in events if event["event_type"] == "optimization_run")
        assert optimization_event["payload"]["eligible_input_ids"] == [1, dataset3]
        assert optimization_event["payload"]["selected_ids"] == [1]
        assert optimization_event["payload"]["validation_outcome"] == "passed"
        assert client.get("/audit/verify").json()["intact"] is True


@pytest.mark.parametrize(
    ("mutation", "reason_code"),
    [
        (None, "EVIDENCE_UNVERIFIED"),
        (
            "UPDATE datasets SET expires_on = '2000-01-01', evidence_independently_verified = 1 WHERE id = 1",
            "LICENSE_EXPIRED",
        ),
        (
            "UPDATE datasets SET revoked = 1, evidence_independently_verified = 1 WHERE id = 1",
            "LICENSE_REVOKED",
        ),
    ],
)
def test_cost_allocation_rejects_datasets_that_are_not_eligible(
    tmp_path, monkeypatch, mutation, reason_code
):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "ineligible-allocation.db")
    with TestClient(app) as client:
        if mutation:
            with database.connect() as connection:
                connection.execute(mutation)

        response = client.post(
            "/api/costs/allocate",
            json={"dataset_ids": [1], "budget": "200.00"},
        )

        assert response.status_code == 409
        excluded = response.json()["detail"]["excluded_datasets"]
        assert excluded[0]["dataset_id"] == 1
        assert reason_code in excluded[0]["reason_codes"]
        assert not any(
            event["event_type"] == "cost_allocation"
            for event in client.get("/audit/events").json()
        )


def test_optimizer_zero_budget_and_no_eligible_datasets(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "empty-optimizer.db")
    with TestClient(app) as client:
        zero_budget = client.post(
            "/api/optimizations",
            json={"purpose": "research_training", "context": "research", "budget": "0.00"},
        )
        assert zero_budget.status_code == 200
        assert zero_budget.json()["selected_dataset_ids"] == []
        assert zero_budget.json()["total_licensing_cost"] == "0.00"
        assert zero_budget.json()["budget_status"] == "WITHIN_BUDGET"

        none_eligible = client.post(
            "/api/optimizations",
            json={
                "purpose": "research_training",
                "context": "research",
                "dataset_ids": [2],
                "budget": "100.00",
            },
        )
        assert none_eligible.status_code == 200
        assert none_eligible.json()["selected_dataset_ids"] == []
        assert none_eligible.json()["excluded_datasets"][0]["dataset_id"] == 2

        negative_budget = client.post(
            "/api/optimizations",
            json={"purpose": "research_training", "context": "research", "budget": "-1.00"},
        )
        assert negative_budget.status_code == 422
        forged_eligibility = client.post(
            "/api/optimizations",
            json={
                "purpose": "research_training",
                "context": "research",
                "budget": "100.00",
                "eligible": True,
            },
        )
        assert forged_eligibility.status_code == 422
        missing = client.post(
            "/api/optimizations",
            json={
                "purpose": "research_training",
                "context": "research",
                "dataset_ids": [999],
                "budget": "10.00",
            },
        )
        assert missing.status_code == 404


def test_expired_and_revoked_signed_datasets_never_enter_optimizer(tmp_path, monkeypatch):
    import base64
    from datetime import date, timedelta

    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    from backend.schemas import RightsPayload
    from backend.signatures import canonicalize_rights

    monkeypatch.setattr(database, "DB_PATH", tmp_path / "revoked-optimizer.db")
    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    payload = RightsPayload(
        dataset_id=1,
        grantor="Synthetic test grantor",
        license_name="Expired revoked synthetic grant",
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
                "signer_id": "optimizer-test-key",
                "public_key": base64.b64encode(public_key).decode(),
                "signature": base64.b64encode(signature).decode(),
            },
        )
        assert signed.status_code == 201
        result = client.post(
            "/api/optimizations",
            json={
                "purpose": "research_training",
                "context": "research",
                "dataset_ids": [1],
                "budget": "500.00",
            },
        )
        assert result.status_code == 200
        assert result.json()["selected_dataset_ids"] == []
        excluded = result.json()["excluded_datasets"][0]
        assert excluded["dataset_id"] == 1
        assert "LICENSE_EXPIRED" in excluded["reason_codes"]
        assert "LICENSE_REVOKED" in excluded["reason_codes"]
