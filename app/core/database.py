from motor.motor_asyncio import AsyncIOMotorClient
from .config import MONGODB_URL
from .logger import logger

# =============================
# DATABASE SETUP
# =============================

mongo_client = None
db = None
users_collection = None
orders_collection = None
addresses_collection = None
conversations_collection = None

def init_db():
    global mongo_client, db, users_collection, orders_collection, addresses_collection, conversations_collection
    try:
        mongo_client = AsyncIOMotorClient(MONGODB_URL)
        db = mongo_client.pharmacy_db
        users_collection = db.users
        orders_collection = db.orders
        addresses_collection = db.addresses
        conversations_collection = db.conversations
        logger.info("✅ Connected to MongoDB")
    except Exception as e:
        logger.error(f"❌ MongoDB Connection Failed: {e}")
        mongo_client = None
        users_collection = None
        orders_collection = None
        addresses_collection = None
        conversations_collection = None

# Initialize immediately for module-level access
init_db()
