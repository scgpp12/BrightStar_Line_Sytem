"""LINE Messaging API 接入：签名校验、事件解析、回复/推送。

与微信客服(kf)同为「平台适配层」：把 LINE 的 webhook 事件归一成内部 msg dict，
交给共用的 webhook._route 处理。回复用 reply token（免费、不计配额），
主动提醒用 push（见 messaging.py）。

零外部依赖：仅标准库（hmac/hashlib/base64/json/urllib）。
LINE 无需可信IP/中转、无需 AES——Lambda 直连 api.line.me；入站只验 HMAC-SHA256 签名。

身份：LINE userId 加前缀 `line:` 作为内部 openid，与企业微信用户天然隔离，
      并让发送层据前缀选择渠道（见 messaging.py）。
"""
import base64
import hashlib
import hmac
import json
import logging
import urllib.request

from . import config

log = logging.getLogger()

USER_PREFIX = "line:"  # 内部 openid 前缀（区分平台）
_REPLY_URL = "https://api.line.me/v2/bot/message/reply"
_PUSH_URL = "https://api.line.me/v2/bot/message/push"


def verify_signature(body_bytes: bytes, signature: str) -> bool:
    """校验 X-Line-Signature = base64(HMAC-SHA256(channel_secret, 原始body字节))。"""
    secret = config.line_secret()
    if not secret:
        return False
    mac = hmac.new(secret.encode("utf-8"), body_bytes, hashlib.sha256).digest()
    expected = base64.b64encode(mac).decode("utf-8")
    return hmac.compare_digest(expected, signature or "")


def parse_events(body_text: str) -> list:
    """解析 LINE webhook body → 归一化事件列表（键与 webhook._route 的 msg 一致）。

    每个元素：{fromUser, msgType, content, event, eventKey, replyToken}
      - 文本消息    → msgType=text, content=文本
      - follow(加好友) → msgType=event, event=subscribe（触发注册）
    其它消息类型 msgType 原样（上层按「非文本」回兜底菜单）。
    """
    out = []
    try:
        data = json.loads(body_text or "{}")
    except Exception:  # noqa: BLE001
        return out
    for ev in data.get("events", []):
        uid = (ev.get("source", {}) or {}).get("userId", "")
        if not uid:
            continue
        base = {"fromUser": USER_PREFIX + uid, "content": "",
                "event": "", "eventKey": "", "replyToken": ev.get("replyToken", "")}
        etype = ev.get("type", "")
        if etype == "message":
            m = ev.get("message", {}) or {}
            if m.get("type") == "text":
                out.append({**base, "msgType": "text",
                            "content": (m.get("text") or "").strip()})
            else:
                out.append({**base, "msgType": m.get("type", "other")})
        elif etype == "follow":
            out.append({**base, "msgType": "event", "event": "subscribe"})
        # 其它事件(unfollow/postback/join...) 暂忽略
    return out


def _post(url: str, payload: dict) -> dict:
    token = config.line_token()
    if not token:
        return {"errcode": -1, "errmsg": "no_line_token"}
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url, data=data,
        headers={"Content-Type": "application/json",
                 "Authorization": "Bearer " + token},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as r:  # noqa: S310
            r.read()
            return {"errcode": 0}
    except Exception as e:  # noqa: BLE001
        log.error("line POST %s error: %s", url, e)
        return {"errcode": -1, "errmsg": str(e)}


def reply(reply_token: str, text: str) -> dict:
    """用 reply token 回复（免费、不计 push 配额；token 一次性、约 30s 内有效）。"""
    if not reply_token:
        return {"errcode": -1, "errmsg": "no_reply_token"}
    return _post(_REPLY_URL, {"replyToken": reply_token,
                              "messages": [{"type": "text", "text": text[:5000]}]})


def reply_messages(reply_token: str, messages: list) -> dict:
    """任意の message オブジェクト列で返信（Quick Reply 等）。"""
    if not reply_token:
        return {"errcode": -1, "errmsg": "no_reply_token"}
    return _post(_REPLY_URL, {"replyToken": reply_token, "messages": messages})


def push(user_id: str, text: str) -> dict:
    """主动推送（开课提醒用；计入 push 配额）。user_id 为不带前缀的 LINE userId。"""
    return _post(_PUSH_URL, {"to": user_id,
                             "messages": [{"type": "text", "text": text[:5000]}]})
