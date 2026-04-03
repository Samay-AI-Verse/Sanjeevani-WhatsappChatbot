import json
from datetime import datetime
from typing import Optional, Dict, List
from ..core.database import users_collection, orders_collection, addresses_collection, conversations_collection
from ..core.config import DEFAULT_MERCHANT_ID, DEFAULT_PHARMACY_ID
from ..core.logger import logger
from ..models.enums import ConversationState

async def get_user_profile(phone: str) -> Optional[Dict]:
    if users_collection is None:
        return None
    return await users_collection.find_one({"user_id": phone})


async def ensure_order_indexes() -> None:
    if orders_collection is None:
        return
    try:
        await orders_collection.create_index([("merchant_id", 1), ("Order Date", -1)], background=True, name="idx_merchant_order_date")
        await orders_collection.create_index(
            [("source_channel", 1), ("source_message_id", 1)],
            unique=True,
            sparse=True,
            background=True,
            name="uniq_source_message",
        )
    except Exception as exc:
        logger.warning(f"Could not ensure order indexes: {exc}")


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
        logger.error("create_order skipped: orders_collection is not initialized")
        return

    # Generate order ID
    order_id = f"ORD{datetime.utcnow().strftime('%Y%m%d%H%M%S')}{phone[-4:]}"

    quantity = int(order_info.get("quantity", 1))
    price = int(order_info.get("price", 0))
    source_channel = order_info.get("source_channel", "whatsapp")
    source_provider = order_info.get("source_provider", "meta")
    source_message_id = order_info.get("source_message_id")
    pharmacy_id = (
        order_info.get("pharmacy_id")
        or order_info.get("merchant_id")
        or DEFAULT_PHARMACY_ID
        or DEFAULT_MERCHANT_ID
    )
    merchant_id = order_info.get("merchant_id") or pharmacy_id

    if not merchant_id:
        merchant_id = "default_pharmacy"
        pharmacy_id = pharmacy_id or merchant_id
        logger.warning(
            "create_order: missing pharmacy/merchant id; using fallback default_pharmacy"
        )

    if source_message_id:
        existing = await orders_collection.find_one(
            {"source_channel": source_channel, "source_message_id": source_message_id},
            {"_id": 0, "order_id": 1, "Order ID": 1},
        )
        if existing:
            return existing.get("order_id") or existing.get("Order ID")

    order_data = {
        # Canonical identity
        "pharmacy_id": pharmacy_id,
        "merchant_id": merchant_id,
        # Assistant shape
        "order_id": order_id,
        "user_id": phone,
        "medicine_name": order_info.get("medicine_name"),
        "quantity": quantity,
        "price": price,
        "total_amount": quantity * price,
        "delivery_address": order_info.get("delivery_address", "Local Pickup / Pending"),
        "address_id": order_info.get("address_id"),
        "order_status": "Pending",
        "payment_status": "pending",
        # System-compatible shape for shared dashboards
        "Order ID": order_id,
        "Patient Name": order_info.get("patient_name") or order_info.get("name") or "Customer",
        "Medicine Name": order_info.get("medicine_name"),
        "Quantity": quantity,
        "Total Amount": quantity * price,
        "Order Status": "Pending",
        "Order Channel": order_info.get("order_channel") or ("WhatsApp" if source_channel == "whatsapp" else "Sanjeevani App"),
        "Order Date": datetime.utcnow(),
        "Payment Method": order_info.get("payment_method", "Unpaid"),
        "Contact Number": phone,
        # Source metadata
        "source_channel": source_channel,
        "source_provider": source_provider,
        "source_message_id": source_message_id,
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
            "order_id": o.get("order_id") or o.get("Order ID"),
            "medicine_name": o.get("medicine_name") or o.get("Medicine Name"),
            "quantity": o.get("quantity") or o.get("Quantity"),
            "total_amount": o.get("total_amount") or o.get("Total Amount"),
            "status": o.get("order_status") or o.get("Order Status"),
            "date": (
                o.get("created_at").isoformat() if o.get("created_at") else "Unknown"
            ),
        }
        for o in orders
    ]
