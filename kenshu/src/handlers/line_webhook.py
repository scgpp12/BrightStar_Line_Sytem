"""LINE Webhook 入口：验签 → 解析事件 → 日次「講師」認証 → webhook._route → reply。

研修 LINE channel ＝「講師」専用。全社花名册(roster)で **每日「部门 姓名」本人确认**し、
role=講師(teacher) のみ通す。受講者は「BrightStar 社員アシスタント」へ誘導。
※ 微信(kf/企業微信)側は従来どおり受講者・講師を兼ねる（本ゲートは LINE のみ）。
"""
import base64
import logging

from common import authlib, business, line
from handlers import webhook

log = logging.getLogger()
log.setLevel(logging.INFO)

CHANNEL = "kenshu"

AUTH_PROMPT = (
    "ご本人確認のため「所属部署 お名前」を入力してください。\n"
    "例：研修部 田中\n"
    "────────\n"
    "请输入「部门 姓名」确认本人。\n例：研修部 田中"
)
MSG_WRONG_ROLE = (
    "ご本人確認できましたが、講師ではありません。\n"
    "受講のお申込みは「BrightStar 社員アシスタント」をご利用ください。\n"
    "────────\n"
    "已确认本人，但你不是讲师。课程报名请用「BrightStar 社員アシスタント」。"
)
MSG_NOT_FOUND = ("社員名簿に該当者が見つかりません。人事にご確認ください。\n"
                 "花名册查无此人，请联系人事。")
MSG_AMBIGUOUS = ("同部署・同氏名が複数います。社員番号も付けて入力してください。\n"
                 "例：研修部 田中 E001")


def _teacher_pred(item):
    return item.get("role") == "teacher"


def _raw_body(event) -> bytes:
    raw = event.get("body") or ""
    if event.get("isBase64Encoded"):
        return base64.b64decode(raw)
    return raw.encode("utf-8")


def _is_whitelisted(openid):
    """TEACHER_OPENIDS 白名单（openid）は日次認証を免除。"""
    try:
        from common import config
        return openid in config.TEACHER_OPENIDS
    except Exception:  # noqa: BLE001
        return False


def _handle_event(ev) -> str:
    raw = ev.get("fromUser")
    if not raw:
        return ""
    openid = business.resolve_openid(raw)

    # 認証済み or 白名单 → 講師として共用ルートへ
    if _is_whitelisted(openid) or authlib.is_authed(CHANNEL, raw):
        return webhook._route(ev)

    # 未認証：テキストなら認証を試みる
    if ev.get("msgType") == "text":
        status, item = authlib.authenticate(CHANNEL, raw, ev.get("content", ""), _teacher_pred)
        if status == "ok":
            name = item.get("name", "")
            return ("✅ 認証OK：%s（%s）\n講師メニュー：建课/发布/改课/删课/学员列表/名单/分组\n"
                    "（「老师帮助」で一覧）" % (name, item.get("department", "")))
        if status == "wrong_role":
            return MSG_WRONG_ROLE
        if status == "not_found":
            return MSG_NOT_FOUND
        if status == "ambiguous":
            return MSG_AMBIGUOUS
        return AUTH_PROMPT  # need_input
    return AUTH_PROMPT      # subscribe / 非文本


def handler(event, context):
    headers = {k.lower(): v for k, v in (event.get("headers") or {}).items()}
    signature = headers.get("x-line-signature", "")
    body_bytes = _raw_body(event)

    if not line.verify_signature(body_bytes, signature):
        return {"statusCode": 403, "body": "invalid signature"}

    for ev in line.parse_events(body_bytes.decode("utf-8")):
        try:
            reply_text = _handle_event(ev)
            if reply_text:
                line.reply(ev.get("replyToken", ""), reply_text)
        except Exception:  # noqa: BLE001
            log.exception("line route error")
    return {"statusCode": 200, "body": ""}
