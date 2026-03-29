"""
Entry point. Starts the FastAPI server.

Run with:
  python main.py

Or for development (auto-reload on file changes):
  uvicorn main:app --reload --port 8000

To expose to WhatsApp (Meta needs a public HTTPS URL):
  ngrok http 8000
  Then set your webhook URL in Meta Developer Portal to:
  https://<your-ngrok-id>.ngrok.io/webhook
"""

from dotenv import load_dotenv
load_dotenv()   # Must be before any other imports that use os.getenv

import uvicorn
from fastapi import FastAPI
from whatsapp_handler import router as whatsapp_router

app = FastAPI(title="Sprinkler Agent")
app.include_router(whatsapp_router)


@app.get("/")
async def root():
    return {"status": "Sprinkler agent running"}


@app.get("/health")
async def health():
    """Quick connectivity check for HA."""
    from ha_client import ha
    try:
        state = await ha.get_state("homeassistant.running")
        return {"status": "ok", "ha": state.get("state", "unknown")}
    except Exception as e:
        return {"status": "error", "detail": str(e)}


@app.post("/chat")
async def test_chat(request: dict):
    """
    Test endpoint — send a message to the agent without WhatsApp.
    Body: {"message": "Water zone 1 for 5 minutes"}
    """
    from agent import chat
    message = request.get("message", "")
    if not message:
        return {"error": "No message provided"}
    reply = await chat(user_id="test_user", message=message)
    return {"reply": reply}


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
