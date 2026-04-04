import json
import os
import re
import uuid
from typing import Any, Dict, List, Optional

import requests
from fastapi import APIRouter, File, Form, Request, UploadFile
from pydantic import BaseModel

from ..core.config import DEFAULT_PHARMACY_ID, VERIFY_TOKEN
from ..core.logger import logger
from ..models.enums import ConversationState
from ..services.db_service import (
    get_user_profile, 
    update_user_profile, 
    get_recent_orders, 
    get_user_addresses, 
    save_user_address,
    get_conversation_state,
    update_conversation_state,
    create_order,
    ensure_order_indexes,
)
from ..services.nlu_service import extract_nlu
from ..services.rule_engine import RuleEngine
from ..services.nlg_service import generate_and_send_response
from ..services.whatsapp import send_whatsapp_text, send_whatsapp_buttons, send_whatsapp_list
from ..services.pharmacy_routing import (
    bind_channel_to_pharmacy,
    ensure_channel_binding_indexes,
    resolve_pharmacy_id,
)
from ..services.medicine_matcher import MedicineMatcher
from ..services.system_api import call_agent_process_order

router = APIRouter()


class FastChatRequest(BaseModel):
    user_id: str
    message: str
    pharmacy_id: Optional[str] = None
    interactive_data: Optional[str] = None
    session_id: Optional[str] = None


def _resolve_onboarding_state(profile: Dict[str, Any], current_state: str) -> str:
    # App fast chat has language-only onboarding.
    if not profile.get("language"):
        return ConversationState.COLLECT_LANGUAGE
    if current_state in [
        ConversationState.COLLECT_LANGUAGE,
        ConversationState.COLLECT_NAME,
        ConversationState.COLLECT_GENDER,
        ConversationState.COLLECT_AGE,
    ]:
        return ConversationState.GREETING
    return current_state


def _norm_lang(lang_value: Optional[str]) -> str:
    raw = (lang_value or "").strip().lower()
    if "hind" in raw or "हिं" in raw:
        return "hindi"
    if "mara" in raw or "मराठ" in raw:
        return "marathi"
    return "english"


def _build_fast_reply(
    backend_command: str,
    profile: Dict[str, Any],
    temp_data: Dict[str, Any],
    recent_orders: List[Dict[str, Any]],
) -> str:
    name = profile.get("name") or "Friend"
    med = temp_data.get("medicine_name") or "medicine"
    qty = temp_data.get("quantity") or 1
    lang = _norm_lang(profile.get("language"))

    if backend_command in ["ask_language", "ask_language_again"]:
        if lang == "hindi":
            return "संजीवनी में आपका स्वागत है। कृपया भाषा चुनें: English / Hindi / Marathi."
        if lang == "marathi":
            return "संजीवनीमध्ये स्वागत आहे. कृपया भाषा निवडा: English / Hindi / Marathi."
        return "Welcome to Sanjeevani. Please choose language: English / Hindi / Marathi."
    if backend_command in ["ask_name", "ask_name_again"]:
        if lang == "hindi":
            return "ठीक है। अब दवाइयों का नाम और मात्रा बताइए।"
        if lang == "marathi":
            return "ठीक आहे. आता औषधाचे नाव आणि प्रमाण सांगा."
        return "Great. Now tell me medicine name and quantity."
    if backend_command in ["ask_gender", "ask_gender_again"]:
        if lang == "hindi":
            return "दवाइयों का नाम और मात्रा बताइए।"
        if lang == "marathi":
            return "औषधाचे नाव आणि प्रमाण सांगा."
        return "Please tell me medicine name and quantity."
    if backend_command in ["ask_age", "ask_age_again"]:
        if lang == "hindi":
            return "दवाइयों का नाम और मात्रा बताइए।"
        if lang == "marathi":
            return "औषधाचे नाव आणि प्रमाण सांगा."
        return "Please tell me medicine name and quantity."
    if backend_command == "registration_complete":
        if lang == "hindi":
            return f"भाषा सेट हो गई, {name}. अब बताइए कौन सी दवा चाहिए।"
        if lang == "marathi":
            return f"भाषा सेट झाली, {name}. आता कोणते औषध हवे ते सांगा."
        return f"Language set, {name}. Tell me which medicine you want to order."
    if backend_command in ["ask_quantity", "ask_quantity_again"]:
        return f"How many units of {med} do you need?"
    if backend_command in ["ask_order_confirmation", "ask_order_confirmation_again"]:
        return f"Please confirm order: {med} x {qty}. Reply YES to confirm or NO to cancel."
    if backend_command in ["ask_address_selection", "ask_full_address"]:
        return "Please share your full delivery address."
    if backend_command in ["ask_prescription_strict", "ask_prescription_strict_again"]:
        return (
            "Your order contains medicines that require prescription. "
            "Please upload a clear prescription image so I can extract all medicines."
        )
    if backend_command == "prescription_uploaded_success":
        return "Prescription received. Please confirm your order details."
    if backend_command == "finalize_order":
        return f"Order confirmed. Order ID: {temp_data.get('order_id', 'PENDING')}."
    if backend_command == "order_cancelled":
        return "Order cancelled."
    if backend_command == "show_tracking":
        if not recent_orders:
            return "No recent orders found."
        top = recent_orders[0]
        return f"Latest order {top.get('order_id')} is {top.get('status')}."
    if backend_command == "acknowledge_cancel":
        return "Okay, cancelled. How can I help you now?"
    return "Tell me medicine name and quantity, or upload a prescription image."


def _extract_text_from_image(file_path: str) -> Optional[str]:
    ocr_api_key = os.getenv("OCR_SPACE_API_KEY", "").strip()
    if not ocr_api_key:
        logger.warning("OCR_SPACE_API_KEY is not set, prescription OCR is disabled.")
        return None

    try:
        with open(file_path, "rb") as file_handle:
            response = requests.post(
                "https://api.ocr.space/parse/image",
                files={"file": file_handle},
                data={
                    "apikey": ocr_api_key,
                    "language": "eng",
                    "isOverlayRequired": False,
                    "detectOrientation": True,
                    "scale": True,
                    "OCREngine": 2,
                },
                timeout=30,
            )

        payload = response.json()
        if payload.get("IsErroredOnProcessing"):
            logger.error(f"OCR processing error: {payload.get('ErrorMessage')}")
            return None

        parsed = payload.get("ParsedResults") or []
        if not parsed:
            return None

        text = (parsed[0].get("ParsedText") or "").strip()
        return text or None
    except Exception as exc:
        logger.error(f"OCR extraction failed: {exc}")
        return None


def _extract_medicine_candidates_from_text(ocr_text: str) -> List[str]:
    if not ocr_text:
        return []

    ignored = {
        "dr", "doctor", "name", "age", "sex", "date", "tab", "tablet", "capsule", "syrup",
        "take", "morning", "night", "after", "before", "food", "daily", "days",
    }
    names: List[str] = []

    line_pattern = re.compile(r"([A-Za-z][A-Za-z0-9\-\+]{2,}(?:\s+[A-Za-z0-9\-\+]{2,})?)")
    for raw_line in ocr_text.splitlines():
        line = raw_line.strip()
        if len(line) < 3:
            continue

        for match in line_pattern.findall(line):
            candidate = match.strip()
            lower = candidate.lower()
            if lower in ignored:
                continue
            if re.search(r"\b(mg|ml|mcg|gm|g)\b", lower):
                candidate = re.sub(r"\b(mg|ml|mcg|gm|g)\b", "", candidate, flags=re.IGNORECASE).strip()
            if len(candidate) < 3:
                continue
            names.append(candidate)

    unique: List[str] = []
    seen = set()
    for name in names:
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        unique.append(name)
    return unique[:15]


@router.post("/chat/fast")
async def chat_fast(body: FastChatRequest):
    user_number = body.user_id.strip()
    user_text = (body.message or "").strip()
    interactive_data = (body.interactive_data or "").strip() or None

    if not user_number or not user_text:
        return {"status": "error", "message": "user_id and message are required"}

    profile = await get_user_profile(user_number) or {"user_id": user_number}
    state_doc = await get_conversation_state(user_number)
    resolved_pharmacy_id = (
        (body.pharmacy_id or "").strip()
        or await resolve_pharmacy_id(channel="app", channel_user_id=user_number)
        or DEFAULT_PHARMACY_ID
    )
    if resolved_pharmacy_id:
        await bind_channel_to_pharmacy(channel="app", channel_user_id=user_number, pharmacy_id=resolved_pharmacy_id)

    current_state = _resolve_onboarding_state(
        profile=profile,
        current_state=state_doc.get("state", ConversationState.COLLECT_LANGUAGE),
    )
    temp_data = state_doc.get("temp_data", {})
    nlu_result = extract_nlu(user_text, current_state)

    backend_command = None
    new_state = None
    new_temp = temp_data.copy()

    if interactive_data:
        if interactive_data == "confirm_order":
            new_state = ConversationState.COLLECT_ADDRESS_SELECTION
            backend_command = "ask_address_selection"
        elif interactive_data == "addr_new":
            new_state = ConversationState.COLLECT_FULL_ADDRESS
            backend_command = "ask_full_address"
        elif interactive_data.startswith("addr_select_"):
            idx = int(interactive_data.split("_")[-1])
            addresses = await get_user_addresses(user_number)
            if idx < len(addresses):
                selected = {k: v for k, v in addresses[idx].items() if k != "_id"}
                new_temp["address_info"] = selected
                new_state = ConversationState.FINALIZE_ORDER
                backend_command = "finalize_order"

    if not backend_command and nlu_result.intent in ["ORDER_MEDICINE", "PROVIDE_INFO"] and nlu_result.items:
        if any(i.name for i in nlu_result.items):
            matcher = MedicineMatcher()
            matched_items = []
            for item in nlu_result.items:
                match = await matcher.find_match(item.name)
                matched_items.append({"name": match["name"] if match else item.name, "quantity": item.quantity or 1})

            agent_resp = await call_agent_process_order(user_number, resolved_pharmacy_id or "GENERAL", matched_items)
            if agent_resp and agent_resp.get("status") == "SUCCESS":
                new_temp["agent_findings"] = agent_resp
                new_temp["medicine_name"] = ", ".join([i["medicine_name"] for i in agent_resp["items"]])
                new_temp["quantity"] = sum([i["requested_qty"] for i in agent_resp["items"]])
                if agent_resp.get("requires_prescription"):
                    new_state = ConversationState.AWAITING_PRESCRIPTION
                    backend_command = "ask_prescription_strict"
                else:
                    new_state = ConversationState.CONFIRM_ORDER
                    backend_command = "ask_order_confirmation"

    if not backend_command:
        new_state, new_temp, backend_command = RuleEngine.process(
            nlu_result=nlu_result,
            current_state=current_state,
            user_profile=profile,
            temp_data=temp_data,
            user_text=user_text,
        )

    # Fast app onboarding policy:
    # language only -> skip name/gender/age collection entirely.
    if backend_command in [
        "ask_name",
        "ask_name_again",
        "ask_gender",
        "ask_gender_again",
        "ask_age",
        "ask_age_again",
    ]:
        backend_command = "registration_complete"
        new_state = ConversationState.GREETING

    # Normalize quick language choices even if NLU misses them.
    if current_state == ConversationState.COLLECT_LANGUAGE and not nlu_result.extracted_user_fields.language:
        lowered = user_text.lower()
        if "eng" in lowered:
            nlu_result.extracted_user_fields.language = "English"
        elif "hind" in lowered or "हिं" in user_text:
            nlu_result.extracted_user_fields.language = "Hindi"
        elif "mara" in lowered or "मराठ" in user_text:
            nlu_result.extracted_user_fields.language = "Marathi"

    if any(val is not None for val in nlu_result.extracted_user_fields.model_dump().values()):
        await update_user_profile(user_number, nlu_result.extracted_user_fields.model_dump(exclude_none=True))
        profile = await get_user_profile(user_number) or profile

    recent_orders = await get_recent_orders(user_number) if backend_command == "show_tracking" else []
    if backend_command == "ask_address_selection" and new_state == ConversationState.COLLECT_ADDRESS_SELECTION:
        addresses = await get_user_addresses(user_number)
        new_temp["available_addresses"] = [{k: v for k, v in a.items() if k != "_id"} for a in addresses]

    order_id = None
    if backend_command == "finalize_order":
        from ..services.nlg_service import format_address_string
        address_info = new_temp.get("address_info", {})
        order_data = {
            "medicine_name": new_temp.get("medicine_name"),
            "quantity": new_temp.get("quantity"),
            "price": new_temp.get("price", 250),
            "delivery_address": format_address_string(address_info) if address_info else "Pending",
            "pharmacy_id": resolved_pharmacy_id,
            "merchant_id": resolved_pharmacy_id,
            "source_channel": "app",
            "source_provider": "sanjeevani_hub",
            "source_message_id": f"app:{user_number}:{uuid.uuid4().hex[:10]}",
            "patient_name": profile.get("name") or "Customer",
        }
        order_id = await create_order(user_number, order_data)
        new_temp["order_id"] = order_id
        await update_conversation_state(user_number, ConversationState.GREETING, {})
    else:
        await update_conversation_state(user_number, new_state, new_temp)

    reply = _build_fast_reply(backend_command, profile, new_temp, recent_orders)
    return {
        "status": "success",
        "text": reply,
        "reply": reply,
        "state": str(new_state),
        "session_id": body.session_id or user_number,
        "backend_command": backend_command,
        "order_id": order_id,
        "pharmacy_id": resolved_pharmacy_id,
        "extracted_data": {
            "medicine_name": new_temp.get("medicine_name"),
            "quantity": new_temp.get("quantity"),
        },
    }


@router.post("/chat/upload-prescription")
async def upload_prescription_fast(
    file: UploadFile = File(...),
    user_id: str = Form(...),
    pharmacy_id: str = Form(default=""),
    session_id: str = Form(default=""),
):
    user_number = user_id.strip()
    if not user_number:
        return {"status": "error", "message": "user_id is required"}

    content_type = (file.content_type or "").lower()
    if not content_type.startswith("image/"):
        return {"status": "error", "message": "Please upload image files only (jpg/png/webp)."}

    uploads_dir = os.path.join("uploads", "prescriptions")
    os.makedirs(uploads_dir, exist_ok=True)
    extension = os.path.splitext(file.filename or "")[1] or ".jpg"
    safe_name = f"{user_number.replace(':', '_').replace('+', '')}_{uuid.uuid4().hex[:10]}{extension}"
    file_path = os.path.join(uploads_dir, safe_name)

    with open(file_path, "wb") as target:
        target.write(await file.read())

    ocr_text = _extract_text_from_image(file_path)
    if not ocr_text:
        return {
            "status": "error",
            "session_id": session_id or user_number,
            "message": "Prescription received, but text was unclear. Please upload a clearer image.",
            "data": {"extracted_medicines": [], "required_next_fields": ["medicine_confirmation"]},
        }

    nlu_result = extract_nlu(ocr_text, ConversationState.AWAITING_PRESCRIPTION)
    names_from_nlu = [item.name for item in (nlu_result.items or []) if item.name]
    candidates = names_from_nlu or _extract_medicine_candidates_from_text(ocr_text)

    matcher = MedicineMatcher()
    extracted_medicines: List[Dict[str, Any]] = []
    unmatched_names: List[str] = []
    seen = set()
    for candidate in candidates:
        key = candidate.strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        match = await matcher.find_match(candidate)
        if match:
            extracted_medicines.append(
                {
                    "input": candidate,
                    "name": match.get("name", candidate),
                    "confidence": match.get("score", 0.0),
                    "requires_prescription": bool(match.get("requires_prescription", False)),
                }
            )
        else:
            unmatched_names.append(candidate)

    profile = await get_user_profile(user_number) or {"user_id": user_number}
    state_doc = await get_conversation_state(user_number)
    temp_data = state_doc.get("temp_data", {}).copy()

    resolved_pharmacy_id = pharmacy_id.strip() or await resolve_pharmacy_id(channel="app", channel_user_id=user_number) or DEFAULT_PHARMACY_ID
    if resolved_pharmacy_id:
        await bind_channel_to_pharmacy(channel="app", channel_user_id=user_number, pharmacy_id=resolved_pharmacy_id)

    extracted_names = [item["name"] for item in extracted_medicines]
    if extracted_names:
        temp_data["medicine_name"] = ", ".join(extracted_names)
        temp_data["quantity"] = temp_data.get("quantity") or 1
        temp_data["prescription_uploaded"] = True
        temp_data["prescription_file"] = safe_name
        new_state = ConversationState.CONFIRM_ORDER
        await update_conversation_state(user_number, new_state, temp_data)
    else:
        new_state = ConversationState.AWAITING_PRESCRIPTION
        await update_conversation_state(user_number, new_state, temp_data)

    if extracted_names:
        medicine_lines = "\n".join([f"{idx}. {name}" for idx, name in enumerate(extracted_names, start=1)])
        message = (
            "Prescription uploaded successfully.\n\n"
            "I extracted these medicines:\n"
            f"{medicine_lines}\n\n"
            "Please confirm quantity for each medicine and share delivery address to place order."
        )
    else:
        message = (
            "Prescription uploaded, but I could not confidently identify medicine names.\n"
            "Please type medicine names manually (example: Dolo 650 x 2)."
        )

    return {
        "status": "success" if extracted_names else "partial_success",
        "message": message,
        "text": message,
        "reply": message,
        "session_id": session_id or user_number,
        "state": str(new_state),
        "data": {
            "ocr_text_preview": ocr_text[:400],
            "extracted_medicines": extracted_medicines,
            "unmatched_candidates": unmatched_names,
            "required_next_fields": ["quantity_confirmation", "delivery_address"] if extracted_names else ["medicine_confirmation"],
            "pharmacy_id": resolved_pharmacy_id,
            "patient_name": profile.get("name"),
        },
    }


@router.on_event("startup")
async def startup_indexes():
    await ensure_order_indexes()
    await ensure_channel_binding_indexes()

@router.get("/webhook")
async def verify_webhook(request: Request):
    p = dict(request.query_params)
    if p.get("hub.mode") == "subscribe" and p.get("hub.verify_token") == VERIFY_TOKEN:
        return int(p.get("hub.challenge", 0))
    return "Verification failed"

@router.post("/webhook")
async def handle_message(request: Request):
    try:
        data = await request.form()
    except:
        return {"status": "no_form_data"}

    user_number = data.get("From", "")
    user_text = data.get("Body", "")

    if not user_number or not user_text:
        try:
            json_data = await request.json()
            user_number = json_data.get("From")
            user_text = json_data.get("Body")
        except: pass

    if not user_number or not user_text:
        return {"status": "ignored"}

    interactive_data = data.get("ButtonPayload")
    source_message_id = data.get("MessageSid")
    if interactive_data:
        user_text = interactive_data.replace("_", " ")

    logger.info(f"📩 Message from {user_number}: {user_text}")

    # 1. Fetch User Profile & State
    profile = await get_user_profile(user_number) or {"user_id": user_number}
    state_doc = await get_conversation_state(user_number)
    resolved_pharmacy_id = await resolve_pharmacy_id(channel="whatsapp", channel_user_id=user_number)
    if resolved_pharmacy_id:
        await bind_channel_to_pharmacy(channel="whatsapp", channel_user_id=user_number, pharmacy_id=resolved_pharmacy_id)
    
    current_state = state_doc.get("state", ConversationState.COLLECT_LANGUAGE)
    if profile.get("language") and profile.get("name") and profile.get("gender") and profile.get("age"):
        if current_state in [ConversationState.COLLECT_LANGUAGE, ConversationState.COLLECT_NAME, ConversationState.COLLECT_GENDER, ConversationState.COLLECT_AGE]:
            current_state = ConversationState.GREETING
    else:
        if not profile.get("language"): current_state = ConversationState.COLLECT_LANGUAGE
        elif not profile.get("name"): current_state = ConversationState.COLLECT_NAME
        elif not profile.get("gender"): current_state = ConversationState.COLLECT_GENDER
        elif not profile.get("age"): current_state = ConversationState.COLLECT_AGE
    
    temp_data = state_doc.get("temp_data", {})
    nlu_result = extract_nlu(user_text, current_state)
    logger.info(f"🧠 NLU Result: {nlu_result.model_dump_json()}")

    # --- INTERACTIVE BUTTON INTERCEPTS ---
    backend_command = None
    new_state = None
    new_temp = temp_data.copy()

    if interactive_data:
        if interactive_data == "confirm_order":
            new_state = ConversationState.COLLECT_ADDRESS_SELECTION
            backend_command = "ask_address_selection"
        elif interactive_data == "addr_new":
            new_state = ConversationState.COLLECT_FULL_ADDRESS
            backend_command = "ask_full_address"
        elif interactive_data.startswith("addr_select_"):
            idx = int(interactive_data.split("_")[-1])
            addresses = await get_user_addresses(user_number)
            if idx < len(addresses):
                selected = {k: v for k, v in addresses[idx].items() if k != '_id'}
                new_temp["address_info"] = selected
                new_state = ConversationState.FINALIZE_ORDER
                backend_command = "finalize_order"
        elif interactive_data == "save_addr_yes":
            await save_user_address(user_number, temp_data.get("address_info", {}))
            new_state = ConversationState.FINALIZE_ORDER
            backend_command = "finalize_order"
        elif interactive_data == "save_addr_no":
            new_state = ConversationState.FINALIZE_ORDER
            backend_command = "finalize_order"

    # --- AGENT PIPELINE INTERCEPT (Twilio) ---
    if not backend_command and nlu_result.intent in ["ORDER_MEDICINE", "PROVIDE_INFO"] and nlu_result.items:
        # Check if we have medicine names
        if any(i.name for i in nlu_result.items):
            # 1. Send immediate feedback
            send_whatsapp_text(user_number, "Checking inventory and pharmacy safety... Please wait a moment. ⏳", provider="twilio")
            
            # 2. Match medicines against Master Database
            matcher = MedicineMatcher()
            matched_items = []
            for item in nlu_result.items:
                match = await matcher.find_match(item.name)
                matched_items.append({"name": match["name"] if match else item.name, "quantity": item.quantity or 1})
            
            # 3. Call System Agent API
            agent_resp = await call_agent_process_order(user_number, resolved_pharmacy_id or "GENERAL", matched_items)
            
            if agent_resp and agent_resp.get("status") == "SUCCESS":
                new_temp["agent_findings"] = agent_resp
                new_temp["medicine_name"] = ", ".join([i["medicine_name"] for i in agent_resp["items"]])
                new_temp["quantity"] = sum([i["requested_qty"] for i in agent_resp["items"]])
                
                if agent_resp.get("requires_prescription"):
                    new_state = ConversationState.AWAITING_PRESCRIPTION
                    backend_command = "ask_prescription_strict"
                else:
                    new_state = ConversationState.CONFIRM_ORDER
                    backend_command = "ask_order_confirmation"

    # 3. Rule Engine Decision
    if not backend_command:
        new_state, new_temp, backend_command = RuleEngine.process(
            nlu_result=nlu_result, current_state=current_state, user_profile=profile, temp_data=temp_data, user_text=user_text
        )

    # 4. Handle DB side-effects
    if any(val is not None for val in nlu_result.extracted_user_fields.model_dump().values()):
        await update_user_profile(user_number, nlu_result.extracted_user_fields.model_dump(exclude_none=True))
        profile = await get_user_profile(user_number)

    if backend_command == "show_tracking":
        recent_orders = await get_recent_orders(user_number)
    else: recent_orders = []

    if backend_command == "ask_address_selection" and new_state == ConversationState.COLLECT_ADDRESS_SELECTION:
        addresses = await get_user_addresses(user_number)
        new_temp["available_addresses"] = [{k: v for k, v in a.items() if k != '_id'} for a in addresses]

    if backend_command == "finalize_order":
        from ..services.nlg_service import format_address_string
        address_info = new_temp.get("address_info", {})
        order_data = {
            "medicine_name": new_temp.get("medicine_name"),
            "quantity": new_temp.get("quantity"),
            "price": new_temp.get("price", 250),
            "delivery_address": format_address_string(address_info) if address_info else "Pending",
            "pharmacy_id": resolved_pharmacy_id,
            "merchant_id": resolved_pharmacy_id,
            "source_channel": "whatsapp",
            "source_provider": "twilio",
            "source_message_id": source_message_id,
            "patient_name": profile.get("name") or "Customer",
        }
        
        logger.info(f"💾 Finalizing order for {user_number}. Data: {json.dumps(order_data, default=str)}")
        try:
            order_id = await create_order(user_number, order_data)
            logger.info(f"✅ Order created successfully: {order_id}")
            new_temp["order_id"] = order_id
        except Exception as e:
            logger.error(f"❌ Failed to create order: {e}")
            
        await update_conversation_state(user_number, ConversationState.GREETING, {})
    else:
        await update_conversation_state(user_number, new_state, new_temp)

    logger.info(f"📤 Sending response to {user_number} (Command: {backend_command})")
    generate_and_send_response(user_number, backend_command, profile, new_temp, recent_orders, provider="twilio", user_text=user_text)

    return {"status": "success"}

# ==========================================
# META CLOUD API ROUTES
# ==========================================
from ..core.config import META_VERIFY_TOKEN
from ..services.whatsapp_meta import send_whatsapp_text_meta

@router.get("/webhook/meta")
async def verify_meta_webhook(request: Request):
    p = dict(request.query_params)
    if p.get("hub.mode") == "subscribe" and p.get("hub.verify_token") == META_VERIFY_TOKEN:
        return int(p.get("hub.challenge", 0))
    return "Verification failed"

@router.post("/webhook/meta")
async def handle_meta_message(request: Request):
    try: data = await request.json()
    except: return {"status": "success"}

    try:
        entry = data["entry"][0]
        changes = entry["changes"][0]
        value = changes["value"]
        if "statuses" in value: return {"status": "success"}
        messages = value.get("messages", [])
        if not messages: return {"status": "success"}
        msg = messages[0]
        user_number = f"whatsapp:+{msg['from']}"
        source_message_id = msg.get("id")
        user_text = ""
        interactive_data = None
        if msg["type"] == "text": user_text = msg["text"]["body"]
        elif msg["type"] == "interactive":
            interactive = msg["interactive"]
            if interactive["type"] == "button_reply":
                interactive_data = interactive["button_reply"]["id"]
                user_text = interactive["button_reply"]["title"]
            elif interactive["type"] == "list_reply":
                interactive_data = interactive["list_reply"]["id"]
                user_text = interactive["list_reply"]["title"]
    except: return {"status": "success"}

    profile = await get_user_profile(user_number) or {"user_id": user_number}
    state_doc = await get_conversation_state(user_number)
    resolved_pharmacy_id = await resolve_pharmacy_id(channel="whatsapp", channel_user_id=user_number)
    
    current_state = state_doc.get("state", ConversationState.COLLECT_LANGUAGE)
    temp_data = state_doc.get("temp_data", {})
    nlu_result = extract_nlu(user_text, current_state)

    backend_command = None
    new_state = None
    new_temp = temp_data.copy()

    if interactive_data:
        if interactive_data == "confirm_order":
            new_state = ConversationState.COLLECT_ADDRESS_SELECTION
            backend_command = "ask_address_selection"
        # ... logic inherited from handle_message ...
        elif interactive_data == "addr_new": new_state = ConversationState.COLLECT_FULL_ADDRESS; backend_command = "ask_full_address"
        elif interactive_data.startswith("addr_select_"):
            idx = int(interactive_data.split("_")[-1])
            addresses = await get_user_addresses(user_number)
            if idx < len(addresses):
                new_temp["address_info"] = {k: v for k, v in addresses[idx].items() if k != '_id'}
                new_state = ConversationState.FINALIZE_ORDER; backend_command = "finalize_order"

    # --- AGENT PIPELINE INTERCEPT (Meta) ---
    if not backend_command and nlu_result.intent in ["ORDER_MEDICINE", "PROVIDE_INFO"] and nlu_result.items:
        if any(i.name for i in nlu_result.items):
            send_whatsapp_text_meta(user_number, "Checking inventory and pharmacy safety... Please wait a moment. ⏳")
            matcher = MedicineMatcher()
            matched_items = []
            for item in nlu_result.items:
                match = await matcher.find_match(item.name)
                matched_items.append({"name": match["name"] if match else item.name, "quantity": item.quantity or 1})
            
            agent_resp = await call_agent_process_order(user_number, resolved_pharmacy_id or "GENERAL", matched_items)
            if agent_resp and agent_resp.get("status") == "SUCCESS":
                new_temp["agent_findings"] = agent_resp
                new_temp["medicine_name"] = ", ".join([i["medicine_name"] for i in agent_resp["items"]])
                new_temp["quantity"] = sum([i["requested_qty"] for i in agent_resp["items"]])
                if agent_resp.get("requires_prescription"):
                    new_state = ConversationState.AWAITING_PRESCRIPTION; backend_command = "ask_prescription_strict"
                else:
                    new_state = ConversationState.CONFIRM_ORDER; backend_command = "ask_order_confirmation"

    if not backend_command:
        new_state, new_temp, backend_command = RuleEngine.process(
            nlu_result=nlu_result, current_state=current_state, user_profile=profile, temp_data=temp_data, user_text=user_text
        )

    if backend_command == "finalize_order":
        from ..services.nlg_service import format_address_string
        address_info = new_temp.get("address_info", {})
        order_data = {
            "medicine_name": new_temp.get("medicine_name"), "quantity": new_temp.get("quantity"), "price": 250,
            "delivery_address": format_address_string(address_info) if address_info else "Pending",
            "pharmacy_id": resolved_pharmacy_id, "merchant_id": resolved_pharmacy_id,
            "source_channel": "whatsapp", "source_provider": "meta", "source_message_id": source_message_id,
            "patient_name": profile.get("name") or "Customer",
        }
        await create_order(user_number, order_data)
        await update_conversation_state(user_number, ConversationState.GREETING, {})
    else:
        await update_conversation_state(user_number, new_state, new_temp)

    generate_and_send_response(user_number, backend_command, profile, new_temp, [], provider="meta", user_text=user_text)
    return {"status": "success"}
