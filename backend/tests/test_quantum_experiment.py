import pytest
from fastapi.testclient import TestClient

from backend import database
from backend.main import app
from backend.qcloud_adapter import QCloudError, submit_qcloud_circuit
from backend.quantum_experiment import (
    prepare_experiment,
    validate_experiment_selection,
)
from backend.schemas import (
    EligibilityResult,
    IngestionDecisionStatus,
    QuantumExperimentRequest,
)


def _request(**overrides):
    body = {
        "purpose": "research_training",
        "context": "research",
        "dataset_ids": [1, 2],
        "budget": "125.00",
        "shots": 128,
        "seed": 73,
    }
    body.update(overrides)
    return body


def test_quantum_request_validation_rejects_empty_or_invalid_dataset_ids():
    with pytest.raises(Exception):
        QuantumExperimentRequest(**_request(dataset_ids=[]))
    with pytest.raises(Exception):
        QuantumExperimentRequest(**_request(dataset_ids=[0]))


def test_licensing_filter_runs_before_quantum_candidates_are_built(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "quantum-filter.db")
    request = QuantumExperimentRequest(**_request())
    with TestClient(app):
        with database.connect() as connection:
            connection.execute(
                "UPDATE datasets SET evidence_independently_verified = 1 WHERE id = 1"
            )
        prepared = prepare_experiment(request)
    assert [candidate.dataset_id for candidate in prepared.candidates] == [1]
    assert {item.dataset_id for item in prepared.excluded} == {2}
    assert prepared.excluded[0].decision == IngestionDecisionStatus.NEEDS_REVIEW


def test_local_qaoa_statevector_simulation_is_seeded_and_audited(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "quantum-local.db")
    with TestClient(app) as client:
        with database.connect() as connection:
            connection.execute(
                "UPDATE datasets SET evidence_independently_verified = 1 WHERE id = 1"
            )
        first = client.post("/api/quantum-experiments", json=_request())
        second = client.post("/api/quantum-experiments", json=_request())
        assert first.status_code == 200
        assert second.status_code == 200
        result = first.json()
        assert result["sdk_name"] == "qpiai-quantum"
        assert result["sdk_version"] == "0.7.5"
        assert result["execution_mode"] == "local_statevector_simulation"
        assert result["result_kind"] == "simulation_result"
        assert result["sample_counts"] == second.json()["sample_counts"]
        assert sum(result["sample_counts"].values()) == 128
        assert result["candidate_dataset_ids"] == [1]
        assert result["baseline_selected_dataset_ids"] == [1]
        assert set(result["selected_dataset_ids"]) <= {1}
        assert result["budget_status"] == "WITHIN_BUDGET"
        assert result["validation_passed"] is True
        assert "quantum advantage" in " ".join(result["limitations"])
        assert result["audit_event_reference"] is not None
        events = client.get("/audit/events").json()
        event = next(item for item in events if item["event_type"] == "quantum_experiment")
        assert event["payload"]["execution_mode"] == "local_statevector_simulation"
        assert event["payload"]["result_kind"] == "simulation_result"
        assert client.get("/audit/verify").json()["intact"] is True


def test_independent_result_validation_rejects_ineligible_and_over_budget():
    eligible = EligibilityResult(
        dataset_id=1,
        decision=IngestionDecisionStatus.APPROVED,
        eligible=True,
        reason_codes=[],
        explanation="Approved for research training.",
        evidence_verification_status="INDEPENDENTLY_VERIFIED",
        rights_ledger_reference=None,
    )
    review = EligibilityResult(
        dataset_id=2,
        decision=IngestionDecisionStatus.NEEDS_REVIEW,
        eligible=False,
        reason_codes=["EVIDENCE_UNVERIFIED"],
        explanation="Evidence needs review.",
        evidence_verification_status="UNVERIFIED",
        rights_ledger_reference=None,
    )
    errors = validate_experiment_selection([2], [eligible, review], {2: 12_500}, 12_500)
    assert errors == ["Dataset 2 is not currently approved."]
    budget_errors = validate_experiment_selection([1], [eligible], {1: 12_500}, 0)
    assert budget_errors == ["Selected dataset costs exceed the configured budget."]


def test_qcloud_missing_credentials_fails_before_sdk_auth(monkeypatch):
    monkeypatch.setattr("backend.qcloud_adapter._read_api_key", lambda: "")
    with pytest.raises(QCloudError, match="API_KEY is not configured"):
        submit_qcloud_circuit(None, 64, "must-not-submit")


def test_qcloud_authentication_failure_is_sanitized_and_does_not_submit(monkeypatch):
    from qpiai_quantum import QpiAIQuantumAuth
    from qpiai_quantum.authentication.exceptions import APIKeyInvalidError

    monkeypatch.setattr("backend.qcloud_adapter._read_api_key", lambda: "test-key-not-a-real-secret")

    def fail_login(api_key):
        raise APIKeyInvalidError("invalid")

    monkeypatch.setattr(QpiAIQuantumAuth, "login", staticmethod(fail_login))
    with pytest.raises(QCloudError, match="QCloud authentication failed"):
        submit_qcloud_circuit(None, 64, "must-not-submit")


def test_qcloud_adapter_submits_measurement_request_and_returns_actual_result_shape(monkeypatch):
    from qpiai_quantum import QpiAIQuantumAuth

    observed = {}
    monkeypatch.setattr("backend.qcloud_adapter._read_api_key", lambda: "test-key-not-real")
    monkeypatch.setattr(QpiAIQuantumAuth, "login", staticmethod(lambda api_key: observed.update(key=api_key)))

    class FakeResult:
        job_id = "mock-qcloud-job"

        def get_counts(self):
            return {"010": 41, "101": 23}

    class FakeCircuit:
        def run(self, **kwargs):
            observed.update(kwargs)
            return FakeResult()

    counts, job_id = submit_qcloud_circuit(FakeCircuit(), 64, "synthetic selection")
    assert counts == {"010": 41, "101": 23}
    assert job_id == "mock-qcloud-job"
    assert observed["shots"] == 64
    assert observed["device_name"] == "QpiAI-QSV-Simulator"


def test_qcloud_request_requires_explicit_remote_confirmation(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "quantum-remote-gate.db")
    monkeypatch.setattr(
        "backend.quantum_experiment.submit_qcloud_circuit",
        lambda *args, **kwargs: pytest.fail("A request without confirmation must not submit a job."),
    )
    with TestClient(app) as client:
        response = client.post(
            "/api/quantum-experiments",
            json=_request(mode="qcloud", confirm_remote_job=False),
        )
        assert response.status_code == 428
        assert "no remote call was made" in response.json()["detail"]
        events = client.get("/audit/events").json()
        event = next(item for item in events if item["event_type"] == "quantum_experiment_not_started")
        assert event["payload"]["remote_job_submitted"] is False
