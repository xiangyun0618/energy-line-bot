from line_utils import reply_text
from web_db import web_db


def show_web_tasks(event):
    tasks = list(
        web_db.tasks.find({"status": {"$ne": "done"}})
        .sort("created_at", -1)
        .limit(5)
    )

    if not tasks:
        reply_text(event.reply_token, "目前網站沒有未完成任務。")
        return

    status_map = {
        "pending": "待處理",
        "doing": "執行中",
        "done": "已完成",
        "abnormal": "異常"
    }

    lines = ["📋 網站目前未完成任務："]

    for i, t in enumerate(tasks, start=1):
        title = t.get("title", "未命名任務")
        factory = t.get("factory", "未指定廠區")
        assignee = t.get("assignee", "未指派")
        due_date = t.get("due_date", "未設定日期")
        status = status_map.get(t.get("status", ""), t.get("status", "未知"))

        lines.append(
            f"\n{i}. {title}\n"
            f"廠區：{factory}\n"
            f"負責人：{assignee}\n"
            f"日期：{due_date}\n"
            f"狀態：{status}"
        )

    reply_text(event.reply_token, "\n".join(lines))