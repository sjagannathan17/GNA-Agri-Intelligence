"""Tests for the nudge_composer tool."""
import json
import pytest
from unittest.mock import patch, MagicMock
from tools.nudge_composer import compose_nudge

PLANTING_PHASE = {
    "phase": "planting",
    "topic": "inoculant_confirmation",
    "template": "Confirm inoculant applied at planting.",
    "reply_options": {"1": "done", "2": "need_help", "3": "skip"},
}

BASE_FARMER = {
    "name": "Agnes Banda",
    "crop": "soy_bean",
    "zone": "IIa",
    "season_number": 2,
    "has_inoculant": True,
    "risk_score": 0.2,
    "preferred_language": "english",
}


@patch("tools.nudge_composer._get_client")
def test_compose_nudge_returns_string(mock_client):
    mock_msg = MagicMock()
    mock_msg.content = [MagicMock(text="Great job Agnes! Have you applied inoculant?\n1 Done  2 Need help  3 Skip")]
    mock_client.return_value.messages.create.return_value = mock_msg

    result = compose_nudge(BASE_FARMER, PLANTING_PHASE, days_since_planting=3)

    assert isinstance(result, str)
    assert len(result) > 0


@patch("tools.nudge_composer._get_client")
def test_first_season_farmer_note_included_in_prompt(mock_client):
    mock_msg = MagicMock()
    mock_msg.content = [MagicMock(text="Some nudge message")]
    mock_client.return_value.messages.create.return_value = mock_msg

    first_season_farmer = {**BASE_FARMER, "season_number": 1}
    compose_nudge(first_season_farmer, PLANTING_PHASE, days_since_planting=2)

    call_args = mock_client.return_value.messages.create.call_args
    prompt = call_args.kwargs["messages"][0]["content"]
    assert "first season" in prompt.lower()


@patch("tools.nudge_composer._get_client")
def test_high_risk_farmer_note_included_in_prompt(mock_client):
    mock_msg = MagicMock()
    mock_msg.content = [MagicMock(text="Some nudge")]
    mock_client.return_value.messages.create.return_value = mock_msg

    high_risk_farmer = {**BASE_FARMER, "risk_score": 0.7}
    compose_nudge(high_risk_farmer, PLANTING_PHASE, days_since_planting=5)

    call_args = mock_client.return_value.messages.create.call_args
    prompt = call_args.kwargs["messages"][0]["content"]
    assert "HIGH RISK" in prompt


@patch("tools.nudge_composer._get_client")
def test_max_tokens_keeps_replies_short(mock_client):
    mock_msg = MagicMock()
    mock_msg.content = [MagicMock(text="Short message")]
    mock_client.return_value.messages.create.return_value = mock_msg

    compose_nudge(BASE_FARMER, PLANTING_PHASE, days_since_planting=1)

    call_args = mock_client.return_value.messages.create.call_args
    assert call_args.kwargs["max_tokens"] <= 250
