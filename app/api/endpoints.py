"""
app/api/endpoints.py
────────────────────
FastAPI route definitions.

Endpoints
─────────
  POST /webhook/whatsapp   – Twilio webhook (incoming messages)
  GET  /memory/{phone}     – User profile + memories (debug/admin)
  GET  /history/{phone}    – Conversation history (debug/admin)
  GET  /followups/{phone}  – All scheduled follow-ups for a user
  GET  /health             – Health check
"""

import asyncio
import re
from typing import Any

from fastapi import APIRouter, Form, HTTPException, Request, Response, BackgroundTasks
from fastapi.responses import PlainTextResponse

from app.agent import memory as mem
from app.agent.graph import run_agent
from app.services.twilio_client import send_whatsapp_message
from app.core.logger import get_logger

logger = get_logger(__name__)

router = APIRouter()


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def _normalise_phone(raw: str) -> str:
    """Strip 'whatsapp:' prefix and any spaces."""
    return raw.replace("whatsapp:", "").strip()


# ──────────────────────────────────────────────────────────────────────────────
# Webhook – incoming WhatsApp messages from Twilio
# ──────────────────────────────────────────────────────────────────────────────

async def _process_and_reply(user_phone: str, incoming_message: str) -> None:
    """Background task: run agent, persist messages, send reply."""
    try:
        # Persist the user's message first
        await mem.add_message(user_phone, "user", incoming_message)

        # Run the full LangGraph agent
        response = await run_agent(user_phone, incoming_message)

        # Persist the agent's response
        await mem.add_message(user_phone, "assistant", response)

        # Sync message count accurately from DB (avoids drift from manual incrementing)
        real_count = await mem.count_messages(user_phone)
        await mem.update_profile(user_phone, {"message_count": real_count})

        # Send via Twilio (delay already built into client)
        await send_whatsapp_message(user_phone, response)

    except Exception as e:
        logger.error("[webhook] error processing message from %s: %s", user_phone, e)


@router.post("/webhook/whatsapp", response_class=PlainTextResponse)
async def whatsapp_webhook(
    background_tasks: BackgroundTasks,
    From: str = Form(...),
    Body: str = Form(...),
):
    """
    Twilio WhatsApp webhook endpoint.
    Responds immediately with an empty 200 so Twilio doesn't retry,
    then processes the message asynchronously.
    """
    user_phone = _normalise_phone(From)
    incoming_message = Body.strip()

    if not incoming_message:
        return PlainTextResponse("", status_code=200)

    logger.info("[webhook] message from %s: %r", user_phone, incoming_message[:80])

    # Fire and forget — process in background so Twilio gets an immediate 200
    background_tasks.add_task(_process_and_reply, user_phone, incoming_message)

    # Twilio expects empty 200 (TwiML not required when replying via API)
    return PlainTextResponse("", status_code=200)


# ──────────────────────────────────────────────────────────────────────────────
# Debug / Admin endpoints
# ──────────────────────────────────────────────────────────────────────────────

@router.get("/memory/{phone}")
async def get_memory(phone: str) -> dict[str, Any]:
    """Return the user's profile and important memories."""
    user_phone = _normalise_phone(phone)
    profile = await mem.get_or_create_profile(user_phone)
    memories = await mem.get_memories(user_phone)
    return {
        "profile": profile,
        "memories": memories,
    }


@router.get("/history/{phone}")
async def get_history(phone: str, limit: int = 20) -> dict[str, Any]:
    """Return recent conversation history for a user."""
    user_phone = _normalise_phone(phone)
    messages = await mem.get_recent_messages(user_phone, limit=limit)
    return {
        "user_phone": user_phone,
        "messages": messages,
        "count": len(messages),
    }


@router.get("/followups/{phone}")
async def get_followups(phone: str) -> dict[str, Any]:
    """Return all scheduled follow-ups for a user."""
    user_phone = _normalise_phone(phone)
    followups = await mem.get_all_followups(user_phone)
    return {
        "user_phone": user_phone,
        "followups": followups,
    }


@router.get("/health")
async def health_check() -> dict[str, str]:
    return {"status": "ok"}
