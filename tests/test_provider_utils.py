"""Tests for provider transient-error tracking and rate-limit helpers."""

import time

from lyrarr.metadata.provider_utils import (
    ProviderRateLimiter,
    ProviderTransientError,
    begin_search,
    is_transient_status,
    note_transient_error,
    search_had_transient_error,
)


class TestRateLimiter:
    def test_first_call_per_provider_does_not_block(self):
        rl = ProviderRateLimiter()
        start = time.monotonic()
        # First call to several distinct providers must not sleep — distinct
        # providers never block each other.
        rl.wait('lrclib')
        rl.wait('deezer')
        rl.wait('genius')
        assert time.monotonic() - start < 0.1

    def test_reserves_future_slot_for_same_provider(self):
        rl = ProviderRateLimiter()
        rl.wait('musicbrainz')
        # The next permitted time is pushed into the future by the provider rate,
        # so a subsequent call would be spaced out.
        assert rl._next_allowed['musicbrainz'] > time.monotonic()

    def test_unknown_provider_uses_default_rate(self):
        rl = ProviderRateLimiter()
        rl.wait('does-not-exist')  # falls back to 1.0s rate, first call doesn't sleep
        assert rl._next_allowed['does-not-exist'] > time.monotonic()


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
