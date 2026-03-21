import json
from typing import Dict
from ..core.logger import logger
from ..services.whatsapp import send_whatsapp_text as send_twilio_text, send_whatsapp_buttons as send_twilio_buttons
from ..services.whatsapp_meta import send_whatsapp_text_meta, send_whatsapp_buttons_meta
from ..services.ai_service import get_conversational_reply

def format_address_string(address: Dict) -> str:
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
        
        # Note: Meta button IDs must be unique and short
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
        msg = f"🎉 *Registration Complete!*\n\n🌟 *Welcome to Sanjeevani Care, {name}!* 🌟\n\nYour trusted pharmacy partner. ⚕️\n\nHow can I help you today?\n👉 *'I want to order Paracetamol'*\n👉 *'Track my order'* "
        send_text(to_number, msg)
        return

    if backend_command == "welcome_user":
        if language == "hindi":
            msg = f"नमस्ते {name}, संजीवनी केयर में आपका स्वागत है। 🙏 मैं आपकी कैसे मदद कर सकता हूँ? \n\nआप दवा मंगवा सकते हैं या अपना ऑर्डर ट्रैक कर सकते हैं। 💊"
        elif language == "marathi":
            msg = f"नमस्कार {name}, संजीवनी केअरमध्ये आपले स्वागत आहे। 🙏 मी तुमची कशी मदत करू शकतो? \n\nतुम्ही औषध ऑर्डर करू शकता किंवा तुमचा ऑर्डर मागोवा घेऊ शकता। 💊"
        else:
            msg = f"Hello {name}, welcome back to Sanjeevani Care! 🙏 How can I help you today?\n\nYou can order medicines or track your existing order. 💊"
        send_text(to_number, msg)
        return

    # --- ORDERING ---
    if backend_command in ["ask_quantity", "ask_quantity_again"]:
        med = temp_data.get('medicine_name', 'this medicine')
        msg = f"How many tablets/strips of *{med}* do you need?" if language == "english" else f"आपको *{med}* की कितनी मात्रा चाहिए?"
        send_text(to_number, msg)
        return

    if backend_command in ["ask_order_confirmation", "ask_order_confirmation_again"]:
        med = temp_data.get("medicine_name")
        qty = temp_data.get("quantity")
        price = temp_data.get("price", 250)
        total = int(qty) * price
        
        summary = f"✨ *Order Summary* ✨\n--------------------------\n💊 *Medicine:* {med}\n📊 *Quantity:* {qty}\n💰 *Estimated Price:* ₹{total}\n🚚 *Delivery:* Home Delivery\n--------------------------\n*Confirm your medicine details?*"
        
        buttons = [
            {"id": "confirm_order", "title": "✅ Confirm & Set Address"},
            {"id": "cancel_order", "title": "❌ Cancel"}
        ]
        send_buttons(to_number, summary, buttons)
        return

    # --- ADDRESS COLLECTION ---
    if backend_command == "ask_address_selection":
        # Check if we have addresses in temp_data (passed from route)
        addresses = temp_data.get("available_addresses", [])
        if addresses:
            msg = "📍 Please select a delivery address or add a new one:" if language == "english" else "📍 कृपया डिलीवरी पता चुनें या नया जोड़ें:"
            items = []
            for i, addr in enumerate(addresses[:3]):
                items.append({"id": f"addr_select_{i}", "title": f"{addr.get('address_type', 'Home')}: {addr['address_line1'][:20]}"})
            items.append({"id": "addr_new", "title": "➕ Add New Address"})
            send_list(to_number, msg, "Delivery Addresses", items)
        else:
            msg = "🏠 Please enter your *Address Line 1* (Street, building, etc.):" if language == "english" else "🏠 कृपया अपना *पता लाइन 1* (सड़क, इमारत, आदि) दर्ज करें:"
            send_text(to_number, msg)
        return

    if backend_command == "ask_address_line2":
        msg = "Enter *Address Line 2* (Area/locality) or type 'Skip':" if language == "english" else "*पता लाइन 2* (क्षेत्र/इलाका) दर्ज करें या 'Skip' लिखें:"
        send_text(to_number, msg)
        return

    if backend_command == "ask_city":
        msg = "Enter your *City*:" if language == "english" else "अपना *शहर* दर्ज करें:"
        send_text(to_number, msg)
        return

    if backend_command == "ask_state":
        msg = "Enter your *State*:" if language == "english" else "अपना *राज्य* दर्ज करें:"
        send_text(to_number, msg)
        return

    if backend_command in ["ask_pincode", "ask_pincode_again"]:
        if backend_command == "ask_pincode_again":
            msg = "❌ Invalid pincode. Please enter 6 digits:" if language == "english" else "❌ गलत पिनकोड। कृपया 6 अंक दर्ज करें:"
        else:
            msg = "Enter your *Pincode* (6 digits):" if language == "english" else "अपना *पिनकोड* (6 अंक) दर्ज करें:"
        send_text(to_number, msg)
        return

    if backend_command == "ask_landmark":
        msg = "Enter a *Landmark* (Optional) or type 'Skip':" if language == "english" else "एक *लैंडमार्क* (वैकल्पिक) दर्ज करें या 'Skip' लिखें:"
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

    if backend_command == "finalize_order":
        order_id = temp_data.get("order_id", "PENDING")
        address_str = format_address_string(temp_data.get("address_info", {}))
        msg = f"🙌 *Order Confirmed!*\n\nThank you, {name}. Your order has been placed successfully.\n\n🆔 *Order ID:* #{order_id}\n📍 *Status:* Being Processed\n🚚 *Delivering to:* {address_str}\n\nStay healthy! ✨"
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
        # Use our LLM specifically for casual chat
        msg = get_conversational_reply(user_text, user_profile)
        send_text(to_number, msg)
        return
        
    if backend_command == "acknowledge_cancel":
        send_text(to_number, "Okay, no problem. How else can I help?")
        return
