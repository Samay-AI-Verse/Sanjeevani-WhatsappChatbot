from enum import Enum

class ConversationState(str, Enum):
    COLLECT_LANGUAGE = "collect_language"
    COLLECT_NAME = "collect_name"
    COLLECT_GENDER = "collect_gender"
    COLLECT_AGE = "collect_age"
    GREETING = "greeting"
    # Ordering
    COLLECT_MEDICINE_NAME = "collect_medicine_name"
    COLLECT_QUANTITY = "collect_quantity"
    CONFIRM_ORDER = "confirm_order"
    
    # Address Collection
    COLLECT_ADDRESS_SELECTION = "collect_address_selection"
    COLLECT_FULL_ADDRESS = "collect_full_address"
    COLLECT_ADDRESS_LINE1 = "collect_address_line1"
    COLLECT_ADDRESS_LINE2 = "collect_address_line2"
    COLLECT_ADDRESS_CITY = "collect_address_city"
    COLLECT_ADDRESS_STATE = "collect_address_state"
    COLLECT_ADDRESS_PINCODE = "collect_address_pincode"
    COLLECT_ADDRESS_LANDMARK = "collect_address_landmark"
    CONFIRM_SAVED_ADDRESS = "confirm_saved_address"
    
    # Finalization
    FINALIZE_ORDER = "finalize_order"
    TRACK_ORDER = "track_order"
    HANDOFF_TO_HUMAN = "handoff_to_human"
    AWAITING_PRESCRIPTION = "awaiting_prescription"
