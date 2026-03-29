# Sprinkler Agent

A conversational AI agent that controls a home irrigation system via WhatsApp. Send a text message like "water zone 2 for 10 minutes" or "did I water the sod yesterday?" and the agent handles it — checking weather, enforcing safety rules, logging history, and reporting back when done.

Built with LangChain, LangGraph, Claude Haiku, Home Assistant, and the Meta WhatsApp Cloud API. Runs on a Windows 11 Home NUC as a persistent background service, exposed to the internet via a permanent Cloudflare Tunnel.

---

## Table of Contents

- [What It Does](#what-it-does)
- [Architecture Overview](#architecture-overview)
- [Hardware Setup](#hardware-setup)
  - [The Stack Explained](#the-stack-explained)
  - [How the ZEN16 Controls the Hunter Controller](#how-the-zen16-controls-the-hunter-controller)
  - [Zone Wiring Map](#zone-wiring-map)
- [How the AI Agent Works](#how-the-ai-agent-works)
  - [LangChain Tools](#langchain-tools)
  - [LangGraph State Machine](#langgraph-state-machine)
  - [Claude Haiku as the LLM](#claude-haiku-as-the-llm)
  - [Conversation Memory](#conversation-memory)
  - [Watering History Log](#watering-history-log)
- [Project Structure](#project-structure)
- [Setup Guide](#setup-guide)
  - [Prerequisites](#prerequisites)
  - [Step 1 — Clone and Install](#step-1--clone-and-install)
  - [Step 2 — Configure Home Assistant](#step-2--configure-home-assistant)
  - [Step 3 — Configure Environment Variables](#step-3--configure-environment-variables)
  - [Step 4 — Add HA Helpers and Safety Automations](#step-4--add-ha-helpers-and-safety-automations)
  - [Step 5 — Set Up WhatsApp (Meta Cloud API)](#step-5--set-up-whatsapp-meta-cloud-api)
  - [Step 6 — Set Up a Permanent Public HTTPS URL](#step-6--set-up-a-permanent-public-https-url)
  - [Step 7 — Run the Agent](#step-7--run-the-agent)
  - [Step 8 — Auto-Start on Boot](#step-8--auto-start-on-boot)
- [Testing Without WhatsApp](#testing-without-whatsapp)
- [Extending the Agent](#extending-the-agent)

---

## What It Does

- **Natural language zone control** — "Run zone 1 for 8 minutes", "Stop everything", "Run the morning schedule"
- **Weather-aware** — checks Open-Meteo (free, no API key required) before recommending watering; skips if significant rain is expected
- **Watering history** — logs every zone run to a local JSON file; answers questions like "when did zone 2 last run?" or "how much did I water this week?"
- **Safety enforced at two layers** — the Python agent checks for zone conflicts before activating anything; Home Assistant automations provide a hardware-level failsafe independent of the agent process
- **12-zone awareness** — knows which zones are wired and active, which are not yet connected, and which are planned for removal
- **Conversational memory** — remembers context across messages in the same WhatsApp session (per phone number)
- **Duplicate message protection** — tracks processed message IDs so Meta's retry behavior doesn't trigger duplicate zone runs
- **WhatsApp interface** — plain text messages; no app to install

---

## Architecture Overview

```
You (WhatsApp)
      │
      ▼
Meta Cloud API  ──POST──►  Cloudflare Tunnel (sprinkler.whitlockhouse.org)
                                   │
                                   ▼
                     FastAPI /webhook  (main.py + whatsapp_handler.py)
                           [returns 200 immediately]
                                   │
                          Background Task
                                   │
                                   ▼
                          LangGraph Agent  (agent.py)
                          ┌────────────────────────────┐
                          │  call_model node            │
                          │    Claude Haiku (LLM)       │
                          │         │                   │
                          │   tool_calls?               │
                          │    yes ──► tool_node        │
                          │              │              │
                          │    ◄─────────┘              │
                          │    no ──► END               │
                          └────────────────────────────┘
                                   │
                          LangChain Tools  (tools.py)
                           │               │
                           ▼               ▼
              HA REST API          watering_log.json
              (ha_client.py)       (history.py)
                   │
                   ▼
      Home Assistant (VM on NUC)
                   │
                   ▼
      Zooz ZEN16 Z-Wave Relays (via SmartThings)
                   │
                   ▼
      Hunter PC-300 Zone Valves
```

**Why the agent returns 200 immediately:**
Meta's webhook system expects a response within ~5 seconds. Zone watering takes minutes. If the agent waited until watering was done before responding, Meta would assume the delivery failed and retry the same message — causing duplicate zone activations. By returning `200 OK` immediately and processing in a FastAPI background task, Meta is satisfied and the agent runs the zone uninterrupted.

---

## Hardware Setup

### The Stack Explained

| Component | Role | Why this component |
|---|---|---|
| Hunter PC-300 | 12-zone irrigation controller | Existing dumb controller — the ZEN16 relays take over its timing function |
| Zooz ZEN16 Multi-Relay × 3 | Z-Wave smart relays, 3 relays each | Each relay closes a circuit on the Hunter's zone terminals, activating that zone's valve |
| Samsung SmartThings Hub | Z-Wave coordinator | The ZEN16s pair to SmartThings over Z-Wave; SmartThings handles the Z-Wave radio protocol |
| Home Assistant OS | Home automation platform | Integrates SmartThings via official integration; exposes each relay as a `switch` entity controllable via REST API |
| Windows 11 Home NUC | Always-on host machine | Runs Home Assistant as a VM (via Hyper-V) and the Python agent as a background process |

**Why Home Assistant?**
HA acts as the abstraction layer between the Python agent and the physical hardware. Without HA, the agent would need to speak Z-Wave protocol directly — complex, hardware-specific, and fragile. With HA, the agent just calls a simple REST API (`POST /api/services/switch/turn_on`) and HA handles the rest: Z-Wave → SmartThings → ZEN16 relay → Hunter terminal → valve opens. This also means the same agent code works regardless of what smart home hardware you use underneath, as long as it's in HA.

**Why SmartThings + HA instead of Z-Wave JS directly?**
The ZEN16 relays were already paired to SmartThings. HA's SmartThings integration bridges them into HA without re-pairing. Z-Wave JS is the more direct approach but requires a dedicated Z-Wave USB stick — SmartThings serves as the Z-Wave hub here.

**Why Windows 11 Home?**
It's what was on the NUC. Home edition lacks native RDP support (no remote desktop), but this is worked around using Chrome Remote Desktop for remote access. The Python agent and Cloudflare Tunnel both run natively on Windows with no compatibility issues.

### How the ZEN16 Controls the Hunter Controller

The Hunter PC-300 has a terminal block with one terminal per zone (plus a common/ground terminal). Normally, the built-in timer closes a circuit between the common and a zone terminal, which sends 24VAC to that zone's solenoid valve, opening it.

The ZEN16 relay is wired **in parallel** with the Hunter's internal contacts on each zone terminal. When Home Assistant turns a ZEN16 relay on, it closes that relay's circuit — exactly as the Hunter's internal timer would. The Hunter controller doesn't know or care that an external relay is doing the switching.

This approach is non-destructive: the Hunter's built-in timer still works as a fallback. The ZEN16 just adds a parallel path to activate each zone.

### Zone Wiring Map

**ZEN16 #1 — wired and active:**

| Relay | Zone | Name | Plant Type |
|---|---|---|---|
| Relay 1 | Zone 2 | Front Lawn Right | New Zoysia Palisades sod |
| Relay 2 | Zone 1 | Front Beds & Trees | New trees & shrubs (bubblers) |
| Relay 3 | Zone 3 | Front Lawn Left | New Zoysia Palisades sod |

**ZEN16 #2 and #3 — installed but not yet wired (zones 4–12 inactive)**

Note: Relay mapping is intentionally non-sequential (Relay 1 → Zone 2, Relay 2 → Zone 1) because the physical wiring was done by zone location, not zone number. The `config.py` file maps this correctly so the agent always refers to zones by their logical number.

---

## How the AI Agent Works

The core of this project is a **ReAct-style agent** (Reason + Act) built with LangGraph on top of LangChain. Rather than a static script that runs preset schedules, the agent reasons about each request in natural language, decides what actions to take, executes them via tools, and formulates a human-readable response.

### LangChain Tools

LangChain tools are Python functions that the LLM can choose to call when it needs real-world information or wants to take an action. Each tool has a **docstring that the LLM reads** to decide when and how to use it — this is the key insight. You're not writing conditional logic like "if user says water → call turn_on". Instead, you describe what a tool does in plain English, and the LLM decides when to invoke it based on the user's intent.

Tools in this project (`tools.py`):

| Tool | Type | What it does |
|---|---|---|
| `get_zone_status` | async | Query HA REST API for a single zone's ON/OFF state |
| `get_all_zones_status` | async | Query all zones at once and return a formatted summary |
| `run_zone` | async | Turn on a zone for N minutes, then turn it off; logs the event |
| `stop_zone` | async | Immediately turn off a specific zone |
| `stop_all_zones` | async | Emergency stop — turns off all wired zones |
| `run_schedule` | async | Run a named preset sequence (e.g. "morning_new_sod"); logs each zone |
| `check_weather` | async | Fetch Open-Meteo forecast and return a watering recommendation |
| `get_zone_info` | sync | Return static info about a zone from config.py |
| `get_watering_history` | sync | Read watering_log.json and summarize recent events |
| `get_last_zone_run` | sync | Find the most recent run for a specific zone |

Each tool is decorated with LangChain's `@tool` decorator, which:
1. Wraps the function in a `StructuredTool` that LangChain can serialize into a JSON schema
2. Extracts the type-annotated function signature to define what arguments the LLM can pass
3. Makes the docstring available to the LLM as the tool's description in the API request

The LLM doesn't execute Python — it *requests* a tool call with specific arguments as a structured response, and LangGraph intercepts that response and executes the actual function. The result is then fed back to the LLM as a `ToolMessage` so it can formulate its reply.

Example — the LLM sees `run_zone` described as:
```
Tool: run_zone
Args: zone_number (int), minutes (int)
Description: Turn on a specific sprinkler zone for a given number of minutes,
then turn it off. SAFETY: Will refuse if another zone is already running.
Will cap duration at 30 minutes maximum.
```

This description is why docstrings in `tools.py` are written carefully — they are the LLM's only instruction manual for each capability.

### LangGraph State Machine

LangGraph is LangChain's framework for building **stateful, multi-step agents** as explicit graphs. Rather than a simple "input → LLM → output" pipeline, LangGraph lets you define nodes (processing steps) and edges (transitions between steps), including conditional branches and cycles.

**Why LangGraph instead of a simple LangChain chain?**

A basic LangChain chain is linear and runs exactly once. LangGraph supports **cycles**, which enables the ReAct (Reason + Act) loop that makes this agent useful:

1. LLM reasons about the user's request
2. LLM requests a tool call (e.g., `check_weather`)
3. LangGraph executes the tool and returns the result
4. LLM receives the result and reasons again — does it need more information? Should it call another tool?
5. This repeats until the LLM produces a final text response with no tool calls

This loop can chain multiple tools in a single message. For example, "run the morning schedule and tell me if it's going to rain today" triggers: `check_weather` → model reads result → `run_schedule` → model reads result → final reply.

The agent graph has three nodes:

```
[entry] → call_model → should_use_tools?
                              │
               yes ─────► tool_node ─────► call_model (loop back)
                              │
               no ──────► END
```

**State** flows between nodes as a typed Pydantic object:

```python
class AgentState(BaseModel):
    messages: Annotated[list, add_messages] = []
```

The `add_messages` annotation is a LangGraph **reducer** — a function that determines how to merge new state into existing state. Instead of replacing the message list on each update, `add_messages` appends to it. This is how the full conversation history (including tool calls and their results) accumulates across the ReAct loop without being lost between nodes.

**Conditional edges** determine routing after each LLM call:

```python
def should_use_tools(state: AgentState) -> str:
    last = state.messages[-1]
    if hasattr(last, "tool_calls") and last.tool_calls:
        return "tools"
    return END
```

If Claude's response contains tool calls, the graph routes to `tool_node`. If the response is plain text, the graph terminates and the text is returned as the reply.

**ToolNode** is a prebuilt LangGraph component that handles tool execution:
1. Reads the tool call requests from the last AI message (name + arguments)
2. Finds the matching Python function in `ALL_TOOLS`
3. Executes it (handling both sync and async functions)
4. Wraps the return value in a `ToolMessage` and appends it to state

This is wired in `agent.py` as:
```python
tool_node = ToolNode(ALL_TOOLS)
graph.add_node("tools", tool_node)
graph.add_edge("tools", "call_model")  # always loop back after tools
```

The `add_edge("tools", "call_model")` is what creates the cycle — after every tool execution, the model gets another turn to decide what to do next.

### Claude Haiku as the LLM

The LLM is Claude Haiku (`claude-haiku-4-5-20251001`) via Anthropic's API, accessed through LangChain's `ChatAnthropic` integration:

```python
llm = ChatAnthropic(
    model="claude-haiku-4-5-20251001",
    temperature=0,
).bind_tools(ALL_TOOLS)
```

**Why `bind_tools`?**
`bind_tools` serializes all tool definitions into JSON schemas and injects them into every API request as the `tools` parameter. This tells Claude which tools are available, what arguments they accept, and what they do. Claude can then respond with structured `tool_use` content blocks instead of just text. LangChain handles all of this serialization automatically — you never write a JSON schema by hand.

**Why temperature=0?**
Temperature controls randomness in the model's output. At temperature 0, the model always selects the highest-probability token — behavior is deterministic and repeatable. For a home automation agent controlling physical hardware, predictability is critical. You want "water zone 2 for 10 minutes" to always produce a `run_zone(zone_number=2, minutes=10)` call, not occasionally decide to do something creative.

**Why Haiku and not a larger model?**
Haiku is Anthropic's fastest and most cost-efficient Claude model. For this use case — understanding natural language commands, selecting the right tool, and producing a friendly reply — Haiku is more than capable. A larger model like Sonnet or Opus would add 2-3x the latency and cost with no meaningful improvement in quality for these straightforward tasks. Speed matters here because users expect a WhatsApp reply to feel near-instant.

**The system prompt:**
Every conversation begins with a system prompt (in `agent.py`) that establishes the agent's identity, rules, and zone knowledge. This includes critical constraints like "only one zone can run at a time" and "zones 2 and 3 are new sod — water 2-3 times daily". The system prompt is the agent's operating manual — it shapes every decision the LLM makes, even when the user doesn't explicitly mention these constraints.

### Conversation Memory

Each WhatsApp user gets their own conversation history, stored in memory and keyed by phone number:

```python
_conversations: dict[str, list] = {}
```

On each message:
1. The new `HumanMessage` is appended to that user's history
2. The full history (plus system prompt) is passed to the LangGraph agent as initial state
3. After the agent finishes, the updated message list (including all tool calls, tool results, and the final AI reply) is saved back — capped at 20 messages

This is what enables natural follow-up conversations:
- User: "Water zone 2 for 10 minutes"
- Agent: *(runs zone 2)*
- User: "And zone 3 for the same amount"
- Agent: *(understands "same amount" = 10 minutes from prior context)*

History resets when the server restarts. For persistence across restarts, swap the dict for a Redis or SQLite backend — the interface is just `dict[str, list[BaseMessage]]`.

### Watering History Log

The agent writes a structured log of every watering event to `watering_log.json` (`history.py`). This is separate from conversation memory — it's a permanent record of what actually ran, not what was said.

**Why a custom log instead of reading HA or SmartThings logs?**
HA system logs are full of noise — network events, integration errors, state changes for unrelated devices. Parsing them to extract "zone 2 ran for 10 minutes at 7am" would be fragile and complex. A purpose-built log written by the agent contains exactly the structured data the agent needs to answer history questions, nothing more.

Each log entry is a JSON object:
```json
{
  "timestamp_utc": "2026-03-28T13:00:00+00:00",
  "event_type": "zone_run",
  "zone": 2,
  "zone_name": "Front Lawn Right",
  "duration_minutes": 10,
  "schedule_name": "morning_new_sod",
  "notes": null
}
```

`run_zone` and `run_schedule` both call `append_event()` automatically after each successful run — the agent never needs to explicitly decide to log. The log is capped at 500 entries so it doesn't grow unbounded.

Two tools query the log:
- `get_watering_history(days)` — returns a formatted summary of the last N days
- `get_last_zone_run(zone_number)` — finds the most recent run for a specific zone

Timestamps are stored in UTC and converted to Austin local time (CDT) for display, so responses like "Zone 2 last ran Thursday Mar 28 at 7:00 AM CDT" are human-readable without timezone confusion.

---

## Project Structure

```
sprinkler_agent/
├── main.py              # FastAPI app entry point + test /chat endpoint
├── agent.py             # LangGraph graph definition + per-user conversation memory
├── tools.py             # LangChain tools (all agent capabilities)
├── ha_client.py         # Async Home Assistant REST API client
├── whatsapp_handler.py  # FastAPI router — Meta webhook + background task processing
├── weather.py           # Open-Meteo weather fetching (no API key required)
├── history.py           # Watering event log — read/write watering_log.json
├── config.py            # Zone definitions, schedules, safety settings
├── watering_log.json    # Auto-created on first zone run
├── requirements.txt
├── .env.example         # Template for secrets
└── ha_config/
    ├── automations.yaml # HA safety automations (30-min timeout, one-zone-at-a-time)
    ├── helpers.yaml     # input_number helpers for zone durations
    └── SETUP.md         # Quick HA setup reference
```

**`config.py` is the single source of truth** for the physical system — zone names, HA entity IDs, which ZEN16 relay each zone maps to, whether it's wired, plant type, and default duration. Adding a new zone or updating wiring only requires changes here; no other files need to be touched.

**`ha_client.py`** is a thin async wrapper around the HA REST API using `httpx`. All zone control goes through this — `turn_on`, `turn_off`, `is_on`, and `get_state`. Making it a separate module keeps `tools.py` clean and makes the HA connection easy to test independently.

**`whatsapp_handler.py`** handles the two webhook endpoints Meta requires:
- `GET /webhook` — webhook verification (Meta sends a challenge; you echo it back with your verify token)
- `POST /webhook` — incoming messages; returns 200 immediately, then runs the agent in a background task

It also deduplicates messages by tracking processed message IDs in a set, so Meta's retry behavior (which fires when it doesn't get a fast enough response) doesn't cause duplicate zone activations.

---

## Setup Guide

This guide is written for the actual hardware used in this project: a **Windows 11 Home NUC** running **Home Assistant OS as a VM**, with **Zooz ZEN16 relays** connected via **SmartThings** to a **Hunter PC-300** controller.

### Prerequisites

- Python 3.11+ installed on Windows (download from python.org — check "Add to PATH" during install)
- Home Assistant running and accessible (local IP or Nabu Casa cloud URL)
- Zooz ZEN16 relays paired to SmartThings and integrated into Home Assistant
- A Meta Developer account (free) for WhatsApp
- An Anthropic API key (get one at console.anthropic.com)
- A Cloudflare account (free) and a domain name for the permanent tunnel URL

### Step 1 — Clone and Install

On your Windows NUC, open PowerShell and run:

```powershell
cd $env:USERPROFILE
git clone https://github.com/yourusername/sprinkler-agent.git sprinkler_agent
cd sprinkler_agent
python -m pip install -r requirements.txt
```

**Why not a virtual environment on Windows?**
A virtual environment (venv) created on Mac or Linux uses a `bin/` directory structure that doesn't work on Windows. If you're setting up fresh on Windows, you can create a Windows-compatible venv with `python -m venv .venv` and activate it with `.\.venv\Scripts\activate`. In this project, packages are installed globally into the system Python to avoid cross-platform venv issues when copying files between Mac (development) and Windows (production).

### Step 2 — Configure Home Assistant

**Find your ZEN16 entity IDs:**
1. Open HA → Settings → Devices & Services → Entities
2. Search for "zen16", "zooz", or "relay"
3. Note the entity IDs for ZEN16 #1's three relays
4. Cross-reference with your physical wiring to map relay → zone number
5. Update `config.py` → `ZONES` → `entity_id` for zones 1, 2, 3

In this project, HA automatically named them with a clean pattern:
- `switch.sprinkler_zone_1`
- `switch.sprinkler_zone_2`
- `switch.sprinkler_zone_3`

Your naming may differ depending on how the devices were named in SmartThings before the HA integration was set up.

**Get a Long-Lived Access Token:**
1. In HA, click your profile icon (bottom left)
2. Scroll to **Long-lived access tokens** → Create token
3. Name it "sprinkler-agent" → copy immediately (shown once only)

**Why a long-lived token?**
HA's REST API uses bearer token authentication. A long-lived token is a static credential that doesn't expire, unlike short-lived OAuth tokens. It functions like an API key scoped to your HA instance. Keep it in `.env` and never commit it to version control.

**HA URL options:**
- Local network: `http://192.168.x.x:8123` — only works when the agent is on the same network
- Nabu Casa cloud URL: `https://xxxx.ui.nabu.casa` — works from anywhere, requires a Nabu Casa subscription (~$6.50/month)

In this project the Nabu Casa URL is used so the agent on the NUC can reach HA even when running inside the Windows host (not inside the HA VM).

### Step 3 — Configure Environment Variables

```powershell
Copy-Item .env.example .env
notepad .env
```

Fill in all values:

```env
# Home Assistant — local IP or Nabu Casa cloud URL
HA_URL=https://xxxx.ui.nabu.casa

# Long-lived token from Step 2
HA_TOKEN=your_ha_token_here

# Anthropic API key from console.anthropic.com
ANTHROPIC_API_KEY=sk-ant-...

# From Meta Developer Portal (see Step 5)
WHATSAPP_TOKEN=your_permanent_system_user_token
WHATSAPP_PHONE_NUMBER_ID=your_phone_number_id

# Any string you choose — must match what you enter in Meta's webhook settings
WHATSAPP_VERIFY_TOKEN=pick-any-secret-string

# Your location for weather (Austin TX defaults)
LATITUDE=30.2672
LONGITUDE=-97.7431
```

**Why a verify token?**
When you register a webhook URL with Meta, they send a GET request containing a challenge string and your verify token to confirm you control that URL. Your server echoes back the challenge only if the token matches. This is a lightweight ownership verification mechanism — without it, anyone could register any URL as a webhook target.

### Step 4 — Add HA Helpers and Safety Automations

**Why safety automations in HA?**
The Python agent enforces safety in software: it checks whether another zone is already running before activating a new one, and it turns zones off after their timer expires. But what if the agent process crashes mid-run? The zone switch stays on indefinitely. The HA automations in `ha_config/automations.yaml` are a **hardware-layer failsafe** that operates entirely inside HA, completely independent of Python. Even if the NUC loses power and the agent dies, HA (running in its own VM) will cut the zone off after 30 minutes.

**Two types of automations are included:**
1. **30-minute timeout** — if any zone has been ON for 30 minutes, HA turns it off automatically and sends a notification
2. **One-zone-at-a-time enforcement** — if a second zone turns on while one is already running, HA immediately turns off the newcomer

**Add automations:**
- Option A (YAML mode): paste the contents of `ha_config/automations.yaml` into your HA `automations.yaml` file
- Option B (UI): Settings → Automations → ⋮ → Edit in YAML → paste each automation block individually

**Add input_number helpers (optional):**
These create sliders in the HA dashboard for adjusting zone durations without editing code. In `configuration.yaml`:
```yaml
input_number: !include ha_config/helpers.yaml
```
Or create them manually: Settings → Devices & Services → Helpers → + Create helper → Number.

### Step 5 — Set Up WhatsApp (Meta Cloud API)

**Why Meta Cloud API?**
Meta provides a free tier for WhatsApp messaging through their Cloud API. You don't need to host your own WhatsApp Business infrastructure or pay per message at low volumes. It's the official, approved path for sending and receiving WhatsApp messages programmatically.

**Create the app:**
1. Go to developers.facebook.com → My Apps → Create App → Business type
2. Add the **WhatsApp** product to your app
3. Under WhatsApp → API Setup:
   - Note your **Phone Number ID**
   - Add your personal number as a test recipient (required while the app is in development mode)
4. Under WhatsApp → Configuration → Webhook:
   - Callback URL: `https://sprinkler.whitlockhouse.org/webhook` (your permanent domain from Step 6)
   - Verify token: the value you set as `WHATSAPP_VERIFY_TOKEN` in `.env`
   - Subscribe to: **messages**

**Get a permanent access token — this is critical:**
The default access token on the API Setup page expires every 24 hours. For a home automation agent that runs continuously, you need a non-expiring token. Here's how:

1. Go to business.facebook.com → Settings → Users → **System Users**
2. Create a System User (name: "sprinkler-agent", role: Admin)
3. Add Assets → Apps → select your WhatsApp app → check "Manage app" → Save
4. Click **Generate New Token** on the system user
5. Select your app, check `whatsapp_business_messaging` and `whatsapp_business_management`
6. Set expiration to **Never** → Generate → copy immediately

This token never expires. Set it as `WHATSAPP_TOKEN` in your `.env`.

### Step 6 — Set Up a Permanent Public HTTPS URL

Meta requires a **public HTTPS URL** to deliver webhook events to your agent. The agent runs on your local NUC, so you need a tunnel that exposes it to the internet.

**Why Cloudflare Tunnel instead of ngrok?**
ngrok's free tier generates a new random URL every time it starts — meaning after every reboot, you'd need to log into the Meta Developer Portal and update your webhook URL. Cloudflare Tunnel with a named domain gives you a permanent URL that never changes, survives reboots, and runs as a Windows service automatically.

**Step 6a — Register a domain through Cloudflare Registrar:**
Go to cloudflare.com → Domain Registration → Register Domains. Search for your domain and purchase it. Cloudflare Registrar charges at-cost (no markup) — typically $8–12/year for a `.com` or `.org`. Because Cloudflare both registers the domain and runs the tunnel, DNS is already managed in the right place with no extra configuration.

**Step 6b — Install cloudflared on Windows:**
```powershell
winget install --id Cloudflare.cloudflared
```

**Step 6c — Authenticate and create a named tunnel** (run in PowerShell as Administrator):
```powershell
# Log in — opens a browser window
cloudflared tunnel login

# Create the tunnel (generates a tunnel ID and credentials file)
cloudflared tunnel create sprinkler-agent

# Route your subdomain to the tunnel (auto-creates the DNS CNAME record)
cloudflared tunnel route dns sprinkler-agent sprinkler.yourdomain.com
```

**Step 6d — Create the config file:**
```powershell
notepad "$env:USERPROFILE\.cloudflared\config.yml"
```
Paste this, replacing `YOUR_TUNNEL_ID` with the ID printed in Step 6c:
```yaml
tunnel: YOUR_TUNNEL_ID
credentials-file: C:\Users\yourusername\.cloudflared\YOUR_TUNNEL_ID.json

ingress:
  - hostname: sprinkler.yourdomain.com
    service: http://localhost:8000
  - service: http_status:404
```

**Why the ingress rules?**
The first rule tells cloudflared to forward all traffic arriving at `sprinkler.yourdomain.com` to your local agent on port 8000. The second rule (catchall) returns a 404 for any other hostname — required by cloudflared's config format.

**Step 6e — Install cloudflared as a Windows service:**
```powershell
cloudflared --config "$env:USERPROFILE\.cloudflared\config.yml" service install
Start-Service cloudflared
```

Installing as a service means cloudflared starts automatically on boot, before any user logs in. Verify it's running:
```powershell
Get-Service cloudflared
```

**Troubleshooting — tunnel shows Inactive in Cloudflare dashboard:**
If the tunnel shows as inactive, the service likely isn't loading the config file. Uninstall and reinstall with the explicit config path:
```powershell
cloudflared service uninstall
# Reboot the NUC to fully clear the old service
cloudflared --config "$env:USERPROFILE\.cloudflared\config.yml" service install
Start-Service cloudflared
```

**Note on VPNs:** If you're on a VPN, `sprinkler.yourdomain.com` may not resolve because the VPN has its own DNS resolver that hasn't picked up the new record. Test from your phone on cellular, or temporarily disconnect the VPN.

### Step 7 — Run the Agent

In PowerShell on the NUC:

```powershell
cd $env:USERPROFILE\sprinkler_agent
python main.py
```

You should see:
```
INFO:     Application startup complete.
```

Test HA connectivity (from your Mac or phone):
```
https://sprinkler.yourdomain.com/health
```

Test the agent directly without WhatsApp:
```bash
curl -X POST https://sprinkler.yourdomain.com/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "What is the status of all zones?"}'
```

### Step 8 — Auto-Start on Boot

The cloudflared tunnel starts automatically via the Windows service (Step 6e). The Python agent needs a Task Scheduler entry.

**Why Task Scheduler instead of a Windows service for the agent?**
Windows services run as the SYSTEM account by default and have restricted access to user-specific files (like `.env` in your home directory). Task Scheduler tasks can run as your user account with full access to your files, which is simpler for a personal project.

**Create the Task Scheduler entry:**
1. Open Task Scheduler (search in Start menu)
2. Click **Create Basic Task** → Name: `Sprinkler Agent` → Next
3. Trigger: **When the computer starts** → Next
4. Action: **Start a program** → Next
5. Program/script: `C:\Users\yourusername\sprinkler_agent\venv\Scripts\python.exe`
   (or `python` if using system Python)
6. Add arguments: `main.py`
7. Start in: `C:\Users\yourusername\sprinkler_agent`
8. Finish → open Properties when prompted
9. General tab → check **Run whether user is logged on or not**
10. Enter your Windows account password when prompted

**Note on Windows 11 Home and remote access:**
Windows 11 Home doesn't support inbound RDP (Remote Desktop Protocol). For remote access to the NUC, use **Chrome Remote Desktop** (remotedesktop.google.com) — it's free, works on Home edition, and requires only Chrome to be installed. AnyDesk is a good alternative that doesn't require Chrome.

---

## Testing Without WhatsApp

The `/chat` endpoint lets you talk to the agent directly from the command line or any HTTP client:

```bash
# Check zone status
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "What zones are running?"}'

# Check weather
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "Should I water today?"}'

# Run a zone (this will actually activate the sprinkler)
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "Run zone 1 for 5 minutes"}'

# Run a preset schedule
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "Run the morning new sod schedule"}'

# Query watering history
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "When did zone 2 last run?"}'

# Get a weekly summary
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "Show me everything I watered this week"}'
```

You can also open `http://localhost:8000/docs` for the interactive FastAPI Swagger UI, which lets you test all endpoints in a browser.

---

## Extending the Agent

**Add a new wired zone:**
In `config.py`, find the zone's entry and change `"wired": True`. Set the correct `entity_id` matching what HA shows. No other changes needed — the agent reads `config.py` at startup and includes all wired zones automatically.

**Add a new watering schedule:**
In `config.py`, add an entry to the `SCHEDULES` dict with a name, description, and list of zone/minute pairs. The `run_schedule` tool reads available schedules at runtime and passes them to the LLM via its docstring.

**Add a new tool:**
1. Write an async (or sync) function in `tools.py` decorated with `@tool`
2. Write a detailed docstring — this is the LLM's only guide for when and how to invoke it
3. Add it to `ALL_TOOLS` at the bottom of the file

The LangGraph agent automatically includes all tools in `ALL_TOOLS` — no changes to `agent.py` are needed.

**Log additional event types:**
The `append_event()` function in `history.py` accepts any dict. Add an `event_type` like `"zone_skipped"` or `"manual_stop"` and the `get_watering_history` tool will include it in summaries. The `format_local_time()` function handles timezone display for any new event type automatically.

**Persist conversation history across restarts:**
Replace the `_conversations: dict[str, list]` in `agent.py` with a Redis or SQLite backend. The interface is just `dict[str, list[BaseMessage]]` — a thin wrapper around either backend would be a drop-in replacement.

**Add a second messaging interface (SMS, Telegram, email):**
The `chat(user_id, message)` function in `agent.py` is interface-agnostic — it takes any string as a user ID and any string as a message. Add a new FastAPI router that calls `chat()` with the appropriate user identifier and you have a second channel with zero changes to the agent logic.

**Expand to backyard zones (ZEN16 #2 and #3):**
1. Wire ZEN16 #2 relays to Hunter PC-300 zone terminals for zones 4–6
2. Verify the HA entity IDs for the new relays
3. In `config.py`, update zones 4–6: set `"wired": True` and correct the `entity_id`
4. Add corresponding safety automations to `ha_config/automations.yaml`
5. Restart the agent — it will immediately be aware of the new zones
