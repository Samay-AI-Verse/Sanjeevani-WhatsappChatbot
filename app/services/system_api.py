import httpx
import os
from typing import List, Dict, Any, Optional
from ..core.logger import logger

SYSTEM_API_URL = os.getenv("SYSTEM_API_URL", "http://localhost:8001/api/v1")

async def call_agent_process_order(user_phone: str, merchant_id: str, items: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """
    Calls the Sanjeevani System Agent API to process the order.
    """
    url = f"{SYSTEM_API_URL}/agent/process-order"
    payload = {
        "user_phone": user_phone,
        "merchant_id": merchant_id,
        "items": items
    }
    
    logger.info(f"Calling System Agent API: {url}")
    
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(url, json=payload)
            if response.status_code == 200:
                return response.json()
            else:
                logger.error(f"System API Error: {response.status_code} - {response.text}")
                return None
    except Exception as e:
        logger.error(f"Failed to call System API: {e}")
        return None
