"""Local bridge credential; never passed as a model tool argument or result."""

import re
import secrets
from pathlib import Path

from relayforge.workspaces.manager import reject_links


def bridge_token(path: Path, *, create=False) -> str:
    reject_links(path)
    if create:
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with path.open("x", encoding="ascii") as stream:
                stream.write(secrets.token_hex(32))
            path.chmod(0o600)
        except FileExistsError:
            pass
    token = path.read_text(encoding="ascii").strip()
    if not re.fullmatch(r"[0-9a-f]{64}", token):
        raise ValueError("Local MCP bridge credential is invalid")
    return token
