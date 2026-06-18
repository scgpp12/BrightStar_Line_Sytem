"""LINE Webhook 入口：验签 → 解析事件 → 复用 webhook._route → reply 回复。

后端业务（注册/课程/报名/登录码/RAG 等）与企业微信完全共用；
本文件只做 LINE 平台适配（签名校验、事件归一、回复）。

LINE 后台「Verify」按钮会发一条 events 为空的 POST；验签通过即回 200。
"""
import base64
import logging

from common import business, line
from common.auth import is_teacher
from handlers import webhook

log = logging.getLogger()
log.setLevel(logging.INFO)

# 研修 LINE channel ＝「講師」専用。一般受講者（学員）の操作は
# 「BrightStar 社員アシスタント」へ誘導する（責務分離・低結合のため）。
# ※ 微信(kf/企業微信)側は従来どおり受講者・講師を兼ねる（本ゲートは LINE のみ）。
TEACHER_AUTH_PREFIXES = ("老师认证", "教師認証", "老師認証", "教师认证")

SHAIN_REDIRECT = (
    "🔔 こちらは「講師」専用アカウントです。\n"
    "受講・受付・お申込みは\n"
    "👉「BrightStar 社員アシスタント」をご利用ください。\n"
    "────────\n"
    "此账号为「讲师」专用。\n"
    "课程报名/咨询等请使用「BrightStar 社員アシスタント」。"
)


def _gate_ok(ev) -> bool:
    """講師、または講師認証コマンドのみ通す。それ以外は社員アシスタントへ。"""
    raw = ev.get("fromUser")
    if not raw:
        return False
    openid = business.resolve_openid(raw)
    if is_teacher(openid):
        return True
    if ev.get("msgType") == "text":
        t = (ev.get("content") or "").strip()
        if any(t.startswith(p) for p in TEACHER_AUTH_PREFIXES):
            return True   # 講師自己認証は通す（学員→講師に昇格できる）
    return False


def _raw_body(event) -> bytes:
    """取原始 body 字节（签名按字节算，不能先 decode 再处理）。"""
    raw = event.get("body") or ""
    if event.get("isBase64Encoded"):
        return base64.b64decode(raw)
    return raw.encode("utf-8")


def handler(event, context):
    headers = {k.lower(): v for k, v in (event.get("headers") or {}).items()}
    signature = headers.get("x-line-signature", "")
    body_bytes = _raw_body(event)

    if not line.verify_signature(body_bytes, signature):
        return {"statusCode": 403, "body": "invalid signature"}

    for ev in line.parse_events(body_bytes.decode("utf-8")):
        try:
            if _gate_ok(ev):
                reply_text = webhook._route(ev)      # 講師：共用路由
            else:
                reply_text = SHAIN_REDIRECT          # 学員：社員アシスタントへ誘導
            line.reply(ev.get("replyToken", ""), reply_text)
        except Exception:  # noqa: BLE001
            log.exception("line route error")
    # LINE 要求尽快回 200（含 Verify 的空事件）
    return {"statusCode": 200, "body": ""}
