"""
WhatsApp / Meta Cloud API handler.

Handles:
  GET /webhook  → webhook verification (Meta requires this on setup)
  POST /webhook → incoming messages from users

Meta API docs: https://developers.facebook.com/docs/whatsapp/cloud-api
"""

import os
import httpx
from fastapi import APIRouter, BackgroundTasks, Request, HTTPException, Query
from fastapi.responses import PlainTextResponse

from agent import chat, clear_conversation

# Tracks message IDs we've already processed so duplicate webhook deliveries are ignored
_processed_message_ids: set[str] = set()

router = APIRouter()


# ---------------------------------------------------------------------------
# Webhook verification (one-time setup with Meta)
# ---------------------------------------------------------------------------

@router.get("/webhook")
async def verify_webhook(
    hub_mode: str = Query(None, alias="hub.mode"),
    hub_verify_token: str = Query(None, alias="hub.verify_token"),
    hub_challenge: str = Query(None, alias="hub.challenge"),
):
    """
    Meta sends a GET request to verify your webhook URL.
    It must respond with the hub.challenge value.
    Set WHATSAPP_VERIFY_TOKEN in .env to any string you choose,
    then enter the same string in the Meta Developer Portal.
    """
    verify_token = os.getenv("WHATSAPP_VERIFY_TOKEN", "")

    if hub_mode == "subscribe" and hub_verify_token == verify_token:
        print("WhatsApp webhook verified.")
        return PlainTextResponse(hub_challenge)

    print(f"Webhook verification failed. Got token: {hub_verify_token}")
    raise HTTPException(status_code=403, detail="Verification failed")


# ---------------------------------------------------------------------------
# Incoming messages
# ---------------------------------------------------------------------------

@router.post("/webhook")
async def receive_message(request: Request, background_tasks: BackgroundTasks):
    """
    Meta sends a POST for each incoming WhatsApp message.
    We acknowledge immediately (return 200) and process in the background.
    This prevents Meta from retrying when the agent takes a long time (e.g. running a zone).
    """
    body = await request.json()

    # Navigate Meta's nested JSON structure
    try:
        entry = body["entry"][0]
        change = entry["changes"][0]
        value = change["value"]

        # Ignore status updates (delivery receipts, read receipts)
        if "statuses" in value and "messages" not in value:
            return {"status": "ok"}

        message_obj = value["messages"][0]
        message_id = message_obj.get("id", "")
        from_number = message_obj["from"]
        msg_type = message_obj.get("type", "")

        # Deduplicate — Meta sometimes delivers the same message more than once
        if message_id in _processed_message_ids:
            print(f"Duplicate message ignored: {message_id}")
            return {"status": "ok"}
        _processed_message_ids.add(message_id)
        # Keep the set from growing forever (cap at 1000 entries)
        if len(_processed_message_ids) > 1000:
            _processed_message_ids.clear()

        if msg_type != "text":
            background_tasks.add_task(
                send_whatsapp_message,
                from_number,
                "Sorry, I can only read text messages right now.",
            )
            return {"status": "ok"}

        user_text = message_obj["text"]["body"].strip()
        print(f"Message from {from_number}: {user_text}")

    except (KeyError, IndexError) as e:
        print(f"Non-message webhook event: {e}")
        return {"status": "ok"}

    # Special commands — handle inline (fast, no agent needed)
    if user_text.lower() in ("reset", "clear", "start over"):
        clear_conversation(from_number)
        background_tasks.add_task(
            send_whatsapp_message, from_number, "Conversation cleared. How can I help?"
        )
        return {"status": "ok"}

    # Hand off to agent in the background — return 200 to Meta immediately
    background_tasks.add_task(process_message, from_number, user_text)
    return {"status": "ok"}


async def process_message(from_number: str, user_text: str):
    """Run the agent and send the reply. Runs as a background task."""
    try:
        reply = await chat(user_id=from_number, message=user_text)
    except Exception as e:
        print(f"Agent error: {e}")
        reply = "Sorry, I had an error processing that. Please try again."

    await send_whatsapp_message(from_number, reply)


# ---------------------------------------------------------------------------
# Sending messages back to WhatsApp
# ---------------------------------------------------------------------------

async def send_whatsapp_message(to: str, text: str):
    """Send a text reply to a WhatsApp user."""
    phone_number_id = os.getenv("WHATSAPP_PHONE_NUMBER_ID", "")
    token = os.getenv("WHATSAPP_TOKEN", "")

    if not phone_number_id or not token:
        print(f"WhatsApp not configured. Would have sent to {to}: {text}")
        return

    url = f"https://graph.facebook.com/v19.0/{phone_number_id}/messages"
    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "text",
        "text": {"body": text},
    }
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(url, json=payload, headers=headers)

    if resp.status_code != 200:
        print(f"WhatsApp send error {resp.status_code}: {resp.text}")
    else:
        print(f"Sent to {to}: {text[:60]}...")
