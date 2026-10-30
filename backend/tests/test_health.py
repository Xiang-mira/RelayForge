import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from relayforge.api.app import create_app
from relayforge.config import Settings


class OfflineDatabase:
    def sessions(self):
        raise AssertionError("Offline routes must fail readiness before opening a session")

    def ready(self):
        return False

    def close(self):
        pass


def test_missing_database_is_visible_and_secrets_are_not_returned():
    app = create_app(
        Settings(database_url="postgresql+psycopg://user:private@localhost/db"), OfflineDatabase()
    )
    with TestClient(app, base_url="http://127.0.0.1") as client:
        response = client.get("/health")
        assert response.status_code == 503
        assert response.json()["database"] == "unreachable"
        assert "private" not in response.text
        assert client.get("/health/live").json() == {"status": "alive", "provider": "mock"}


def test_default_mock_and_loopback():
    settings = Settings(_env_file=None)
    assert settings.model_provider == "mock"
    assert settings.api_host == "127.0.0.1"


def test_unsupported_database_does_not_hide_a_backend_substitution_or_echo_secrets():
    with pytest.raises(ValidationError) as caught:
        Settings(database_url="sqlite:///secret-invalid-location")
    assert "secret-invalid-location" not in str(caught.value)


def test_offline_api_and_cross_origin_write_do_not_pretend_success():
    app = create_app(Settings(), OfflineDatabase())
    with TestClient(app, base_url="http://127.0.0.1") as client:
        assert client.get("/api/tasks").status_code == 503
        response = client.post(
            "/api/tasks", json={"title": "hello"}, headers={"origin": "https://untrusted.example"}
        )
        assert response.status_code == 403
        assert client.get("/health/live", headers={"host": "attacker.example"}).status_code == 400
