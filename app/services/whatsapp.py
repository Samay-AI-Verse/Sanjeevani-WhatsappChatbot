from twilio.rest import Client
from typing import List, Dict
from ..core.config import TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_WHATSAPP_NUMBER
from ..core.logger import logger

# Initialize Twilio Client
client = None
if TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN:
    client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
    logger.info("✅ Twilio Client Initialized")
else:
    logger.error("⚠️ TWILIO_ACCOUNT_SID or AUTH_TOKEN is missing!")

def send_whatsapp_text(to: str, text: str):
    if not client:
        logger.error("Twilio client not initialized!")
        return
    try:
        # User number must be in whatsapp: format for twilio
        to_formatted = to if to.startswith("whatsapp:") else f"whatsapp:{to}"
        client.messages.create(
            from_=TWILIO_WHATSAPP_NUMBER,
            body=text,
            to=to_formatted
        )
    except Exception as e:
        logger.error(f"Twilio Send Text Error: {e}")

def send_whatsapp_buttons(to: str, text: str, buttons: List[Dict[str, str]]):
    """
    Simulated buttons using text-based choices for Twilio Sandbox simplicity.
    Real buttons require approved templates.
    """
    if not client:
        return
    
    choice_text = text + "\n"
    for btn in buttons:
        choice_text += f"\n👉 {btn['title']}"
    
    send_whatsapp_text(to, choice_text)

def send_whatsapp_list(to: str, text: str, button_text: str, sections: List[Dict]):
    """
    Simulated list using text.
    """
    choice_text = text + "\n"
    for section in sections:
        for row in section.get("rows", []):
            choice_text += f"\n📍 {row['title']}"
            
    send_whatsapp_text(to, choice_text)
