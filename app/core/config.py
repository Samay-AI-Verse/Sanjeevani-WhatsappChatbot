import os
from dotenv import load_dotenv

load_dotenv()

# =============================
# TWILIO CONFIGURATION
# =============================
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
TWILIO_WHATSAPP_NUMBER = os.getenv("TWILIO_WHATSAPP_NUMBER", "whatsapp:+14155238886")
# Verify token is technically not used by Twilio, but we'll keep it as a fallback secret or remove if unwanted
VERIFY_TOKEN = os.getenv("VERIFY_TOKEN", "verify_me")

GROQ_API_KEY = os.getenv("GROQ_API_KEY")
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.1-8b-instant")
MONGODB_URL = os.getenv("MONGODB_URL", "mongodb://localhost:27017")

# =============================
# META CLOUD API CONFIGURATION
# =============================
META_ACCESS_TOKEN = os.getenv("META_ACCESS_TOKEN", "")
META_PHONE_NUMBER_ID = os.getenv("META_PHONE_NUMBER_ID", "")
META_VERIFY_TOKEN = os.getenv("META_VERIFY_TOKEN", VERIFY_TOKEN)
