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
You are a context-aware Natural Language Understanding (NLU) engine for Sanjeevani, a smart pharmacy bot.
Your ONLY job is to extract facts, intents, and medicine requests from the user's message.

Instruction:
1. Extract all medicines mentioned (e.g., "Augmentin", "Dolo 650", "Metformin").
2. For each medicine, extract the Quantity (default to 1 if not mentioned) and strip unit words like "strips", "tablets", "packs".
3. Identify the user's intent: GREETING, ORDER_MEDICINE, TRACK_ORDER, PROVIDE_INFO, COMPLAINT, or PRICE_ISSUE.
4. PRICE_ISSUE should be used if the user complains that prices are high or asks for a discount/low rate.
5. DO NOT generate conversational replies.

Current State Context: {current_state}

Outputs MUST strictly match this JSON schema:
{
  "intent": "ORDER_MEDICINE | CONFIRM | CANCEL | PROVIDE_INFO | TRACK_ORDER | GREETING | COMPLAINT | PRICE_ISSUE | UNKNOWN",
  "items": [{"name": "Dolo 650", "quantity": 10, "dosage": null}],
  "extracted_user_fields": {"name": null, "age": null, "gender": null, "language": null},
  "prescription_check_needed": false,
  "confidence": 0.95,
  "user_message_type": "text"
}

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
