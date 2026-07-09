"""Tests for the LRCLIB publish proof-of-work solver."""

import hashlib

import pytest

from lyrarr.metadata.lyrics.lrclib_publish import LrclibPublishError, solve_challenge


def test_trivial_target_accepts_first_nonce():
    # Target of all FF accepts any hash.
    nonce = solve_challenge('prefix', 'FF' * 32)
    assert nonce == '0'


def test_solved_nonce_satisfies_target():
    # First byte must be zero — needs a few hundred attempts on average.
    target_hex = '00' + 'FF' * 31
    prefix = 'AbC123'
    nonce = solve_challenge(prefix, target_hex, max_attempts=1_000_000)
    digest = hashlib.sha256(f'{prefix}{nonce}'.encode()).digest()
    assert int.from_bytes(digest, 'big') <= int.from_bytes(bytes.fromhex(target_hex), 'big')


def test_lowercase_target_accepted():
    nonce = solve_challenge('prefix', 'ff' * 32)
    assert nonce == '0'


def test_unsolvable_target_raises():
    with pytest.raises(LrclibPublishError):
        solve_challenge('prefix', '00' * 32, max_attempts=10)
