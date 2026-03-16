"""T039: Shared fixtures for tool-registry tests."""
import pytest


@pytest.fixture(autouse=True)
def reset_auth_context():
    """Reset ContextVar auth state before and after each test to prevent leakage."""
    from shared.auth_context import set_auth

    set_auth("", "")
    yield
    set_auth("", "")
