"""
GNA Agri-Intelligence — Entry Point
------------------------------------
Starts the WhatsApp webhook server and registers all scheduled jobs:
  - Daily 7:00 AM nudges (per active farmer)
  - Nightly 11:00 PM risk batch re-scoring
  - Weekly Monday 6:00 AM executive briefing

Usage:
    python -m venv venv && source venv/bin/activate
    pip install -r requirements.txt
    cp .env.example .env  # fill in keys
    python main.py
"""

import asyncio
import logging
import os

from dotenv import load_dotenv
load_dotenv()

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
import uvicorn

from agents.orchestrator import Orchestrator, Event
from memory.farmer_store import init_db, FarmerStore
from whatsapp.webhook import app

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)

orchestrator = Orchestrator()
farmer_store = FarmerStore()


async def job_daily_nudges():
    """Fires at 7:00 AM — send a nudge to every active farmer."""
    farmers = farmer_store.get_all_active()
    logger.info(f"Daily nudge job: {len(farmers)} farmers")
    for farmer in farmers:
        event = Event(type="SCHEDULED_NUDGE", farmer_id=farmer["farmer_id"])
        await orchestrator.handle(event)


async def job_nightly_risk_batch():
    """Fires at 11:00 PM — re-score all farmers and fire threshold alerts."""
    logger.info("Nightly risk batch starting")
    event = Event(type="RISK_BATCH")
    await orchestrator.handle(event)


async def job_weekly_exec_briefing():
    """Fires Monday 6:00 AM — generate the weekly executive report."""
    logger.info("Weekly exec briefing job starting")
    event = Event(type="EXEC_REPORT_REQ")
    await orchestrator.handle(event)


async def start():
    init_db()

    scheduler = AsyncIOScheduler()
    scheduler.add_job(job_daily_nudges, CronTrigger(hour=7, minute=0), id="daily_nudges")
    scheduler.add_job(job_nightly_risk_batch, CronTrigger(hour=23, minute=0), id="risk_batch")
    scheduler.add_job(job_weekly_exec_briefing, CronTrigger(day_of_week="mon", hour=6, minute=0), id="exec_briefing")
    scheduler.start()
    logger.info("Scheduler started — 3 jobs registered")

    port = int(os.environ.get("PORT", 8000))
    logger.info(f"Starting webhook server on port {port}")
    config = uvicorn.Config(app, host="0.0.0.0", port=port, log_level=os.environ.get("LOG_LEVEL", "info").lower())
    server = uvicorn.Server(config)
    await server.serve()


if __name__ == "__main__":
    asyncio.run(start())
