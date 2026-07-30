"""Tests for the ``_cli_model_limitations_hint`` heuristic.

The helper returns an actionable hint when an openai-codex model in
chat_completions mode responds with a "CLI model limitations" explanation
— the pattern where the model describes itself as a text-only remote
agent that cannot execute code or access the filesystem.
"""

from __future__ import annotations

from pathlib import Path

import pytest


# ── Verbatim sample of the known CLI-limitations response pattern ──────────

_SAMPLE_CLI_LIMITATIONS_RESPONSE = """\
Here is why I cannot interact with your environment in these raw CLI turns:

1. Execution boundary: I am running as a remote cli model. This text prompt \
only provides me with read-only memory context (pinned status, brain \
artifacts, config). I do not have access to the host's execution engine, \
file system, or network sockets.
2. Platform limitation: Unlike a full IDE extension which can invoke your \
shell, Docker/Podman remotes, and OS files directly through an LSP/plugin, \
this CLI setup is a simple text-input/text-output state machine.\
"""


def _make_agent(tmp_path: Path, **overrides):
    from run_agent import AIAgent

    kwargs = dict(
        model="gpt-4o",
        provider="openai-codex",
        api_key="sk-dummy",
        base_url="https://chatgpt.com/backend-api/codex",
        quiet_mode=True,
        skip_context_files=True,
        skip_memory=True,
        platform="cli",
    )
    kwargs.update(overrides)
    return AIAgent(**kwargs)


@pytest.fixture(autouse=True)
def _isolate_hermes_home(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    (tmp_path / ".env").write_text("", encoding="utf-8")



# ── Positive cases: hint fires ─────────────────────────────────────────────


def test_hint_fires_for_verbatim_cli_limitations_response(tmp_path):
    """The verbatim known-pattern response triggers the hint."""
    agent = _make_agent(tmp_path)
    agent.api_mode = "chat_completions"
    hint = agent._cli_model_limitations_hint(_SAMPLE_CLI_LIMITATIONS_RESPONSE)
    assert hint is not None
    assert "/codex-runtime codex_app_server" in hint


def test_hint_fires_for_execution_boundary_plus_raw_cli(tmp_path):
    """Two signals from different categories still fire."""
    agent = _make_agent(tmp_path)
    agent.api_mode = "chat_completions"
    response = (
        "I have an execution boundary here: I cannot run commands.\n"
        "This is because these raw CLI turns provide no shell access."
    )
    hint = agent._cli_model_limitations_hint(response)
    assert hint is not None
    assert "codex_app_server" in hint


def test_hint_fires_for_execution_engine_plus_platform_limitation(tmp_path):
    """execution engine + platform limitation signals together fire the hint."""
    agent = _make_agent(tmp_path)
    agent.api_mode = "chat_completions"
    response = (
        "I do not have access to the host's execution engine or filesystem.\n"
        "This is a platform limitation of the current setup."
    )
    hint = agent._cli_model_limitations_hint(response)
    assert hint is not None


def test_hint_includes_npm_install_suggestion(tmp_path):
    """The hint tells the user how to install the codex CLI."""
    agent = _make_agent(tmp_path)
    agent.api_mode = "chat_completions"
    hint = agent._cli_model_limitations_hint(_SAMPLE_CLI_LIMITATIONS_RESPONSE)
    assert hint is not None
    assert "npm i -g @openai/codex" in hint


# ── Negative cases: hint stays None ────────────────────────────────────────


def test_hint_skipped_for_codex_app_server_mode(tmp_path):
    """When api_mode is already codex_app_server, the hint must not fire."""
    agent = _make_agent(tmp_path)
    agent.api_mode = "codex_app_server"
    assert agent._cli_model_limitations_hint(_SAMPLE_CLI_LIMITATIONS_RESPONSE) is None


def test_hint_skipped_for_codex_responses_mode(tmp_path):
    """codex_responses mode also bypasses the hint (different failure mode)."""
    agent = _make_agent(tmp_path)
    agent.api_mode = "codex_responses"
    assert agent._cli_model_limitations_hint(_SAMPLE_CLI_LIMITATIONS_RESPONSE) is None


def test_hint_skipped_for_non_codex_provider(tmp_path):
    """The hint only fires for openai-codex; other providers are not affected."""
    agent = _make_agent(tmp_path, provider="openrouter")
    agent.api_mode = "chat_completions"
    assert agent._cli_model_limitations_hint(_SAMPLE_CLI_LIMITATIONS_RESPONSE) is None


def test_hint_skipped_for_empty_response(tmp_path):
    """Empty response text must not trigger."""
    agent = _make_agent(tmp_path)
    agent.api_mode = "chat_completions"
    assert agent._cli_model_limitations_hint("") is None


def test_hint_skipped_for_none_response(tmp_path):
    """None response text must not trigger."""
    agent = _make_agent(tmp_path)
    agent.api_mode = "chat_completions"
    assert agent._cli_model_limitations_hint(None) is None


def test_hint_skipped_for_single_signal_only(tmp_path):
    """A response with only one matching phrase does not fire (avoids FP)."""
    agent = _make_agent(tmp_path)
    agent.api_mode = "chat_completions"
    # Only one signal: "raw cli" — not enough to fire
    response = "I can't run that command from raw CLI input."
    assert agent._cli_model_limitations_hint(response) is None


def test_hint_skipped_for_unrelated_refusal(tmp_path):
    """A generic "I can't do that" response does not falsely trigger."""
    agent = _make_agent(tmp_path)
    agent.api_mode = "chat_completions"
    response = "I'm sorry, I cannot help with that request as it violates policy."
    assert agent._cli_model_limitations_hint(response) is None


def test_hint_is_case_insensitive(tmp_path):
    """Detection must not depend on the capitalisation the model chooses."""
    agent = _make_agent(tmp_path)
    agent.api_mode = "chat_completions"
    response = (
        "EXECUTION BOUNDARY: I have no access to the execution engine.\n"
        "PLATFORM LIMITATION: Text-input/text-output only."
    )
    hint = agent._cli_model_limitations_hint(response)
    assert hint is not None
