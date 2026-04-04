from typing import List, Dict, Any, Optional
from app.database.mongo_client import get_db
from app.utils.logger import get_logger

logger = get_logger(__name__)

class MedicineMatcher:
    """
    Search-based medicine matching service.
    Instead of local FAISS, uses MongoDB Atlas Search (or simple regex fallback)
    to match user-input names to the medicine_master dataset.
    """

    def __init__(self):
        self._db = None

    @property
    def db(self):
        if self._db is None:
            from ..services.db_service import get_db_instance
            self._db = get_db_instance()
        return self._db

    async def find_match(self, user_input: str) -> Optional[Dict[str, Any]]:
        """
        Finds the most relevant medicine from the master dataset.
        Priority:
        1. Exact Match (Cleaned)
        2. Atlas Search / Regex Match
        """
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
        # In a real Atlas environment, we would use $search stage here.
        # Example Atlas Search Stage (User must create index 'medicine_search' on 'brand_name'):
        # [
        #   { "$search": { "index": "medicine_search", "text": { "query": user_input, "path": "brand_name", "fuzzy": {} } } },
        #   { "$limit": 1 }
        # ]
        
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
