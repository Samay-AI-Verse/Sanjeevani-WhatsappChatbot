import os
import json
from typing import Dict
from ..core.logger import logger
from ..services.whatsapp import send_whatsapp_text as send_twilio_text, send_whatsapp_buttons as send_twilio_buttons
from ..services.whatsapp_meta import send_whatsapp_text_meta, send_whatsapp_buttons_meta
from ..services.ai_service import get_conversational_reply

def format_address_string(address: Dict) -> str:
    if address.get("full_address"):
        return address["full_address"]
    parts = [address.get(k) for k in ["address_line1", "address_line2", "city", "state", "pincode"] if address.get(k)]
    if address.get("landmark"): parts.insert(2, f"Near {address['landmark']}")
    return ", ".join(parts)

def generate_and_send_response(to_number: str, backend_command: str, user_profile: Dict, temp_data: Dict, recent_orders: list = None, provider: str = "twilio", user_text: str = ""):
def _build_order_summary(temp_data: Dict) -> str:
    findings = temp_data.get("agent_findings", {})
    refill_nudge = findings.get("refill_nudge", "")
    med = temp_data.get("medicine_name")
    qty = temp_data.get("quantity")
    
    # Calculate price based on item rows (mock or real from agent)
    stock_rows = findings.get("items") or []
    total = 0
    for row in stock_rows:
        total += int(row.get("requested_qty", 1)) * float(row.get("price", 250.0))
    if total == 0: total = int(qty or 1) * 250
        
    summary = f"✨ *Order Summary* ✨\n--------------------------\n💊 *Medicine:* {med}\n📊 *Quantity:* {qty}\n💰 *Estimated Price:* ₹{total}\n🚚 *Delivery:* Home Delivery\n--------------------------\n"
    if refill_nudge:
        summary += f"🔔 *Refill Reminder:* {refill_nudge}\n--------------------------\n"
    summary += "*Confirm your order details?*"
    return summary


def generate_response_text(backend_command: str, user_profile: Dict, temp_data: Dict, recent_orders: list = None, user_text: str = "") -> dict:
    """
    Core NLG generator. Returns a dict containing the AI generated response.
    Format: {"text": str, "buttons": list, "list_title": str, "list_items": list}
    """
    language = user_profile.get("language", "English").lower()
    name = user_profile.get("name", "")
    
    gender_labels = ["male", "female", "other", "पुरुष", "महिला", "अन्य"]
    if any(label in name.lower() for label in gender_labels) and len(name.split()) <= 3:
        name = "Friend" if language == "english" else ("दोस्त" if language == "hindi" else "मित्र")
    name = name or ("Friend" if language == "english" else "दोस्त")

    resp = {"text": "", "buttons": None, "list_title": None, "list_items": None}

    if backend_command in ["ask_language", "ask_language_again"]:
        resp["text"] = "👋 *Welcome to Sanjeevani Care!* \n\n🌐 Which language do you prefer to chat in?"
        resp["buttons"] = [
            {"id": "lang_eng", "title": "English"},
            {"id": "lang_hin", "title": "हिंदी"},
            {"id": "lang_mar", "title": "मराठी"}
        ]
        return resp

    if backend_command in ["ask_name", "ask_name_again"]:
        resp["text"] = "Awesome! What is your full name?" if language == "english" else "कृपया अपना पूरा नाम बताएं।"
        return resp

    if backend_command in ["ask_gender", "ask_gender_again"]:
        resp["text"] = f"Nice to meet you, *{name}*! What is your gender?" if language == "english" else f"आपसे मिलकर अच्छा लगा, *{name}*! आपका लिंग क्या है?"
        resp["buttons"] = [
            {"id": "gender_male", "title": "Male / पुरुष"},
            {"id": "gender_female", "title": "Female / महिला"},
            {"id": "gender_other", "title": "Other / अन्य"}
        ]
        return resp

    if backend_command in ["ask_age", "ask_age_again"]:
        resp["text"] = "Almost done! ⏳ How old are you? (e.g. 25)" if language == "english" else "बस एक आखिरी सवाल! ⏳ आपकी उम्र क्या है? (जैसे 25)"
        return resp

    if backend_command == "registration_complete":
        resp["text"] = f"✨ *Welcome Aboard, {name}!* ✨\n\n🎉 *Registration Successful!*\nYour trusted pharmacy partner is now just a text away. ⚕️\n\n🌟 *Let's get started:* \n👉 Type *'Order Paracetamol'* \n👉 Type *'Track Order'* \n\nHow else can I help you today? 🙏"
        return resp

    if backend_command == "welcome_user":
        if language == "hindi":
            resp["text"] = f"✨ *संजीवनी केयर में आपका स्वागत है, {name}!* ✨\n\n🙏 आपकी सेवा में फिर से हाज़िर हैं। हम आज आपकी किस प्रकार मदद कर सकते हैं? \n\n💊 *दवा मंगवाएं* या अपना *ऑर्डर ट्रैक करें*। बस हमें बताएं!"
        elif language == "marathi":
            resp["text"] = f"✨ *संजीवनी केअरमध्ये आपले स्वागत आहे, {name}!* ✨\n\n🙏 पुन्हा तुमची सेवा करण्यास आम्हाला आनंद होत आहे। आज आम्ही तुमची कशी मदत करू शकतो? \n\n💊 *औषध ऑर्डर करा* किंवा तुमच्या *ऑर्डरचा मागोवा घ्या*। बस आम्हाला सांगा!"
        else:
            resp["text"] = f"✨ *Welcome Back, {name}!* ✨\n\n🙏 Good to see you again. How can we help you stay healthy today?\n\nYou can *order medicines* or *track your current orders*. 💊"
        return resp

    if backend_command in ["ask_quantity", "ask_quantity_again"]:
        med = temp_data.get('medicine_name', 'this medicine')
        resp["text"] = f"How many units of *{med}* do you need?" if language == "english" else f"आपको *{med}* की कितनी मात्रा चाहिए?"
        return resp

    if backend_command in ["ask_order_confirmation", "ask_order_confirmation_again"]:
        resp["text"] = _build_order_summary(temp_data)
        resp["buttons"] = [
            {"id": "confirm_order", "title": "✅ Confirm Order"},
            {"id": "cancel_order", "title": "❌ Cancel"}
        ]
        return resp

    if backend_command in ["ask_prescription_strict", "ask_prescription_strict_again"]:
        if language == "hindi":
            resp["text"] = "⚠️ *प्रिस्क्रिप्शन आवश्यक है!* \n\nआपकी ऑर्डर में कुछ ऐसी दवाएं हैं जिनके लिए डॉक्टर का पर्चा अनिवार्य है। कृपया अपने प्रिस्क्रिप्शन की एक साफ़ फोटो यहाँ भेजें। 📸"
        else:
            resp["text"] = "⚠️ *Prescription Required!* \n\nYour order contains restricted medications. Please upload a clear photo of your doctor's prescription using the attachment icon. 📸"
        return resp

    if backend_command == "prescription_uploaded_success":
        resp["text"] = "✅ *Prescription Received!* \n\nThank you. Our pharmacist will verify it shortly. Let's proceed with your order summary." if language == "english" else "✅ *प्रिस्क्रिप्शन प्राप्त हुआ!* \n\nधन्यवाद। हमारे फार्मासिस्ट इसे सत्यापित करेंगे।"
        return resp

    if backend_command == "ask_address_selection":
        addresses = temp_data.get("available_addresses", [])
        if addresses:
            resp["text"] = "📍 Please select a delivery address or add a new one:" if language == "english" else "📍 कृपया डिलीवरी पता चुनें या नया जोड़ें:"
            items = []
            for i, addr in enumerate(addresses[:3]):
                items.append({"id": f"addr_select_{i}", "title": f"{addr.get('address_type', 'Home')}: {addr['address_line1'][:20]}"})
            items.append({"id": "addr_new", "title": "➕ Add New Address"})
            resp["list_title"] = "Delivery Addresses"
            resp["list_items"] = items
        else:
            resp["text"] = "🏠 Please enter your *Full Delivery Address* (Street, Area, City, etc.):" if language == "english" else "🏠 कृपया अपना *पूरा डिलीवरी पता* दर्ज करें:"
        return resp

    if backend_command == "ask_full_address" or backend_command == "ask_address_again":
        resp["text"] = "🏠 Please enter your *Full Delivery Address* (Street, Area, City, etc.):" if language == "english" else "🏠 कृपया अपना *पूरा डिलीवरी पता* दर्ज करें:"
        return resp

    if backend_command == "ask_save_address":
        address_str = format_address_string(temp_data.get("address_info", {}))
        resp["text"] = f"📍 *Confirm Delivery Address:*\n\n{address_str}\n\nWould you like to save this for future orders?" if language == "english" else f"📍 *डिलीवरी पता पुष्ट करें:*\n\n{address_str}\n\nक्या आप इसे भविष्य के लिए सुरक्षित करना चाहेंगे?"
        resp["buttons"] = [
            {"id": "save_addr_yes", "title": "Yes, Save"},
            {"id": "save_addr_no", "title": "Use Once"}
        ]
        return resp

    if backend_command == "inventory_check_failed":
        if language == "hindi":
            resp["text"] = "❌ कुछ दवाइयाँ अभी स्टॉक में उपलब्ध नहीं हैं। कृपया दवा या मात्रा बदलकर फिर से प्रयास करें।"
        else:
            resp["text"] = "❌ Some requested medicines are currently out of stock. Please change medicine or quantity and try again."
        return resp

    if backend_command == "handoff_to_system_for_confirmation":
        ref = temp_data.get("handoff_reference", "PENDING")
        if language == "hindi":
            resp["text"] = f"✅ *Request Received*\n\nInventory और safety check पूरा हो गया है। आपका अनुरोध Sanjeevani System को भेज दिया गया है।\n\n🆔 Reference: {ref}"
        else:
            resp["text"] = f"✅ *Request Received*\n\nInventory and safety checks are complete. Your request has been sent for pharmacist confirmation.\n\n🆔 Reference: {ref}"
        return resp

    if backend_command == "finalize_order":
        order_id = temp_data.get("order_id", "PENDING")
        address_str = format_address_string(temp_data.get("address_info", {}))
        resp["text"] = f"🙌 *Order Confirmed!* \n\nThank you, *{name}*. Your order is being processed. 🚚\n\n🆔 *Order ID:* #{order_id}\n📍 *Status:* In Progress\n📍 *Delivering to:* {address_str}\n\nStay healthy! ✨"
        return resp

    if backend_command == "order_cancelled":
        resp["text"] = "❌ Order cancelled. Let me know if you need anything else!"
        return resp

    if backend_command == "show_tracking":
        if not recent_orders:
            resp["text"] = "No recent orders found."
        else:
            order_list = "\n\n".join([f"📦 *ID:* {o['order_id']}\n💊 *Item:* {o['medicine_name']}\n📊 *Status:* {o['status'].title()}" for o in recent_orders])
            resp["text"] = f"Here are your recent orders:\n\n{order_list}"
        return resp

    if backend_command in ["general_greeting_or_fallback", "fallback_general"]:
        resp["text"] = get_conversational_reply(user_text, user_profile)
        return resp
        
    if backend_command == "acknowledge_cancel":
        resp["text"] = "Okay, no problem. How else can I help?"
        return resp

    resp["text"] = "I can help with pharmacy orders, uploading prescriptions, and tracking your delivery. Let me know what you need! 🏥"
    return resp

def generate_and_send_response(to_number: str, backend_command: str, user_profile: Dict, temp_data: Dict, recent_orders: list = None, provider: str = "twilio", user_text: str = ""):
    resp = generate_response_text(backend_command, user_profile, temp_data, recent_orders, user_text)
    
    txt = resp["text"]
    btns = resp.get("buttons")
    items = resp.get("list_items")
    list_title = resp.get("list_title", "Options")
    
    if items:
        if provider == "meta":
            from ..services.whatsapp_meta import send_whatsapp_list_meta
            send_whatsapp_list_meta(to_number, txt, list_title, items)
        else:
            btn_txt = f"{txt}\n\n" + "\n".join([f"• {i['title']}" for i in items])
            send_twilio_text(to_number, btn_txt)
    elif btns:
        if provider == "meta":
            send_whatsapp_buttons_meta(to_number, txt, btns)
        else:
            send_twilio_buttons(to_number, txt, btns)
    else:
        if provider == "meta":
            send_whatsapp_text_meta(to_number, txt)
        else:
            send_twilio_text(to_number, txt)
