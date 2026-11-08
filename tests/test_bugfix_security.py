"""Behavior regressions from the November audit: security."""

import json
from types import SimpleNamespace

import pytest


@pytest.mark.parametrize(
    "dirty,secret",
    [
        ('{"api_key": "abc123secret"}', "abc123secret"),
        ("Authorization: Bearer xyzsecret", "xyzsecret"),
        ("'password': 'hello world'", "hello world"),
    ],
)
def test_redaction_common_formats(modules, dirty, secret):
    assert secret not in modules["notify"]._sanitize_text(dirty)


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com/h",
        "https://10.0.0.1/h",
        "https://169.254.169.254/h",
        "https://[fd00::1]/h",
    ],
)
def test_webhook_rejects_insecure_or_private_targets(modules, url):
    assert not modules["notify"]._validate_webhook_url(url)


def test_nested_payload_secrets_are_redacted(modules, monkeypatch):
    n, captured = modules["notify"], []
    monkeypatch.setattr(n, "_webhook", lambda url, payload: captured.append(payload) or True)
    n.send_alert(
        {"notifier": "webhook", "webhook_url": "https://example.com/h"},
        title="t",
        body="b",
        payload={"metadata": {"api_key": "abcsecret"}},
    )
    assert "abcsecret" not in json.dumps(captured)


def test_webhook_private_dns_and_redirects_rejected(modules, monkeypatch):
    notify = modules["notify"]
    monkeypatch.setattr(
        notify.socket, "getaddrinfo", lambda *a, **k: [(2, 1, 6, "", ("10.0.0.1", 443))]
    )
    called = []
    monkeypatch.setattr(notify.http.client, "HTTPSConnection", lambda *a, **k: called.append(a))
    assert not notify._webhook("https://public.example/h", {})
    assert not called


def test_webhook_transport_pins_dns_preserves_hostname_and_does_not_redirect(modules, monkeypatch):
    notify, captured = modules["notify"], {}
    monkeypatch.setattr(
        notify.socket, "getaddrinfo", lambda *a, **k: [(2, 1, 6, "", ("8.8.8.8", 443))]
    )
    monkeypatch.setattr(
        notify.socket, "create_connection", lambda addr, *a: captured.update(address=addr)
    )

    class FakeConnection:
        def __init__(self, host, port, **kwargs):
            captured.update(host=host, port=port)

        def request(self, method, target, **kwargs):
            captured.update(method=method, target=target)
            self._create_connection(("rebound.invalid", 443))

        def getresponse(self):
            return SimpleNamespace(status=302, read=lambda n: b"")

        def close(self):
            captured["closed"] = True

    monkeypatch.setattr(notify.http.client, "HTTPSConnection", FakeConnection)
    assert not notify._webhook("https://public.example/h?x=1", {})
    assert captured == {
        "host": "public.example",
        "port": 443,
        "address": ("8.8.8.8", 443),
        "method": "POST",
        "target": "/h?x=1",
        "closed": True,
    }


def test_webhook_private_optin_remains_https_only_except_loopback(modules):
    notify = modules["notify"]
    assert notify._validate_webhook_url("https://10.0.0.1/h", allow_private=True)
    assert not notify._validate_webhook_url("http://10.0.0.1/h", allow_private=True)
    assert notify._validate_webhook_url("http://localhost:8080/h")
