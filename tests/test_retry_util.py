import ssl
import pytest
import requests
from retry_util import with_retries, TRANSPORT_ERRORS


def _noop_sleep(_):
    pass


def test_returns_value_on_first_success():
    assert with_retries(lambda: "ok", sleep=_noop_sleep) == "ok"


def test_retries_then_succeeds():
    calls = {"n": 0}

    def fn():
        calls["n"] += 1
        if calls["n"] < 3:
            raise requests.exceptions.ChunkedEncodingError("truncated body")
        return "ok"

    assert with_retries(fn, sleep=_noop_sleep) == "ok"
    assert calls["n"] == 3


def test_exhausts_and_reraises_last_error():
    def fn():
        raise ssl.SSLError("UNEXPECTED_EOF_WHILE_READING")

    with pytest.raises(ssl.SSLError):
        with_retries(fn, max_retries=2, sleep=_noop_sleep)


def test_does_not_catch_unrelated_errors():
    def fn():
        raise ValueError("not transient")

    with pytest.raises(ValueError):
        with_retries(fn, sleep=_noop_sleep)


def test_transport_errors_includes_the_observed_classes():
    from http.client import IncompleteRead
    assert IncompleteRead in TRANSPORT_ERRORS
    assert ssl.SSLError in TRANSPORT_ERRORS
    assert requests.exceptions.ChunkedEncodingError in TRANSPORT_ERRORS
