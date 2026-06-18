"""BrightStar 社員アシスタント —— 一般従業員むけ統合フロント（LINE）。

研修(受講・申込) と 人事(勤怠・通勤費の提出) の「社員側」機能を 1 つの channel に集約。
两套后端を vendoring（kenshu.* / jinji.*）し、ユーザーごとの mode（研修/人事）で振り分ける。

- I/O 層は jinji.common.line に一本化（署名検証・返信・添付DL すべて社員 channel の SSM 凭证）。
- 研修側：kenshu.handlers.webhook._route(msg) はテキストを返す → jline.reply で返信。
- 人事側：jinji.handlers.line_webhook._route(ev, base) は内部で返信（登録・提出・テンプレ・履歴・/dl）。
- mode は DynamoDB(SHAIN_SESSION_TABLE) に保存。未選択時はチューザーを表示。
"""
import base64
import os

import boto3

from jinji.common import config as jconfig
from jinji.common import line as jline
from jinji.handlers import line_webhook as jinji_web
from kenshu.handlers import webhook as kenshu_web

KENSHU, JINJI = "kenshu", "jinji"

# モード切替キーワード
KENSHU_WORDS = {"研修", "研修メニュー", "研修モード", "研修アシスタント", "けんしゅう", "📚研修", "けんしゅうモード"}
JINJI_WORDS = {"人事", "人事メニュー", "人事モード", "勤怠", "通勤費", "通勤费", "経費", "提出", "🗂️人事"}

CHOOSER = (
    "ようこそ！ご利用の機能を選んでください👇\n"
    "・「研修」… 研修の受講・お申込み\n"
    "・「人事」… 勤怠・通勤費の提出 / テンプレ / 履歴\n"
    "────────\n"
    "请选择功能：\n"
    "・发「研修」… 研修报名/咨询\n"
    "・发「人事」… 考勤·通勤费提交/模板/履历"
)

_ddb = None
SESSION_TABLE = os.environ.get("SHAIN_SESSION_TABLE", "")


# ---------------- mode 永続化 ----------------
def _table():
    global _ddb
    if _ddb is None:
        _ddb = boto3.resource("dynamodb", region_name=jconfig.REGION)
    return _ddb.Table(SESSION_TABLE)


def _get_mode(uid):
    try:
        item = _table().get_item(Key={"userId": uid}).get("Item")
        return item.get("mode") if item else None
    except Exception as e:  # noqa: BLE001
        print("get_mode error:", repr(e))
        return None


def _set_mode(uid, mode):
    try:
        _table().put_item(Item={"userId": uid, "mode": mode})
    except Exception as e:  # noqa: BLE001
        print("set_mode error:", repr(e))


# ---------------- HTTP 補助 ----------------
def _method(event):
    return ((event.get("requestContext", {}) or {}).get("http", {}) or {}).get("method", "")


def _raw_body(event):
    b = event.get("body") or ""
    return base64.b64decode(b) if event.get("isBase64Encoded") else b.encode("utf-8")


def _header(event, name):
    for k, v in (event.get("headers") or {}).items():
        if k.lower() == name.lower():
            return v
    return None


def _base_url(event):
    host = _header(event, "host") or \
        (event.get("requestContext", {}) or {}).get("domainName", "")
    return "https://" + host


# ---------------- エントリ ----------------
def handler(event, context):
    # GET /dl?... → 人事の添付/テンプレDL（社員 channel の Function URL ホストで 302）
    if _method(event) == "GET":
        return jinji_web._handle_download(event)

    body = _raw_body(event)
    sig = _header(event, "x-line-signature")
    if not jline.verify_signature(body, sig):
        return {"statusCode": 403, "body": "bad signature"}

    base = _base_url(event)
    for ev in jline.parse_events(body.decode("utf-8")):
        try:
            _dispatch(ev, base)
        except Exception as e:  # noqa: BLE001
            print("shain route error:", repr(e))
    return {"statusCode": 200, "body": "OK"}


# ---------------- 振り分け ----------------
def _kenshu_msg(ev, *, as_event=False):
    """jinji 解析イベント → kenshu webhook._route が期待する msg 形へ。"""
    if as_event:
        return {"fromUser": ev.get("fromUser"), "msgType": "event",
                "content": "", "event": "subscribe", "eventKey": ""}
    return {"fromUser": ev.get("fromUser"), "msgType": ev.get("msgType"),
            "content": ev.get("content") or "", "event": ev.get("event") or "",
            "eventKey": ev.get("eventKey") or ""}


def _dispatch(ev, base):
    uid = ev.get("fromUser")
    if not uid:
        return
    rt = ev.get("replyToken")
    mtype = ev.get("msgType")
    text = (ev.get("content") or "").strip()

    # フォロー時：チューザー
    if mtype == "event" and ev.get("event") == "subscribe":
        jline.reply(rt, CHOOSER)
        return

    # 明示モード切替（→ そのドメインの入口を表示）
    if mtype == "text" and text in KENSHU_WORDS:
        _set_mode(uid, KENSHU)
        jline.reply(rt, kenshu_web._route(_kenshu_msg(ev, as_event=True)))
        return
    if mtype == "text" and text in JINJI_WORDS:
        _set_mode(uid, JINJI)
        _jinji_entry(ev, base)
        return

    # ファイル/画像 → 人事(提出)に固定
    if mtype in ("file", "image"):
        _set_mode(uid, JINJI)
        jinji_web._route(ev, base)
        return

    # 既存モードに従って振り分け
    mode = _get_mode(uid)
    if mode == KENSHU:
        jline.reply(rt, kenshu_web._route(_kenshu_msg(ev)))
        return
    if mode == JINJI:
        jinji_web._route(ev, base)
        return

    # 未選択：チューザー
    jline.reply(rt, CHOOSER)


def _jinji_entry(ev, base):
    """人事モードに入った直後：登録案内 or メニューを出す（subscribe 相当）。"""
    fake = dict(ev)
    fake["msgType"] = "event"
    fake["event"] = "subscribe"
    jinji_web._route(fake, base)
