import io
import json

import pytest
from pydantic import SecretStr

from relayforge.config import Settings
from relayforge.providers.coding import ProviderUnavailable, RemoteJsonProvider, coding_provider
from relayforge.providers.mock_coding import MockCodingProvider


def test_default_provider_never_initializes_network_adapter(monkeypatch):
    def forbidden(*args):
        raise AssertionError("Default mock must not initialize remote networking")

    monkeypatch.setattr("relayforge.providers.coding.build_opener", forbidden)
    assert isinstance(coding_provider(Settings(model_provider="mock")), MockCodingProvider)


def test_opt_in_adapter_sends_only_bounded_model_request_and_rejects_secret_echo():
    settings = Settings(
        model_provider="remote_json",
        model_name="user-model",
        model_base_url="http://127.0.0.1:9100",
        model_api_key=SecretStr("test-secret"),
    )
    provider = RemoteJsonProvider(settings)
    requests = []

    class Transport:
        text = "return a + b"

        def open(self, request, timeout):
            requests.append(request)
            assert timeout == 30
            return io.BytesIO(json.dumps({"text": self.text}).encode())

    transport = Transport()
    provider.opener = transport
    assert provider.patch("fix_addition", "return a - b") == "return a + b"
    request = requests[0]
    assert request.full_url == "http://127.0.0.1:9100/coding"
    assert request.headers["Authorization"] == "Bearer test-secret"
    assert json.loads(request.data) == {
        "model": "user-model",
        "operation": "patch",
        "template": "fix_addition",
        "source": "return a - b",
    }
    transport.text = "test-secret"
    with pytest.raises(ProviderUnavailable) as rejected:
        provider.patch("fix_addition", "source")
    assert "test-secret" not in str(rejected.value)
    transport.text = "x" * (64 * 1024)
    with pytest.raises(ProviderUnavailable):
        provider.plan("fix_addition", "source")


@pytest.mark.parametrize(
    "endpoint",
    [
        None,
        "http://external.example",
        "https://user:pass@example.com",
        "https://example.com?key=secret",
    ],
)
def test_remote_provider_requires_deliberate_valid_endpoint(endpoint):
    with pytest.raises(ProviderUnavailable):
        coding_provider(
            Settings(model_provider="remote_json", model_base_url=endpoint, model_name="test")
        )
