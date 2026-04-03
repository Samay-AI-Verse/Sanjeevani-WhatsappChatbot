from motor.motor_asyncio import AsyncIOMotorClient
import os
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
channel_bindings_collection = None

def init_db():
    global mongo_client, db, users_collection, orders_collection, addresses_collection, conversations_collection, channel_bindings_collection
    try:
        mongo_client = AsyncIOMotorClient(MONGODB_URL)
        db_name = os.getenv("MONGODB_DB_NAME", "sanjeevani_rx_db")
        db = mongo_client[db_name]
        users_collection = db.users
        orders_collection = db.consumer_orders
        addresses_collection = db.addresses
        conversations_collection = db.conversations
        channel_bindings_collection = db.channel_bindings
        logger.info("✅ Connected to MongoDB")
    except Exception as e:
        logger.error(f"❌ MongoDB Connection Failed: {e}")
        mongo_client = None
        users_collection = None
        orders_collection = None
        addresses_collection = None
        conversations_collection = None
        channel_bindings_collection = None

# Initialize immediately for module-level access
init_db()
