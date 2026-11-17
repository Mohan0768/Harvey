"""The Google Cloud brain: the one path that needs no interactive sign-in.

Harvey's default brain shells out to the Claude CLI, which needs a login a
server cannot perform. When that login lapses every generated email silently
falls back to template-only wording, which is exactly the failure this
provider exists to remove. These tests pin the behaviour that makes it safe
to leave running: a configuration mistake stops immediately and says why, a
network blip is retried, and a draft is never lost to bookkeeping.
"""

import sys
import tempfile
import types

import pytest
import pytest_asyncio

import harvey.brain as brain_module
from harvey.brain import (GOOGLE_CLOUD_MODEL, Brain, BrainUnavailable,
                          PROVIDER_LABELS)
from harvey.config import BRAIN_PROVIDERS, BrainConfig
from harvey.state import StateManager


class FakeUsage:
    def __init__(self):
        self.input_tokens = 120
        self.output_tokens = 45
        self.cache_read_input_tokens = 0
        self.cache_creation_input_tokens = 0


class FakeBlock:
    def __init__(self, text):
        self.type = "text"
        self.text = text


class FakeResponse:
    def __init__(self, text):
        self.content = [FakeBlock(text)]
        self.usage = FakeUsage()


class FakeMessages:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        outcome = self.outcomes.pop(0) if self.outcomes else FakeResponse("ok")
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class FakeClient:
    last = None

    def __init__(self, outcomes):
        self.messages = FakeMessages(outcomes)
        FakeClient.last = self


def install_fake_anthropic(monkeypatch, outcomes, available=True):
    """Stand in for the anthropic package, installed or not."""
    module = types.ModuleType("anthropic")
    shared = FakeClient(outcomes)
    if available:
        def factory(**kwargs):
            shared.init_kwargs = kwargs
            return shared
        module.AnthropicGoogleCloud = factory
    monkeypatch.setitem(sys.modules, "anthropic", module)
    return shared


@pytest_asyncio.fixture
async def brain(monkeypatch):
    # A configured brain. The tests that care about missing ids clear these
    # themselves; everything else is about what happens once a call goes out.
    monkeypatch.setenv("ANTHROPIC_GOOGLE_CLOUD_PROJECT", "test-project")
    monkeypatch.setenv("ANTHROPIC_GOOGLE_CLOUD_WORKSPACE_ID", "wrkspc_test")
    # Google credentials are a property of the machine, not of this code.
    monkeypatch.setattr(brain_module, "_google_credentials_problem",
                        lambda: "")
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        state = StateManager(tmp + "/t.db")
        await state.init_db()
        yield Brain(state, BrainConfig(provider="google_cloud"))


# ── wiring ──

def test_google_cloud_is_a_supported_provider():
    assert "google_cloud" in BRAIN_PROVIDERS
    assert BrainConfig(provider="google_cloud").provider == "google_cloud"
    assert PROVIDER_LABELS["google_cloud"]


def test_the_default_model_is_the_current_opus():
    assert GOOGLE_CLOUD_MODEL == "claude-opus-5-5"


# ── the happy path ──

@pytest.mark.asyncio
async def test_a_call_returns_the_text(brain, monkeypatch):
    client = install_fake_anthropic(monkeypatch, [FakeResponse("  Hello.  ")])
    assert await brain.think("hi") == "Hello."
    assert client.messages.calls[0]["model"] == GOOGLE_CLOUD_MODEL


@pytest.mark.asyncio
async def test_a_configured_model_wins_over_the_default(monkeypatch):
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        state = StateManager(tmp + "/t.db")
        await state.init_db()
        monkeypatch.setenv("ANTHROPIC_GOOGLE_CLOUD_PROJECT", "test-project")
        monkeypatch.setenv("ANTHROPIC_GOOGLE_CLOUD_WORKSPACE_ID", "wrkspc_test")
        monkeypatch.setattr(brain_module, "_google_credentials_problem",
                            lambda: "")
        b = Brain(state, BrainConfig(provider="google_cloud",
                                     model="claude-sonnet-5-5"))
        client = install_fake_anthropic(monkeypatch, [FakeResponse("hi")])
        await b.think("x")
        assert client.messages.calls[0]["model"] == "claude-sonnet-5-5"


@pytest.mark.asyncio
async def test_the_sdk_is_told_not_to_retry_underneath_us(brain, monkeypatch):
    """Two retry loops around one call multiply the wait and hide the cause."""
    client = install_fake_anthropic(monkeypatch, [FakeResponse("hi")])
    await brain.think("x", timeout=42)
    assert client.init_kwargs["max_retries"] == 0
    assert client.init_kwargs["timeout"] == 42


@pytest.mark.asyncio
async def test_json_parsing_works_through_this_provider(brain, monkeypatch):
    install_fake_anthropic(
        monkeypatch, [FakeResponse('{"subject": "s", "body": "b"}')])
    assert await brain.think_json("x") == {"subject": "s", "body": "b"}


@pytest.mark.asyncio
async def test_usage_is_recorded(brain, monkeypatch):
    install_fake_anthropic(monkeypatch, [FakeResponse("hi")])
    await brain.think("x", agent="writer", task="beta_invite")
    import sqlite3

    conn = sqlite3.connect(str(brain.state.db_path))
    conn.row_factory = sqlite3.Row
    rows = [dict(r) for r in conn.execute(
        "SELECT agent, task, input_tokens, output_tokens FROM usage_events")]
    conn.close()
    assert rows and rows[0]["agent"] == "writer"
    assert rows[0]["task"] == "beta_invite"
    assert rows[0]["output_tokens"] == 45


# ── failure behaviour ──

@pytest.mark.asyncio
async def test_a_permission_problem_stops_at_once_and_says_why(brain, monkeypatch):
    """A missing role will still be missing on the third attempt. Retrying
    buries the one sentence the operator needs."""
    client = install_fake_anthropic(monkeypatch, [
        RuntimeError("403: caller lacks roles/claude.user on the project"),
        FakeResponse("never reached"),
    ])
    assert await brain.think("x") == ""
    assert len(client.messages.calls) == 1
    status = await brain.state.get_setting("brain_status")
    assert "roles/claude.user" in status


@pytest.mark.asyncio
async def test_a_missing_workspace_id_is_treated_the_same_way(brain, monkeypatch):
    client = install_fake_anthropic(monkeypatch, [
        RuntimeError("workspace_id is required"), FakeResponse("x")])
    assert await brain.think("x") == ""
    assert len(client.messages.calls) == 1


@pytest.mark.asyncio
async def test_a_transient_failure_is_retried(brain, monkeypatch):
    client = install_fake_anthropic(monkeypatch, [
        RuntimeError("connection reset by peer"),
        FakeResponse("second time lucky"),
    ])
    assert await brain.think("x", max_retries=2) == "second time lucky"
    assert len(client.messages.calls) == 2


@pytest.mark.asyncio
async def test_giving_up_returns_empty_rather_than_raising(brain, monkeypatch):
    """Callers already handle an empty answer; an exception here would take
    the heartbeat down."""
    install_fake_anthropic(monkeypatch, [
        RuntimeError("connection reset"), RuntimeError("connection reset"),
        RuntimeError("connection reset"), RuntimeError("connection reset"),
    ])
    assert await brain.think("x", max_retries=1) == ""


@pytest.mark.asyncio
async def test_a_missing_sdk_says_what_to_install(brain, monkeypatch):
    install_fake_anthropic(monkeypatch, [], available=False)
    with pytest.raises(BrainUnavailable) as caught:
        await brain.think("x")
    assert "anthropic[google_cloud]" in str(caught.value)


@pytest.mark.asyncio
async def test_a_draft_survives_broken_bookkeeping(brain, monkeypatch):
    """Usage accounting is the least important thing in this function."""
    async def boom(*a, **k):
        raise RuntimeError("ledger is down")

    monkeypatch.setattr(brain.state, "record_usage_event", boom)
    install_fake_anthropic(monkeypatch, [FakeResponse("the draft")])
    assert await brain.think("x") == "the draft"


# ── the same path, reached with a plain API key ──
#
# Google Cloud has no API keys at all, so a user who has a key needs the
# other provider. They share one method because they differ only in how
# they prove who you are.

@pytest_asyncio.fixture
async def key_brain():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        state = StateManager(tmp + "/t.db")
        await state.init_db()
        yield Brain(state, BrainConfig(provider="anthropic"))


def install_fake_plain(monkeypatch, outcomes, available=True):
    module = types.ModuleType("anthropic")
    shared = FakeClient(outcomes)
    if available:
        def factory(**kwargs):
            shared.init_kwargs = kwargs
            return shared
        module.Anthropic = factory
    monkeypatch.setitem(sys.modules, "anthropic", module)
    return shared


def test_the_api_key_provider_is_supported():
    assert "anthropic" in BRAIN_PROVIDERS
    assert BrainConfig(provider="anthropic").provider == "anthropic"


@pytest.mark.asyncio
async def test_a_key_backed_call_returns_text(key_brain, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    client = install_fake_plain(monkeypatch, [FakeResponse("Hello.")])
    assert await key_brain.think("hi") == "Hello."
    assert client.messages.calls[0]["model"] == "claude-opus-5-5"


@pytest.mark.asyncio
async def test_a_missing_key_says_so_instead_of_failing_obscurely(
        key_brain, monkeypatch):
    """Without this the SDK raises its own error deep in a retry loop and
    the operator sees an empty draft with no reason."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    install_fake_plain(monkeypatch, [FakeResponse("never reached")])
    with pytest.raises(BrainUnavailable) as caught:
        await key_brain.think("hi")
    assert "ANTHROPIC_API_KEY" in str(caught.value)


@pytest.mark.asyncio
async def test_the_key_provider_does_not_need_the_google_cloud_client(
        key_brain, monkeypatch):
    """Installing the google_cloud extra is not a prerequisite for a user
    who simply has a key."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    module = types.ModuleType("anthropic")
    shared = FakeClient([FakeResponse("fine")])
    module.Anthropic = lambda **k: shared   # no AnthropicGoogleCloud at all
    monkeypatch.setitem(sys.modules, "anthropic", module)
    assert await key_brain.think("hi") == "fine"


# ── telling the operator what is missing ──

@pytest.mark.asyncio
@pytest.mark.parametrize("project,workspace,expected", [
    (None, None, "ANTHROPIC_GOOGLE_CLOUD_PROJECT"),
    ("proj", None, "ANTHROPIC_GOOGLE_CLOUD_WORKSPACE_ID"),
    (None, "wrkspc_x", "ANTHROPIC_GOOGLE_CLOUD_PROJECT"),
])
async def test_missing_ids_are_named_before_any_call(
        brain, monkeypatch, project, workspace, expected):
    """Without this the SDK raises from inside its own retry loop and the
    operator gets an empty draft with no idea which value is missing."""
    for var in ("ANTHROPIC_GOOGLE_CLOUD_PROJECT", "GOOGLE_CLOUD_PROJECT",
                "ANTHROPIC_GOOGLE_CLOUD_WORKSPACE_ID"):
        monkeypatch.delenv(var, raising=False)
    if project:
        monkeypatch.setenv("ANTHROPIC_GOOGLE_CLOUD_PROJECT", project)
    if workspace:
        monkeypatch.setenv("ANTHROPIC_GOOGLE_CLOUD_WORKSPACE_ID", workspace)

    monkeypatch.setattr(brain_module, "_google_credentials_problem",
                        lambda: "")
    client = install_fake_anthropic(monkeypatch, [FakeResponse("x")])
    with pytest.raises(BrainUnavailable) as caught:
        await brain.think("hi")
    assert expected in str(caught.value)
    assert client.messages.calls == [], "nothing should have been sent"


@pytest.mark.asyncio
async def test_google_cloud_project_env_var_is_accepted_too(brain, monkeypatch):
    """Google's own variable is what a GCP environment already sets."""
    monkeypatch.delenv("ANTHROPIC_GOOGLE_CLOUD_PROJECT", raising=False)
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "from-google")
    monkeypatch.setenv("ANTHROPIC_GOOGLE_CLOUD_WORKSPACE_ID", "wrkspc_x")
    monkeypatch.setattr(brain_module, "_google_credentials_problem",
                        lambda: "")
    install_fake_anthropic(monkeypatch, [FakeResponse("ok")])
    assert await brain.think("hi") == "ok"


@pytest.mark.asyncio
async def test_a_missing_google_credential_is_named(brain, monkeypatch):
    """This platform has no API key: the credential is Google's. Saying so
    is the difference between a two minute fix and an afternoon."""
    monkeypatch.setattr(
        brain_module, "_google_credentials_problem",
        lambda: "No Google credentials found. Either set "
                "GOOGLE_APPLICATION_CREDENTIALS to a service account key "
                "file with roles/claude.user, or run "
                "'gcloud auth application-default login'.")
    client = install_fake_anthropic(monkeypatch, [FakeResponse("x")])
    with pytest.raises(BrainUnavailable) as caught:
        await brain.think("hi")
    assert "GOOGLE_APPLICATION_CREDENTIALS" in str(caught.value)
    assert client.messages.calls == [], "nothing should have been sent"


def test_the_credential_check_runs_once(monkeypatch):
    """It reads disk and may reach a metadata server, so repeating it on
    every draft would be a per-email cost for a per-machine fact."""
    monkeypatch.setattr(brain_module, "_google_credentials_checked", False)
    monkeypatch.setattr(brain_module, "_google_credentials_error", None)
    calls = []

    class FakeAuth:
        @staticmethod
        def default():
            calls.append(1)
            return (object(), "proj")

    monkeypatch.setitem(sys.modules, "google.auth", FakeAuth)
    monkeypatch.setitem(sys.modules, "google", types.ModuleType("google"))
    sys.modules["google"].auth = FakeAuth

    assert brain_module._google_credentials_problem() == ""
    assert brain_module._google_credentials_problem() == ""
    assert len(calls) == 1
