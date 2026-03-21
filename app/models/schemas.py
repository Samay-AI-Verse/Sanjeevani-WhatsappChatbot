from pydantic import BaseModel, Field
from typing import List, Optional

class ExtractedItem(BaseModel):
    name: Optional[str] = Field(None, description="Name of the medicine")
    quantity: Optional[int] = Field(None, description="Quantity or number of strips/tablets")
    dosage: Optional[str] = Field(None, description="Optional dosage like 500mg")

class ExtractedUserFields(BaseModel):
    name: Optional[str] = None
    age: Optional[int] = None
    gender: Optional[str] = None
    language: Optional[str] = None

class NLUExtractionResult(BaseModel):
    intent: str = Field(..., description="ORDER_MEDICINE | CONFIRM | CANCEL | PROVIDE_INFO | TRACK_ORDER | GREETING | COMPLAINT | UNKNOWN")
    items: List[ExtractedItem] = Field(default_factory=list)
    extracted_user_fields: ExtractedUserFields = Field(default_factory=ExtractedUserFields)
    prescription_check_needed: bool = False
    confidence: float = Field(..., ge=0.0, le=1.0)
    user_message_type: str = "text"
