"""Gmail readiness: say which step is missing, not just "not connected".

is_configured() folds three situations into one False. These pin the words
a person actually sees, because "not connected" sent one straight back to a
Settings page they had already filled in half of.
"""

import pytest

import harvey.integrations.gmail as gmail_mod
from harvey.integrations.gmail import GmailProvider


@pytest.fixture
def provider(monkeypatch):
    def build(client_id="", client_secret="", token=False):
        monkeypatch.setattr(
            gmail_mod, "_load_token",
            (lambda: {"refresh_token": "r"}) if token else (lambda: None))
        p = GmailProvider(config=None, env=None)
        p.client_id, p.client_secret = client_id, client_secret
        return p
    return build


def test_nothing_saved_points_at_save_credentials(provider):
    p = provider()
    assert p.is_configured() is False
    msg = p.readiness()
    assert "client ID" in msg and "client secret" in msg
    assert "Save credentials" in msg


def test_only_the_secret_missing_says_only_that(provider):
    msg = provider(client_id="x.apps.googleusercontent.com").readiness()
    assert "client secret" in msg
    assert "client ID" not in msg


def test_credentials_but_no_consent_points_at_connect(provider):
    msg = provider(client_id="x.apps", client_secret="shh").readiness()
    assert "never been" in msg and "Connect Gmail" in msg
    assert "Save credentials" not in msg


def test_fully_connected_is_silent(provider):
    p = provider(client_id="x.apps", client_secret="shh", token=True)
    assert p.is_configured() is True
    assert p.readiness() == ""
