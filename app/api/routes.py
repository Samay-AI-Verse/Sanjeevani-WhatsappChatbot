import json
from fastapi import APIRouter, Request
from ..core.config import VERIFY_TOKEN
from ..core.logger import logger
from ..models.enums import ConversationState
from ..services.db_service import (
    get_user_profile, 
    update_user_profile, 
    get_recent_orders, 
    get_user_addresses, 
    get_conversation_state,
    update_conversation_state,
    create_order
)
from ..services.nlu_service import extract_nlu
from ..services.rule_engine import RuleEngine
from ..services.nlg_service import generate_and_send_response
from ..services.whatsapp import send_whatsapp_text, send_whatsapp_buttons, send_whatsapp_list

router = APIRouter()

@router.get("/webhook")
async def verify_webhook(request: Request):
    p = dict(request.query_params)
    if p.get("hub.mode") == "subscribe" and p.get("hub.verify_token") == VERIFY_TOKEN:
        return int(p.get("hub.challenge", 0))
    return "Verification failed"

@router.post("/webhook")
async def handle_message(request: Request):
    try:
        # Twilio sends form data
        data = await request.form()
    except:
        return {"status": "no_form_data"}

    user_number = data.get("From", "")
    user_text = data.get("Body", "")

    if not user_number or not user_text:
        # Check for JSON fallback
        try:
            json_data = await request.json()
            user_number = json_data.get("From")
            user_text = json_data.get("Body")
        except:
            pass

    if not user_number or not user_text:
        return {"status": "ignored"}

    interactive_data = data.get("ButtonPayload")
    # If there is a button payload, treat it as the primary text for NLU
    if interactive_data:
        user_text = interactive_data.replace("_", " ")

    logger.info(f"📩 Message from {user_number}: {user_text}")

    # 1. Fetch User Profile & State
    profile = await get_user_profile(user_number) or {"user_id": user_number}
    state_doc = await get_conversation_state(user_number)
    
    # State Override: If profile is complete but state is stuck in onboarding, force to GREETING
    current_state = state_doc.get("state", ConversationState.COLLECT_LANGUAGE)
    if profile.get("language") and profile.get("name") and profile.get("gender") and profile.get("age"):
        if current_state in [
            ConversationState.COLLECT_LANGUAGE, ConversationState.COLLECT_NAME, 
            ConversationState.COLLECT_GENDER, ConversationState.COLLECT_AGE
        ]:
            current_state = ConversationState.GREETING
    else:
        # Force onboarding if profile is missing fields, even if state is greeting
        if not profile.get("language"):
            current_state = ConversationState.COLLECT_LANGUAGE
        elif not profile.get("name"):
            current_state = ConversationState.COLLECT_NAME
        elif not profile.get("gender"):
            current_state = ConversationState.COLLECT_GENDER
        elif not profile.get("age"):
            current_state = ConversationState.COLLECT_AGE
    
    # If migrating from old general state
    if current_state == "general": 
        current_state = ConversationState.COLLECT_LANGUAGE if not profile.get("language") else ConversationState.GREETING

    temp_data = state_doc.get("temp_data", {})

    # 2. Extract Intent and Entities (NLU) purely via JSON
    nlu_result = extract_nlu(user_text, current_state)
    logger.info(f"🧠 NLU Result: {nlu_result.model_dump_json()}")

    # 3. Rule Engine Decision
    new_state, new_temp, backend_command = RuleEngine.process(
        nlu_result=nlu_result,
        current_state=current_state,
        user_profile=profile,
        temp_data=temp_data,
        user_text=user_text
    )
    logger.info(f"🚦 Rule Engine: state -> {new_state}, cmd -> {backend_command}")

    # 4. Handle DB side-effects based on backend_command
    # Update profile fields ONLY if user is explicitly providing info
    if nlu_result.intent == "PROVIDE_INFO" and any(vars(nlu_result.extracted_user_fields).values()):
        await update_user_profile(user_number, nlu_result.extracted_user_fields.model_dump(exclude_none=True))
        profile = await get_user_profile(user_number) # Reload to get names for NLG

    recent_orders = []
    if backend_command == "show_tracking":
        recent_orders = await get_recent_orders(user_number)

    order_id = None
    if backend_command == "finalize_order":
        order_data = {
            "medicine_name": new_temp.get("medicine_name"),
            "quantity": new_temp.get("quantity"),
            "price": new_temp.get("price", 250), # Hardcoded MVP price
        }
        order_id = await create_order(user_number, order_data)
        new_temp["order_id"] = order_id
        # Clearing temp data since order is done
        new_temp = {}

    # 5. Save updated state memory
    await update_conversation_state(user_number, new_state, new_temp)

    # 6. Generate Response (NLG) and send
    # Pass the temp_data before it was cleared if we are finalizing, so NLG can use the order_id
    resp_temp_data = temp_data if backend_command != "finalize_order" else {"order_id": order_id}
    if backend_command == "ask_order_confirmation":
        resp_temp_data = new_temp # need updated temp data to show summary

    generate_and_send_response(user_number, backend_command, profile, resp_temp_data, recent_orders, provider="twilio", user_text=user_text)

    return {"status": "success"}


# ==========================================
# META CLOUD API ROUTES
# ==========================================

from ..core.config import META_VERIFY_TOKEN

@router.get("/webhook/meta")
async def verify_meta_webhook(request: Request):
    """Webhook verification for Meta Cloud API"""
    p = dict(request.query_params)
    if p.get("hub.mode") == "subscribe" and p.get("hub.verify_token") == META_VERIFY_TOKEN:
        return int(p.get("hub.challenge", 0))
    return "Verification failed"

@router.post("/webhook/meta")
async def handle_meta_message(request: Request):
    """Handle incoming messages from Meta Cloud API"""
    try:
        data = await request.json()
    except:
        return {"status": "success", "reason": "No JSON"}

    # Extract info from Meta's nested JSON payload
    try:
        entry = data["entry"][0]
        changes = entry["changes"][0]
        value = changes["value"]
        
        # WhatsApp status completely ignores this
        if "statuses" in value:
            return {"status": "success"}
            
        messages = value.get("messages", [])
        if not messages:
            return {"status": "success"}
            
        msg = messages[0]
        user_number = f"whatsapp:+{msg['from']}" # Standardize to match DB
        
        # Determine message type and extract text
        user_text = ""
        interactive_data = None
        
        if msg["type"] == "text":
            user_text = msg["text"]["body"]
        elif msg["type"] == "interactive":
            interactive = msg["interactive"]
            if interactive["type"] == "button_reply":
                interactive_data = interactive["button_reply"]["id"]
                user_text = interactive["button_reply"]["title"]
            elif interactive["type"] == "list_reply":
                interactive_data = interactive["list_reply"]["id"]
                user_text = interactive["list_reply"]["title"]
    except Exception as e:
        logger.error(f"Meta JSON parsing error: {e}")
        return {"status": "success"}

    logger.info(f"📩 [META] Message from {user_number}: {user_text}")

    # The rest of the pipeline is 100% identical to Twilio
    profile = await get_user_profile(user_number) or {"user_id": user_number}
    state_doc = await get_conversation_state(user_number)
    
    current_state = state_doc.get("state", ConversationState.COLLECT_LANGUAGE)
    if profile.get("language") and profile.get("name") and profile.get("gender") and profile.get("age"):
        if current_state in [
            ConversationState.COLLECT_LANGUAGE, ConversationState.COLLECT_NAME, 
            ConversationState.COLLECT_GENDER, ConversationState.COLLECT_AGE
        ]:
            current_state = ConversationState.GREETING
    else:
        if not profile.get("language"): current_state = ConversationState.COLLECT_LANGUAGE
        elif not profile.get("name"): current_state = ConversationState.COLLECT_NAME
        elif not profile.get("gender"): current_state = ConversationState.COLLECT_GENDER
        elif not profile.get("age"): current_state = ConversationState.COLLECT_AGE
    
    if current_state == "general": 
        current_state = ConversationState.COLLECT_LANGUAGE if not profile.get("language") else ConversationState.GREETING

    temp_data = state_doc.get("temp_data", {})
    nlu_result = extract_nlu(user_text, current_state)
    new_state, new_temp, backend_command = RuleEngine.process(
        nlu_result=nlu_result, current_state=current_state,
        user_profile=profile, temp_data=temp_data, user_text=user_text
    )

    # Update profile fields ONLY if user is explicitly providing info
    if nlu_result.intent == "PROVIDE_INFO" and any(vars(nlu_result.extracted_user_fields).values()):
        await update_user_profile(user_number, nlu_result.extracted_user_fields.model_dump(exclude_none=True))
        profile = await get_user_profile(user_number)

    recent_orders = []
    if backend_command == "show_tracking":
        recent_orders = await get_recent_orders(user_number)

    order_id = None
    if backend_command == "finalize_order":
        order_data = {"medicine_name": new_temp.get("medicine_name"), "quantity": new_temp.get("quantity"), "price": 250}
        order_id = await create_order(user_number, order_data)
        new_temp = {}

    await update_conversation_state(user_number, new_state, new_temp)

    resp_temp_data = temp_data if backend_command != "finalize_order" else {"order_id": order_id}
    if backend_command == "ask_order_confirmation":
        resp_temp_data = new_temp

    # Important: Tell NLG Service to use META as the provider!
    generate_and_send_response(user_number, backend_command, profile, resp_temp_data, recent_orders, provider="meta", user_text=user_text)

    return {"status": "success"}
