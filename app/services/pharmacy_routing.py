from __future__ import annotations

from datetime import datetime
from typing import Optional

from ..core.config import DEFAULT_MERCHANT_ID, DEFAULT_PHARMACY_ID
from ..core.database import channel_bindings_collection
from ..core.logger import logger


def _fallback_pharmacy_id() -> Optional[str]:
    return DEFAULT_PHARMACY_ID or DEFAULT_MERCHANT_ID or None


async def ensure_channel_binding_indexes() -> None:
    if channel_bindings_collection is None:
        return
    try:
        await channel_bindings_collection.create_index(
            [("channel", 1), ("channel_user_id", 1)],
            unique=True,
            background=True,
            name="uniq_channel_user",
        )
        await channel_bindings_collection.create_index(
            [("pharmacy_id", 1), ("is_active", 1)],
            background=True,
            name="idx_pharmacy_active",
        )
    except Exception as exc:
        logger.warning(f"Could not ensure channel binding indexes: {exc}")


async def bind_channel_to_pharmacy(
    *,
    channel: str,
    channel_user_id: str,
    pharmacy_id: str,
) -> None:
    if channel_bindings_collection is None or not pharmacy_id:
        return
    await channel_bindings_collection.update_one(
        {"channel": channel, "channel_user_id": channel_user_id},
        {
            "$set": {
                "pharmacy_id": pharmacy_id,
                "merchant_id": pharmacy_id,
                "is_active": True,
                "updated_at": datetime.utcnow(),
            },
            "$setOnInsert": {
                "created_at": datetime.utcnow(),
            },
        },
        upsert=True,
    )


async def resolve_pharmacy_id(
    *,
    channel: str,
    channel_user_id: str,
    explicit_pharmacy_id: Optional[str] = None,
) -> Optional[str]:
    if explicit_pharmacy_id:
        return explicit_pharmacy_id

    if channel_bindings_collection is not None:
        binding = await channel_bindings_collection.find_one(
            {"channel": channel, "channel_user_id": channel_user_id, "is_active": {"$ne": False}},
            {"_id": 0, "pharmacy_id": 1},
        )
        if binding and binding.get("pharmacy_id"):
            return binding["pharmacy_id"]

    return _fallback_pharmacy_id()
