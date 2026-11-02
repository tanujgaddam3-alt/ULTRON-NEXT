import argparse
import asyncio
import json
import sys
from pathlib import Path

import websockets
from websockets.exceptions import WebSocketException

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ultron.application.runtime.local_auth_helper import (
    GATEWAY_URL,
    TOKEN_PATH,
    read_backend_token,
)


async def verify_interface_token(token: str) -> bool:
    try:
        async with websockets.connect(
            GATEWAY_URL,
            open_timeout=5,
            close_timeout=1,
            proxy=None,
        ) as websocket:
            greeting = json.loads(await asyncio.wait_for(websocket.recv(), timeout=5))
            if (
                not isinstance(greeting, dict)
                or
                greeting.get("type") != "connected"
                or greeting.get("authenticationRequired") is not True
            ):
                return False
            await websocket.send(json.dumps({
                "action": "AUTHENTICATE",
                "correlationId": "local-token-verification",
                "credential": token,
            }))
            result = json.loads(await asyncio.wait_for(websocket.recv(), timeout=5))
            return (
                isinstance(result, dict)
                and
                result.get("action") == "AUTHENTICATION_RESULT"
                and result.get("status") == "AUTHENTICATED"
            )
    except (OSError, TimeoutError, ValueError, WebSocketException):
        return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Verify a protected token file against the loopback interface gateway."
    )
    parser.add_argument(
        "--token-file",
        type=Path,
        default=TOKEN_PATH,
        help="Protected token file to test; its contents are never displayed.",
    )
    args = parser.parse_args(argv)
    try:
        token = read_backend_token(args.token_file)
        if asyncio.run(verify_interface_token(token)):
            print("PASS: gateway accepted the protected token; no channels were subscribed.")
            return 0
    except (OSError, ValueError):
        pass
    print("FAIL: gateway did not accept the protected token; no credential was displayed.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())