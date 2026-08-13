import os
import json
from pymongo import MongoClient
from dotenv import load_dotenv
load_dotenv()
from datetime import date

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

        # 其他資料目前仍維持 JSON
        self.tasks = _load(TASKS_FILE, [])
        self.factories = _load(FACTORIES_FILE, [])
        self.equipments = _load(EQUIPMENTS_FILE, [])

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
    def seed_factories(self, names):
        """若無廠區資料，則初始化"""
        if not self.factories:
            self.factories = names
            self._save_factories()

    def get_factories(self):
        return list(self.factories)

    def add_factory(self, name: str):
        """新增廠區名稱，如果已存在就回 False"""
        name = name.strip()
        if not name:
            return False
        if name in self.factories:
            return False
        self.factories.append(name)
        self._save_factories()
        return True

    def delete_factory(self, name: str):
        """刪除廠區，若不存在回 False"""
        name = name.strip()
        if name not in self.factories:
            return False
        self.factories.remove(name)
        self._save_factories()
        return True


    # ===================== 任務 =====================
    def create_task(self, factory, machine, assigned_user_id, task_type="巡檢", date_str=None):
        """建立任務"""
        if date_str is None:
            date_str = date.today().isoformat()

        task = {
            "id": len(self.tasks) + 1,
            "factory": factory,
            "machine": machine,
            "assigned_user_id": assigned_user_id,
            "task_type": task_type,
            "date": date_str,
            "status": "待執行"
        }
        self.tasks.append(task)
        self._save_tasks()
        return task

    def get_tasks_by_date(self, date_str):
        return [t for t in self.tasks if t["date"] == date_str]

    def update_task_status(self, task_id, status):
        for t in self.tasks:
            if t["id"] == task_id:
                t["status"] = status
                self._save_tasks()
                return True
        return False
    def complete_task_for_user(self, task_id, user_id):
        '''
        只允許被指派的使用者完成自己的任務。

        回傳值：
        - "success"：完成成功
        - "not_found"：找不到任務
        - "forbidden"：任務不是指派給此使用者
        - "already_done"：任務已經完成
        '''
        for task in self.tasks:
            if task["id"] != task_id:
                continue

            if task.get("assigned_user_id") != user_id:
                return "forbidden"

            if task.get("status") == "已完成":
                return "already_done"

            task["status"] = "已完成"
            self._save_tasks()
            return "success"

        return "not_found"
    
    def add_equipment(self, factory: str, name: str, eq_type: str = ""):
        """新增設備，回傳設備物件"""
        factory = factory.strip()
        name = name.strip()
        if not factory or not name:
            return None

        # 建 ID（簡單用長度+1）
        eq_id = len(self.equipments) + 1
        eq = {
            "id": eq_id,
            "factory": factory,
            "name": name,
            "type": eq_type
        }
        self.equipments.append(eq)
        self._save_equipments()
        return eq

    def delete_equipment(self, eq_id: int):
        """用 id 刪除設備"""
        for i, e in enumerate(self.equipments):
            if e["id"] == eq_id:
                self.equipments.pop(i)
                self._save_equipments()
                return True
        return False

    def list_equipments(self, factory: str | None = None):
        if not factory:
            return list(self.equipments)
        return [e for e in self.equipments if e["factory"] == factory]


    # ===================== 儲存 =====================
    def _save_users(self):
        _save(USERS_FILE, self.users)

    def _save_tasks(self):
        _save(TASKS_FILE, self.tasks)

    def _save_factories(self):
        _save(FACTORIES_FILE, self.factories)
