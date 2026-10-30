"""Isolated test-owned execution server; fault knobs are never MCP tool arguments."""

import argparse
import os

import uvicorn

from relayforge.providers.mock_external import InjectedCrash, MockExternal


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--crash-after-commit", action="store_true")
    args = parser.parse_args()
    if args.crash_after_commit:
        execute = MockExternal.execute

        def crash(self, action_key, digest, idempotency_key, fault="none"):
            try:
                return execute(self, action_key, digest, idempotency_key, "after_external_before_receipt")
            except InjectedCrash:
                os._exit(86)

        MockExternal.execute = crash
    from relayforge.api.app import create_app

    uvicorn.run(create_app(), host="127.0.0.1", port=args.port, log_level="error")


if __name__ == "__main__":
    main()
