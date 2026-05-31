"""Tests for provider transient-error tracking and rate-limit helpers."""

from lyrarr.metadata.provider_utils import (
    ProviderTransientError,
    begin_search,
    is_transient_status,
    note_transient_error,
    search_had_transient_error,
)


class TestTransientFlag:
    def test_lifecycle(self):
        begin_search()
        assert search_had_transient_error() is False
        note_transient_error()
        assert search_had_transient_error() is True
        # A fresh search resets the flag.
        begin_search()
        assert search_had_transient_error() is False

    def test_default_before_begin(self):
        # Even without begin_search() the accessor must not raise.
        assert search_had_transient_error() in (True, False)


class TestIsTransientStatus:
    def test_rate_limit_and_server_errors_are_transient(self):
        assert is_transient_status(429)
        assert is_transient_status(500)
        assert is_transient_status(502)
        assert is_transient_status(503)

    def test_client_and_ok_are_not_transient(self):
        assert not is_transient_status(200)
        assert not is_transient_status(400)
        assert not is_transient_status(404)


def test_transient_error_is_exception():
    assert issubclass(ProviderTransientError, Exception)
