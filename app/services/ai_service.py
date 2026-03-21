import json
import httpx
from groq import Groq
from typing import Optional, Dict, List
from ..core.config import GROQ_API_KEY, GROQ_MODEL
from ..core.logger import logger

# Groq Setup
groq_client = None
if GROQ_API_KEY:
    groq_client = Groq(api_key=GROQ_API_KEY, http_client=httpx.Client())
    logger.info("✅ Groq AI Configured")
else:
    logger.error("⚠️ GROQ_API_KEY is missing!")

SYSTEM_INSTRUCTION = """
You are "Sanjeevani Care" - a beautiful, efficient, and direct Pharmacy Assistant.
Your goal is to provide a premium experience without being repetitive or boring.

------------------------------------
CORE RULES (STOP THE LOOPS)
------------------------------------

1. **NO REPETITION**: NEVER start sentences with "I understand that..." or "I have noted that...". Be direct.
   - ❌ "I understand your name is Samay. Could you tell me your age?"
   - ✅ "Nice to meet you, Samay! How old are you?"

2. **CHECK CONTEXT FIRST**: Look at "CURRENT USER DATA". 
   - If a field (Name, Gender, Age, Language) is ALREADY present, NEVER ask for it again. 
   - Move immediately to the next missing field or to "How can I help you order?".

3. **MANDATORY BEAUTIFUL WELCOME**: 
   - If the user says "hi", "hello", or "hii" for the first time, respond with a stunning welcome:
     "🌟 *Welcome to Sanjeevani Care* 🌟\nYour health is our priority! Let's get started with a quick profile setup."

4. **STRICT ONBOARDING FLOW (ONLY ASK ONCE)**:
   - 1. **Language**: Ask for (English/Hindi/Marathi).
   - 2. **Name**
   - 3. **Gender**
   - 4. **Age**
   - Once all 4 are present, NEVER ask onboarding questions again.

5. **SMART CONFIRMATION (RECOGNIZE TYPOS)**:
   - Users might type typos like "confrom", "yess", "thek", "ok", "haan", "theek h".
   - Recognize these as "CONFIRM_ORDER" or "ORDER_PLACED" intents immediately.
   - Do not ask for address if it's already in the context (though for this MVP, we assume local pickup).
   - Once confirmed, finalize the order immediately.

6. **DETAILED ORDER SUMMARIES**:
   - Use structure and emojis for summaries and confirmations.

------------------------------------
OUTPUT FORMAT (STRICT JSON)
------------------------------------

Always respond ONLY in JSON:

{
  "intent": "ASK_LANGUAGE" | "ASK_ONBOARDING" | "WELCOME" | "ORDER_MEDICINE" | "CONFIRM_ORDER" | "ORDER_PLACED" | "TRACK_ORDER" | "GENERAL",
  "user_info": {
    "name": "...", 
    "gender": "...", 
    "age": "...", 
    "language": "..."
  },
  "medicine_name": "...",
  "quantity": "...",
  "price": 250,
  "reply_text": "Your professional, non-repetitive message with emojis"
}

------------------------------------
ORDER TRACKING
------------------------------------

If user asks about order status:
- Show recent orders with status
- Intent: "TRACK_ORDER"
"""

def process_ai_interaction(
    user_text: str,
    user_profile: Optional[Dict],
    recent_orders: List[Dict],
    user_addresses: List[Dict],
    conversation_state: Dict,
) -> Dict:
    if not groq_client:
        return {"intent": "ERROR", "reply_text": "AI not configured."}

    # Context Building
    profile_str = "Unknown (First time user)"
    if user_profile:
        profile_str = json.dumps(
            {
                "name": user_profile.get("name"),
                "age": user_profile.get("age"),
                "gender": user_profile.get("gender"),
                "language": user_profile.get("language"),
            },
            indent=2,
        )

    addresses_str = "No saved addresses"
    if user_addresses:
        addresses_str = json.dumps(
            [
                {
                    "type": a.get("address_type"),
                    "line1": a.get("address_line1"),
                    "city": a.get("city"),
                    "pincode": a.get("pincode"),
                    "is_default": a.get("is_default"),
                }
                for a in user_addresses
            ],
            indent=2,
        )

    msg_context = f"""
    CURRENT USER DATA: {profile_str}
    RECENT ORDERS: {json.dumps(recent_orders)}
    SAVED ADDRESSES: {addresses_str}
    CONVERSATION STATE: {conversation_state.get('state', 'GENERAL')}
    TEMP DATA: {json.dumps(conversation_state.get('temp_data', {}))}
    USER MESSAGE: "{user_text}"
    """

    try:
        completion = groq_client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_INSTRUCTION},
                {"role": "user", "content": msg_context},
            ],
            temperature=0.1,
            response_format={"type": "json_object"},
        )
        return json.loads(completion.choices[0].message.content.strip())
    except Exception as e:
        logger.error(f"AI Error: {e}")
        return {
            "intent": "ERROR",
            "reply_text": "Sorry, I'm having trouble thinking right now.",
        }
