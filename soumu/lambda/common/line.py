"""LINE Messaging API 适配器（零依赖：仅标准库 + config）。

- verify_signature: X-Line-Signature = base64(HMAC-SHA256(channel_secret, raw_body))
- parse_events: 解析 webhook 事件 → 统一内部消息结构
- reply / push: 回复 / 主动推送文本
- download_content: 下载用户发来的文件/图片二进制（content API）

内部用户标识统一加前缀 line:，与研修助手版保持一致。
"""
import base64
import hashlib
import hmac
import json
import urllib.error
import urllib.request

from . import config

USER_PREFIX = "line:"

_API = "https://api.line.me/v2/bot/message"
_DATA_API = "https://api-data.line.me/v2/bot/message"


def verify_signature(body_bytes, signature):
    if not signature:
        return False
    mac = hmac.new(config.line_secret().encode("utf-8"), body_bytes, hashlib.sha256)
    expected = base64.b64encode(mac.digest()).decode("utf-8")
    return hmac.compare_digest(expected, signature)


def parse_events(body_text):
    """webhook body(JSON) → [ {fromUser, msgType, content, messageId, fileName,
    event, eventKey, replyToken} ]"""
    out = []
    try:
        data = json.loads(body_text or "{}")
    except Exception:
        return out
    for ev in data.get("events", []):
        src = ev.get("source", {})
        uid = src.get("userId")
        from_user = (USER_PREFIX + uid) if uid else None
        etype = ev.get("type")
        reply_token = ev.get("replyToken")
        if etype == "message":
            msg = ev.get("message", {})
            mtype = msg.get("type")
            out.append({
                "fromUser": from_user,
                "msgType": mtype,                    # text / file / image ...
                "content": msg.get("text", ""),
                "messageId": msg.get("id"),
                "fileName": msg.get("fileName"),     # file 类型才有
                "event": None,
                "eventKey": None,
                "replyToken": reply_token,
            })
        elif etype == "follow":
            out.append({
                "fromUser": from_user, "msgType": "event", "content": "",
                "messageId": None, "fileName": None,
                "event": "subscribe", "eventKey": None, "replyToken": reply_token,
            })
        elif etype == "unfollow":
            out.append({
                "fromUser": from_user, "msgType": "event", "content": "",
                "messageId": None, "fileName": None,
                "event": "unsubscribe", "eventKey": None, "replyToken": reply_token,
            })
        elif etype == "postback":                    # 日時ピッカー等のボタン応答
            pb = ev.get("postback", {}) or {}
            out.append({
                "fromUser": from_user, "msgType": "postback", "content": "",
                "messageId": None, "fileName": None,
                "data": pb.get("data", ""), "params": pb.get("params") or {},
                "event": None, "eventKey": None, "replyToken": reply_token,
            })
    return out


def _post(url, payload, token=None):
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", "Bearer " + (token or config.line_token()))
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            resp.read()
        return {"errcode": 0}
    except urllib.error.HTTPError as e:
        msg = e.read().decode("utf-8", "ignore")
        print("LINE API %s %s -> %s %s" % (url.rsplit("/", 1)[-1], "", e.code, msg))
        return {"errcode": e.code, "errmsg": msg}
    except Exception as e:  # noqa: BLE001
        print("LINE API error:", repr(e))
        return {"errcode": -1, "errmsg": str(e)}


def reply(reply_token, text):
    return _post(_API + "/reply", {
        "replyToken": reply_token,
        "messages": [{"type": "text", "text": text}],
    })


def reply_messages(reply_token, messages):
    """回复任意消息对象列表（text / template 等）。"""
    return _post(_API + "/reply", {
        "replyToken": reply_token,
        "messages": messages,
    })


def buttons_message(alt_text, text, actions):
    """构造 buttons 模板消息：长 URL 藏在按钮后面，用户只看到按钮。
    actions: [{label, uri}, ...] 最多 4 个；label≤20 字，uri≤1000 字。"""
    return {
        "type": "template",
        "altText": alt_text,
        "template": {
            "type": "buttons",
            "text": text[:160],
            "actions": [
                {"type": "uri", "label": a["label"][:20], "uri": a["uri"]}
                for a in actions[:4]
            ],
        },
    }


def datetime_picker_message(alt_text, text, label, data, initial=None, min_dt=None, max_dt=None):
    """日時ピッカー1ボタンの buttons 模板（催促予約用）。
    initial/min/max は 'YYYY-MM-DDThh:mm' 形式。選択結果は postback(params.datetime) で届く。"""
    action = {"type": "datetimepicker", "label": label[:20], "data": data, "mode": "datetime"}
    if initial:
        action["initial"] = initial
    if min_dt:
        action["min"] = min_dt
    if max_dt:
        action["max"] = max_dt
    return {
        "type": "template",
        "altText": alt_text,
        "template": {"type": "buttons", "text": text[:160], "actions": [action]},
    }


def push(user_id, text, token=None):
    """user_id 可带或不带 line: 前缀。
    token 指定时用该 token 推送（催促を社員channelのbotから送る等）。未指定は自channel。"""
    if user_id.startswith(USER_PREFIX):
        user_id = user_id[len(USER_PREFIX):]
    return _post(_API + "/push", {
        "to": user_id,
        "messages": [{"type": "text", "text": text}],
    }, token=token)


def carousel_message(alt_text, columns):
    """carousel 模板：每列 title/text + 按钮（各列按钮数须一致，这里统一 1 个）。
    columns: [{title, text, actions:[{label, uri}]}]，最多 10 列。"""
    cols = []
    for c in columns[:10]:
        cols.append({
            "title": (c.get("title", "") or "")[:40],
            "text": ((c.get("text", " ") or " ")[:60]) or " ",
            "actions": [
                {"type": "uri", "label": a["label"][:20], "uri": a["uri"]}
                for a in c["actions"][:3]
            ],
        })
    return {
        "type": "template",
        "altText": alt_text,
        "template": {"type": "carousel", "columns": cols},
    }


def download_content(message_id):
    """下载用户发来的文件/图片二进制。失败返回 None。"""
    url = "%s/%s/content" % (_DATA_API, message_id)
    req = urllib.request.Request(url, method="GET")
    req.add_header("Authorization", "Bearer " + config.line_token())
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.read()
    except Exception:  # noqa: BLE001
        return None
