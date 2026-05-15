"""
WhatsApp Client
---------------
Sends messages via Meta's WhatsApp Business Cloud API.
"""

import os
import httpx

PHONE_ID = os.environ.get("WHATSAPP_PHONE_NUMBER_ID")
TOKEN = os.environ.get("WHATSAPP_ACCESS_TOKEN")
API_URL = f"https://graph.facebook.com/v19.0/{PHONE_ID}/messages"


async def send_message(to: str, text: str) -> bool:
    """Send a plain text WhatsApp message. Returns True on success."""
    if not PHONE_ID or not TOKEN:
        print(f"[DEV MODE] Would send to {to}: {text}")
        return True

    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "text",
        "text": {"body": text},
    }
    headers = {
        "Authorization": f"Bearer {TOKEN}",
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient() as client:
        response = await client.post(API_URL, json=payload, headers=headers)
        return response.status_code == 200
