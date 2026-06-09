"""
app/agent/graph.py
──────────────────
Builds and compiles the LangGraph agent.

Graph flow
──────────
  load_memory
       ↓
   reasoning  ──── (tool called?) ────► tool_execution ──┐
       ↓                                                   │
  (no tool)  ◄─────────────────────────────────────────────┘
       ↓
   vibe_check
       ↓
  memory_update
       ↓
  schedule_followup
       ↓
     [END]

The reasoning ↔ tool_execution loop repeats until Mistral produces a
plain-text reply (no more tool calls), then the flow continues to vibe_check.
"""

from langgraph.graph import StateGraph, END

from app.agent.nodes import (
    AgentState,
    load_memory_node,
    reasoning_node,
    tool_execution_node,
    vibe_check_node,
    memory_update_node,
    schedule_followup_node,
)
from app.core.logger import get_logger

logger = get_logger(__name__)


def _should_run_tools(state: AgentState) -> str:
    """
    Conditional edge: after reasoning, decide where to go next.

    Returns
    -------
    'tool_execution'  – if the LLM produced tool call(s)
    'vibe_check'      – if the LLM produced a plain-text reply
    """
    tool_messages = state.get("tool_messages", [])
    if tool_messages:
        last = tool_messages[-1]
        # AIMessage with tool_calls means we need to execute them
        if hasattr(last, "tool_calls") and last.tool_calls:
            return "tool_execution"
    return "vibe_check"


def build_graph() -> StateGraph:
    """Construct the agent graph with tool-calling support."""
    graph = StateGraph(AgentState)

    # Register nodes
    graph.add_node("load_memory", load_memory_node)
    graph.add_node("reasoning", reasoning_node)
    graph.add_node("tool_execution", tool_execution_node)
    graph.add_node("vibe_check", vibe_check_node)
    graph.add_node("memory_update", memory_update_node)
    graph.add_node("schedule_followup", schedule_followup_node)

    # Set entry point
    graph.set_entry_point("load_memory")

    # load_memory → reasoning (always)
    graph.add_edge("load_memory", "reasoning")

    # reasoning → conditional branch
    graph.add_conditional_edges(
        "reasoning",
        _should_run_tools,
        {
            "tool_execution": "tool_execution",
            "vibe_check": "vibe_check",
        },
    )

    # After tool execution, loop back to reasoning so Mistral
    # can read the tool result and produce its next response
    graph.add_edge("tool_execution", "reasoning")

    # Linear tail of the pipeline
    graph.add_edge("vibe_check", "memory_update")
    graph.add_edge("memory_update", "schedule_followup")
    graph.add_edge("schedule_followup", END)

    return graph


# Compile once at module level — reused for every incoming message
_graph = build_graph()
agent_graph = _graph.compile()


async def run_agent(user_phone: str, incoming_message: str) -> str:
    """
    Run the full agent graph for an incoming message.

    Returns the final human-sounding response string.
    """
    initial_state: AgentState = {
        "user_phone": user_phone,
        "incoming_message": incoming_message,
        "profile": {},
        "memories": [],
        "recent_messages": [],
        "tool_messages": [],
        "draft_response": "",
        "final_response": "",
        "extraction_result": {},
    }

    logger.info("[graph] running agent for %s", user_phone)
    result = await agent_graph.ainvoke(initial_state)

    final = result.get("final_response", "").strip()
    if not final:
        logger.warning("[graph] empty final response — falling back to draft")
        final = result.get("draft_response", "").strip().lower()

    return final
