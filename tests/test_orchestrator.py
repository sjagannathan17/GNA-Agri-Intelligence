"""Tests for the Orchestrator event routing."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from agents.orchestrator import Orchestrator, Event


@pytest.fixture
def mock_stores():
    farmer_store = MagicMock()
    conversation_store = MagicMock()
    farmer_store.get_by_phone.return_value = {
        "farmer_id": "F001",
        "name": "Joseph Phiri",
        "phone": "+260971234567",
        "zone": "IIa",
        "season_number": 2,
        "crop": "soy_bean",
        "has_inoculant": True,
        "risk_score": 0.3,
    }
    return farmer_store, conversation_store


@pytest.fixture
def orchestrator(mock_stores):
    farmer_store, conversation_store = mock_stores
    with patch("agents.orchestrator.FarmerStore", return_value=farmer_store), \
         patch("agents.orchestrator.ConversationStore", return_value=conversation_store), \
         patch("agents.nudge_scheduler.NudgeSchedulerAgent") as ns, \
         patch("agents.farmer_chat.FarmerChatAgent") as fc, \
         patch("agents.risk_monitor.RiskMonitorAgent") as rm, \
         patch("agents.exec_briefing.ExecBriefingAgent") as eb:

        ns.return_value.run = AsyncMock()
        fc.return_value.run = AsyncMock()
        fc.return_value.send_confirmation = AsyncMock()
        rm.return_value.run_batch = AsyncMock()
        rm.return_value.run_realtime = AsyncMock()
        eb.return_value.run = AsyncMock()

        orch = Orchestrator()
        orch._farmer_store = farmer_store
        orch._conversation_store = conversation_store
        yield orch


@pytest.mark.asyncio
async def test_scheduled_nudge_routes_to_nudge_agent(orchestrator):
    event = Event(type="SCHEDULED_NUDGE", farmer_id="F001")
    await orchestrator.handle(event)
    orchestrator.nudge_agent.run.assert_called_once_with(event)


@pytest.mark.asyncio
async def test_risk_batch_routes_to_risk_agent(orchestrator):
    event = Event(type="RISK_BATCH")
    await orchestrator.handle(event)
    orchestrator.risk_agent.run_batch.assert_called_once()


@pytest.mark.asyncio
async def test_exec_report_routes_to_exec_agent(orchestrator):
    event = Event(type="EXEC_REPORT_REQ")
    await orchestrator.handle(event)
    orchestrator.exec_agent.run.assert_called_once_with(event)


@pytest.mark.asyncio
async def test_free_text_message_routes_to_chat_agent(orchestrator):
    event = Event(type="WHATSAPP_MESSAGE", farmer_phone="+260971234567", text="Why are my plants yellow?")
    await orchestrator.handle(event)
    orchestrator.chat_agent.run.assert_called_once()


@pytest.mark.asyncio
async def test_numbered_reply_1_logs_done(orchestrator):
    event = Event(type="WHATSAPP_MESSAGE", farmer_phone="+260971234567", text="1")
    await orchestrator.handle(event)
    orchestrator.farmer_store.log_nudge_response.assert_called_with("F001", "done")


@pytest.mark.asyncio
async def test_numbered_reply_2_triggers_risk_agent(orchestrator):
    event = Event(type="WHATSAPP_MESSAGE", farmer_phone="+260971234567", text="2")
    await orchestrator.handle(event)
    orchestrator.risk_agent.run_realtime.assert_called_once()


@pytest.mark.asyncio
async def test_unknown_phone_is_ignored(orchestrator):
    orchestrator.farmer_store.get_by_phone.return_value = None
    event = Event(type="WHATSAPP_MESSAGE", farmer_phone="+260000000000", text="hello")
    await orchestrator.handle(event)
    orchestrator.chat_agent.run.assert_not_called()
