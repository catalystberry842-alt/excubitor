"""Behavior regressions from the November audit: docs."""


def test_status_auth_and_config_docs_complete():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    text = (root / "README.md").read_text()
    assert "authentication" in text.lower() and "/status" in text
    for key in (
        "price_table",
        "text_dedup_seconds",
        "seen_ttl_hours",
        "turn_ttl_hours",
        "max_tool_rows_per_turn",
    ):
        assert "`" + key + "`" in text
    assert "**8 hooks**" in text
    assert "7 hooks" not in (root / "AUDIT.md").read_text()
