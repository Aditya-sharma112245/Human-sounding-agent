"""
app/services/twilio_client.py
──────────────────────────────
Sends WhatsApp messages via Twilio.

Key behaviour
─────────────
  - Introduces a length-proportional human-like delay before sending.
    The min/max delay range is configured via MIN_TYPING_DELAY and
    MAX_TYPING_DELAY in .env (defaults: 1.0s–3.0s).
  - Adds ±15% random jitter so consecutive messages don't feel robotic.
  - Strips any trailing whitespace / newlines from the message.
"""

import asyncio
import random

from twilio.rest import Client

from app.core.config import settings
from app.core.logger import get_logger

logger = get_logger(__name__)

_client = Client(settings.twilio_account_sid, settings.twilio_auth_token)

# Simulated typing speed in characters per second.
# ~40 cps ≈ a fast but realistic WhatsApp typist.
_CHARS_PER_SECOND = 40.0


def _typing_delay(message: str) -> float:
    """
    Return a realistic typing delay based on message length.

    Min and max bounds are read from settings (MIN_TYPING_DELAY / MAX_TYPING_DELAY in .env).
    Adds a small random jitter (±15%) so consecutive messages don't feel robotic.
    """
    base = len(message) / _CHARS_PER_SECOND
    base = max(settings.min_typing_delay, min(settings.max_typing_delay, base))
    jitter = base * random.uniform(-0.15, 0.15)
    return round(base + jitter, 2)


async def send_whatsapp_message(to_phone: str, message: str) -> None:
    """
    Send a WhatsApp message to `to_phone`.

    `to_phone` should be in E.164 format, e.g. '+1XXXXXXXXXX'.
    We wrap the number in 'whatsapp:' automatically.
    """
    # Normalise the destination
    if not to_phone.startswith("whatsapp:"):
        to_phone = f"whatsapp:{to_phone}"

    clean_message = message.strip()

    # Smart delay — proportional to length, feels like real typing
    delay = _typing_delay(clean_message)
    logger.debug("[twilio] waiting %.2fs before sending (typing delay, %d chars)", delay, len(clean_message))
    await asyncio.sleep(delay)

    try:
        msg = _client.messages.create(
            from_=settings.twilio_whatsapp_from,
            to=to_phone,
            body=clean_message,
        )
        logger.info("[twilio] sent message SID=%s to %s", msg.sid, to_phone)
    except Exception as e:
        logger.error("[twilio] failed to send message to %s: %s", to_phone, e)
        logger.info("[twilio fallback] Agent's response to %s: %s", to_phone, clean_message)
        # Don't re-raise the exception. This allows testing to continue via console logs when the Twilio limits are hit.
