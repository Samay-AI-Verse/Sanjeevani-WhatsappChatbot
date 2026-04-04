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
    """
    NLG Service equivalent. Using hardcoded templates for MVP stability, 
    but can be swapped with a fast LLM generator if needed.
    """
    def send_text(num, txt):
        if provider == "meta": return send_whatsapp_text_meta(num, txt)
        else: return send_twilio_text(num, txt)
        
    def send_buttons(num, txt, btns):
        if provider == "meta": return send_whatsapp_buttons_meta(num, txt, btns)
        else: return send_twilio_buttons(num, txt, btns)
        
    def send_list(num, txt, title, items):
        if provider == "meta":
            # Meta supports List Messages
            from ..services.whatsapp_meta import send_whatsapp_list_meta
            return send_whatsapp_list_meta(num, txt, title, items)
        else:
            # Twilio fallback to buttons if too many, or just text
            btn_txt = f"{txt}\n\n" + "\n".join([f"• {i['title']}" for i in items])
            return send_text(num, btn_txt)
    
    # Default to English if not set
    language = user_profile.get("language", "English").lower()
    name = user_profile.get("name", "")
    
    # --- NAME SANITIZATION ---
    gender_labels = ["male", "female", "other", "पुरुष", "महिला", "अन्य"]
    if any(label in name.lower() for label in gender_labels) and len(name.split()) <= 3:
        name = "Friend" if language == "english" else ("दोस्त" if language == "hindi" else "मित्र")
    
    # --- ONBOARDING ---
    if backend_command in ["ask_language", "ask_language_again"]:
        msg = "👋 *Welcome to Sanjeevani Care!* \n\n🌐 Which language do you prefer to chat in?"
        buttons = [
            {"id": "lang_eng", "title": "English"},
            {"id": "lang_hin", "title": "हिंदी"},
            {"id": "lang_mar", "title": "मराठी"}
        ]
        send_buttons(to_number, msg, buttons)
        return

    if backend_command in ["ask_name", "ask_name_again"]:
        msg = "Awesome! What is your full name?" if language == "english" else "कृपया अपना पूरा नाम बताएं।"
        send_text(to_number, msg)
        return

    if backend_command in ["ask_gender", "ask_gender_again"]:
        msg = f"Nice to meet you, *{name}*! What is your gender?" if language == "english" else f"आपसे मिलकर अच्छा लगा, *{name}*! आपका लिंग क्या है?"
        buttons = [
            {"id": "gender_male", "title": "Male / पुरुष"},
            {"id": "gender_female", "title": "Female / महिला"},
            {"id": "gender_other", "title": "Other / अन्य"}
        ]
        send_buttons(to_number, msg, buttons)
        return

    if backend_command in ["ask_age", "ask_age_again"]:
        msg = "Almost done! ⏳ How old are you? (e.g. 25)" if language == "english" else "बस एक आखिरी सवाल! ⏳ आपकी उम्र क्या है? (जैसे 25)"
        send_text(to_number, msg)
        return

    if backend_command == "registration_complete":
        msg = f"✨ *Welcome Aboard, {name}!* ✨\n\n🎉 *Registration Successful!*\nYour trusted pharmacy partner is now just a text away. ⚕️\n\n🌟 *Let's get started:* \n👉 Type *'Order Paracetamol'* \n👉 Type *'Track Order'* \n\nHow else can I help you today? 🙏"
        send_text(to_number, msg)
        return

    if backend_command == "welcome_user":
        if language == "hindi":
            msg = f"✨ *संजीवनी केयर में आपका स्वागत है, {name}!* ✨\n\n🙏 आपकी सेवा में फिर से हाज़िर हैं। हम आज आपकी किस प्रकार मदद कर सकते हैं? \n\n💊 *दवा मंगवाएं* या अपना *ऑर्डर ट्रैक करें*। बस हमें बताएं!"
        elif language == "marathi":
            msg = f"✨ *संजीवनी केअरमध्ये आपले स्वागत आहे, {name}!* ✨\n\n🙏 पुन्हा तुमची सेवा करण्यास आम्हाला आनंद होत आहे। आज आम्ही तुमची कशी मदत करू शकतो? \n\n💊 *औषध ऑर्डर करा* किंवा तुमच्या *ऑर्डरचा मागोवा घ्या*। बस आम्हाला सांगा!"
        else:
            msg = f"✨ *Welcome Back, {name}!* ✨\n\n🙏 Good to see you again. How can we help you stay healthy today?\n\nYou can *order medicines* or *track your current orders*. 💊"
        send_text(to_number, msg)
        return

    # --- ORDERING ---
    if backend_command in ["ask_quantity", "ask_quantity_again"]:
        med = temp_data.get('medicine_name', 'this medicine')
        msg = f"How many tablets/strips of *{med}* do you need?" if language == "english" else f"आपको *{med}* की कितनी मात्रा चाहिए?"
        send_text(to_number, msg)
        return

    if backend_command in ["ask_order_confirmation", "ask_order_confirmation_again"]:
        findings = temp_data.get("agent_findings", {})
        refill_nudge = findings.get("refill_nudge", "")
        med = temp_data.get("medicine_name")
        qty = temp_data.get("quantity")
        price = temp_data.get("price", 250)
        total = int(qty) * price
        
        summary = f"✨ *Order Summary* ✨\n--------------------------\n💊 *Medicine:* {med}\n📊 *Quantity:* {qty}\n💰 *Estimated Price:* ₹{total}\n🚚 *Delivery:* Home Delivery\n--------------------------\n"
        if refill_nudge:
            summary += f"🔔 *Refill Reminder:* {refill_nudge}\n--------------------------\n"
        summary += "*Confirm your order details?*"
        
        buttons = [
            {"id": "confirm_order", "title": "✅ Confirm & Set Address"},
            {"id": "cancel_order", "title": "❌ Cancel"}
        ]
        send_buttons(to_number, summary, buttons)
        return

    # --- SAFETY / PRESCRIPTION ---
    if backend_command in ["ask_prescription_strict", "ask_prescription_strict_again"]:
        if language == "hindi":
            msg = "⚠️ *प्रिस्क्रिप्शन आवश्यक है!* \n\nआपकी ऑर्डर में कुछ ऐसी दवाएं हैं जिनके लिए डॉक्टर का पर्चा अनिवार्य है। कृपया अपने प्रिस्क्रिप्शन की एक साफ़ फोटो यहाँ भेजें। 📸"
        else:
            msg = "⚠️ *Prescription Required!* \n\nYour order contains restricted medications (e.g., Habit-forming). Please upload a clear photo of your doctor's prescription to continue. 📸"
        send_text(to_number, msg)
        return

    if backend_command == "prescription_uploaded_success":
        if language == "hindi":
            msg = "✅ *प्रिस्क्रिप्शन प्राप्त हुआ!* \n\nधन्यवाद। हमारे फार्मासिस्ट इसे सत्यापित करेंगे। अब हम आपकी ऑर्डर की पुष्टि कर सकते हैं।"
        else:
            msg = "✅ *Prescription Received!* \n\nThank you. Our pharmacist will verify it shortly. Let's proceed with your order summary."
        send_text(to_number, msg)
        return

    # --- ADDRESS COLLECTION ---
    if backend_command == "ask_address_selection":
        addresses = temp_data.get("available_addresses", [])
        if addresses:
            msg = "📍 Please select a delivery address or add a new one:" if language == "english" else "📍 कृपया डिलीवरी पता चुनें या नया जोड़ें:"
            items = []
            for i, addr in enumerate(addresses[:3]):
                items.append({"id": f"addr_select_{i}", "title": f"{addr.get('address_type', 'Home')}: {addr['address_line1'][:20]}"})
            items.append({"id": "addr_new", "title": "➕ Add New Address"})
            send_list(to_number, msg, "Delivery Addresses", items)
        else:
            msg = "🏠 Please enter your *Full Delivery Address* (Street, Area, City, etc.):" if language == "english" else "🏠 कृपया अपना *पूरा डिलीवरी पता* (सड़क, इलाका, शहर, आदि) दर्ज करें:"
            send_text(to_number, msg)
        return

    if backend_command == "ask_full_address":
        msg = "🏠 Please enter your *Full Delivery Address* (Street, Area, City, etc.):" if language == "english" else "🏠 कृपया अपना *पूरा डिलीवरी पता* (सड़क, इलाका, शहर, आदि) दर्ज करें:"
        send_text(to_number, msg)
        return

    if backend_command == "ask_save_address":
        address_str = format_address_string(temp_data.get("address_info", {}))
        msg = f"📍 *Confirm Delivery Address:*\n\n{address_str}\n\nWould you like to save this for future orders?" if language == "english" else f"📍 *डिलीवरी पता पुष्ट करें:*\n\n{address_str}\n\nक्या आप इसे भविष्य के लिए सुरक्षित करना चाहेंगे?"
        buttons = [
            {"id": "save_addr_yes", "title": "Yes, Save / हाँ"},
            {"id": "save_addr_no", "title": "No, Use Once / नहीं"}
        ]
        send_buttons(to_number, msg, buttons)
        return

    if backend_command == "inventory_check_failed":
        if language == "hindi":
            msg = "❌ कुछ दवाइयाँ अभी स्टॉक में उपलब्ध नहीं हैं। कृपया दवा या मात्रा बदलकर फिर से प्रयास करें।"
        else:
            msg = "❌ Some requested medicines are currently out of stock. Please change medicine or quantity and try again."
        send_text(to_number, msg)
        return

    if backend_command == "handoff_to_system_for_confirmation":
        ref = temp_data.get("handoff_reference", "PENDING")
        if language == "hindi":
            msg = (
                "✅ *Request Received*\n\n"
                "Inventory और safety check पूरा हो गया है।\n"
                "आपका अनुरोध Sanjeevani System में pharmacist confirmation के लिए भेज दिया गया है।\n\n"
                f"🆔 Reference: {ref}"
            )
        elif language == "marathi":
            msg = (
                "✅ *Request Received*\n\n"
                "Inventory आणि safety check पूर्ण झाले आहे.\n"
                "तुमची विनंती Sanjeevani System मध्ये pharmacist confirmation साठी पाठवली आहे.\n\n"
                f"🆔 Reference: {ref}"
            )
        else:
            msg = (
                "✅ *Request Received*\n\n"
                "Inventory and safety checks are complete.\n"
                "Your request has been sent to Sanjeevani System for pharmacist confirmation.\n\n"
                f"🆔 Reference: {ref}"
            )
        send_text(to_number, msg)
        return

    if backend_command == "finalize_order":
        order_id = temp_data.get("order_id", "PENDING")
        address_str = format_address_string(temp_data.get("address_info", {}))
        msg = (
            f"🙌 *Order Confirmed!* \n\n"
            f"Thank you, *{name}*. Your order is being processed. 🚚\n\n"
            f"🆔 *Order ID:* #{order_id}\n"
            f"📍 *Status:* In Progress\n"
            f"📍 *Delivering to:* {address_str}\n\n"
            f"Stay healthy! ✨"
        )
        send_text(to_number, msg)
        return

    if backend_command == "order_cancelled":
        msg = "❌ Order cancelled. Let me know if you need anything else!"
        send_text(to_number, msg)
        return

    # --- TRACKING ---
    if backend_command == "show_tracking":
        if not recent_orders:
            send_text(to_number, "No recent orders found.")
        else:
            order_list = "\n\n".join([f"📦 *ID:* {o['order_id']}\n💊 *Item:* {o['medicine_name']}\n📊 *Status:* {o['status'].title()}" for o in recent_orders])
            send_text(to_number, f"Here are your recent orders:\n\n{order_list}")
        return

    # --- GENERAL (Conversational Chatbot Fallback) ---
    if backend_command in ["general_greeting_or_fallback", "fallback_general"]:
        msg = get_conversational_reply(user_text, user_profile)
        send_text(to_number, msg)
        return
        
    if backend_command == "acknowledge_cancel":
        send_text(to_number, "Okay, no problem. How else can I help?")
        return
