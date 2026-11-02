from ultron.security.identity.authenticator import WebSocketAuthenticator


TEST_INTERFACE_TOKEN = "T" * 43


def make_test_authenticator(
    principal: str = "test-operator",
) -> WebSocketAuthenticator:
    return WebSocketAuthenticator(
        TEST_INTERFACE_TOKEN,
        principal=principal,
    )


def authenticate_test_websocket(
    authenticator: WebSocketAuthenticator,
    websocket,
):
    session = authenticator.authenticate(websocket, TEST_INTERFACE_TOKEN)
    if session is None:
        raise AssertionError("The isolated test WebSocket did not authenticate.")
    return websocket
