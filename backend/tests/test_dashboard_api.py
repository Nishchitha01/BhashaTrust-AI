from fastapi.testclient import TestClient

from backend import database, manifests
from backend.main import app


def test_dashboard_summary_and_manifest_read_api_use_backend_data(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "dashboard.db")
    monkeypatch.setattr(manifests, "MANIFEST_ROOT", tmp_path / "manifests")

    with TestClient(app) as client:
        with database.connect() as connection:
            connection.execute(
                "UPDATE datasets SET evidence_independently_verified = 1 WHERE id = 1"
            )
        health = client.get("/health")
        summary = client.get("/api/dashboard/summary?purpose=research_training&context=research")
        latest_before = client.get("/api/manifests/latest")
        assert health.status_code == 200
        assert summary.status_code == 200
        assert summary.json()["dataset_count"] == 2
        assert summary.json()["approved_count"] == 1
        assert summary.json()["needs_review_count"] == 1
        assert summary.json()["total_licensing_cost"] == "205.00"
        assert {item["dataset_id"] for item in summary.json()["dataset_statuses"]} == {1, 2}
        assert latest_before.json()["manifests"] == {
            "accepted": None,
            "rejected": None,
            "needs_review": None,
        }

        generated = client.post(
            "/api/manifests/generate",
            json={"purpose": "research_training", "context": "research"},
        )
        latest_after = client.get("/api/manifests/latest")
        assert generated.status_code == 200
        assert latest_after.status_code == 200
        assert latest_after.json()["manifests"]["accepted"]["manifest"]["entries"][0]["dataset_id"] == 1
        assert client.get("/audit/verify").json()["intact"] is True


def test_local_vite_origin_is_allowed_by_backend_cors():
    with TestClient(app) as client:
        response = client.options(
            "/api/datasets",
            headers={
                "Origin": "http://127.0.0.1:5173",
                "Access-Control-Request-Method": "GET",
            },
        )
        assert response.status_code == 200
        assert response.headers["access-control-allow-origin"] == "http://127.0.0.1:5173"


def test_local_dashboard_can_send_reviewer_authorization_header():
    with TestClient(app) as client:
        response = client.options(
            "/api/review/queue",
            headers={
                "Origin": "http://127.0.0.1:5173",
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "authorization",
            },
        )
        assert response.status_code == 200
        assert response.headers["access-control-allow-origin"] == "http://127.0.0.1:5173"
        assert "authorization" in response.headers["access-control-allow-headers"].lower()


def test_unlisted_origin_is_not_granted_backend_cors():
    with TestClient(app) as client:
        response = client.options(
            "/api/datasets",
            headers={
                "Origin": "https://untrusted.example",
                "Access-Control-Request-Method": "GET",
            },
        )
        assert response.status_code == 400
        assert "access-control-allow-origin" not in response.headers
