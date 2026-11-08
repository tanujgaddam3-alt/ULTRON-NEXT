from __future__ import annotations

import socket

import pytest

from ultron.application.content.provider_network import pinned_https_get


def test_pinned_https_uses_validated_ip_tls_sni_and_ignores_proxy_env(
    monkeypatch,
):
    observed = {}

    class RawSocket:
        def __init__(self, family, kind, protocol):
            observed["socket_args"] = (family, kind, protocol)

        def settimeout(self, value):
            observed["timeout"] = value

        def connect(self, address):
            observed["address"] = address

        def close(self):
            observed["raw_closed"] = True

    class TLSContext:
        def wrap_socket(self, raw, *, server_hostname):
            observed["server_hostname"] = server_hostname
            return raw

    class Response:
        status = 200
        headers = {}

        def read(self, size=-1):
            return b"ok"

        def close(self):
            pass

    class Connection:
        def __init__(self, hostname, port, *, timeout, context):
            observed["connection"] = (hostname, port, timeout, context)

        def request(self, method, target, *, headers):
            observed["request"] = (method, target, headers)

        def getresponse(self):
            return Response()

        def close(self):
            observed["connection_closed"] = True

    monkeypatch.setattr("socket.socket", RawSocket)
    monkeypatch.setattr(
        "ultron.application.content.provider_network.ssl.create_default_context",
        lambda: TLSContext(),
    )
    monkeypatch.setattr(
        "ultron.application.content.provider_network.http.client.HTTPSConnection",
        Connection,
    )
    monkeypatch.setattr(
        "ultron.application.content.sources.PublicFeedFetcher._public_addresses",
        classmethod(
            lambda cls, host, port, *, timeout: [
                (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("93.184.216.34", port))
            ]
        ),
    )
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("https_proxy", "http://127.0.0.1:2")

    with pinned_https_get(
        "https://images-api.nasa.gov/search?q=test",
        allowed_hosts=("nasa.gov",),
        user_agent="ULTRON-test",
        timeout=4,
    ) as response:
        assert response.read() == b"ok"

    assert observed["address"] == ("93.184.216.34", 443)
    assert observed["server_hostname"] == "images-api.nasa.gov"
    assert observed["request"][0:2] == ("GET", "/search?q=test")
    assert observed["request"][2]["Host"] == "images-api.nasa.gov"
    assert observed["connection_closed"] is True


def test_pinned_https_rejects_host_before_dns(monkeypatch):
    resolver_called = False

    def resolve(*args, **kwargs):
        nonlocal resolver_called
        resolver_called = True
        return []

    monkeypatch.setattr(
        "ultron.application.content.sources.PublicFeedFetcher._public_addresses",
        classmethod(lambda cls, *args, **kwargs: resolve()),
    )

    with pytest.raises(PermissionError, match="hostname allowlist"):
        pinned_https_get(
            "https://not-nasa.example/video.mp4",
            allowed_hosts=("nasa.gov",),
            user_agent="ULTRON-test",
            timeout=4,
        )
    assert resolver_called is False
