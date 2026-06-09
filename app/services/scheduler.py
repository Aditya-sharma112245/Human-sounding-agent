"""
app/services/scheduler.py
──────────────────────────
APScheduler-based background job runner.

Responsibilities
────────────────
  - Polls the `scheduled_followups` table every minute.
  - Sends any pending follow-up messages that are due.
  - Marks them as sent after delivery.

Design notes
────────────
  - Uses AsyncIOScheduler so it integrates cleanly with FastAPI's event loop.
  - The scheduler is started/stopped via FastAPI lifespan events in main.py.
"""

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.agent import memory as mem
from app.services.twilio_client import send_whatsapp_message
from app.core.logger import get_logger

logger = get_logger(__name__)

scheduler = AsyncIOScheduler()


async def _dispatch_pending_followups() -> None:
    """Check for due follow-ups and send them."""
    pending = await mem.get_pending_followups()

    if not pending:
        return

    logger.info("[scheduler] %d follow-up(s) due", len(pending))

    for followup in pending:
        followup_id = followup["id"]
        user_phone = followup["user_phone"]
        message = followup["message"]

        try:
            await send_whatsapp_message(user_phone, message)
            await mem.mark_followup_sent(followup_id)
            
            # CRITICAL FIX: Persist the message so the agent remembers asking it!
            await mem.add_message(user_phone, "assistant", message)
            
            profile = await mem.get_or_create_profile(user_phone)
            new_count = profile.get("message_count", 0) + 1
            await mem.update_profile(user_phone, {"message_count": new_count})
            
            logger.info("[scheduler] follow-up #%d sent to %s", followup_id, user_phone)
        except Exception as e:
            logger.error(
                "[scheduler] failed to send follow-up #%d to %s: %s",
                followup_id, user_phone, e,
            )


def start_scheduler() -> None:
    """Register jobs and start the scheduler. Called during app startup."""
    scheduler.add_job(
        _dispatch_pending_followups,
        trigger="interval",
        minutes=1,
        id="followup_dispatch",
        replace_existing=True,
    )
    scheduler.start()
    logger.info("[scheduler] started — polling for follow-ups every 60s")


def stop_scheduler() -> None:
    """Gracefully shut down the scheduler. Called during app shutdown."""
    if scheduler.running:
        scheduler.shutdown(wait=False)
        logger.info("[scheduler] stopped")
