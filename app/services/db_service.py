import json
from datetime import datetime
from typing import Optional, Dict, List
from ..core.database import users_collection, orders_collection, addresses_collection, conversations_collection
from ..core.logger import logger
from ..models.enums import ConversationState

async def get_user_profile(phone: str) -> Optional[Dict]:
    if users_collection is None:
        return None
    return await users_collection.find_one({"user_id": phone})


async def update_user_profile(phone: str, user_data: Dict):
    if users_collection is None:
        return
    existing = await users_collection.find_one({"user_id": phone})

    # Clean None values
    update_data = {k: v for k, v in user_data.items() if v is not None}
    update_data["user_id"] = phone

    if not existing:
        update_data["created_at"] = datetime.utcnow()
        await users_collection.insert_one(update_data)
    else:
        await users_collection.update_one({"user_id": phone}, {"$set": update_data})


async def get_conversation_state(phone: str) -> Dict:
    """Get or create conversation state for user"""
    if conversations_collection is None:
        return {"state": ConversationState.GREETING, "temp_data": {}}

    state = await conversations_collection.find_one({"user_id": phone})
    if not state:
        state = {
            "user_id": phone,
            "state": ConversationState.GREETING,
            "temp_data": {},
            "updated_at": datetime.utcnow(),
        }
        await conversations_collection.insert_one(state)
    return state


async def update_conversation_state(phone: str, new_state: str, temp_data: Dict = None):
    """Update conversation state for user"""
    if conversations_collection is None:
        return

    update = {"state": new_state, "updated_at": datetime.utcnow()}
    if temp_data is not None:
        update["temp_data"] = temp_data

    await conversations_collection.update_one(
        {"user_id": phone}, {"$set": update}, upsert=True
    )


async def save_user_address(phone: str, address_data: Dict) -> str:
    """Save user address and return address ID"""
    if addresses_collection is None:
        return None

    address = {
        "user_id": phone,
        "full_address": address_data.get("full_address"),
        "address_line1": address_data.get("address_line1"),
        "address_line2": address_data.get("address_line2"),
        "city": address_data.get("city"),
        "state": address_data.get("state"),
        "pincode": address_data.get("pincode"),
        "landmark": address_data.get("landmark"),
        "address_type": address_data.get("address_type", "Home"),
        "is_default": address_data.get("is_default", False),
        "created_at": datetime.utcnow(),
    }

    # If this is set as default, remove default from others
    if address["is_default"]:
        await addresses_collection.update_many(
            {"user_id": phone, "is_default": True}, {"$set": {"is_default": False}}
        )

    result = await addresses_collection.insert_one(address)
    return str(result.inserted_id)


async def get_user_addresses(phone: str) -> List[Dict]:
    """Get all addresses for user"""
    if addresses_collection is None:
        return []

    cursor = addresses_collection.find({"user_id": phone}).sort("is_default", -1)
    addresses = await cursor.to_list(length=10)
    return addresses


async def create_order(phone: str, order_info: Dict):
    """Create a new order"""
    if orders_collection is None:
        return

    # Generate order ID
    order_id = f"ORD{datetime.utcnow().strftime('%Y%m%d%H%M%S')}{phone[-4:]}"

    order_data = {
        "order_id": order_id,
        "user_id": phone,
        "medicine_name": order_info.get("medicine_name"),
        "quantity": int(order_info.get("quantity", 1)),
        "price": int(order_info.get("price", 0)),
        "total_amount": int(order_info.get("quantity", 1)) * int(order_info.get("price", 0)),
        "delivery_address": order_info.get("delivery_address", "Local Pickup / Pending"),
        "address_id": order_info.get("address_id"),
        "order_status": "confirmed",
        "payment_status": "pending",
        "created_at": datetime.utcnow(),
        "updated_at": datetime.utcnow(),
    }

    await orders_collection.insert_one(order_data)
    return order_id


async def get_recent_orders(phone: str) -> List[Dict]:
    """Get recent orders for user"""
    if orders_collection is None:
        return []
    cursor = orders_collection.find({"user_id": phone}).sort("created_at", -1).limit(3)
    orders = await cursor.to_list(length=3)
    return [
        {
            "order_id": o.get("order_id"),
            "medicine_name": o.get("medicine_name"),
            "quantity": o.get("quantity"),
            "total_amount": o.get("total_amount"),
            "status": o.get("order_status"),
            "date": (
                o.get("created_at").isoformat() if o.get("created_at") else "Unknown"
            ),
        }
        for o in orders
    ]
