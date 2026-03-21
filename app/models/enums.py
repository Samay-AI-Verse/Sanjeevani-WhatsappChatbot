from enum import Enum

class ConversationState(str, Enum):
    COLLECT_LANGUAGE = "collect_language"
    COLLECT_NAME = "collect_name"
    COLLECT_GENDER = "collect_gender"
    COLLECT_AGE = "collect_age"
    GREETING = "greeting"
    COLLECT_MEDICINE_NAME = "collect_medicine_name"
    COLLECT_QUANTITY = "collect_quantity"
    CONFIRM_ORDER = "confirm_order"
    FINALIZE_ORDER = "finalize_order"
    TRACK_ORDER = "track_order"
    HANDOFF_TO_HUMAN = "handoff_to_human"
