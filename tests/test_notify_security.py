"""Notifier sanitization, URL/topic validation, secret redaction."""



def test_sanitize_redacts_secrets(modules):
    n = modules["notify"]
    dirty = "Authorization: Bearer sk-abcdefghijklmnopqrstuvwxyz password=hunter2"
    clean = n._sanitize_text(dirty)
    assert "sk-[REDACTED]" in clean or "REDACTED" in clean
    assert "hunter2" not in clean
    assert "password=[REDACTED]" in clean.lower() or "REDACTED" in clean


def test_webhook_url_validation(modules):
    n = modules["notify"]
    assert n._validate_webhook_url("https://example.com/hook")
    assert n._validate_webhook_url("http://127.0.0.1:9000/x")
    assert not n._validate_webhook_url("javascript:alert(1)")
    assert not n._validate_webhook_url("https://user:pass@evil.com/x")
    assert not n._validate_webhook_url("file:///etc/passwd")
    assert not n._validate_webhook_url("")


def test_ntfy_topic_validation(modules):
    n = modules["notify"]
    assert n._validate_ntfy_topic("excubitor-test-abc")
    assert not n._validate_ntfy_topic("../etc/passwd")
    assert not n._validate_ntfy_topic("a/b")
    assert not n._validate_ntfy_topic("")


def test_send_alert_log_mode_no_network(modules, monkeypatch):
    n = modules["notify"]
    called = []
    monkeypatch.setattr(n.urllib.request, "urlopen", lambda *a, **k: called.append(1))
    ok = n.send_alert({"notifier": "log"}, title="t", body="b api_key=SECRET123")
    assert ok
    assert called == []


def test_send_alert_rejects_bad_webhook(modules, monkeypatch):
    n = modules["notify"]
    called = []
    monkeypatch.setattr(n, "_webhook", lambda *a, **k: called.append(1) or True)
    ok = n.send_alert(
        {"notifier": "webhook", "webhook_url": "javascript:x"},
        title="t",
        body="b",
    )
    assert ok
    assert called == []


def test_send_alert_strips_dirty_payload_keys(modules, monkeypatch):
    n = modules["notify"]
    captured = {}

    def fake_webhook(url, payload):
        captured.update(payload)
        return True

    monkeypatch.setattr(n, "_webhook", fake_webhook)
    n.send_alert(
        {"notifier": "webhook", "webhook_url": "https://example.com/h"},
        title="hang",
        body="reason",
        payload={"user_message": "secret prompt", "args": {"cmd": "rm"}, "turn_id": "t1"},
    )
    assert "user_message" not in captured
    assert "args" not in captured
    assert captured.get("turn_id") == "t1"
