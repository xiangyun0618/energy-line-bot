# conversation.py
# 使用 MongoDB 保存互動流程狀態
# flow 用來區分：registration / create_task

_state_collection = None


def init_state_store(db_manager):
    global _state_collection
    _state_collection = db_manager.mongo_db["conversation_states"]


def _collection():
    if _state_collection is None:
        raise RuntimeError("conversation state store 尚未初始化")
    return _state_collection


def start_flow(user_id, flow_name):
    _collection().replace_one(
        {"user_id": user_id},
        {
            "user_id": user_id,
            "flow": flow_name,
            "step": 1,
            "temp": {}
        },
        upsert=True
    )


def start_registration(user_id):
    start_flow(user_id, "registration")


def start_create_task(user_id):
    start_flow(user_id, "create_task")


def get_state(user_id):
    return _collection().find_one(
        {"user_id": user_id},
        {"_id": 0}
    )


def get_flow(user_id):
    state = get_state(user_id)

    if not state:
        return None

    return state.get("flow")


def advance(user_id):
    _collection().update_one(
        {"user_id": user_id},
        {"$inc": {"step": 1}}
    )


def set_temp(user_id, key, value):
    result = _collection().update_one(
        {"user_id": user_id},
        {
            "$set": {
                f"temp.{key}": value
            }
        }
    )

    if result.matched_count == 0:
        raise RuntimeError(
            "使用者目前沒有進行中的流程。"
        )


def get_temp(user_id, key, default=None):
    state = get_state(user_id)

    if not state:
        return default

    return state.get(
        "temp",
        {}
    ).get(
        key,
        default
    )


def clear(user_id):
    _collection().delete_one(
        {"user_id": user_id}
    )