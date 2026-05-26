import os
from pymongo import MongoClient


MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017/energy_monitor")

mongo_client = MongoClient(MONGO_URI)

web_db = mongo_client["energy_monitor"]