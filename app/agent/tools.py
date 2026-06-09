"""
app/agent/tools.py
──────────────────
LangChain tool definitions for the agent's tool-calling capability.

Tools
─────
  save_note      – persist a titled note to the SQLite notes table
  get_notes      – retrieve all saved notes for the user
  delete_note    – remove a note by its title
  set_reminder   – schedule a WhatsApp follow-up at a specific delay
  web_search     – run a Tavily web search and return a concise answer

Design
──────
  Each tool is a thin async wrapper around memory.py or an external API.
  Tools are stateless by themselves; they receive the user_phone through a
  closure (build_tools) so we don't pollute the tool signature with
  infrastructure details (LangChain's tool router only sees what's in the
  function signature).
"""

from datetime import datetime, timedelta, timezone
from typing import Optional

from langchain_core.tools import tool

from app.agent import memory as mem
from app.core.config import settings
from app.core.logger import get_logger

logger = get_logger(__name__)


def build_tools(user_phone: str) -> list:
    """
    Return a list of LangChain tools pre-bound to the given user_phone.

    Why a factory?
    LangChain tools must have a fixed, LLM-visible signature. By closing over
    user_phone we keep DB context out of the schema while still allowing the
    tool functions to be stateful per user.
    """

    # ──────────────────────────────────────────────────────────────────────────
    # Note tools
    # ──────────────────────────────────────────────────────────────────────────

    @tool
    async def save_note(title: str, content: str) -> str:
        """
        Save or update a personal note for the user.

        Use this when the user wants to remember something specific, like a
        password, a link, an idea, or any piece of information they name.

        Args:
            title:   Short identifier for the note (e.g. 'wifi password', 'book ideas').
            content: The actual content to store (e.g. 'MyRouter123').

        Returns:
            A confirmation string.
        """
        await mem.save_note(user_phone, title, content)
        logger.info("[tool:save_note] saved note '%s' for %s", title, user_phone)
        return f"note '{title}' saved."

    @tool
    async def get_notes(filter_title: Optional[str] = None) -> str:
        """
        Retrieve the user's saved notes.

        Use this when the user asks to recall something they stored earlier,
        or asks to see all their notes.

        Args:
            filter_title: Optional title to look up a specific note. If omitted,
                          all notes are returned.

        Returns:
            A formatted string listing the note(s), or a message if none exist.
        """
        if filter_title:
            note = await mem.get_note_by_title(user_phone, filter_title)
            if note:
                return f"[note: {note['title']}]\n{note['content']}"
            
            # If we didn't find the exact title, don't just fail. 
            # Fetch all notes as a fallback so the LLM can search through them.
            notes = await mem.get_notes(user_phone)
            if not notes:
                return f"no note found with title '{filter_title}', and no notes saved at all."
            
            lines = [f"- {n['title']}: {n['content']}" for n in notes]
            return f"couldn't find exact title '{filter_title}'. here are all saved notes:\n" + "\n".join(lines)

        notes = await mem.get_notes(user_phone)
        if not notes:
            return "no notes saved yet."

        lines = [f"- {n['title']}: {n['content']}" for n in notes]
        return "saved notes:\n" + "\n".join(lines)

    @tool
    async def delete_note(title: str) -> str:
        """
        Delete a saved note by its title.

        Use this when the user explicitly asks to remove or delete a note.

        Args:
            title: The title of the note to delete (case-insensitive).

        Returns:
            A confirmation or a message if the note was not found.
        """
        deleted = await mem.delete_note(user_phone, title)
        if deleted:
            logger.info("[tool:delete_note] deleted note '%s' for %s", title, user_phone)
            return f"note '{title}' deleted."
        return f"couldn't find a note called '{title}'."

    # ──────────────────────────────────────────────────────────────────────────
    # Reminder tool
    # ──────────────────────────────────────────────────────────────────────────

    @tool
    async def set_reminder(message: str, delay_hours: float = 0.0, absolute_time: str = "") -> str:
        """
        Set a reminder that sends the user a WhatsApp message after a delay.

        Use this when the user says things like:
          - "remind me to call mom in 2 hours"  → use delay_hours=2.0
          - "set a reminder for my meeting in 30 minutes" → use delay_hours=0.5
          - "ping me about the oven in 45 minutes" → use delay_hours=0.75
          - "remind me at 9:33am" → use absolute_time="9:33am"
          - "message me at 10pm tonight" → use absolute_time="10:00pm"
          - "remind me tomorrow at 8am" → use absolute_time="8:00am tomorrow"

        Args:
            message:       The reminder message to send (e.g. "call mom").
            delay_hours:   How many hours from now to send it. Use this for relative times
                           like "in 30 minutes" (0.5) or "in 2 hours" (2.0). Set to 0 if
                           using absolute_time instead.
            absolute_time: Optional. A human-readable time string for specific times like
                           "9:33am", "10:00pm", "8:30am tomorrow". Only used if delay_hours=0.

        Returns:
            A confirmation string with the scheduled time.
        """
        import pytz
        from app.core.config import settings as _settings

        tz = pytz.timezone(_settings.user_timezone)
        now_utc = datetime.now(timezone.utc)
        now_local = now_utc.astimezone(tz)

        send_at_utc: datetime | None = None

        # Try absolute_time first if provided and delay_hours not set
        if absolute_time and delay_hours == 0.0:
            import re as _re
            abs_str = absolute_time.strip().lower()
            tomorrow = "tomorrow" in abs_str
            abs_str = abs_str.replace("tomorrow", "").strip()

            # Parse HH:MM or H:MMam/pm formats
            time_match = _re.search(r"(\d{1,2}):(\d{2})\s*(am|pm)?", abs_str)
            if not time_match:
                # Try just hour like "9am" or "10pm"
                time_match = _re.search(r"(\d{1,2})\s*(am|pm)", abs_str)
                if time_match:
                    hour = int(time_match.group(1))
                    minute = 0
                    meridiem = time_match.group(2)
                else:
                    # Fall back to delay_hours=1 if parsing fails
                    send_at_utc = now_utc + timedelta(hours=1)
            
            if time_match and send_at_utc is None:
                if time_match.lastindex >= 3 and time_match.group(3):
                    hour = int(time_match.group(1))
                    minute = int(time_match.group(2)) if time_match.lastindex >= 2 else 0
                    meridiem = time_match.group(3)
                elif time_match.lastindex >= 2 and time_match.group(2) in ("am", "pm"):
                    hour = int(time_match.group(1))
                    minute = 0
                    meridiem = time_match.group(2)
                else:
                    hour = int(time_match.group(1))
                    minute = int(time_match.group(2))
                    meridiem = None

                if meridiem == "pm" and hour != 12:
                    hour += 12
                elif meridiem == "am" and hour == 12:
                    hour = 0

                target_local = now_local.replace(hour=hour, minute=minute, second=0, microsecond=0)
                if tomorrow:
                    target_local += timedelta(days=1)
                elif target_local <= now_local:
                    # If specified time already passed today, schedule for tomorrow
                    target_local += timedelta(days=1)

                send_at_utc = target_local.astimezone(timezone.utc)

        # Fall back to delay_hours
        if send_at_utc is None:
            if delay_hours <= 0:
                return "delay must be greater than 0, or provide an absolute_time."
            send_at_utc = now_utc + timedelta(hours=delay_hours)

        # Build the human-readable confirmation FIRST (before scheduling)
        # so that a formatting error doesn't leave a phantom scheduled reminder
        send_local = send_at_utc.astimezone(tz)
        try:
            # %I gives 12-hour with leading zero; strip it manually (Windows-safe)
            time_label = send_local.strftime("%I:%M%p").lstrip("0").lower()
        except Exception:
            time_label = send_local.strftime("%H:%M").lower()

        # Determine if it's today or tomorrow
        if send_local.date() == now_local.date():
            when_str = f"today at {time_label}"
        elif (send_local.date() - now_local.date()).days == 1:
            when_str = f"tomorrow at {time_label}"
        else:
            delay_h = (send_at_utc - now_utc).total_seconds() / 3600
            hours = int(delay_h)
            when_str = f"in {hours} hour{'s' if hours != 1 else ''}"

        # Schedule AFTER confirmation string is safely built
        await mem.schedule_followup(user_phone, message, send_at_utc)

        logger.info(
            "[tool:set_reminder] scheduled reminder for %s at %s: '%s'",
            user_phone, send_at_utc.isoformat(), message,
        )
        return f"reminder set — i'll ping you {when_str}."

    # ──────────────────────────────────────────────────────────────────────────
    # Web search tool
    # ──────────────────────────────────────────────────────────────────────────

    @tool
    async def web_search(query: str) -> str:
        """
        Search the web for up-to-date information using Tavily.

        Use this when:
          - The user asks about current events, news, or real-time data.
          - The user asks a factual question you are not certain about.
          - The user asks about prices, scores, weather, or anything time-sensitive.

        Do NOT use this for things you already know confidently.

        Args:
            query: A clear, specific search query.

        Returns:
            A concise answer from the search results.
        """
        if not settings.tavily_api_key:
            return "web search is not configured (missing TAVILY_API_KEY)."

        try:
            # Import here to avoid a hard startup failure if package is missing
            from tavily import AsyncTavilyClient  # type: ignore

            client = AsyncTavilyClient(api_key=settings.tavily_api_key)
            response = await client.search(
                query=query,
                search_depth="basic",
                max_results=3,
                include_answer=True,
            )

            # Prefer the direct answer if Tavily provides one
            answer = response.get("answer", "").strip()
            if answer:
                logger.info("[tool:web_search] query='%s' -> answer found", query)
                return answer

            # Fall back to top result snippets
            results = response.get("results", [])
            if not results:
                return "search returned no results."

            snippets = []
            for r in results[:2]:
                title = r.get("title", "")
                content = r.get("content", "")[:300]
                if content:
                    snippets.append(f"{title}: {content}")

            result_text = "\n\n".join(snippets)
            logger.info("[tool:web_search] query='%s' -> %d snippet(s)", query, len(snippets))
            return result_text if result_text else "search returned no useful content."

        except Exception as e:
            logger.error("[tool:web_search] error for query='%s': %s", query, e)
            return f"search failed: {e}"

    return [save_note, get_notes, delete_note, set_reminder, web_search]
