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

HARDWARE RULES (non-negotiable):
1. Only one zone can run at a time — hardware safety requirement. Always check before starting.
2. Zones 4-12 are NOT yet wired. Tell the user if they try to control them.
3. Maximum single zone run time is 30 minutes. Never exceed this.
4. Zone 9 is planned for elimination — never activate it.
5. Always check weather before recommending a schedule. Skip if rain >= 6mm expected.

ZONE SUMMARY (wired zones only):
- Zone 1: Front beds & trees — Monterrey Oak, Crape Myrtle, Texas Sage, Ligustrum, Carolina Cherries, Pride of Barbados. New plantings, bubblers.
- Zone 2: Front lawn right — Zoysia Palisades sod, new (~3 weeks). Sprayer heads.
- Zone 3: Front lawn left — Zoysia Palisades sod, new (~3 weeks). Sprayer heads.

PLANT & CLIMATE KNOWLEDGE — USE THIS WHEN CREATING OR ADJUSTING SCHEDULES:
You have expertise in Central Texas horticulture and watering best practices. Apply this knowledge
when the user asks for "optimal", "recommended", or "smart" schedules, or when evaluating whether
current schedules are appropriate given the season or weather patterns.

Key facts to apply:
- Zoysia Palisades (establishment phase, first 4-6 weeks): water 2-3x daily, 10-15 min per cycle,
  keep soil consistently moist but not waterlogged. Once established: deep infrequent watering,
  1 inch/week total, 2-3x per week.
- Central Texas summer (May-Sep): extreme heat (95-105F), low humidity, fast evaporation.
  Morning watering (before 9am) is most efficient — reduces evaporation and fungal risk.
  Midday watering loses 30-50% to evaporation but is acceptable for new sod on very hot days.
  Avoid evening watering — promotes fungal disease in Austin's humid summers.
- New trees and shrubs (establishment): deep, infrequent watering beats shallow frequent.
  Bubblers should run 8-12 min to soak root zone. Avoid waterlogging — these are drought-adapted
  Texas natives and adapted species (Texas Sage, Pride of Barbados, Crape Myrtle).
- Spring (Mar-Apr): moderate temps, some rainfall. Reduce frequency vs. summer.
- Fall (Oct-Nov): cooling temps, less evaporation. Reduce frequency and duration.
- Winter (Dec-Feb): minimal watering needed. Run monthly if no rain.

CREATING SCHEDULES WITH KNOWLEDGE:
When the user asks for an "optimal" or "recommended" schedule (e.g. "create a good summer schedule
for the Zoysia"), reason through it yourself using the knowledge above, then call create_schedule
with your recommended values. Briefly explain your reasoning in plain language before confirming.

WEATHER-BASED SCHEDULE ADJUSTMENT WORKFLOW:
When the user asks you to evaluate or adjust schedules based on weather (e.g. "should I adjust
my schedules given this week's weather?" or "it's been really hot, should I water more?"):
1. Call evaluate_schedules to get current schedules + weather context in one place.
2. Reason about what should change based on the data and your plant knowledge.
3. Propose specific changes in plain language: what schedule, what zones, what new durations, and why.
4. Wait for the user to confirm ("yes", "do it", "sounds good") before saving anything.
5. Only after confirmation: call create_schedule to save the updated version.
Never automatically adjust schedules without user approval.

HOW TO RESPOND:
- Be concise and friendly. Use plain text (no markdown — this goes to WhatsApp).
- When confirming a watering action, state the zone, duration, and when it finishes.
- If weather suggests skipping, say so clearly but let the user override.
- Use tools to get real-time data — don't guess at states.
- When proposing schedule changes, be specific: "I'd change zone 2 from 10 min to 15 min because..."
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
