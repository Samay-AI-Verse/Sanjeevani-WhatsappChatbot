import re
from typing import Dict
from .db_service import update_conversation_state, create_order
from .whatsapp import send_whatsapp_text, send_whatsapp_buttons
from ..models.enums import ConversationState

async def handle_order_flow(user_number: str, user_text: str, conversation_state: Dict):
    """
    General handler for order flow steps if needed.
    Currently, much of the logic is handled in routes.py via AI intents.
    """
    pass

def format_order_summary(order_info: Dict) -> str:
    """Format order summary for display"""
    return f"💊 *Medicine:* {order_info.get('medicine_name')}\n📊 *Quantity:* {order_info.get('quantity')}\n💰 *Price:* ₹{order_info.get('price', 250)}"
