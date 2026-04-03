from typing import Dict, Tuple, Optional
from ..models.enums import ConversationState
from ..models.schemas import NLUExtractionResult
from ..core.logger import logger

class RuleEngine:
    @staticmethod
    def process(
        nlu_result: NLUExtractionResult, 
        current_state: str, 
        user_profile: Dict, 
        temp_data: Dict,
        user_text: str = ""
    ) -> Tuple[str, Dict, str]:
        """
        Takes NLU output and current state, returns (new_state, updated_temp_data, backend_command)
        """
        intent = nlu_result.intent
        fields = nlu_result.extracted_user_fields
        items = nlu_result.items
        
        # --- GLOBAL OVERRIDES ---
        if intent == "CANCEL":
            return ConversationState.GREETING, {}, "acknowledge_cancel"
            
        if intent == "TRACK_ORDER":
            return ConversationState.TRACK_ORDER, temp_data, "show_tracking"

        # --- STATE MACHINE LOGIC ---
        
        # 1. Onboarding Phase
        if current_state == ConversationState.COLLECT_LANGUAGE:
            if fields.language:
                return ConversationState.COLLECT_NAME, temp_data, "ask_name"
            if user_text:
                # Basic fallback for buttons or textual inputs
                clean_t = user_text.lower()
                if "hind" in clean_t:
                    nlu_result.extracted_user_fields.language = "Hindi"
                    return ConversationState.COLLECT_NAME, temp_data, "ask_name"
                if "eng" in clean_t:
                    nlu_result.extracted_user_fields.language = "English"
                    return ConversationState.COLLECT_NAME, temp_data, "ask_name"
                if "marat" in clean_t:
                    nlu_result.extracted_user_fields.language = "Marathi"
                    return ConversationState.COLLECT_NAME, temp_data, "ask_name"
            return ConversationState.COLLECT_LANGUAGE, temp_data, "ask_language_again"

        if current_state == ConversationState.COLLECT_NAME:
            if fields.name:
                return ConversationState.COLLECT_GENDER, temp_data, "ask_gender"
            
            # Filter out likely button clicks or common non-name inputs
            clean_text = user_text.strip()
            lower_text = clean_text.lower()
            button_labels = ["male", "female", "other", "english", "hindi", "marathi", "पुरुष", "महिला", "अन्य", "हिंदी", "मराठी"]
            
            if any(label in lower_text for label in button_labels) and len(clean_text.split()) <= 3:
                # If it looks like a button label, ignore it as a name
                return ConversationState.COLLECT_NAME, temp_data, "ask_name_again"

            if (fields.name) or (len(clean_text.split()) > 0 and intent != "UNKNOWN"):
                if not fields.name: nlu_result.extracted_user_fields.name = clean_text
                return ConversationState.COLLECT_GENDER, temp_data, "ask_gender"
            return ConversationState.COLLECT_NAME, temp_data, "ask_name_again"

        if current_state == ConversationState.COLLECT_GENDER:
            if fields.gender:
                return ConversationState.COLLECT_AGE, temp_data, "ask_age"
            clean_t = user_text.lower()
            if "female" in clean_t or "woman" in clean_t or "girl" in clean_t or "महिला" in clean_t:
                nlu_result.extracted_user_fields.gender = "Female"
                return ConversationState.COLLECT_AGE, temp_data, "ask_age"
            elif "male" in clean_t or "man" in clean_t or "boy" in clean_t or "पुरुष" in clean_t:
                nlu_result.extracted_user_fields.gender = "Male"
                return ConversationState.COLLECT_AGE, temp_data, "ask_age"
            elif "other" in clean_t or "any" in clean_t or "अन्य" in clean_t:
                nlu_result.extracted_user_fields.gender = "Other"
                return ConversationState.COLLECT_AGE, temp_data, "ask_age"

            return ConversationState.COLLECT_GENDER, temp_data, "ask_gender_again"

        if current_state == ConversationState.COLLECT_AGE:
            if fields.age is not None:
                return ConversationState.GREETING, temp_data, "registration_complete"
            import re
            nums = re.findall(r'\d+', user_text)
            if nums:
                nlu_result.extracted_user_fields.age = int(nums[0])
                return ConversationState.GREETING, temp_data, "registration_complete"
            return ConversationState.COLLECT_AGE, temp_data, "ask_age_again"

        # 2. General / Ordering Phase
        if current_state == ConversationState.GREETING:
            if intent == "GREETING":
                return ConversationState.GREETING, temp_data, "welcome_user"
                
            if intent == "ORDER_MEDICINE" and items:
                medicine = items[0]
                if medicine.name:
                    temp_data["medicine_name"] = medicine.name
                if medicine.quantity:
                    temp_data["quantity"] = medicine.quantity
                    return ConversationState.CONFIRM_ORDER, temp_data, "ask_order_confirmation"
                else:
                    return ConversationState.COLLECT_QUANTITY, temp_data, "ask_quantity"
            return ConversationState.GREETING, temp_data, "general_greeting_or_fallback"

        if current_state == ConversationState.COLLECT_QUANTITY:
            # If they just gave a number
            if items and items[0].quantity:
                temp_data["quantity"] = items[0].quantity
                return ConversationState.CONFIRM_ORDER, temp_data, "ask_order_confirmation"
            
            # Backend Fallback: Parse number straight from text if NLU failed to output items
            import re
            nums = re.findall(r'\d+', user_text)
            if nums:
                temp_data["quantity"] = int(nums[0])
                return ConversationState.CONFIRM_ORDER, temp_data, "ask_order_confirmation"

            return ConversationState.COLLECT_QUANTITY, temp_data, "ask_quantity_again"

        if current_state == ConversationState.CONFIRM_ORDER:
            if intent == "CONFIRM":
                return ConversationState.COLLECT_ADDRESS_SELECTION, temp_data, "ask_address_selection"
            if intent == "CANCEL":
                return ConversationState.GREETING, temp_data, "order_cancelled"
            return ConversationState.CONFIRM_ORDER, temp_data, "ask_order_confirmation_again"

        # 3. Address Collection Phase
        if current_state == ConversationState.COLLECT_ADDRESS_SELECTION:
            if user_text and intent not in ["CANCEL", "TRACK_ORDER"]:
                if "address_info" not in temp_data: temp_data["address_info"] = {}
                temp_data["address_info"]["full_address"] = user_text
                return ConversationState.FINALIZE_ORDER, temp_data, "finalize_order"
            return ConversationState.COLLECT_ADDRESS_SELECTION, temp_data, "ask_address_selection"
            
        if current_state == ConversationState.COLLECT_FULL_ADDRESS:
            if "address_info" not in temp_data: temp_data["address_info"] = {}
            temp_data["address_info"]["full_address"] = user_text
            # To keep it simple as requested, we go straight to finalization
            return ConversationState.FINALIZE_ORDER, temp_data, "finalize_order"

        if current_state == ConversationState.COLLECT_ADDRESS_LINE1:
            if "address_info" not in temp_data: temp_data["address_info"] = {}
            temp_data["address_info"]["address_line1"] = user_text
            return ConversationState.COLLECT_ADDRESS_LINE2, temp_data, "ask_address_line2"
            
        if current_state == ConversationState.COLLECT_ADDRESS_LINE2:
            if user_text.lower() != "skip":
                temp_data["address_info"]["address_line2"] = user_text
            return ConversationState.COLLECT_ADDRESS_CITY, temp_data, "ask_city"
            
        if current_state == ConversationState.COLLECT_ADDRESS_CITY:
            temp_data["address_info"]["city"] = user_text
            return ConversationState.COLLECT_ADDRESS_STATE, temp_data, "ask_state"
            
        if current_state == ConversationState.COLLECT_ADDRESS_STATE:
            temp_data["address_info"]["state"] = user_text
            return ConversationState.COLLECT_ADDRESS_PINCODE, temp_data, "ask_pincode"
            
        if current_state == ConversationState.COLLECT_ADDRESS_PINCODE:
            import re
            if re.match(r"^\d{6}$", user_text.strip()):
                temp_data["address_info"]["pincode"] = user_text.strip()
                return ConversationState.COLLECT_ADDRESS_LANDMARK, temp_data, "ask_landmark"
            return ConversationState.COLLECT_ADDRESS_PINCODE, temp_data, "ask_pincode_again"
            
        if current_state == ConversationState.COLLECT_ADDRESS_LANDMARK:
            if user_text.lower() != "skip":
                temp_data["address_info"]["landmark"] = user_text
            return ConversationState.CONFIRM_SAVED_ADDRESS, temp_data, "ask_save_address"
            
        if current_state == ConversationState.FINALIZE_ORDER:
            return ConversationState.GREETING, {}, "finalize_order"

        # Default safety net
        return ConversationState.GREETING, temp_data, "fallback_general"

