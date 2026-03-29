"""
LangGraph agent: Claude Haiku + sprinkler tools.

Architecture:
  User message → call_model node → (has tool calls?) → tool_node → call_model → ...
                                                      → (no tool calls) → return to user

The agent keeps conversation history per WhatsApp user (keyed by phone number),
so it can remember context across messages in the same session.
"""

import os
from typing import Annotated
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import SystemMessage, HumanMessage
from langgraph.graph import StateGraph, END
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode
from pydantic import BaseModel

from tools import ALL_TOOLS


# ---------------------------------------------------------------------------
# System prompt — sets the agent's personality and rules
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """You are a friendly home sprinkler assistant for a house in Austin, TX.
You control a 12-zone sprinkler system connected to Home Assistant via Zooz ZEN16 relays.

IMPORTANT RULES:
1. Only one zone can run at a time — this is a hardware safety requirement. Always check before starting.
2. Zones 1-3 are new plantings (installed ~3 weeks ago). They need daily watering.
   - Zones 2 & 3 are new Zoysia sod: water 2-3 times/day, 10-15 min each cycle while establishing.
   - Zone 1 is new trees/shrubs: water daily, 8-10 min.
3. Zones 4-12 are NOT yet wired. Inform the user if they try to control them.
4. Always check weather before recommending a schedule. Skip if rain ≥ 6mm expected.
5. Maximum single zone run time is 30 minutes. Never exceed this.
6. Zone 9 is planned for elimination — don't activate it.

ZONE SUMMARY (wired zones only):
- Zone 1: Front beds & trees (bubblers, Monterrey Oak, Crape Myrtle, Texas Sage, etc.)
- Zone 2: Front lawn right (Zoysia Palisades sod, new)
- Zone 3: Front lawn left (Zoysia Palisades sod, new)

HOW TO RESPOND:
- Be concise and friendly. Use plain text (no markdown — this goes to WhatsApp).
- When confirming a watering action, state the zone, duration, and when it finishes.
- If weather suggests skipping, say so clearly but let the user override.
- Use tools to get real-time data — don't guess at states.

EXAMPLE MESSAGES YOU MIGHT RECEIVE:
- "Water zone 1 for 10 minutes"
- "Run the morning schedule"
- "Is anything running?"
- "Skip today — it rained"
- "How long should I water the new sod?"
- "What zones are wired?"
"""


# ---------------------------------------------------------------------------
# LangGraph state
# ---------------------------------------------------------------------------

class AgentState(BaseModel):
    messages: Annotated[list, add_messages] = []


# ---------------------------------------------------------------------------
# Build the graph
# ---------------------------------------------------------------------------

def build_agent():
    llm = ChatAnthropic(
        model="claude-haiku-4-5-20251001",
        api_key=os.getenv("ANTHROPIC_API_KEY"),
        temperature=0,
    ).bind_tools(ALL_TOOLS)

    tool_node = ToolNode(ALL_TOOLS)

    def call_model(state: AgentState) -> dict:
        """Send messages to Claude Haiku and get a response (possibly with tool calls)."""
        messages = [SystemMessage(content=SYSTEM_PROMPT)] + state.messages
        response = llm.invoke(messages)
        return {"messages": [response]}

    def should_use_tools(state: AgentState) -> str:
        """Route: if the last message has tool calls, go to tools; otherwise finish."""
        last = state.messages[-1]
        if hasattr(last, "tool_calls") and last.tool_calls:
            return "tools"
        return END

    graph = StateGraph(AgentState)
    graph.add_node("call_model", call_model)
    graph.add_node("tools", tool_node)

    graph.set_entry_point("call_model")
    graph.add_conditional_edges("call_model", should_use_tools, {"tools": "tools", END: END})
    graph.add_edge("tools", "call_model")   # After tools, always go back to model

    return graph.compile()


# ---------------------------------------------------------------------------
# Conversation memory (in-memory, per WhatsApp user phone number)
# ---------------------------------------------------------------------------

# Maps phone_number → list of messages (conversation history)
# This resets when the server restarts. For persistence, swap with Redis or SQLite.
_conversations: dict[str, list] = {}

_agent = None

def get_agent():
    global _agent
    if _agent is None:
        _agent = build_agent()
    return _agent


async def chat(user_id: str, message: str) -> str:
    """
    Send a message from a user and get the agent's response.

    Args:
        user_id: phone number or any unique identifier (used for conversation history)
        message: the text message from the user
    Returns:
        The agent's text reply
    """
    agent = get_agent()

    # Get or initialize conversation history for this user
    history = _conversations.get(user_id, [])
    history.append(HumanMessage(content=message))

    # Run the agent
    result = await agent.ainvoke({"messages": history})

    # Extract the final AI message
    updated_messages = result["messages"]
    reply_msg = updated_messages[-1]
    reply_text = reply_msg.content if hasattr(reply_msg, "content") else str(reply_msg)

    # Save updated history (cap at 20 messages to avoid unbounded growth)
    _conversations[user_id] = list(result["messages"])[-20:]

    return reply_text


def clear_conversation(user_id: str):
    """Reset conversation history for a user (e.g., on 'reset' command)."""
    _conversations.pop(user_id, None)
