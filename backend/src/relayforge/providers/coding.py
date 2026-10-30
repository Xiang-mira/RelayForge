"""Host-only coding interface. Providers propose text; the runtime owns every tool."""

import json
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from relayforge.providers.mock_coding import MockCodingProvider


class CodingProvider(Protocol):
    name: str

    def plan(self, template: str, source: str) -> str: ...

    def patch(self, template: str, source: str) -> str: ...


class ProviderUnavailable(RuntimeError):
    pass


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # Credentials and source must never follow another endpoint.


class RemoteJsonProvider:
    """Optional user-owned adapter endpoint; not a vendor API implementation."""

    name = "remote_json"

    def __init__(self, settings):
        self.key = settings.model_api_key.get_secret_value()
        self.model = settings.model_name
        self.endpoint = (settings.model_base_url or "").rstrip("/") + "/coding"
        parsed = urlsplit(self.endpoint)
        if (
            not self.model
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or not parsed.hostname
            or not (
                parsed.scheme == "https"
                or (parsed.scheme == "http" and parsed.hostname == "127.0.0.1")
            )
        ):
            raise ProviderUnavailable(
                "Remote provider requires a configured HTTPS or loopback endpoint and model"
            )
        self.opener = build_opener(NoRedirect())

    def call(self, operation, template, source):
        body = json.dumps(
            {"model": self.model, "operation": operation, "template": template, "source": source}
        ).encode("utf-8")
        if len(body) > 128 * 1024:
            raise ProviderUnavailable("Provider input exceeds the controlled size limit")
        headers = {"Content-Type": "application/json"}
        if self.key:
            headers["Authorization"] = "Bearer " + self.key
        request = Request(self.endpoint, data=body, headers=headers, method="POST")
        try:
            with self.opener.open(request, timeout=30) as response:
                raw = response.read(64 * 1024 + 1)
            if len(raw) > 64 * 1024:
                raise ValueError("Oversized response")
            result = json.loads(raw)
            if (
                not isinstance(result, dict)
                or set(result) != {"text"}
                or not isinstance(result["text"], str)
                or not result["text"].strip()
                or (self.key and self.key in result["text"])
            ):
                raise ValueError("Invalid response")
            return result["text"]
        except (OSError, HTTPError, URLError, ValueError):
            raise ProviderUnavailable(
                "Configured provider failed; no tool was authorized by its response"
            ) from None

    def plan(self, template: str, source: str) -> str:
        return self.call("plan", template, source)

    def patch(self, template: str, source: str) -> str:
        return self.call("patch", template, source)


def coding_provider(settings) -> CodingProvider:
    return (
        MockCodingProvider() if settings.model_provider == "mock" else RemoteJsonProvider(settings)
    )
