import json
import httpx
from groq import Groq
from typing import Dict, Any
from ..core.config import GROQ_API_KEY, GROQ_MODEL
from ..core.logger import logger
from ..models.schemas import NLUExtractionResult

groq_client = None
if GROQ_API_KEY:
    groq_client = Groq(api_key=GROQ_API_KEY, http_client=httpx.Client())

EXTRACTOR_SYSTEM_PROMPT = """
You are a strict Natural Language Understanding (NLU) engine for a pharmacy WhatsApp bot.
Your ONLY job is to extract facts and intents from the user's message.
DO NOT generate conversational replies. DO NOT guess medicine names if misspelled badly.

Current State Context: {current_state}

Outputs MUST strictly match this JSON schema:
{
  "intent": "ORDER_MEDICINE | CONFIRM | CANCEL | PROVIDE_INFO | TRACK_ORDER | GREETING | COMPLAINT | UNKNOWN",
  "items": [{"name": "Dolo 650", "quantity": 10, "dosage": null}],
  "extracted_user_fields": {"name": null, "age": null, "gender": null, "language": null},
  "prescription_check_needed": false,
  "confidence": 0.95,
  "user_message_type": "text"
}

Intent Guide:
- GREETING: basic hello, hi
- ORDER_MEDICINE: asking for a drug
- CONFIRM: yes, haan, theek hai, ok, done, confirm
- CANCEL: no, reject, stop, cancel
- PROVIDE_INFO: giving name, age, language, or just a number when asked for quantity
- TRACK_ORDER: where is my order
- COMPLAINT: angry, frustrated, need human

Output ONLY valid JSON.
"""

def extract_nlu(user_text: str, current_state: str) -> NLUExtractionResult:
    if not groq_client:
        logger.error("Groq client not initialized")
        return NLUExtractionResult(intent="UNKNOWN", confidence=0.0)
    
    prompt = EXTRACTOR_SYSTEM_PROMPT.replace("{current_state}", current_state)
    
    try:
        completion = groq_client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[
                {"role": "system", "content": prompt},
                {"role": "user", "content": user_text},
            ],
            temperature=0.0,
            response_format={"type": "json_object"},
        )
        content = completion.choices[0].message.content.strip()
        data = json.loads(content)
        
        # Clean up types before Pydantic validation
        if "items" in data:
            for item in data["items"]:
                if isinstance(item.get("quantity"), str):
                    try:
                        item["quantity"] = int(''.join(filter(str.isdigit, item["quantity"])))
                    except:
                        item["quantity"] = None
                        
        if "extracted_user_fields" in data:
            age = data["extracted_user_fields"].get("age")
            if isinstance(age, str):
                try: 
                    data["extracted_user_fields"]["age"] = int(''.join(filter(str.isdigit, age)))
                except: 
                    data["extracted_user_fields"]["age"] = None
                
        return NLUExtractionResult(**data)
    except Exception as e:
        logger.error(f"NLU Extraction Error: {e}")
        return NLUExtractionResult(intent="UNKNOWN", confidence=0.0)
