"""Hashing helpers."""


def test_args_hash_stable_under_key_order(modules):
    h = modules["hashing"]
    a = h.args_hash({"b": 2, "a": 1})
    b = h.args_hash({"a": 1, "b": 2})
    assert a == b


def test_message_key_prefers_id(modules):
    h = modules["hashing"]
    assert h.message_key("Telegram", "1", "abc") == "telegram:1:abc"
    k = h.message_key("telegram", "1", None, raw="hello")
    assert k.startswith("telegram:1:h:")


def test_result_hash_truncates_large(modules):
    h = modules["hashing"]
    big = "x" * 20000
    assert len(h.result_hash(big)) == 16
