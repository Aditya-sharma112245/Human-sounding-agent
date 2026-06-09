"""
app/agent/nodes.py
──────────────────
LangGraph node functions.

Each node receives the current AgentState and returns a dict of updates.

Nodes
─────
  load_memory        – fetch user profile, memories, recent messages
  reasoning          – call Mistral (with tools bound), produce a draft or invoke a tool
  tool_execution     – run tool calls requested by reasoning, append results to scratchpad
  vibe_check         – evaluate and rewrite draft if it sounds robotic
  memory_update      – extract facts, update profile, save memories
  schedule_followup  – conditionally register a proactive follow-up
"""

import asyncio
import json
import re
from datetime import datetime, timedelta, timezone
from typing import TypedDict

from langchain_mistralai import ChatMistralAI
from langchain_core.messages import HumanMessage, SystemMessage, AIMessage, ToolMessage

from app.agent import memory as mem
from app.agent.prompts import (
    build_system_prompt,
    VIBE_CHECK_PROMPT,
    MEMORY_EXTRACTION_PROMPT,
    SUMMARIZE_PROMPT,
)
from app.agent.tools import build_tools
from app.core.config import settings
from app.core.logger import get_logger

logger = get_logger(__name__)


# ──────────────────────────────────────────────────────────────────────────────
# State definition
# ──────────────────────────────────────────────────────────────────────────────

class AgentState(TypedDict):
    user_phone: str
    incoming_message: str
    profile: dict
    memories: list[str]
    recent_messages: list[dict]
    # Internal tool-calling scratchpad (AIMessages + ToolMessages)
    tool_messages: list
    draft_response: str
    final_response: str
    extraction_result: dict


# ──────────────────────────────────────────────────────────────────────────────
# LLM client (shared across nodes)
# ──────────────────────────────────────────────────────────────────────────────

def _get_llm(temperature: float = 0.85) -> ChatMistralAI:
    return ChatMistralAI(
        model=settings.mistral_model,
        api_key=settings.mistral_api_key,
        temperature=temperature,
        max_tokens=300,
        max_retries=3,  # SDK-level retry on transient errors
    )


async def _invoke_with_retry(llm, messages: list, max_attempts: int = 4) -> object:
    """
    Invoke the LLM with exponential backoff on 429 rate-limit errors.

    mistral-large has stricter RPM limits — a single agent turn can fire
    3-4 LLM calls within milliseconds, so 429s are likely. This wrapper
    waits and retries automatically instead of crashing the webhook.
    """
    delay = 2.0  # seconds before first retry
    for attempt in range(1, max_attempts + 1):
        try:
            return await llm.ainvoke(messages)
        except Exception as e:
            err_str = str(e).lower()
            is_rate_limit = "429" in err_str or "rate limit" in err_str or "rate_limited" in err_str
            if is_rate_limit and attempt < max_attempts:
                logger.warning(
                    "[llm] rate limit hit (attempt %d/%d) — waiting %.1fs before retry",
                    attempt, max_attempts, delay,
                )
                await asyncio.sleep(delay)
                delay *= 2  # exponential backoff: 2s → 4s → 8s
            else:
                raise  # give up or non-rate-limit error


# ──────────────────────────────────────────────────────────────────────────────
# Node 1: Load Memory
# ──────────────────────────────────────────────────────────────────────────────

async def load_memory_node(state: AgentState) -> dict:
    """Load user profile, important memories, and recent conversation history."""
    user_phone = state["user_phone"]
    logger.debug("[load_memory] loading context for %s", user_phone)

    profile = await mem.get_or_create_profile(user_phone)
    memories = await mem.get_memories(user_phone)
    recent_messages = await mem.get_recent_messages(user_phone, limit=20)

    # Recalculate and sync relationship state
    count = await mem.count_messages(user_phone)
    relationship_state = mem.compute_relationship_state(count)
    if relationship_state != profile.get("relationship_state"):
        await mem.update_profile(user_phone, {
            "relationship_state": relationship_state,
            "message_count": count,
        })
        profile["relationship_state"] = relationship_state

    return {
        "profile": profile,
        "memories": memories,
        "recent_messages": recent_messages,
    }


# ──────────────────────────────────────────────────────────────────────────────
# Node 2: Reasoning (primary LLM call)
# ──────────────────────────────────────────────────────────────────────────────

async def reasoning_node(state: AgentState) -> dict:
    """Call Mistral (with tools bound) to generate a response or invoke a tool."""
    user_phone = state["user_phone"]
    profile = state["profile"]
    memories = state["memories"]
    recent_messages = state["recent_messages"]
    incoming = state["incoming_message"]
    prior_tool_messages = state.get("tool_messages", [])

    logger.debug("[reasoning] generating response for %s", user_phone)

    # Build tools first so we can inject their guidance into the system prompt
    tools = build_tools(user_phone)

    system_prompt = build_system_prompt(
        profile=profile,
        memories=memories,
        relationship_state=profile.get("relationship_state", "new"),
        tools=tools,
    )

    # Build LangChain message list from history
    lc_messages: list = [SystemMessage(content=system_prompt)]
    for msg in recent_messages:
        if msg["role"] == "user":
            lc_messages.append(HumanMessage(content=msg["content"]))
        else:
            lc_messages.append(AIMessage(content=msg["content"]))

    # Add current incoming message
    lc_messages.append(HumanMessage(content=incoming))

    # Append any prior tool call results from this turn's scratchpad
    if prior_tool_messages:
        lc_messages.extend(prior_tool_messages)

    # Bind tools and invoke
    llm = _get_llm(temperature=0.85)
    llm_with_tools = llm.bind_tools(tools)
    response = await _invoke_with_retry(llm_with_tools, lc_messages)

    # Detect if the model wants to call a tool
    if response.tool_calls:
        logger.info(
            "[reasoning] tool call(s) requested: %s",
            [tc["name"] for tc in response.tool_calls],
        )
        updated_tools = list(prior_tool_messages) + [response]
        return {"tool_messages": updated_tools, "draft_response": ""}

    draft = response.content.strip()

    # ── Hallucination guard ────────────────────────────────────────────────────
    # Mistral sometimes verbally confirms a tool action (e.g. "i'll remind you
    # at 9:43am") without actually calling set_reminder. Detect this and retry
    # with an explicit instruction to call the tool.
    if not prior_tool_messages:  # Only on first pass to avoid infinite loops
        hallucination = _detect_tool_hallucination(draft, incoming)
        if hallucination:
            logger.warning(
                "[reasoning] HALLUCINATION detected — LLM said '%s' without calling tool. Retrying with forced tool call instruction.",
                draft[:80],
            )
            # Inject a corrective instruction and retry at lower temperature
            corrective_msg = (
                f"IMPORTANT: You just responded with '{draft}' but you did NOT actually call "
                f"the tool. You MUST call the appropriate tool NOW. Do not respond with text — "
                f"call the tool function directly."
            )
            lc_messages_retry = lc_messages + [
                AIMessage(content=draft),
                HumanMessage(content=corrective_msg),
            ]
            llm_strict = _get_llm(temperature=0.1)
            llm_strict_with_tools = llm_strict.bind_tools(tools, tool_choice="any")
            response2 = await _invoke_with_retry(llm_strict_with_tools, lc_messages_retry)
            if response2.tool_calls:
                logger.info(
                    "[reasoning] retry succeeded — tool call(s): %s",
                    [tc["name"] for tc in response2.tool_calls],
                )
                updated_tools = list(prior_tool_messages) + [response2]
                return {"tool_messages": updated_tools, "draft_response": ""}
            else:
                logger.warning("[reasoning] retry still returned no tool call — proceeding with original draft")
                draft = response2.content.strip() or draft
    # ──────────────────────────────────────────────────────────────────────────

    logger.debug("[reasoning] draft: %s", draft)
    return {"draft_response": draft, "tool_messages": prior_tool_messages}


# ── Hallucination detection patterns ──────────────────────────────────────────
# Covers set_reminder, save_note, and web_search.
# Each pattern is scoped to the tool it corresponds to.

import re as _re

# Trigger words per tool — only check patterns if the incoming message has intent
_TOOL_TRIGGERS = {
    "reminder": [
        "remind", "reminder", "ping", "alert", "notify",
        "at ", "o'clock", "am", "pm", "tomorrow", "tonight",
        "in 30", "in 15", "in an hour", "minutes", "hours",
        "quote", "joke", "motivational",
    ],
    "note": [
        "save", "note", "remember", "store", "write down",
        "keep this", "jot", "record",
    ],
    "search": [
        "movie", "movies", "cinema", "film",
        "news", "latest", "current", "today", "right now",
        "weather", "score", "price", "stock",
        "what's happening", "look up", "search",
    ],
}

# Patterns that look like verbal confirmation WITHOUT a tool call
_HAL_PATTERNS_BY_TOOL = {
    "reminder": [
        _re.compile(r"i.?ll (remind|ping|message|send|text) you", _re.IGNORECASE),
        _re.compile(r"i.?ll (send|give) you (a |the )?(quote|joke|reminder|message)", _re.IGNORECASE),
        _re.compile(r"\breminder\b (is )?set", _re.IGNORECASE),
        _re.compile(r"(set|scheduled) (a |the )?\breminder\b", _re.IGNORECASE),
        _re.compile(r"i.?ll (let you know|check in|follow up)", _re.IGNORECASE),
        _re.compile(r"will (remind|ping|send|message) you", _re.IGNORECASE),
        _re.compile(r"going to (remind|ping|send|message) you", _re.IGNORECASE),
        _re.compile(r"done.{0,10}i.?ll (send|ping|remind|message)", _re.IGNORECASE),
    ],
    "note": [
        _re.compile(r"(saved|stored|noted|recorded) (the |your |a )?note", _re.IGNORECASE),
        _re.compile(r"note (was |has been )?(saved|stored|recorded)", _re.IGNORECASE),
        _re.compile(r"i.?ll (remember|note|save) (that|this)", _re.IGNORECASE),
        _re.compile(r"got it.{0,20}(saved|noted|stored)", _re.IGNORECASE),
        _re.compile(r"^(ok, |okay, |sure, )?saved(\.| it)?", _re.IGNORECASE),
    ],
    "search": [
        _re.compile(r"searching for you", _re.IGNORECASE),
        _re.compile(r"looking (that|it) up", _re.IGNORECASE),
        _re.compile(r"here.{0,10}(are|is).{0,10}(some|the) (movies|films|results|news)", _re.IGNORECASE),
        _re.compile(r"currently (in theaters|showing|playing)", _re.IGNORECASE),
    ],
}


def _detect_tool_hallucination(draft: str, incoming: str) -> bool:
    """
    Return True if the LLM's draft looks like it's verbally confirming a tool
    action without having actually called the tool.

    Checks all tool categories: set_reminder, save_note, web_search.
    Uses trigger words to avoid false positives on unrelated messages.
    """
    # 1. Catch raw tool name leakage (LLM outputs 'set_reminder{...' instead of calling it)
    tool_names = ["set_reminder", "save_note", "get_notes", "delete_note", "web_search"]
    if any(t_name in draft for t_name in tool_names):
        return True

    # 2. Catch verbal confirmation hallucinations
    incoming_lower = incoming.lower()
    for tool_type, triggers in _TOOL_TRIGGERS.items():
        if any(word in incoming_lower for word in triggers):
            patterns = _HAL_PATTERNS_BY_TOOL.get(tool_type, [])
            if any(pat.search(draft) for pat in patterns):
                return True
    return False


# ──────────────────────────────────────────────────────────────────────────────
# Node 3: Vibe Check (reflection + rewrite)
# ──────────────────────────────────────────────────────────────────────────────

# Phrases that immediately flag a response as "AI-sounding"
_BAD_PHRASES = [
    "as an ai",
    "i apologize",
    "i'm sorry",
    "i understand your concern",
    "thank you for reaching out",
    "certainly",
    "of course",
    "i would be happy",
    "happy to help",
    "great question",
    "absolutely",
    "no problem",
    "how can i assist",
    "i hope this helps",
    "feel free to",
    "please let me know",
    "additionally,",
    "furthermore,",
    "in conclusion",
]

_EMOJI_PATTERN = re.compile(
    "["
    "\U0001F600-\U0001F64F"
    "\U0001F300-\U0001F5FF"
    "\U0001F680-\U0001F6FF"
    "\U0001F1E0-\U0001F1FF"
    "\u2702-\u27B0"
    "\u24C2-\U0001F251"
    "]+",
    flags=re.UNICODE,
)


def _needs_rewrite(text: str) -> bool:
    """Return True if the draft fails the humanness checks."""
    lower = text.lower()
    # Check for banned phrases
    if any(phrase in lower for phrase in _BAD_PHRASES):
        return True
    # Check for emojis
    if _EMOJI_PATTERN.search(text):
        return True
    # Check for uppercase (more than just sentence-start caps indicates corporate tone)
    if sum(1 for c in text if c.isupper()) > len(text) * 0.15:
        return True
    # Check for very long responses (more than ~300 chars = probably too wordy)
    if len(text) > 320:
        return True
    return False


async def vibe_check_node(state: AgentState) -> dict:
    """Evaluate the draft. If it fails the humanness bar, rewrite it."""
    draft = state["draft_response"]

    # Guard: if draft is empty (e.g. a tool path edge-case), emit a safe fallback.
    if not draft or not draft.strip():
        logger.warning("[vibe_check] received empty draft — using safe fallback")
        return {"final_response": "sorry, something went wrong on my end"}

    if not _needs_rewrite(draft):
        logger.debug("[vibe_check] draft passed — no rewrite needed")
        return {"final_response": draft.replace("*", "").lower()}

    logger.info("[vibe_check] draft FAILED humanness check — rewriting")
    prompt = VIBE_CHECK_PROMPT.format(draft=draft)
    llm = _get_llm(temperature=0.6)
    response = await _invoke_with_retry(llm, [HumanMessage(content=prompt)])
    final = response.content.strip().lower()

    # Strip any residual emojis and asterisks after rewrite
    final = _EMOJI_PATTERN.sub("", final).replace("*", "").strip()

    # Safety net — if rewrite returned empty, fall back to lowercased original
    if not final:
        logger.warning("[vibe_check] rewrite returned empty — falling back to original draft")
        final = _EMOJI_PATTERN.sub("", draft).replace("*", "").strip().lower()

    logger.debug("[vibe_check] rewritten response: %s", final)
    return {"final_response": final}


# ──────────────────────────────────────────────────────────────────────────────
# Node 3.5: Tool Execution
# ──────────────────────────────────────────────────────────────────────────────

async def tool_execution_node(state: AgentState) -> dict:
    """
    Execute any tool calls that the reasoning node has requested.

    Iterates over the tool_calls on the last AIMessage in the scratchpad,
    runs each corresponding tool function, and appends a ToolMessage with
    the result back into the scratchpad so the reasoning node can read it
    on its next invocation.
    """
    user_phone = state["user_phone"]
    tool_messages = list(state.get("tool_messages", []))

    # The last item in tool_messages is the AIMessage with tool_calls
    last_ai_message = tool_messages[-1]
    if not hasattr(last_ai_message, "tool_calls") or not last_ai_message.tool_calls:
        logger.warning("[tool_execution] called but no tool_calls found on last message")
        return {}

    # Build the tool map once per invocation
    tools = build_tools(user_phone)
    tool_map = {t.name: t for t in tools}

    results: list[ToolMessage] = []
    for tool_call in last_ai_message.tool_calls:
        tool_name = tool_call["name"]
        tool_args = tool_call["args"]
        tool_call_id = tool_call["id"]

        tool_fn = tool_map.get(tool_name)
        if tool_fn is None:
            result_content = f"error: unknown tool '{tool_name}'"
            logger.error("[tool_execution] unknown tool requested: %s", tool_name)
        else:
            try:
                logger.info(
                    "[tool_execution] running '%s' with args: %s",
                    tool_name, tool_args,
                )
                result_content = await tool_fn.ainvoke(tool_args)
                logger.info(
                    "[tool_execution] '%s' returned: %s",
                    tool_name, str(result_content)[:200],
                )
            except Exception as e:
                result_content = f"tool error: {e}"
                logger.error(
                    "[tool_execution] '%s' raised: %s", tool_name, e
                )

        results.append(
            ToolMessage(
                content=str(result_content),
                tool_call_id=tool_call_id,
            )
        )

    tool_messages.extend(results)
    return {"tool_messages": tool_messages}


# ──────────────────────────────────────────────────────────────────────────────
# Node 4: Memory Update
# ──────────────────────────────────────────────────────────────────────────────

async def memory_update_node(state: AgentState) -> dict:
    """
    Extract facts from the latest exchange and persist them.
    Also conditionally updates the rolling conversation summary.
    """
    user_phone = state["user_phone"]
    recent_messages = state["recent_messages"]
    incoming = state["incoming_message"]
    final_response = state["final_response"]

    # Build a short conversation snippet for extraction (last 6 messages + current)
    snippet_messages = recent_messages[-6:] + [
        {"role": "user", "content": incoming},
        {"role": "assistant", "content": final_response},
    ]
    conversation_text = "\n".join(
        f"{m['role']}: {m['content']}" for m in snippet_messages
    )

    prompt = MEMORY_EXTRACTION_PROMPT.format(conversation=conversation_text)
    llm = _get_llm(temperature=0.2)  # Low temperature for reliable JSON

    try:
        response = await _invoke_with_retry(llm, [HumanMessage(content=prompt)])
        raw = response.content.strip()

        # Extract JSON — handle markdown code fences if present
        json_match = re.search(r"\{.*\}", raw, re.DOTALL)
        if not json_match:
            raise ValueError("No JSON found in extraction response")

        result = json.loads(json_match.group())
    except Exception as e:
        logger.warning("[memory_update] extraction failed: %s", e)
        result = {"memories": [], "profile_updates": {}, "schedule_followup": {"should_followup": False}}

    # Persist new memories
    for memory_text in result.get("memories", []):
        if memory_text:
            await mem.add_memory(user_phone, memory_text)

    # Persist profile updates
    raw_profile_updates = result.get("profile_updates", {})
    
    # Only update non-empty values
    profile_updates = {k: v for k, v in raw_profile_updates.items() if v}
    
    if profile_updates:
        # Separate schema columns from arbitrary facts to avoid SQL errors
        allowed_columns = {"name", "relationship_state", "message_count", "summary"}
        safe_updates = {}
        facts = {}
        
        for k, v in profile_updates.items():
            if k in allowed_columns:
                safe_updates[k] = v
            else:
                facts[k] = v
                
        if facts:
            safe_updates["facts"] = facts
            
        if safe_updates:
            await mem.update_profile(user_phone, safe_updates)

    # Conditionally regenerate conversation summary
    count = await mem.count_messages(user_phone)
    if count > 0 and count % settings.summarize_every_n_messages == 0:
        await _update_summary(user_phone, conversation_text)

    return {"extraction_result": result}


async def _update_summary(user_phone: str, conversation_text: str) -> None:
    """Generate and save a rolling summary of the conversation."""
    prompt = SUMMARIZE_PROMPT.format(conversation=conversation_text)
    llm = _get_llm(temperature=0.3)
    try:
        response = await _invoke_with_retry(llm, [HumanMessage(content=prompt)])
        summary = response.content.strip()
        await mem.update_profile(user_phone, {"summary": summary})
        logger.info("[summary] updated summary for %s", user_phone)
    except Exception as e:
        logger.warning("[summary] failed to update summary: %s", e)


# ──────────────────────────────────────────────────────────────────────────────
# Node 5: Schedule Follow-Up
# ──────────────────────────────────────────────────────────────────────────────

async def schedule_followup_node(state: AgentState) -> dict:
    """
    Register a proactive follow-up if the memory extraction flagged one.
    A real friend only follows up on things that genuinely matter.
    """
    extraction = state.get("extraction_result", {})
    followup_info = extraction.get("schedule_followup", {})

    if not followup_info.get("should_followup", False):
        return {}

    user_phone = state["user_phone"]
    message = followup_info.get("message", "").strip()
    delay_hours = followup_info.get("delay_hours", 24)

    if not message:
        return {}

    send_at = datetime.now(timezone.utc) + timedelta(hours=float(delay_hours))
    await mem.schedule_followup(user_phone, message, send_at)

    logger.info(
        "[schedule_followup] registered for %s in ~%sh: '%s'",
        user_phone, delay_hours, message,
    )
    return {}
