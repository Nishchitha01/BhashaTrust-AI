from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from backend import database, optimizer
from backend.main import app
from backend.optimizer import Candidate
from backend.qpiai_opt_adapter import (
    QpiOptError,
    QpiOptResult,
    build_knapsack_model,
    build_knapsack_qubo,
    decode_solution,
    solve_with_qpiai,
)


def _request(dataset_ids=(1, 2), **overrides):
    body = {
        "purpose": "research_training",
        "context": "research",
        "dataset_ids": list(dataset_ids),
        "budget": "125.00",
        "solver": "qpiai_opt",
        "confirm_remote_job": True,
    }
    body.update(overrides)
    return body


def test_qubo_formulation_encodes_binary_knapsack():
    candidates = [
        Candidate(dataset_id=4, cost_minor_units=500, utility_score=6),
        Candidate(dataset_id=8, cost_minor_units=800, utility_score=9),
    ]
    qubo, variable_count = build_knapsack_qubo(candidates, 1000)
    assert variable_count == 12  # 2 dataset bits plus 10 binary slack bits.
    assert qubo[(0, 0)] < 0  # utility is maximized by minimizing its negative.
    assert (0, 1) in qubo
    assert decode_solution([1, 0] + [0] * 10, 2, 12) == [0]
    with pytest.raises(QpiOptError, match="unsupported solution format"):
        decode_solution([1], 2, 12)


def test_current_sdk_builds_a_binary_budget_model_without_credentials():
    candidates = [
        Candidate(dataset_id=4, cost_minor_units=500, utility_score=6),
        Candidate(dataset_id=8, cost_minor_units=800, utility_score=9),
    ]
    model, variable_names = build_knapsack_model(candidates, 1000)
    assert model.name == "BhashaTrust dataset selection"
    assert variable_names == {4: "dataset_4", 8: "dataset_8"}
    assert str(model.objective) == "6*dataset_4 + 9*dataset_8"
    assert "Sense=MAX" in str(model)
    budget = model.constraints[0]["constraint"]
    assert str(budget.lhs) == "500*dataset_4 + 800*dataset_8"
    assert budget.lhs.constant == 0
    assert budget.rhs.constant == 1000
    assert model.constraints[0]["label"] == "licensing_budget"


def test_current_sdk_platform_result_is_mapped_and_auditable(monkeypatch):
    from types import SimpleNamespace

    candidates = [
        Candidate(dataset_id=4, cost_minor_units=500, utility_score=6),
        Candidate(dataset_id=8, cost_minor_units=800, utility_score=9),
    ]
    observed = {}

    class FakeClient:
        def __init__(self, api_key):
            observed["api_key"] = api_key

    def fake_solve(model, *, mode, client):
        observed["model"] = model
        observed["mode"] = mode
        observed["client"] = client
        return SimpleNamespace(
            execution_mode="platform",
            solution={"dataset_4": 0.0, "dataset_8": 1.0},
            job_id="test-job-123",
            model_status="optimal",
        )

    monkeypatch.setattr("backend.qpiai_opt_adapter._setting", lambda name: "test-key-not-real")
    monkeypatch.setattr(
        "backend.qpiai_opt_adapter._load_sdk",
        lambda: (
            __import__("qpiai_opt.opt_modeller", fromlist=["Binary"]).Binary,
            __import__("qpiai_opt.opt_modeller", fromlist=["Model"]).Model,
            FakeClient,
            fake_solve,
        ),
    )

    result = solve_with_qpiai(candidates, 1000)
    assert result == QpiOptResult(selected_indexes=[1], job_id="test-job-123", status="optimal")
    assert observed["mode"] == "platform"
    assert observed["api_key"] == "test-key-not-real"


def test_current_sdk_rejects_malformed_and_non_platform_results(monkeypatch):
    from types import SimpleNamespace

    candidates = [Candidate(dataset_id=4, cost_minor_units=500, utility_score=6)]
    monkeypatch.setattr("backend.qpiai_opt_adapter._setting", lambda name: "test-key-not-real")
    monkeypatch.setattr(
        "backend.qpiai_opt_adapter._load_sdk",
        lambda: (
            __import__("qpiai_opt.opt_modeller", fromlist=["Binary"]).Binary,
            __import__("qpiai_opt.opt_modeller", fromlist=["Model"]).Model,
            lambda **kwargs: object(),
            lambda *args, **kwargs: SimpleNamespace(
                execution_mode="local",
                solution={"dataset_4": 1},
            ),
        ),
    )
    with pytest.raises(QpiOptError, match="unexpected execution mode"):
        solve_with_qpiai(candidates, 1000)

    monkeypatch.setattr(
        "backend.qpiai_opt_adapter._load_sdk",
        lambda: (
            __import__("qpiai_opt.opt_modeller", fromlist=["Binary"]).Binary,
            __import__("qpiai_opt.opt_modeller", fromlist=["Model"]).Model,
            lambda **kwargs: object(),
            lambda *args, **kwargs: SimpleNamespace(
                execution_mode="platform",
                solution={"dataset_4": 2},
            ),
        ),
    )
    with pytest.raises(QpiOptError, match="non-binary"):
        solve_with_qpiai(candidates, 1000)


def test_qpiai_request_passes_only_currently_eligible_candidates(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "qpiai-eligible.db")
    observed = {}

    def fake_qpiai(candidates, budget):
        observed["ids"] = [item.dataset_id for item in candidates]
        observed["budget"] = budget
        return QpiOptResult(selected_indexes=[0], job_id="mock-job-1", status="optimal")

    monkeypatch.setattr(optimizer, "solve_with_qpiai", fake_qpiai)
    with TestClient(app) as client:
        with database.connect() as connection:
            connection.execute(
                "UPDATE datasets SET evidence_independently_verified = 1 WHERE id = 1"
            )
        response = client.post("/api/optimizations", json=_request())
        assert response.status_code == 200
        result = response.json()
        assert observed == {"ids": [1], "budget": 12500}
        assert result["selected_dataset_ids"] == [1]
        assert result["excluded_datasets"][0]["dataset_id"] == 2
        assert result["solver_used"] == "qpiai_opt"
        assert result["result_origin"] == "qpiai_opt"
        assert result["algorithm"] == "qpiai_opt_milp"
        assert result["solver_job_id"] == "mock-job-1"
        assert result["solver_status"] == "optimal"
        assert result["optimality_guaranteed"] is False
        assert result["validation_passed"] is True
        event = next(
            item for item in client.get("/audit/events").json()
            if item["event_type"] == "optimization_run"
        )
        assert event["payload"]["eligible_input_ids"] == [1]
        assert event["payload"]["solver_used"] == "qpiai_opt"
        assert event["payload"]["solver_job_id"] == "mock-job-1"
        assert event["payload"]["solver_status"] == "optimal"
        assert event["payload"]["result"]["solver_job_id"] == "mock-job-1"
        assert event["payload"]["objective"]["expression"] == "sum(q_i * x_i)"
        assert event["payload"]["objective"]["constraint"] == "sum(c_i * x_i) <= B"


def test_expired_and_revoked_license_never_reaches_qpiai(tmp_path, monkeypatch):
    import base64

    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    from backend.schemas import RightsPayload
    from backend.signatures import canonicalize_rights

    monkeypatch.setattr(database, "DB_PATH", tmp_path / "qpiai-revoked.db")
    key = Ed25519PrivateKey.generate()
    public = key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    payload = RightsPayload(
        dataset_id=1,
        grantor="Synthetic test grantor",
        license_name="Expired and revoked test grant",
        permitted_purposes=["research_training"],
        expires_on=date.today() - timedelta(days=1),
        revoked=True,
    )
    signature = key.sign(canonicalize_rights(payload))
    monkeypatch.setattr(
        optimizer,
        "solve_with_qpiai",
        lambda *args: pytest.fail("No ineligible dataset may reach QpiAI Opt."),
    )
    with TestClient(app) as client:
        signed = client.post(
            "/datasets/1/rights-records",
            json={
                "payload": payload.model_dump(mode="json"),
                "signer_id": "qpiai-test-key",
                "public_key": base64.b64encode(public).decode(),
                "signature": base64.b64encode(signature).decode(),
            },
        )
        assert signed.status_code == 201
        response = client.post("/api/optimizations", json=_request(dataset_ids=(1,)))
        assert response.status_code == 200
        assert response.json()["selected_dataset_ids"] == []
        assert response.json()["solver_used"] == "local_baseline"
        assert "no QpiAI request was made" in response.json()["fallback_reason"]
        assert "LICENSE_EXPIRED" in response.json()["excluded_datasets"][0]["reason_codes"]
        assert "LICENSE_REVOKED" in response.json()["excluded_datasets"][0]["reason_codes"]


def test_qpiai_invalid_over_budget_result_is_rejected_and_audited(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "qpiai-over-budget.db")
    monkeypatch.setattr(
        optimizer,
        "solve_with_qpiai",
        lambda candidates, budget: QpiOptResult(
            selected_indexes=[0], job_id="over-budget-mock", status="optimal"
        ),
    )
    with TestClient(app) as client:
        with database.connect() as connection:
            connection.execute(
                "UPDATE datasets SET evidence_independently_verified = 1 WHERE id = 1"
            )
        response = client.post("/api/optimizations", json=_request(dataset_ids=(1,), budget="0.00"))
        assert response.status_code == 409
        event = next(
            item for item in client.get("/audit/events").json()
            if item["event_type"] == "optimization_rejected"
        )
        assert event["payload"]["solver_used"] == "qpiai_opt"
        assert event["payload"]["validation_outcome"] == "rejected"
        assert "Selected dataset costs exceed the configured budget." in event["payload"]["validation_errors"]


def test_missing_credentials_and_auth_failures_use_explicit_local_fallback(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "qpiai-fallback.db")
    monkeypatch.setattr("backend.qpiai_opt_adapter._setting", lambda name: "")
    with pytest.raises(QpiOptError, match="credentials are not configured"):
        solve_with_qpiai([Candidate(dataset_id=1, cost_minor_units=12500, utility_score=90)], 12500)

    monkeypatch.setattr(
        optimizer,
        "solve_with_qpiai",
        lambda candidates, budget: (_ for _ in ()).throw(QpiOptError("QpiAI authentication failed.")),
    )
    with TestClient(app) as client:
        with database.connect() as connection:
            connection.execute(
                "UPDATE datasets SET evidence_independently_verified = 1 WHERE id = 1"
            )
        response = client.post("/api/optimizations", json=_request(dataset_ids=(1,)))
        assert response.status_code == 200
        result = response.json()
        assert result["selected_dataset_ids"] == [1]
        assert result["requested_solver"] == "qpiai_opt"
        assert result["solver_used"] == "local_baseline"
        assert result["result_origin"] == "local_baseline"
        assert result["execution_status"] == "fallback"
        assert result["fallback_reason"] == "QpiAI authentication failed."


def test_local_baseline_remains_independent_of_qpiai_availability(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "qpiai-local.db")
    monkeypatch.setattr(
        optimizer,
        "solve_with_qpiai",
        lambda *args: (_ for _ in ()).throw(AssertionError("QpiAI must not be called")),
    )
    with TestClient(app) as client:
        response = client.post(
            "/api/optimizations",
            json={
                "purpose": "research_training",
                "context": "research",
                "dataset_ids": [1],
                "budget": "125.00",
                "solver": "local",
            },
        )
        assert response.status_code == 200
        assert response.json()["solver_used"] == "local_baseline"
        assert response.json()["execution_status"] == "completed"


def test_qpiai_request_without_per_request_confirmation_does_not_submit(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "qpiai-unconfirmed.db")
    monkeypatch.setattr(
        optimizer,
        "solve_with_qpiai",
        lambda *args: (_ for _ in ()).throw(AssertionError("remote execution must be gated")),
    )
    with TestClient(app) as client:
        with database.connect() as connection:
            connection.execute(
                "UPDATE datasets SET evidence_independently_verified = 1 WHERE id = 1"
            )
        response = client.post(
            "/api/optimizations",
            json=_request(dataset_ids=(1,), confirm_remote_job=False),
        )
        assert response.status_code == 200
        result = response.json()
        assert result["solver_used"] == "local_baseline"
        assert result["execution_status"] == "fallback"
        assert "not explicitly confirmed" in result["fallback_reason"]
