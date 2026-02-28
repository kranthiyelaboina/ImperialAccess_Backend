"""
db.py — MongoDB Atlas connection and collection accessors
"""
from pymongo import MongoClient
from config import MONGO_URI, MONGO_DB_NAME

_client = None
_db = None


def get_db():
    """Get the MongoDB database instance (lazy singleton)."""
    global _client, _db
    if _db is None:
        _client = MongoClient(MONGO_URI)
        _db = _client[MONGO_DB_NAME]
        print(f"[DB] Connected to MongoDB: {MONGO_DB_NAME}")
    return _db


def get_collection(name):
    """Shorthand to get a collection."""
    return get_db()[name]


# =====================================================
# COLLECTION ACCESSORS
# =====================================================
def users_col():
    return get_collection("users")

def guest_profiles_col():
    return get_collection("guest_profiles")

def lounge_registrations_col():
    return get_collection("lounge_registrations")

def payments_col():
    return get_collection("payments")

def dining_tokens_col():
    return get_collection("dining_tokens")

def attendance_logs_col():
    return get_collection("attendance_logs")

def agent_events_col():
    return get_collection("agent_events")


# =====================================================
# INDEX SETUP (call once on startup)
# =====================================================
def setup_indexes():
    """Create MongoDB indexes for performance."""
    users_col().create_index("username", unique=True)
    users_col().create_index("email", unique=True, sparse=True)
    guest_profiles_col().create_index("user_id", unique=True)
    dining_tokens_col().create_index("token_code", unique=True)
    payments_col().create_index("transaction_ref", unique=True, sparse=True)
    attendance_logs_col().create_index("timestamp")
    attendance_logs_col().create_index("guest_name")
    agent_events_col().create_index("timestamp")
    print("[DB] Indexes created.")
