"""Shared fixtures for feature 002 tests."""

import pytest

from shared.auth_context import set_auth


@pytest.fixture(autouse=True)
def reset_auth_context():
    """Reset auth ContextVar state before and after every test.

    Prevents token leakage between tests that call set_auth() with real values.
    Without this, token set in one test bleeds into the next test's ContextVar.
    """
    set_auth("", "")
    yield
    set_auth("", "")
