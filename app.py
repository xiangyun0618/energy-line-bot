from __future__ import unicode_literals
from flask import Flask, request, abort
from linebot import WebhookHandler, LineBotApi
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage, FollowEvent
import schedule
import threading
import time
from datetime import date
from pymongo import MongoClient
from line_utils import init_line_api, reply_text, push_text
from web_tasks import show_web_tasks
from dotenv import load_dotenv
import os


load_dotenv()
from db_manager import DBManager
import conversation as cs
from defaults import DEFAULT_FACTORIES, DEFAULT_ROLES
# ----------------- 單一帳號測試模式 -----------------
SINGLE_ACCOUNT_TEST_MODE = True

TASK_TYPES = [
    "例行巡檢",
    "故障檢查",
    "維修"
]

# Line bot鑰匙
CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET")

# -----------------------健康檢查-----------------------------

app = Flask(__name__)
handler = WebhookHandler(CHANNEL_SECRET)
line_bot_api = LineBotApi(CHANNEL_ACCESS_TOKEN)

@app.route("/", methods=["GET"])
def home():
    return "Energy Line Bot is running!", 200

@app.route("/healthz", methods=["GET"])
def healthz():
    return {"status": "ok"}, 200

init_line_api(line_bot_api)

# 資料庫
db = DBManager()
db.seed_factories(DEFAULT_FACTORIES)
# 讓 conversation.py 使用同一個 MongoDB
cs.init_state_store(db)
MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017/energy_monitor")
mongo_client = MongoClient(MONGO_URI)
web_db = mongo_client["energy_monitor"]

try:
    mongo_client.admin.command("ping")
    print("✅ MongoDB Atlas 連線成功")
except Exception as e:
    print("❌ MongoDB 連線失敗：", e)

# ----------------- Webhook --------------------
@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers['X-Line-Signature']
    body = request.get_data(as_text=True)

    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)

    return 'OK'


# ----------------- Follow Event --------------------
@handler.add(FollowEvent)
def handle_follow(event):
    user_id = event.source.user_id
    push_text(
        user_id,
        "哈囉！我是儲能巡檢助手。\n輸入「註冊」即可開始註冊。"
    )


# ----------------- 訊息事件 --------------------
@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    user_id = event.source.user_id
    msg = event.message.text.strip()

    st = cs.get_state(user_id)

    print(f"[DEBUG] 收到訊息: {msg}")
    print(f"[DEBUG] registration state: {st}")

    if st:
        # 所有流程都支援取消
        if msg == "取消":
            cs.clear(user_id)

            reply_text(
                event.reply_token,
                "已取消目前操作。"
            )
            return

        flow = st.get("flow")

        if flow == "registration":
            handle_registration(
                event,
                st
            )
            return

        if flow == "create_task":
            handle_create_task(
                event,
                st
            )
            return

        # 不認識的 state，避免卡死
        cs.clear(user_id)

        reply_text(
        event.reply_token,
        "流程狀態異常，已自動重設，請重新操作。"
        )
        return
    
    # 只有管理員可以維護廠區與設備
    user = db.get_user(user_id)

    # 新增廠區：格式「新增廠區 北區二廠」
    if msg.startswith("新增廠區"):
        if not user or user.get("role") != "管理員":
            reply_text(event.reply_token, "只有管理員可以新增廠區。")
            return

        name = msg.replace("新增廠區", "", 1).strip()
        if not name:
            reply_text(event.reply_token, "請在『新增廠區』後面加上名稱，例如：新增廠區 北區二廠")
            return

        ok = db.add_factory(name)
        if ok:
            reply_text(event.reply_token, f"已新增廠區：{name}")
        else:
            reply_text(event.reply_token, f"新增失敗，可能廠區已存在：{name}")
        return

    # 刪除廠區：格式「刪除廠區 北區二廠」
    if msg.startswith("刪除廠區"):
        if not user or user.get("role") != "管理員":
            reply_text(event.reply_token, "只有管理員可以刪除廠區。")
            return

        name = msg.replace("刪除廠區", "", 1).strip()
        if not name:
            reply_text(event.reply_token, "請在『刪除廠區』後面加上名稱，例如：刪除廠區 北區二廠")
            return

        ok = db.delete_factory(name)
        if ok:
            reply_text(event.reply_token, f"已刪除廠區：{name}")
        else:
            reply_text(event.reply_token, f"刪除失敗，找不到廠區：{name}")
        return
    
    # 新增設備：格式「新增設備 廠區名 設備名稱」
    # 範例：新增設備 北區廠 PCS-01
    if msg.startswith("新增設備"):
        if not user or user.get("role") != "管理員":
            reply_text(event.reply_token, "只有管理員可以新增設備。")
            return

        parts = msg.split()
        if len(parts) < 3:
            reply_text(event.reply_token, "格式錯誤，請用：新增設備 廠區名 設備名稱\n例如：新增設備 北區廠 PCS-01")
            return

        factory = parts[1]
        eq_name = " ".join(parts[2:])

        eq = db.add_equipment(factory, eq_name)
        if eq:
            reply_text(event.reply_token, f"已新增設備：{factory} / {eq_name}（ID: {eq['id']}）")
        else:
            reply_text(event.reply_token, "新增設備失敗，請確認輸入。")
        return

    # 刪除設備：格式「刪除設備 ID」
    # 範例：刪除設備 3
    if msg.startswith("刪除設備"):
        if not user or user.get("role") != "管理員":
            reply_text(event.reply_token, "只有管理員可以刪除設備。")
            return

        parts = msg.split()
        if len(parts) != 2 or not parts[1].isdigit():
            reply_text(event.reply_token, "格式錯誤，請用：刪除設備 設備ID\n例如：刪除設備 3")
            return

        eq_id = int(parts[1])
        ok = db.delete_equipment(eq_id)
        if ok:
            reply_text(event.reply_token, f"已刪除設備（ID: {eq_id}）。")
        else:
            reply_text(event.reply_token, f"刪除失敗，找不到設備 ID: {eq_id}")
        return
    
# ----------------- 互動式建立任務 --------------------
    if msg == "建立任務":

        # 只有管理員可以建立任務
        if not user or user.get("role") != "管理員":
            reply_text(
                event.reply_token,
                "只有管理員可以建立任務。"
            )
            return

        # 從 MongoDB 取得目前所有廠區
        factories = db.get_factories()

        if not factories:
            reply_text(
                event.reply_token,
                "目前沒有可用廠區。"
            )
            return

        # 啟動「建立任務」互動流程
        cs.start_create_task(user_id)

        # 暫存這次可以選擇的廠區
        cs.set_temp(
            user_id,
            "factory_options",
            factories
        )

        # 顯示第一步
        reply_text(
            event.reply_token,
            "【建立任務 1/5】\n"
            "請選擇廠區：\n"
            +"\n".join(
                f"{i + 1}. {factory}"
                for i, factory in enumerate(factories)
            )
            + "\n\n輸入「取消」可中止。"
        )
        return

    # ---- 指令 ----
    if msg == "註冊":
        cs.start_registration(user_id)
        reply_text(event.reply_token, "開始註冊流程。\n請輸入你的姓名：")
        return

    if msg == "我的任務":
        show_today_tasks(event, user_id)
        return

    if msg == "未完成任務":
        if not user or user.get("role") != "管理員":
            reply_text(
                event.reply_token,
                "只有管理員可以查詢未完成任務。"
            )
            return
        show_pending_tasks(event)
        return

    if msg == "全部任務":
        if not user or user.get("role") != "管理員":
            reply_text(
                event.reply_token,
                "只有管理員可以查詢全部任務。"
            )
            return
        show_all_tasks(event)
        return

    if msg.startswith("完成"):
        print("[COMMAND] entered complete-task branch")
        parts = msg.split()

        if len(parts) != 2 or not parts[1].isdigit():
            reply_text(
            event.reply_token,
            "格式錯誤。\n"
            "請輸入：完成 任務ID\n"
            "例如：完成 1"
        )
            return

        task_id = int(parts[1])
        result = db.complete_task_for_user(task_id, user_id)

        print(f"[COMMAND] complete task_id={task_id}, result={result}")

        if result == "success":
            reply_text(event.reply_token, f"✅ 任務 {task_id} 已完成。")
        elif result == "already_done":
            reply_text(event.reply_token, f"任務 {task_id} 已經是完成狀態。")
        elif result == "forbidden":
            reply_text(
            event.reply_token,
            "你無法完成這筆任務，因為它不是指派給你的。"
            )
        else:
            reply_text(event.reply_token, f"找不到任務 ID：{task_id}")

        return

    if msg == "網站任務":
        show_web_tasks(event)
        return
    reply_text(
        event.reply_token,
        "我不懂你說什麼。\n"
        "可使用：\n"
        "註冊\n"
        "建立任務\n"
        "我的任務\n"
        "未完成任務\n"
        "全部任務\n"
        "完成 任務ID\n"
        "網站任務"
    )

# ----------------- 建立任務流程 --------------------
def handle_create_task(event, state):
    user_id = event.source.user_id
    reply_token = event.reply_token

    step = state["step"]
    msg = event.message.text.strip()


    # ==========================================
    # STEP 1：選廠區
    # ==========================================
    if step == 1:
        factories = cs.get_temp(
            user_id,
            "factory_options",
            []
        )

        if not msg.isdigit():
            reply_text(
                reply_token,
                "請輸入廠區前面的數字。"
            )
            return

        index = int(msg) - 1

        if not 0 <= index < len(factories):
            reply_text(
                reply_token,
                "廠區編號不存在。"
            )
            return

        factory = factories[index]

        cs.set_temp(
            user_id,
            "factory",
            factory
        )

        # 查這個廠區的設備
        equipments = db.list_equipments(factory)

        if not equipments:
            cs.clear(user_id)

            reply_text(
                reply_token,
                f"「{factory}」目前沒有設備，"
                "請先新增設備後再建立任務。"
            )
            return

        cs.set_temp(
            user_id,
            "equipment_options",
            equipments
        )

        cs.advance(user_id)

        reply_text(
            reply_token,
            "【建立任務 2/5】\n"
            f"廠區：{factory}\n"
            "請選擇設備：\n"
            + "\n".join(
                f"{i + 1}. {eq['name']}"
                for i, eq in enumerate(equipments)
            )
        )
        return


    # ==========================================
    # STEP 2：選設備
    # ==========================================
    if step == 2:
        equipments = cs.get_temp(
            user_id,
            "equipment_options",
            []
        )

        if not msg.isdigit():
            reply_text(
                reply_token,
                "請輸入設備前面的數字。"
            )
            return

        index = int(msg) - 1

        if not 0 <= index < len(equipments):
            reply_text(
                reply_token,
                "設備編號不存在。"
            )
            return

        equipment = equipments[index]

        cs.set_temp(
            user_id,
            "machine",
            equipment["name"]
        )

        cs.advance(user_id)

        reply_text(
            reply_token,
            "【建立任務 3/5】\n"
            "請選擇任務類型：\n"
            + "\n".join(
                f"{i + 1}. {task_type}"
                for i, task_type in enumerate(TASK_TYPES)
            )
        )
        return


    # ==========================================
    # STEP 3：選任務類型
    # ==========================================
    if step == 3:

        if not msg.isdigit():
            reply_text(
                reply_token,
                "請輸入任務類型前面的數字。"
            )
            return

        index = int(msg) - 1

        if not 0 <= index < len(TASK_TYPES):
            reply_text(
                reply_token,
                "任務類型編號不存在。"
            )
            return

        task_type = TASK_TYPES[index]

        cs.set_temp(
            user_id,
            "task_type",
            task_type
        )

        factory = cs.get_temp(
            user_id,
            "factory"
        )

        # 找負責此廠區的維修員
        technicians = []

        for technician in db.get_all_users():

            if technician.get("role") != "維修員":
                continue

            factory_priority = technician.get(
                "factory_priority",
                {}
            )

            if factory not in factory_priority:
                continue

            technicians.append({
                "user_id": technician["user_id"],
                "name": technician.get(
                    "name",
                    "未命名維修員"
                ),
                "priority": factory_priority[factory]
            })

        # 第一優先排前面
        technicians.sort(
            key=lambda x: x["priority"]
        )

        if not technicians:
            cs.clear(user_id)

            reply_text(
                reply_token,
                f"目前沒有負責「{factory}」的維修員。\n"
                "請先完成維修員註冊。"
            )
            return

        cs.set_temp(
            user_id,
            "technician_options",
            technicians
        )

        cs.advance(user_id)

        reply_text(
            reply_token,
            "【建立任務 4/5】\n"
            "請選擇維修員：\n"
            + "\n".join(
                f"{i + 1}. {tech['name']} "
                f"（第 {tech['priority']} 優先）"
                for i, tech in enumerate(technicians)
            )
        )
        return


    # ==========================================
    # STEP 4：選維修員
    # ==========================================
    if step == 4:
        technicians = cs.get_temp(
            user_id,
            "technician_options",
            []
        )

        if not msg.isdigit():
            reply_text(
                reply_token,
                "請輸入維修員前面的數字。"
            )
            return

        index = int(msg) - 1

        if not 0 <= index < len(technicians):
            reply_text(
                reply_token,
                "維修員編號不存在。"
            )
            return

        technician = technicians[index]

        cs.set_temp(
            user_id,
            "assigned_user_id",
            technician["user_id"]
        )

        cs.set_temp(
            user_id,
            "assigned_user_name",
            technician["name"]
        )

        cs.advance(user_id)

        factory = cs.get_temp(user_id, "factory")
        machine = cs.get_temp(user_id, "machine")
        task_type = cs.get_temp(user_id, "task_type")

        reply_text(
            reply_token,
            "【建立任務 5/5】\n"
            "請確認任務內容：\n\n"
            f"廠區：{factory}\n"
            f"設備：{machine}\n"
            f"類型：{task_type}\n"
            f"維修員：{technician['name']}\n\n"
            "輸入「確認」建立任務\n"
            "輸入「取消」中止"
        )
        return


    # ==========================================
    # STEP 5：確認建立
    # ==========================================
    if step == 5:

        if msg != "確認":
            reply_text(
                reply_token,
                "請輸入「確認」建立任務，"
                "或輸入「取消」。"
            )
            return

        factory = cs.get_temp(user_id, "factory")
        machine = cs.get_temp(user_id, "machine")
        task_type = cs.get_temp(user_id, "task_type")

        assigned_user_id = cs.get_temp(
            user_id,
            "assigned_user_id"
        )

        assigned_user_name = cs.get_temp(
            user_id,
            "assigned_user_name"
        )

        # ------------------------------------------
        # 單帳號碩論測試模式
        # ------------------------------------------
        # 如果選到 TEST_ 開頭的虛擬維修員，
        # 實際把任務指派給目前這個真實 LINE 帳號。
        effective_assigned_user_id = assigned_user_id

        is_test_technician = (
            isinstance(assigned_user_id, str)
            and assigned_user_id.startswith("TEST_")
        )

        if SINGLE_ACCOUNT_TEST_MODE and is_test_technician:
            effective_assigned_user_id = user_id

            print(
                "[TEST MODE] 虛擬維修員",
                assigned_user_id,
                "→ 實際由目前 LINE 帳號模擬",
                user_id
            )

        # 真正建立任務
        task = db.create_task(
            factory=factory,
            machine=machine,
            assigned_user_id=effective_assigned_user_id,
            task_type=task_type
        )

        # 流程完成，刪除 conversation state
        cs.clear(user_id)

        # 通知維修員
        push_success = True

        try:
            push_text(
                effective_assigned_user_id,
                "收到新的巡檢任務\n"
                f"任務 ID：{task['id']}\n"
                f"廠區：{factory}\n"
                f"設備：{machine}\n"
                f"類型：{task_type}\n\n"
                f"完成後請輸入：完成 {task['id']}"
            )

        except Exception as e:
            push_success = False
            print(
                "[TASK PUSH ERROR]",
                e
            )

        if SINGLE_ACCOUNT_TEST_MODE and is_test_technician:
            notify_text = (
                "單帳號測試模式："
                "目前帳號同時模擬維修員，已收到任務通知"
            )
        else:
            notify_text = (
            "已通知維修員"
            if push_success
            else "任務已建立，但推播失敗"
        )

        reply_text(
            reply_token,
            "任務建立成功\n"
            f"任務 ID：{task['id']}\n"
            f"廠區：{factory}\n"
            f"設備：{machine}\n"
            f"類型：{task_type}\n"
            f"維修員：{assigned_user_name}\n"
            f"{notify_text}"
        )
        return

# ----------------- 註冊流程 --------------------
def handle_registration(event, state):
    user_id = event.source.user_id
    reply_token = event.reply_token
    step = state["step"]
    msg = event.message.text.strip()

    # STEP 1：輸入姓名
    if step == 1:
        cs.set_temp(user_id, "name", msg)
        cs.advance(user_id)

        reply_text(
            reply_token,
            "請輸入你的角色（輸入數字）：\n"
            + "\n".join(
                f"{i + 1}. {role}"
                for i, role in enumerate(DEFAULT_ROLES)
            )
        )
        return

    # STEP 2：選擇角色
    if step == 2:
        if not msg.isdigit():
            reply_text(
                reply_token,
                "輸入錯誤，請輸入角色前面的數字。"
            )
            return

        role_index = int(msg) - 1

        if not 0 <= role_index < len(DEFAULT_ROLES):
            reply_text(
                reply_token,
                "角色編號不存在，請重新輸入。"
            )
            return

        role = DEFAULT_ROLES[role_index]
        cs.set_temp(user_id, "role", role)

        # 管理員不需要設定負責廠區
        if role == "管理員":
            _finish_admin_registration(
                user_id,
                reply_token
            )
            return

        # 維修員進入主要廠區選擇
        cs.advance(user_id)

        factories = db.get_factories()

        reply_text(
            reply_token,
            "請選擇主要負責廠區（輸入數字）：\n"
            + "\n".join(
                f"{i + 1}. {factory}"
                for i, factory in enumerate(factories)
            )
        )
        return

    # STEP 3：選擇主要廠區
    if step == 3:
        factories = db.get_factories()

        if not msg.isdigit():
            reply_text(
                reply_token,
                "輸入錯誤，請輸入廠區前面的數字。"
            )
            return

        factory_index = int(msg) - 1

        if not 0 <= factory_index < len(factories):
            reply_text(
                reply_token,
                "廠區編號不存在，請重新輸入。"
            )
            return

        primary_factory = factories[factory_index]

        # 第一個選擇的廠區固定為第一優先
        cs.set_temp(
            user_id,
            "primary_factory",
            primary_factory
        )

        cs.advance(user_id)

        reply_text(
            reply_token,
            f"已將「{primary_factory}」設為第一優先廠區。\n"
            "是否還要設定第二優先廠區？\n"
            "請回覆「是」或「否」。"
        )
        return

    # STEP 4：是否設定第二優先廠區
    if step == 4:
        answer = msg.strip().lower()

        # 不設定第二優先 → 直接完成
        if answer in ["否", "沒有", "無", "n", "no"]:
            _finish_registration_without_second(
                user_id,
                reply_token
            )
            return

        # 輸入不是「是」
        if answer not in ["是", "有", "y", "yes"]:
            reply_text(
                reply_token,
                "請回覆「是」或「否」。"
            )
            return

        primary_factory = cs.get_temp(
            user_id,
            "primary_factory"
        )

        # 排除第一優先廠區
        second_options = [
            factory
            for factory in db.get_factories()
            if factory != primary_factory
        ]

        # 沒有其他廠區就直接完成
        if not second_options:
            _finish_registration_without_second(
                user_id,
                reply_token
            )
            return

        cs.set_temp(
            user_id,
            "second_options",
            second_options
        )

        # 進入 STEP 5：選第二優先廠區
        cs.advance(user_id)

        reply_text(
            reply_token,
            "請選擇第二優先廠區（輸入數字）：\n"
            + "\n".join(
                f"{i + 1}. {factory}"
                for i, factory in enumerate(second_options)
            )
        )
        return

    # STEP 5：選擇第二優先廠區
    if step == 5:
        second_options = (
            cs.get_temp(user_id, "second_options") or []
        )

        if not msg.isdigit():
            reply_text(
                reply_token,
                "輸入錯誤，請輸入廠區前面的數字。"
            )
            return

        second_index = int(msg) - 1

        if not 0 <= second_index < len(second_options):
            reply_text(
                reply_token,
                "廠區編號不存在，請重新輸入。"
            )
            return

        second_factory = second_options[second_index]

        # 第二個選擇的廠區固定為第二優先
        cs.set_temp(
            user_id,
            "second_factory",
            second_factory
        )

        # 選完就直接完成註冊
        _finish_registration_with_second(
            user_id,
            reply_token
        )
        return

# ----------------- 管理員註冊完成 --------------------
def _finish_admin_registration(user_id, reply_token):
    name = cs.get_temp(user_id, "name")

    success = db.add_user(
        user_id=user_id,
        name=name,
        factory_priority={},
        role="管理員"
    )

    if success:
        reply_text(
            reply_token,
            "註冊完成！\n"
            f"姓名：{name}\n"
            "身分：管理員"
        )
    else:
        # 使用者已存在時更新角色
        db.update_user(
            user_id,
            name=name,
            role="管理員"
        )

        reply_text(
            reply_token,
            "帳號資料已更新！\n"
            f"姓名：{name}\n"
            "身分：管理員"
        )

    cs.clear(user_id)

# ----------------- 維修員註冊完成：只有主要廠區 --------------------
def _finish_registration_without_second(user_id, reply_token):
    name = cs.get_temp(user_id, "name")
    role = cs.get_temp(user_id, "role")
    primary_factory = cs.get_temp(
        user_id,
        "primary_factory"
    )

    factory_priority = {
        primary_factory: 1
    }

    if db.get_user(user_id):
        db.update_user(
            user_id,
            name=name,
            role=role,
            factory_priority=factory_priority
        )

        message_title = "✅ 帳號資料已更新！"

    else:
        db.add_user(
            user_id=user_id,
            name=name,
            factory_priority=factory_priority,
            role=role
        )

        message_title = "✅ 註冊成功！"

    reply_text(
        reply_token,
        f"{message_title}\n"
        f"姓名：{name}\n"
        f"角色：{role}\n"
        f"第一優先廠區：{primary_factory}\n"
        "第二優先廠區：未設定"
    )

    # 結束註冊狀態
    cs.clear(user_id)

# ----------------- 維修員註冊完成：包含第二優先廠區 --------------------
def _finish_registration_with_second(user_id, reply_token):
    name = cs.get_temp(user_id, "name")
    role = cs.get_temp(user_id, "role")
    primary_factory = cs.get_temp(
        user_id,
        "primary_factory"
    )
    second_factory = cs.get_temp(
        user_id,
        "second_factory"
    )

    factory_priority = {
        primary_factory: 1,
        second_factory: 2
    }

    if db.get_user(user_id):
        db.update_user(
            user_id,
            name=name,
            role=role,
            factory_priority=factory_priority
        )

        message_title = "✅ 帳號資料已更新！"

    else:
        db.add_user(
            user_id=user_id,
            name=name,
            factory_priority=factory_priority,
            role=role
        )

        message_title = "✅ 註冊成功！"

    reply_text(
        reply_token,
        f"{message_title}\n"
        f"姓名：{name}\n"
        f"角色：{role}\n"
        f"第一優先廠區：{primary_factory}\n"
        f"第二優先廠區：{second_factory}"
    )

    # 結束註冊狀態
    cs.clear(user_id)

# ----------------- 查詢任務 --------------------
def show_today_tasks(event, user_id):
    today = date.today().isoformat()

    tasks = db.get_tasks_by_user(
        user_id,
        today
    )

    if not tasks:
        reply_text(
            event.reply_token,
            "今天沒有指派給你的任務。"
        )
        return

    # 未完成排前面
    tasks.sort(
        key=lambda t: (
            t.get("status") == "已完成",
            t.get("id", 0)
        )
    )

    lines = []

    for task in tasks:

        status_icon = (
            "已完成" if task.get("status") == "已完成"
            else "未完成"
        )

        lines.append(
            f"{status_icon} 任務 {task['id']}\n"
            f"廠區：{task['factory']}\n"
            f"設備：{task['machine']}\n"
            f"類型：{task['task_type']}\n"
            f"狀態：{task['status']}"
        )

    reply_text(
        event.reply_token,
        "\n\n".join(lines)
    )

# ----------------- 管理員：查詢未完成任務 --------------------
def show_pending_tasks(event):
    tasks = list(
        db.tasks_collection.find(
            {
                "status": {
                    "$ne": "已完成"
                }
            },
            {
                "_id": 0
            }
        ).sort("id", 1)
    )

    if not tasks:
        reply_text(
            event.reply_token,
            "目前沒有未完成任務。"
        )
        return

    lines = [
        f"未完成任務共 {len(tasks)} 筆"
    ]

    for task in tasks:
        lines.append(
            f"\n任務 {task['id']}\n"
            f"廠區：{task['factory']}\n"
            f"設備：{task['machine']}\n"
            f"類型：{task['task_type']}\n"
            f"狀態：{task['status']}\n"
            f"日期：{task['date']}"
        )

    reply_text(
        event.reply_token,
        "\n".join(lines)
    )

# ----------------- 管理員：查詢全部任務 --------------------
def show_all_tasks(event):
    tasks = list(
        db.tasks_collection.find(
            {},
            {
                "_id": 0
            }
        ).sort("id", -1)
    )

    if not tasks:
        reply_text(
            event.reply_token,
            "目前沒有任何任務紀錄。"
        )
        return

    lines = [
        f"全部任務共 {len(tasks)} 筆"
    ]

    for task in tasks:
            lines.append(
            f"\n任務 {task['id']}\n"
            f"廠區：{task['factory']}\n"
            f"設備：{task['machine']}\n"
            f"類型：{task['task_type']}\n"
            f"狀態：{task['status']}\n"
            f"日期：{task['date']}"
        )

    reply_text(
        event.reply_token,
        "\n".join(lines)
    )

# ----------------- 任務派送（依優先級） --------------------
def assign_daily_tasks():
    today = date.today().isoformat()
    factories = db.get_factories()
    users = db.get_all_users()

    for fac in factories:
        candidates = []

        # 找所有負責此廠區的維修員
        for user in users:
            role = user.get("role", "")
            fp = user.get("factory_priority", {})

            if role != "維修員":
                continue

            if fac in fp:   # 此人負責這個廠區
                candidates.append((user, fp[fac]))

        if not candidates:
            continue

        # 依照優先級排序（小 → 大）
        candidates.sort(key=lambda x: x[1])
        chosen = candidates[0][0]  # 取最優先者

        # 模擬派任
        machine = f"逆變器-{fac[-1]}01"
        task = db.create_task(
            factory=fac,
            machine=machine,
            assigned_user_id=chosen["user_id"],
            task_type="例行巡檢",
            date_str=today
        )

        # 推播任務
        push_text(
            chosen["user_id"],
            f" 今日任務\n廠區：{fac}\n機台：{machine}\n任務ID：{task['id']}\n完成後回覆：完成 {task['id']}"
        )


# ----------------- 背景排程 --------------------
def schedule_loop():
    while True:
        schedule.run_pending()
        time.sleep(1)

schedule.every().day.at("08:30").do(assign_daily_tasks)
# 若要測試立即派任：取消註解下一行
# schedule.every(1).minutes.do(assign_daily_tasks)


# ----------------- 主程式 --------------------
if __name__ == "__main__":
    print("目前廠區：", db.get_factories())
    print("目前使用者：", db.get_all_users())
    print("Render auto deploy test")

    t = threading.Thread(target=schedule_loop, daemon=True)
    t.start()

    port = int(os.environ.get("PORT", 5050))
    app.run(host="0.0.0.0", port=port, debug=False)