from typing import List, Dict, Any, Optional
from ..core.database import db
from ..core.logger import logger

class MedicineMatcher:
    """
    Search-based medicine matching service.
    Instead of local FAISS, uses MongoDB Atlas Search (or simple regex fallback)
    to match user-input names to the medicine_master dataset.
    """

    def __init__(self):
        self._db = db

    @property
    def db(self):
        return self._db

    async def find_match(self, user_input: str) -> Optional[Dict[str, Any]]:
        """
        Finds the most relevant medicine from the master dataset.
        Priority:
        1. Exact Match (Cleaned)
        2. Atlas Search / Regex Match
        """
        if self.db is None:
            logger.error("MedicineMatcher: database connection is not initialized")
            return None
            
        clean_input = user_input.strip().lower()
        
        # 1. Exact Match
        match = await self.db["medicine_master"].find_one({"brand_name_clean": clean_input})
        if match:
            return {
                "name": match["brand_name"],
                "score": 1.0,
                "requires_prescription": match.get("requires_prescription", False) or match.get("habit_forming", False)
            }
            
        # 2. Regex Fallback (Fuzzy-ish)
        cursor = self.db["medicine_master"].find({
            "brand_name": {"$regex": f"{user_input}", "$options": "i"}
        }).limit(1)
        
        async for m in cursor:
            return {
                "name": m["brand_name"],
                "score": 0.8,
                "requires_prescription": m.get("requires_prescription", False) or m.get("habit_forming", False)
            }

        return None

