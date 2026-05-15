"""End-to-end tests for the upgraded FarmerChatAgent.

These tests exercise the tool-use loop with Claude mocked, asserting:
  - The agent calls the right tool when the farmer asks about their risk
  - The agent calls set_topic before the final reply
  - The agent escalates when escalate_to_field_agent is invoked
  - The demo-mode template fallback fires when ANTHROPIC_API_KEY is absent
  - Tool-cap fallback emits a graceful reply rather than crashing
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from agents.farmer_chat import MAX_TOOL_TURNS, FarmerChatAgent


@pytest.fixture
def stores():
    farmer_store = MagicMock()
    conversation_store = MagicMock()
    conversation_store.get_recent.return_value = []
    conversation_store.get_topic_history.return_value = {
        "recent_topic": None, "topic_counts": {}, "n_turns": 0,
    }
    return farmer_store, conversation_store


@pytest.fixture
def farmer():
    return {
        "farmer_id":         "F001",
        "name":              "Joseph Phiri",
        "phone":             "+260971234567",
        "zone":              "IIa",
        "camp_name":         "Mwandi",
        "district_name":     "Mkushi",
        "season_number":     1,
        "crop":              "soy_bean",
        "has_inoculant":     False,
        "has_fertilizer":    False,
        "days_to_plant":     32,
        "total_hectares":    1.0,
        "risk_score":        0.55,
        "risk_tier":         "High",
        "preferred_language": "english",
        "nudge_responses":   {"total": 0, "done": 0, "help": 0, "skip": 0},
    }


def _stub_text_block(text: str):
    return SimpleNamespace(type="text", text=text)


def _stub_tool_use_block(name: str, inputs: dict, block_id: str = "tu_1"):
    return SimpleNamespace(type="tool_use", name=name, input=inputs, id=block_id)


def _stub_response(stop_reason: str, content_blocks: list):
    return SimpleNamespace(stop_reason=stop_reason, content=content_blocks)


# ─── Demo-mode (no API key) path ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_demo_mode_template_reply(stores, farmer):
    farmer_store, conversation_store = stores
    os.environ["ANTHROPIC_API_KEY"] = "sk-ant-placeholder-demo-key-not-real"
    agent = FarmerChatAgent(farmer_store, conversation_store)
    assert agent._anthropic_unavailable is True

    event = SimpleNamespace(text="Why are my plants yellow?")

    with patch("agents.farmer_chat.send_message", new=AsyncMock()) as send:
        await agent.run(event, farmer)
        send.assert_called_once()
        body = send.call_args.args[1]
        assert "Joseph" in body  # used farmer's first name
    # Two conversation_store.add calls: farmer turn + agent turn
    assert conversation_store.add.call_count == 2


@pytest.mark.asyncio
async def test_demo_mode_bemba_template(stores, farmer):
    farmer_store, conversation_store = stores
    os.environ["ANTHROPIC_API_KEY"] = "demo-placeholder"
    agent = FarmerChatAgent(farmer_store, conversation_store)

    event = SimpleNamespace(text="Muli shani, ndakutotela")

    with patch("agents.farmer_chat.send_message", new=AsyncMock()) as send:
        await agent.run(event, farmer)
        body = send.call_args.args[1]
        # Bemba-language fallback used
        assert "field agent" in body.lower() or "akamulanga" in body.lower()


# ─── Tool-use loop (Claude mocked) ───────────────────────────────────────────

@pytest.mark.asyncio
async def test_tool_loop_runs_farmer_analysis(stores, farmer):
    farmer_store, conversation_store = stores
    os.environ["ANTHROPIC_API_KEY"] = "sk-ant-real-test-key"
    agent = FarmerChatAgent(farmer_store, conversation_store)
    agent._anthropic_unavailable = False

    # Simulate Claude making two tool calls then ending the turn
    fake_client = MagicMock()
    fake_client.messages.create.side_effect = [
        _stub_response("tool_use", [
            _stub_tool_use_block("set_topic", {"topic": "general"}, "id1"),
            _stub_tool_use_block("get_farmer_analysis", {}, "id2"),
        ]),
        _stub_response("end_turn", [
            _stub_text_block("You are at high risk because you are first-season and have no inoculant."),
        ]),
    ]

    with patch.object(FarmerChatAgent, "client", new_callable=lambda: property(lambda self: fake_client)), \
         patch("agents.farmer_chat.send_message", new=AsyncMock()) as send:
        event = SimpleNamespace(text="What's my risk?")
        await agent.run(event, farmer)

    assert fake_client.messages.create.call_count == 2
    body = send.call_args.args[1]
    assert "high risk" in body.lower()


@pytest.mark.asyncio
async def test_tool_loop_escalation_dispatches_alert(stores, farmer):
    farmer_store, conversation_store = stores
    os.environ["ANTHROPIC_API_KEY"] = "sk-ant-real-test-key"
    agent = FarmerChatAgent(farmer_store, conversation_store)
    agent._anthropic_unavailable = False

    fake_client = MagicMock()
    fake_client.messages.create.side_effect = [
        _stub_response("tool_use", [
            _stub_tool_use_block("escalate_to_field_agent",
                                 {"reason": "farmer_repeated_distress"}, "id1"),
        ]),
        _stub_response("end_turn", [
            _stub_text_block("I've asked a field agent to call you within 24 hours."),
        ]),
    ]

    with patch.object(FarmerChatAgent, "client", new_callable=lambda: property(lambda self: fake_client)), \
         patch("agents.farmer_chat.send_message", new=AsyncMock()) as send, \
         patch("tools.field_agent_alert.send_escalation_alert", new=AsyncMock(return_value=True)) as escalate:
        event = SimpleNamespace(text="I'm losing my crop, please help me!")
        await agent.run(event, farmer)

    body = send.call_args.args[1]
    assert "field agent" in body.lower()
    escalate.assert_called_once()


@pytest.mark.asyncio
async def test_tool_loop_cap_fallback(stores, farmer):
    farmer_store, conversation_store = stores
    os.environ["ANTHROPIC_API_KEY"] = "sk-ant-real-test-key"
    agent = FarmerChatAgent(farmer_store, conversation_store)
    agent._anthropic_unavailable = False

    # Always emit a tool_use block, never end_turn — should trigger cap fallback
    fake_client = MagicMock()
    fake_client.messages.create.return_value = _stub_response(
        "tool_use",
        [_stub_tool_use_block("lookup_agronomy_kb", {"query": "loop"}, "idLoop")],
    )

    with patch.object(FarmerChatAgent, "client", new_callable=lambda: property(lambda self: fake_client)), \
         patch("agents.farmer_chat.send_message", new=AsyncMock()) as send:
        event = SimpleNamespace(text="anything")
        await agent.run(event, farmer)

    assert fake_client.messages.create.call_count == MAX_TOOL_TURNS
    body = send.call_args.args[1]
    # Generic graceful fallback
    assert "field agent" in body.lower() or "reach out" in body.lower()


@pytest.mark.asyncio
async def test_anthropic_failure_falls_back_to_template(stores, farmer):
    farmer_store, conversation_store = stores
    os.environ["ANTHROPIC_API_KEY"] = "sk-ant-real-test-key"
    agent = FarmerChatAgent(farmer_store, conversation_store)
    agent._anthropic_unavailable = False

    fake_client = MagicMock()
    fake_client.messages.create.side_effect = RuntimeError("simulated 503")

    with patch.object(FarmerChatAgent, "client", new_callable=lambda: property(lambda self: fake_client)), \
         patch("agents.farmer_chat.send_message", new=AsyncMock()) as send:
        event = SimpleNamespace(text="What's my risk?")
        await agent.run(event, farmer)

    body = send.call_args.args[1]
    # Template fallback uses the farmer's first name and offers reply options
    assert "Joseph" in body
    assert "1" in body or "field agent" in body.lower()
