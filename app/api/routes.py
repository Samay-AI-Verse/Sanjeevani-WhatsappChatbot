import os
import re
import uuid
from typing import Any, Dict, List, Optional

import requests
from fastapi import APIRouter, File, Form, Request, UploadFile
from pydantic import BaseModel

from ..core.config import DEFAULT_PHARMACY_ID, META_VERIFY_TOKEN, VERIFY_TOKEN
from ..core.logger import logger
from ..models.enums import ConversationState
from ..services.db_service import (
    ensure_order_indexes,
    get_conversation_state,
    get_recent_orders,
    get_user_addresses,
    get_user_profile,
    save_user_address,
    update_conversation_state,
    update_user_profile,
)
from ..services.medicine_matcher import MedicineMatcher
from ..services.nlg_service import generate_and_send_response
from ..services.nlu_service import extract_nlu
from ..services.pharmacy_routing import (
    bind_channel_to_pharmacy,
    ensure_channel_binding_indexes,
    resolve_pharmacy_id,
)
from ..services.rule_engine import RuleEngine
from ..services.system_api import call_agent_process_order
from ..services.whatsapp import send_whatsapp_text
from ..services.whatsapp_meta import send_whatsapp_text_meta

router = APIRouter()


class FastChatRequest(BaseModel):
    user_id: str
    message: str
    pharmacy_id: Optional[str] = None
    interactive_data: Optional[str] = None
    session_id: Optional[str] = None


def _resolve_language_only_state(profile: Dict[str, Any], current_state: str) -> str:
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


def _resolve_full_onboarding_state(profile: Dict[str, Any], current_state: str) -> str:
    if profile.get("language") and profile.get("name") and profile.get("gender") and profile.get("age"):
        if current_state in [
            ConversationState.COLLECT_LANGUAGE,
            ConversationState.COLLECT_NAME,
            ConversationState.COLLECT_GENDER,
            ConversationState.COLLECT_AGE,
        ]:
            return ConversationState.GREETING
        return current_state

    if not profile.get("language"):
        return ConversationState.COLLECT_LANGUAGE
    if not profile.get("name"):
        return ConversationState.COLLECT_NAME
    if not profile.get("gender"):
        return ConversationState.COLLECT_GENDER
    if not profile.get("age"):
        return ConversationState.COLLECT_AGE
    return current_state


def _norm_lang(lang_value: Optional[str]) -> str:
    raw = (lang_value or "").strip().lower()
    if "hind" in raw or "???" in raw:
        return "hindi"
    if "mara" in raw or "????" in raw:
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
            return "??????? ??? ???? ?????? ??? ????? ???? ?????: English / Hindi / Marathi."
        if lang == "marathi":
            return "???????????? ?????? ???. ????? ???? ?????: English / Hindi / Marathi."
        return "Welcome to Sanjeevani. Please choose language: English / Hindi / Marathi."
    if backend_command in ["registration_complete", "welcome_user"]:
        if lang == "hindi":
            return f"???? ??? ?? ??, {name}. ?? ??? ?? ??? ?? ?????? ??????"
        if lang == "marathi":
            return f"???? ??? ????, {name}. ??? ?????? ??? ??? ?????? ?????."
        return f"Language set, {name}. Tell me medicine name and quantity."
    if backend_command in ["ask_quantity", "ask_quantity_again"]:
        return f"How many units of {med} do you need?"
    if backend_command in ["ask_order_confirmation", "ask_order_confirmation_again"]:
        findings = temp_data.get("agent_findings") or {}
        stock_rows = findings.get("items") or []
        out_items = [i.get("medicine_name") for i in stock_rows if not i.get("in_stock")]
        if out_items:
            return f"Some items are out of stock ({', '.join(out_items)}). Please change item/quantity."
        return f"Inventory checked for {med} x {qty}. Share delivery address to send for pharmacist confirmation."
    if backend_command in ["ask_address_selection", "ask_full_address"]:
        return "Please share your full delivery address."
    if backend_command in ["ask_prescription_strict", "ask_prescription_strict_again"]:
        return "Prescription is required. Please upload a clear prescription image."
    if backend_command == "inventory_check_failed":
        return "Some requested items are out of stock right now. Please change medicine or quantity and try again."
    if backend_command == "handoff_to_system_for_confirmation":
        ref = temp_data.get("handoff_reference", "PENDING")
        return (
            f"Request captured. Inventory check complete. Sent to Sanjeevani System for pharmacist confirmation. "
            f"Reference: {ref}."
        )
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


def _extract_items_for_agent(nlu_result, temp_data: Dict[str, Any]) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []

    if nlu_result and getattr(nlu_result, "items", None):
        for item in nlu_result.items:
            if item.name:
                items.append({"name": item.name, "quantity": item.quantity or 1})

    if items:
        return items

    med_text = str(temp_data.get("medicine_name") or "").strip()
    qty = int(temp_data.get("quantity") or 1)
    if not med_text:
        return []

    for part in [p.strip() for p in med_text.split(",") if p.strip()]:
        items.append({"name": part, "quantity": qty})
    return items


async def _ensure_agent_findings(
    user_number: str,
    merchant_id: str,
    temp_data: Dict[str, Any],
    nlu_result=None,
) -> bool:
    if temp_data.get("agent_findings"):
        return True

    raw_items = _extract_items_for_agent(nlu_result, temp_data)
    if not raw_items:
        return False

    matcher = MedicineMatcher()
    matched_items: List[Dict[str, Any]] = []
    for item in raw_items:
        match = await matcher.find_match(item["name"])
        matched_items.append({"name": match["name"] if match else item["name"], "quantity": int(item.get("quantity") or 1)})

    agent_resp = await call_agent_process_order(user_number, merchant_id or "GENERAL", matched_items)
    if not (agent_resp and agent_resp.get("status") == "SUCCESS"):
        return False

    temp_data["agent_findings"] = agent_resp
    temp_data["medicine_name"] = ", ".join([i["medicine_name"] for i in agent_resp.get("items", [])]) or temp_data.get("medicine_name")
    temp_data["quantity"] = sum([int(i.get("requested_qty", 1)) for i in agent_resp.get("items", [])]) or temp_data.get("quantity") or 1
    return True


async def _run_conversation_turn(
    *,
    user_number: str,
    user_text: str,
    interactive_data: Optional[str],
    current_state: str,
    temp_data: Dict[str, Any],
    profile: Dict[str, Any],
    resolved_pharmacy_id: str,
    app_mode: bool,
    provider: str,
) -> tuple[str, str, Dict[str, Any], Dict[str, Any], list]:
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
        elif interactive_data == "save_addr_yes":
            await save_user_address(user_number, temp_data.get("address_info", {}))
            new_state = ConversationState.FINALIZE_ORDER
            backend_command = "finalize_order"
        elif interactive_data == "save_addr_no":
            new_state = ConversationState.FINALIZE_ORDER
            backend_command = "finalize_order"

    if not backend_command and nlu_result.intent in ["ORDER_MEDICINE", "PROVIDE_INFO"] and nlu_result.items:
        if provider == "twilio":
            send_whatsapp_text(user_number, "Checking inventory and pharmacy safety... Please wait a moment.", provider="twilio")
        elif provider == "meta":
            send_whatsapp_text_meta(user_number, "Checking inventory and pharmacy safety... Please wait a moment.")

        await _ensure_agent_findings(
            user_number=user_number,
            merchant_id=resolved_pharmacy_id or "GENERAL",
            temp_data=new_temp,
            nlu_result=nlu_result,
        )
        findings = new_temp.get("agent_findings") or {}
        if findings.get("status") == "SUCCESS":
            if findings.get("requires_prescription"):
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

    if app_mode and backend_command in [
        "ask_name", "ask_name_again", "ask_gender", "ask_gender_again", "ask_age", "ask_age_again"
    ]:
        backend_command = "registration_complete"
        new_state = ConversationState.GREETING

    if current_state == ConversationState.COLLECT_LANGUAGE and not nlu_result.extracted_user_fields.language:
        lowered = user_text.lower()
        if "eng" in lowered:
            nlu_result.extracted_user_fields.language = "English"
        elif "hind" in lowered or "???" in user_text:
            nlu_result.extracted_user_fields.language = "Hindi"
        elif "mara" in lowered or "????" in user_text:
            nlu_result.extracted_user_fields.language = "Marathi"

    if any(val is not None for val in nlu_result.extracted_user_fields.model_dump().values()):
        await update_user_profile(user_number, nlu_result.extracted_user_fields.model_dump(exclude_none=True))
        profile = await get_user_profile(user_number) or profile

    if backend_command in ["ask_order_confirmation", "ask_order_confirmation_again", "finalize_order"]:
        await _ensure_agent_findings(
            user_number=user_number,
            merchant_id=resolved_pharmacy_id or "GENERAL",
            temp_data=new_temp,
            nlu_result=nlu_result,
        )
        findings = new_temp.get("agent_findings") or {}
        rows = findings.get("items") or []
        if rows and any(not bool(i.get("in_stock", False)) for i in rows):
            backend_command = "inventory_check_failed"
            new_state = ConversationState.GREETING

    recent_orders = await get_recent_orders(user_number) if backend_command == "show_tracking" else []

    if backend_command == "ask_address_selection" and new_state == ConversationState.COLLECT_ADDRESS_SELECTION:
        addresses = await get_user_addresses(user_number)
        new_temp["available_addresses"] = [{k: v for k, v in a.items() if k != "_id"} for a in addresses]

    if backend_command == "finalize_order":
        backend_command = "handoff_to_system_for_confirmation"
        new_temp["handoff_reference"] = f"REQ-{uuid.uuid4().hex[:8].upper()}"
        await update_conversation_state(user_number, ConversationState.GREETING, {})
    else:
        await update_conversation_state(user_number, new_state, new_temp)

    return backend_command, new_state, new_temp, profile, recent_orders


@router.on_event("startup")
async def startup_indexes():
    await ensure_order_indexes()
    await ensure_channel_binding_indexes()


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

    current_state = _resolve_language_only_state(profile, state_doc.get("state", ConversationState.COLLECT_LANGUAGE))
    temp_data = state_doc.get("temp_data", {})

    backend_command, new_state, new_temp, profile, recent_orders = await _run_conversation_turn(
        user_number=user_number,
        user_text=user_text,
        interactive_data=interactive_data,
        current_state=current_state,
        temp_data=temp_data,
        profile=profile,
        resolved_pharmacy_id=resolved_pharmacy_id,
        app_mode=True,
        provider="app",
    )

    reply = _build_fast_reply(backend_command, profile, new_temp, recent_orders)
    return {
        "status": "success",
        "text": reply,
        "reply": reply,
        "state": str(new_state),
        "session_id": body.session_id or user_number,
        "backend_command": backend_command,
        "pharmacy_id": resolved_pharmacy_id,
        "extracted_data": {
            "medicine_name": new_temp.get("medicine_name"),
            "quantity": new_temp.get("quantity"),
            "handoff_reference": new_temp.get("handoff_reference"),
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
            "Please confirm quantity and delivery address."
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
        },
    }


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
    except Exception:
        return {"status": "no_form_data"}

    user_number = data.get("From", "")
    user_text = data.get("Body", "")
    source_message_id = data.get("MessageSid")

    if not user_number or not user_text:
        try:
            json_data = await request.json()
            user_number = json_data.get("From")
            user_text = json_data.get("Body")
            source_message_id = source_message_id or json_data.get("MessageSid")
        except Exception:
            pass

    if not user_number or not user_text:
        return {"status": "ignored"}

    interactive_data = data.get("ButtonPayload")
    if interactive_data:
        user_text = interactive_data.replace("_", " ")

    profile = await get_user_profile(user_number) or {"user_id": user_number}
    state_doc = await get_conversation_state(user_number)
    resolved_pharmacy_id = await resolve_pharmacy_id(channel="whatsapp", channel_user_id=user_number)
    if resolved_pharmacy_id:
        await bind_channel_to_pharmacy(channel="whatsapp", channel_user_id=user_number, pharmacy_id=resolved_pharmacy_id)

    current_state = _resolve_full_onboarding_state(profile, state_doc.get("state", ConversationState.COLLECT_LANGUAGE))
    temp_data = state_doc.get("temp_data", {})

    backend_command, _, new_temp, profile, recent_orders = await _run_conversation_turn(
        user_number=user_number,
        user_text=user_text,
        interactive_data=interactive_data,
        current_state=current_state,
        temp_data=temp_data,
        profile=profile,
        resolved_pharmacy_id=resolved_pharmacy_id or DEFAULT_PHARMACY_ID,
        app_mode=False,
        provider="twilio",
    )

    generate_and_send_response(user_number, backend_command, profile, new_temp, recent_orders, provider="twilio", user_text=user_text)
    return {"status": "success", "source_message_id": source_message_id}


@router.get("/webhook/meta")
async def verify_meta_webhook(request: Request):
    p = dict(request.query_params)
    if p.get("hub.mode") == "subscribe" and p.get("hub.verify_token") == META_VERIFY_TOKEN:
        return int(p.get("hub.challenge", 0))
    return "Verification failed"


@router.post("/webhook/meta")
async def handle_meta_message(request: Request):
    try:
        data = await request.json()
    except Exception:
        return {"status": "success"}

    try:
        entry = data["entry"][0]
        changes = entry["changes"][0]
        value = changes["value"]
        if "statuses" in value:
            return {"status": "success"}
        messages = value.get("messages", [])
        if not messages:
            return {"status": "success"}

        msg = messages[0]
        user_number = f"whatsapp:+{msg['from']}"
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
    except Exception:
        return {"status": "success"}

    profile = await get_user_profile(user_number) or {"user_id": user_number}
    state_doc = await get_conversation_state(user_number)
    resolved_pharmacy_id = await resolve_pharmacy_id(channel="whatsapp", channel_user_id=user_number)

    current_state = _resolve_full_onboarding_state(profile, state_doc.get("state", ConversationState.COLLECT_LANGUAGE))
    temp_data = state_doc.get("temp_data", {})

    backend_command, _, new_temp, profile, recent_orders = await _run_conversation_turn(
        user_number=user_number,
        user_text=user_text,
        interactive_data=interactive_data,
        current_state=current_state,
        temp_data=temp_data,
        profile=profile,
        resolved_pharmacy_id=resolved_pharmacy_id or DEFAULT_PHARMACY_ID,
        app_mode=False,
        provider="meta",
    )

    generate_and_send_response(user_number, backend_command, profile, new_temp, recent_orders, provider="meta", user_text=user_text)
    return {"status": "success"}
