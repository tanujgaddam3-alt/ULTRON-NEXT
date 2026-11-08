import pytest

from ultron.application.content.sources import PublicFeedFetcher
from ultron.domain.content_discovery import ConfiguredFeed


def _feed(**changes):
    values = {
        "feed_id": "feed",
        "feed_name": "Feed",
        "canonical_url": "https://feed.example/rss",
        "allowed_hostname": "feed.example",
        "source_category": "technology",
        "enabled": True,
    }
    values.update(changes)
    return ConfiguredFeed(**values)


def _mock_feed_response(monkeypatch, *, content_type, payload):
    calls = {}

    class RawSocket:
        def __init__(self, family, kind):
            pass

        def settimeout(self, value):
            pass

        def connect(self, address):
            calls["address"] = address

        def close(self):
            pass

    class TLSContext:
        def wrap_socket(self, raw, *, server_hostname):
            calls["server_hostname"] = server_hostname
            return raw

    class Response:
        status = 200

        def getheader(self, name, default=""):
            return content_type if name == "Content-Type" else default

        def getheaders(self):
            return [("Content-Type", content_type)]

        def read(self, limit):
            return payload

    class Connection:
        def __init__(self, host, port):
            pass

        def request(self, method, path, headers):
            calls["headers"] = headers

        def getresponse(self):
            return Response()

    monkeypatch.setattr("socket.socket", RawSocket)
    monkeypatch.setattr(
        "ultron.application.content.sources.ssl.create_default_context",
        lambda: TLSContext(),
    )
    monkeypatch.setattr(
        "ultron.application.content.sources.http.client.HTTPConnection",
        Connection,
    )
    monkeypatch.setattr(
        PublicFeedFetcher,
        "_public_addresses",
        classmethod(
            lambda cls, host, port, *, timeout=2.0: [
                (2, 1, 6, "", ("93.184.216.34", port))
            ]
        ),
    )
    return calls


def test_fetcher_requires_https_unless_feed_and_fetcher_allow_http():
    with pytest.raises(ValueError, match="explicit opt-in"):
        _feed(canonical_url="http://feed.example/rss")
    allowed = PublicFeedFetcher(allow_http=True)
    allowed._request = lambda url, feed: type("Response", (), {"status": 200, "headers": {}, "payload": b"", "url": url})()
    assert allowed.fetch(_feed(canonical_url="http://feed.example/rss", allow_http=True)).status == 200


def test_archive_advanced_search_accepts_json_content_type(monkeypatch):
    calls = _mock_feed_response(
        monkeypatch,
        content_type="application/json; charset=utf-8",
        payload=b'{"response":{"docs":[]}}',
    )
    url = "https://archive.org/advancedsearch.php?q=education&output=json"
    feed = _feed(
        feed_name="Internet Archive",
        canonical_url=url,
        allowed_hostname="archive.org",
    )

    response = PublicFeedFetcher()._request(url, feed)

    assert response.payload == b'{"response":{"docs":[]}}'
    assert calls["address"] == ("93.184.216.34", 443)
    assert calls["server_hostname"] == "archive.org"
    assert calls["headers"]["Accept"] == "application/json"


def test_non_archive_feed_rejects_json_content_type(monkeypatch):
    _mock_feed_response(
        monkeypatch,
        content_type="application/json",
        payload=b'{"response":{"docs":[]}}',
    )

    with pytest.raises(ValueError, match="unsupported content type"):
        PublicFeedFetcher()._request("https://feed.example/rss", _feed())


def test_fetcher_rejects_redirect_outside_allowlist():
    class RedirectingFetcher(PublicFeedFetcher):
        def __init__(self):
            super().__init__()
            self.calls = 0

        def _request(self, url, feed):
            self.calls += 1
            return type(
                "Response",
                (),
                {
                    "status": 302,
                    "headers": {"location": "https://other.example/rss"},
                    "payload": b"",
                    "url": url,
                },
            )()

    fetcher = RedirectingFetcher()
    with pytest.raises(PermissionError, match="allowlist"):
        fetcher.fetch(_feed())
    assert fetcher.calls == 1


def test_public_dns_validation_rejects_private_and_accepts_public(monkeypatch):
    import json
    import socket
    from types import SimpleNamespace

    def response(address):
        family = socket.AF_INET6 if ":" in address else socket.AF_INET
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps([[family, address, 443, 0, 0]]),
            stderr="",
        )

    monkeypatch.setattr(
        "ultron.application.content.sources.subprocess.run",
        lambda *args, **kwargs: response("127.0.0.1"),
    )
    with pytest.raises(PermissionError, match="non-public"):
        PublicFeedFetcher._public_addresses("feed.example", 443)

    monkeypatch.setattr(
        "ultron.application.content.sources.subprocess.run",
        lambda *args, **kwargs: response("93.184.216.34"),
    )
    assert PublicFeedFetcher._public_addresses("feed.example", 443)[0][4][0] == "93.184.216.34"


def test_request_uses_validated_address_tls_sni_and_host_header(monkeypatch):
    calls = {}

    class RawSocket:
        def __init__(self, family, kind):
            calls["family"] = family

        def settimeout(self, value):
            calls.setdefault("timeouts", []).append(value)

        def connect(self, address):
            calls["connect"] = address

        def close(self):
            calls["closed"] = True

    class TLSContext:
        def wrap_socket(self, raw, *, server_hostname):
            calls["sni"] = server_hostname
            return raw

    class Response:
        status = 304

        def getheader(self, name, default=""):
            return ""

        def getheaders(self):
            return []

        def read(self, limit):
            return b""

    class Connection:
        sock = None

        def __init__(self, host, port):
            calls["connection_host"] = (host, port)

        def request(self, method, path, headers):
            calls["request"] = (method, path, headers)

        def getresponse(self):
            return Response()

    monkeypatch.setattr(
        "socket.socket",
        RawSocket,
    )
    monkeypatch.setattr(
        "ultron.application.content.sources.ssl.create_default_context",
        lambda: TLSContext(),
    )
    monkeypatch.setattr(
        "ultron.application.content.sources.http.client.HTTPConnection",
        Connection,
    )
    monkeypatch.setattr(
        PublicFeedFetcher,
        "_public_addresses",
        classmethod(
            lambda cls, host, port, *, timeout=2.0: [
                (2, 1, 6, "", ("93.184.216.34", port))
            ]
        ),
    )
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:9")

    response = PublicFeedFetcher()._request("https://feed.example/rss", _feed())

    assert response.status == 304
    assert calls["connect"] == ("93.184.216.34", 443)
    assert calls["sni"] == "feed.example"
    assert calls["connection_host"] == ("feed.example", 443)
    assert calls["request"][0:2] == ("GET", "/rss")
    assert calls["request"][2]["Host"] == "feed.example"


def test_request_falls_back_only_across_validated_public_addresses(monkeypatch):
    attempts = []

    class RawSocket:
        def __init__(self, family, kind):
            self.address = None

        def settimeout(self, value):
            pass

        def connect(self, address):
            self.address = address
            attempts.append(address)
            if address[0] == "93.184.216.34":
                raise OSError("first address unavailable")

        def close(self):
            pass

    class Response:
        status = 304

        def getheader(self, name, default=""):
            return ""

        def getheaders(self):
            return []

        def read(self, limit):
            return b""

    class Connection:
        sock = None

        def __init__(self, host, port):
            pass

        def request(self, method, path, headers):
            pass

        def getresponse(self):
            return Response()

    monkeypatch.setattr("socket.socket", RawSocket)
    monkeypatch.setattr(
        "ultron.application.content.sources.http.client.HTTPConnection",
        Connection,
    )
    monkeypatch.setattr(
        PublicFeedFetcher,
        "_public_addresses",
        classmethod(
            lambda cls, host, port, *, timeout=2.0: [
                (2, 1, 6, "", ("93.184.216.34", port)),
                (2, 1, 6, "", ("93.184.216.35", port)),
            ]
        ),
    )

    assert PublicFeedFetcher()._request("http://feed.example/rss", _feed(allow_http=True)).status == 304
    assert attempts == [("93.184.216.34", 80), ("93.184.216.35", 80)]
