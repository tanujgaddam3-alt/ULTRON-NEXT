import json
import ipaddress
import os
import subprocess
import socket
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest
from pydantic import ValidationError

from ultron.application.content.sources import (
    FeedIngestionService,
    FetchResponse,
    PublicFeedFetcher,
    canonicalize_public_url,
)
from ultron.application.content.store import ContentStore
from ultron.domain.content_discovery import ConfiguredFeed
from ultron.domain.source_url import (
    canonicalize_source_url,
    redact_source_urls,
    validate_source_url,
)


def test_commons_dns_failure_fails_closed_without_external_resolver(monkeypatch):
    from ultron.application.content import wikimedia_commons

    monkeypatch.setattr(
        wikimedia_commons.socket,
        "getaddrinfo",
        lambda *args, **kwargs: (_ for _ in ()).throw(socket.gaierror("resolver unavailable")),
    )
    resolved = wikimedia_commons._resolve_public_addresses(
        "commons.wikimedia.org", attempts=1
    )
    assert resolved == ()


def _feed(**changes):
    values = {
        "feed_id": "security-feed",
        "feed_name": "Security feed",
        "canonical_url": "https://feed.example/rss?category=technology",
        "allowed_hostname": "feed.example",
        "source_category": "technology",
        "enabled": True,
    }
    values.update(changes)
    return ConfiguredFeed(**values)


def _dns_rows(*addresses):
    return [
        [
            socket.AF_INET6 if ":" in address else socket.AF_INET,
            address,
            443,
            0,
            0,
        ]
        for address in addresses
    ]


def _mock_dns(monkeypatch, rows, *, returncode=0):
    result = SimpleNamespace(
        returncode=returncode,
        stdout=json.dumps(rows),
        stderr="",
    )
    calls = []

    def run(*args, **kwargs):
        calls.append((args, kwargs))
        return result

    monkeypatch.setattr(
        "ultron.application.content.sources.subprocess.run",
        run,
    )
    return calls


@pytest.mark.parametrize(
    ("addresses", "expected_family"),
    [
        (("93.184.216.34",), socket.AF_INET),
        (("2606:4700:4700::1111",), socket.AF_INET6),
    ],
)
def test_dns_accepts_public_ipv4_and_ipv6(monkeypatch, addresses, expected_family):
    _mock_dns(monkeypatch, _dns_rows(*addresses))
    result = PublicFeedFetcher._public_addresses("feed.example", 443)
    assert result[0][0] == expected_family
    assert result[0][4][0] == addresses[0]


@pytest.mark.parametrize(
    "address",
    [
        "10.0.0.1",
        "127.0.0.1",
        "169.254.1.1",
        "224.0.0.1",
        "240.0.0.1",
        "0.0.0.0",
        "::",
        "::1",
        "fc00::1",
        "fe80::1",
        "ff02::1",
        "2001:db8::1",
        "::ffff:127.0.0.1",
    ],
)
def test_dns_rejects_non_public_addresses(monkeypatch, address):
    _mock_dns(monkeypatch, _dns_rows(address))
    with pytest.raises(PermissionError, match="non-public"):
        PublicFeedFetcher._public_addresses("feed.example", 443)


def test_dns_rejects_mixed_public_and_private_answers(monkeypatch):
    _mock_dns(
        monkeypatch,
        _dns_rows("93.184.216.34", "10.0.0.8"),
    )
    with pytest.raises(PermissionError, match="non-public"):
        PublicFeedFetcher._public_addresses("feed.example", 443)


@pytest.mark.parametrize(
    ("returncode", "stdout", "error"),
    [
        (2, "[]", "resolution failed"),
        (0, "[]", "no addresses"),
        (0, "not-json", "invalid results"),
    ],
)
def test_dns_failure_empty_or_malformed_results_fail_closed(
    monkeypatch,
    returncode,
    stdout,
    error,
):
    monkeypatch.setattr(
        "ultron.application.content.sources.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=returncode,
            stdout=stdout,
            stderr="",
        ),
    )
    with pytest.raises(RuntimeError, match=error):
        PublicFeedFetcher._public_addresses("feed.example", 443)


def test_dns_timeout_kills_bounded_subprocess_without_background_worker(monkeypatch):
    import subprocess

    calls = []

    def timeout(*args, **kwargs):
        calls.append(kwargs)
        raise subprocess.TimeoutExpired(args[0], kwargs["timeout"])

    monkeypatch.setattr("ultron.application.content.sources.subprocess.run", timeout)
    with pytest.raises(TimeoutError, match="timed out"):
        PublicFeedFetcher._public_addresses("feed.example", 443, timeout=0.25)
    assert calls[0]["timeout"] == 0.25


def test_dns_subprocess_is_bounded_and_does_not_inherit_proxy_environment(monkeypatch):
    calls = _mock_dns(monkeypatch, _dns_rows("93.184.216.34"))
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:8080")
    PublicFeedFetcher._public_addresses("feed.example", 443, timeout=0.4)
    kwargs = calls[0][1]
    assert kwargs["timeout"] == 0.4
    assert kwargs["env"] == {"PATH": os.defpath}
    assert kwargs["stdout"] is not None
    assert kwargs["stderr"] == subprocess.DEVNULL


@pytest.mark.parametrize(
    "url",
    [
        "https://user:pass@feed.example/rss",
        "https://feed.example:99999/rss",
        "https://feed.example:/rss",
        "https://localhost/rss",
        "https://x.localhost/rss",
        "https://127.0.0.1/rss",
        "https://127.0.0.1./rss",
        "https://2130706433/rss",
        "https://0x7f000001/rss",
        "https://[::1]/rss",
        "file:///etc/passwd",
        "ftp://feed.example/rss",
        "https://feed.example/rss?access_token=hidden",
        "https://feed.example/rss?key=hidden",
        "https://feed.example/rss?auth_token=hidden",
        "https://feed.example/rss#access_token=hidden",
        "/etc/passwd",
        "https://feed.example\\@attacker.example/rss",
        "https://bad host/rss",
    ],
)
def test_source_url_policy_rejects_unsafe_forms(url):
    with pytest.raises(ValueError):
        validate_source_url(
            url,
            accepted_schemes=frozenset({"http", "https"}),
            reject_sensitive_query=True,
        )


def test_url_policy_keeps_http_explicit_and_preserves_safe_query():
    url = "HTTP://Feed.Example:80/rss?category=technology&lang=en#section"
    assert validate_source_url(
        url,
        accepted_schemes=frozenset({"http", "https"}),
    ) == url
    assert canonicalize_public_url(url) == (
        "http://feed.example/rss?category=technology&lang=en"
    )
    assert canonicalize_source_url(
        "https://FEED.example:443/rss?category=technology#top"
    ) == "https://feed.example/rss?category=technology"
    assert canonicalize_source_url(
        "https://feed.example/rss?category=technology"
    ) == canonicalize_source_url(
        "HTTPS://FEED.EXAMPLE/rss?category=technology#ignored"
    )


def test_source_reference_validation_reuses_shared_https_policy():
    assert validate_source_url(
        "https://source.example/media",
        accepted_schemes=frozenset({"https"}),
    ) == "https://source.example/media"
    with pytest.raises(ValueError, match="unsupported"):
        validate_source_url(
            "http://source.example/media",
            accepted_schemes=frozenset({"https"}),
        )
    with pytest.raises(ValueError, match="fragments"):
        validate_source_url(
            "https://source.example/media#opaque-secret",
            accepted_schemes=frozenset({"https"}),
            reject_fragment=True,
        )


def test_configured_feed_rejects_credentials_and_requires_explicit_http():
    with pytest.raises(ValidationError) as error:
        _feed(canonical_url="https://feed.example/rss?api_key=secret")
    assert "secret" not in str(error.value)
    with pytest.raises(ValueError, match="explicit opt-in"):
        _feed(canonical_url="http://feed.example/rss")
    assert _feed(
        canonical_url="http://feed.example/rss?category=technology",
        allow_http=True,
    ).canonical_url.endswith("?category=technology")
    with pytest.raises(ValidationError):
        _feed(canonical_url="https://feed.example/rss#opaque-secret")


def test_store_revalidates_feed_before_persisting_credential_query(tmp_path):
    store = ContentStore(tmp_path / "revalidate-feed.db")
    unsafe = _feed().model_copy(
        update={"canonical_url": "https://feed.example/rss?token=do-not-store"}
    )
    with pytest.raises(ValueError) as error:
        store.upsert_configured_feed(unsafe)
    assert "do-not-store" not in str(error.value)
    assert "do-not-store" not in repr(error.value)
    assert store._connection.execute(
        "SELECT COUNT(*) FROM content_configured_feeds"
    ).fetchone()[0] == 0

    error_feed = _feed().model_copy(
        update={
            "last_error": (
                "fetch failed at "
                "https://feed.example/rss?api_key=do-not-store"
                "#access_token=fragment-secret"
            )
        }
    )
    stored = store.upsert_configured_feed(error_feed)
    assert "do-not-store" not in stored.last_error
    assert "fragment-secret" not in stored.last_error
    assert "#" not in stored.last_error
    assert "%3Credacted%3E" in stored.last_error
    store.close()


def test_redaction_masks_sensitive_query_and_userinfo_but_preserves_safe_values():
    message = (
        "fetch failed at https://user:pass@feed.example/rss?"
        "api_key=secret&category=technology"
    )
    redacted = redact_source_urls(message)
    assert "secret" not in redacted
    assert "user" not in redacted
    assert "pass" not in redacted
    assert "category=technology" in redacted
    assert "%3Credacted%3E" in redacted


def test_redaction_discards_url_fragments():
    redacted = redact_source_urls(
        "failed at https://feed.example/rss#access_token=fragment-secret"
    )
    assert "fragment-secret" not in redacted
    assert "#" not in redacted


def test_redaction_masks_common_credential_query_aliases():
    redacted = redact_source_urls(
        "fetch failed at https://feed.example/rss?"
        "key=key-secret&auth_token=token-secret&category=technology"
    )
    assert "key-secret" not in redacted
    assert "token-secret" not in redacted
    assert "category=technology" in redacted


class _SequenceFetcher(PublicFeedFetcher):
    def __init__(self, responses, **kwargs):
        super().__init__(**kwargs)
        self.responses = iter(responses)
        self.requested = []

    def _request(self, url, feed):
        self.requested.append(url)
        return next(self.responses)


def _response(status, *, location=None, url="https://feed.example/rss"):
    headers = {} if location is None else {"location": location}
    return FetchResponse(status, headers, b"", url)


def test_same_host_redirect_is_revalidated_and_accepted():
    fetcher = _SequenceFetcher(
        [
            _response(302, location="/next"),
            _response(200, url="https://feed.example/next"),
        ]
    )
    response = fetcher.fetch(_feed())
    assert response.status == 200
    assert fetcher.requested == [
        "https://feed.example/rss?category=technology",
        "https://feed.example/next",
    ]


@pytest.mark.parametrize(
    "location",
    [
        "https://other.example/rss",
        "https://localhost/rss",
        "https://127.0.0.1/rss",
        "https://[::1]/rss",
        "https://169.254.1.1/rss",
        "https://user:pass@feed.example/rss",
        "file:///etc/passwd",
        "ftp://feed.example/rss",
    ],
)
def test_redirect_rejects_untrusted_host_credentials_and_schemes(location):
    fetcher = _SequenceFetcher([_response(302, location=location)])
    with pytest.raises((PermissionError, ValueError)):
        fetcher.fetch(_feed())
    assert len(fetcher.requested) == 1


def test_redirect_loop_and_limit_are_rejected():
    fetcher = _SequenceFetcher(
        [_response(302, location="/rss")] * 3,
        max_redirects=2,
    )
    with pytest.raises(RuntimeError, match="redirect limit"):
        fetcher.fetch(_feed())
    assert len(fetcher.requested) == 3


def test_dns_is_resolved_and_revalidated_for_each_redirect(monkeypatch):
    resolutions = iter(
        [
            _dns_rows("93.184.216.34"),
            _dns_rows("10.0.0.4"),
        ]
    )
    calls = []
    monkeypatch.setattr(
        "ultron.application.content.sources.PublicFeedFetcher._public_addresses",
        classmethod(
            lambda cls, host, port, *, timeout=2.0: (
                calls.append((host, port, timeout)) or
                _validated_fake_addresses(next(resolutions), port)
            )
        ),
    )

    class ResolvingRedirectFetcher(PublicFeedFetcher):
        def __init__(self):
            super().__init__()
            self.responses = iter(
                [
                    _response(302, location="/next"),
                    _response(200, url="https://feed.example/next"),
                ]
            )

        def _request(self, url, feed):
            parsed = urlsplit(url)
            self._public_addresses(parsed.hostname, 443, timeout=self.dns_timeout)
            return next(self.responses)

    with pytest.raises(PermissionError, match="non-public"):
        ResolvingRedirectFetcher().fetch(_feed())
    assert len(calls) == 2


def _validated_fake_addresses(rows, port):
    result = []
    for family, address, _, flowinfo, scope_id in rows:
        parsed = ipaddress.ip_address(address)
        if not parsed.is_global:
            raise PermissionError("Feed DNS resolved to a non-public address.")
        sockaddr = (
            (address, port)
            if family == socket.AF_INET
            else (address, port, flowinfo, scope_id)
        )
        result.append((family, socket.SOCK_STREAM, 0, "", sockaddr))
    return result


def test_invalid_credential_feed_error_is_redacted_before_persistence(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("ULTRON_CRE_SOURCE_FETCH_ENABLED", "true")
    store = ContentStore(tmp_path / "source-url-security.db")
    feed = _feed()
    store.upsert_configured_feed(feed)
    class LeakingFetcher(PublicFeedFetcher):
        def fetch(self, configured):
            raise RuntimeError(
                "provider failed for https://feed.example/rss?token=do-not-persist"
            )

    with pytest.raises(RuntimeError):
        FeedIngestionService(store, fetcher=LeakingFetcher()).ingest(feed.feed_id)
    persisted = store.get_configured_feed(feed.feed_id)
    assert persisted.last_error is not None
    assert "do-not-persist" not in persisted.last_error
    assert "%3Credacted%3E" in persisted.last_error
    store.close()


def test_workflow_operator_error_status_redacts_sensitive_url_values(tmp_path):
    from ultron.application.content.workflow import ContentWorkflowRuntime

    store = ContentStore(tmp_path / "source-url-status.db")
    runtime = ContentWorkflowRuntime(
        store,
        worker_id="security-test",
        source_refresh_enabled=True,
        permissions=frozenset({"content.research"}),
    )

    def fail_schedule():
        raise RuntimeError(
            "feed failed at https://feed.example/rss?api_key=operator-secret"
        )

    runtime.feed_refresh.schedule_due = fail_schedule
    runtime._stop.wait = lambda timeout: runtime._stop.set()
    runtime._run()
    assert "operator-secret" not in runtime.status()["last_error"]
    store.close()
