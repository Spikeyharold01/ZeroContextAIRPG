import logging

import pytest

from proxy_server.services.structured_tail import END, START, extract_tail


def test_exact_tail_is_sliced_and_safe_metadata_logged(caplog):
    secret = '{"private":"campaign secret"}'
    with caplog.at_level(logging.DEBUG):
        result = extract_tail(f"Visible\n{START}\n{secret}\n{END}", request_id="r")
    assert result.narrative == "Visible"
    assert secret not in caplog.text
    assert result.payload_hash in caplog.text


def test_secure_debug_is_explicit_and_bounded(caplog):
    secret = '{"private":"campaign secret"}'
    with caplog.at_level(logging.DEBUG):
        extract_tail(f"Visible\n{START}\n{secret}\n{END}", request_id="r",
                     secure_debug_raw_output=True, secure_debug_max_characters=12)
    assert "SENSITIVE HIDDEN MODEL STATE" in caplog.text
    assert secret not in caplog.text
    assert secret[:12] in caplog.text


def test_secure_debug_redacts_known_sensitive_fields(caplog):
    secret = '{"api_key":"never-log","database_path":"/private/game.db","safe":"ok"}'
    with caplog.at_level(logging.DEBUG):
        extract_tail(f"Visible\n{START}\n{secret}\n{END}", request_id="r", secure_debug_raw_output=True)
    assert "never-log" not in caplog.text and "/private/game.db" not in caplog.text
    assert "<redacted>" in caplog.text


@pytest.mark.parametrize("raw", ["Visible", f"Visible{START}{{}}{END}{START}{{}}{END}",
                                  f"Visible{START}{{}}", f"Visible{START}{{}}{END}trailing"])
def test_missing_multiple_or_non_tail_blocks_reject(raw):
    with pytest.raises(ValueError):
        extract_tail(raw, request_id="r")
