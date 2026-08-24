import os
import json
from pymongo import MongoClient
from dotenv import load_dotenv
load_dotenv()
from datetime import date, datetime, timezone

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
os.makedirs(DATA_DIR, exist_ok=True)

USERS_FILE = os.path.join(DATA_DIR, "users.json")
TASKS_FILE = os.path.join(DATA_DIR, "tasks.json")
FACTORIES_FILE = os.path.join(DATA_DIR, "factories.json")
EQUIPMENTS_FILE = os.path.join(DATA_DIR, "equipments.json")


# ------------------- 共用讀寫 -------------------
def _load(path, default):
    """讀取 JSON；若檔案不存在則自動建立。"""

    if not os.path.exists(path):
        _save(path, default)
        return default

    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    except (json.JSONDecodeError, OSError):
        return default


def _save(path, obj):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)


# ------------------- 主類別 -------------------
class DBManager:
    def __init__(self):
        # MongoDB
        mongo_uri = os.getenv(
            "MONGO_URI",
            "mongodb://localhost:27017/energy_monitor"
        )

        self.mongo_client = MongoClient(mongo_uri)
        self.mongo_db = self.mongo_client["energy_monitor"]

        self.users_collection = self.mongo_db["users"]
        self.factories_collection = self.mongo_db["factories"]

        self.equipments_collection = self.mongo_db["equipments"]
        self.tasks_collection = self.mongo_db["tasks"]


    # ===================== 使用者 =====================
    def add_user(self, user_id, name=None, factory_priority=None, role=None):
        """
        factory_priority 格式：
        { "北區廠": 1, "東區廠": 2 }
        """
        if self.get_user(user_id):
            return False
        
        user = {
            "user_id": user_id,
            "name": name,
            "factory_priority": factory_priority,
            "role": role
        }

        self.users_collection.insert_one(user)

        return True

    def get_user(self, user_id):
        user = self.users_collection.find_one(
            {"user_id": user_id},
            {"_id": 0}
        )

        return user

    def get_all_users(self):
        return list(
            self.users_collection.find(
                {},
                {"_id": 0}
            )
        )

    def _save_equipments(self):
        _save(EQUIPMENTS_FILE, self.equipments)

    def update_user(self, user_id, **kwargs):
        result = self.users_collection.update_one(
            {"user_id": user_id},
            {"$set": kwargs}
        )

        return result.matched_count > 0

    # ===================== 廠區 =====================
    def seed_factories(self, factories):
        """若無廠區資料，則初始化"""
        for name in factories:
            self.factories_collection.update_one(
                {"name": name},
                {"$setOnInsert": {"name": name}},
                upsert=True
            )

    def get_factories(self):
        docs = self.factories_collection.find(
            {},
            {"_id": 0, "name": 1}
        )

        return [doc["name"] for doc in docs]

    def add_factory(self, name: str):
        """新增廠區名稱，如果已存在就回 False"""
        if self.factories_collection.find_one({"name": name}):
            return False

        self.factories_collection.insert_one({
            "name": name
        })

        return True

    def delete_factory(self, name: str):
        """刪除廠區，若不存在回 False"""
        result = self.factories_collection.delete_one({
            "name": name
        })

        return result.deleted_count > 0

    # ===================== 任務 =====================
    def create_task(self, factory, machine, assigned_user_id, task_type="巡檢", date_str=None):
        """建立任務並寫入 MongoDB"""

        if date_str is None:
            date_str = date.today().isoformat()

    # 找目前最大的任務 ID
        last_task = self.tasks_collection.find_one(sort=[("id", -1)])

        if last_task:
            new_id = last_task.get("id", 0) + 1
        else:
            new_id = 1

        task = {
            "id": new_id,
            "factory": factory,
            "machine": machine,
            "assigned_user_id": assigned_user_id,
            "task_type": task_type,
            "date": date_str,
            "status": "待執行",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "completed_at": None
        }

        self.tasks_collection.insert_one(task)

    # insert_one 會在 task 裡加入 _id
    # app.py 不需要這個欄位，所以移除
        task.pop("_id", None)
        return task
    
    def get_tasks_by_date(self, target_date):
        return list(
            self.tasks_collection.find(
            {"date": target_date},
            {"_id": 0}
            )
        )

    def get_tasks_by_user(self, user_id, target_date=None):
        query = {
        "assigned_user_id": user_id
        }

        if target_date:
            query["date"] = target_date

        return list(
            self.tasks_collection.find(
                query,
                {"_id": 0}
            )
        )
    
    def complete_task_for_user(self, task_id, user_id):
        '''
        只允許被指派的使用者完成自己的任務。

        回傳值：
        - "success"：完成成功
        - "not_found"：找不到任務
        - "forbidden"：任務不是指派給此使用者
        - "already_done"：任務已經完成
        '''
        task = self.tasks_collection.find_one(
            {"id": task_id}
        )

        if not task:
            return "not_found"

        if task.get("assigned_user_id") != user_id:
            return "forbidden"

        if task.get("status") == "已完成":
            return "already_done"

        self.tasks_collection.update_one(
            {"id": task_id},
            {
                "$set": {
                    "status": "已完成",
                    "completed_at": datetime.now(timezone.utc).isoformat()
                }
            }
        )

        return "success"
    
    def add_equipment(self, factory, name):
        """新增設備，回傳設備物件"""
        last = self.equipments_collection.find_one(
            sort=[("id", -1)]
        )

        new_id = 1 if not last else last["id"] + 1

        equipment = {
            "id": new_id,
            "factory": factory,
            "name": name
        }

        self.equipments_collection.insert_one(equipment)

        return equipment

    def delete_equipment(self, equipment_id):
        """用 id 刪除設備"""
        result = self.equipments_collection.delete_one(
            {"id": equipment_id}
        )

        return result.deleted_count > 0

    def list_equipments(self, factory=None):
        query = {}

        if factory:
            query["factory"] = factory

        return list(
            self.equipments_collection.find(
            query,
            {"_id": 0}
            )
        )


    # ===================== 儲存 =====================
    def _save_users(self):
        _save(USERS_FILE, self.users)

    def _save_tasks(self):
        _save(TASKS_FILE, self.tasks)

    def _save_factories(self):
        _save(FACTORIES_FILE, self.factories)
