from linebot.models import TextSendMessage


line_bot_api = None


def init_line_api(api):
    global line_bot_api
    line_bot_api = api


def reply_text(reply_token, text):
    line_bot_api.reply_message(reply_token, TextSendMessage(text=text))


def push_text(user_id, text):
    line_bot_api.push_message(user_id, TextSendMessage(text=text))