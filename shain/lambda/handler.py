"""BrightStar 社員アシスタント —— 一般従業員むけ統合フロント（LINE）。

研修(受講・申込) と 人事(勤怠・通勤費の提出) の「社員側」機能を 1 つの channel に集約。
两套后端を vendoring（kenshu.* / jinji.*）し、ユーザーごとの mode（研修/人事）で振り分ける。

セキュリティ／フロー：
  1) 初回のみ本人確認：「部门 姓名」(重名は社員番号追加) を全社花名册(roster)で照合 → 在籍者のみ登録。
     在籍しない場合は「人事へ連絡」。
  2) 登録後は **毎日（東京時間）最初の利用時に「研修／人事」チューザー** を表示（mode は日次リセット）。
  3) 研修モード：花名册の氏名から受講者(Students)を自動プロビジョニング（二重登録なし）→ kenshu._route。
     人事モード：jinji._route（登録・提出・テンプレ・履歴・/dl）。
I/O は jinji.common.line に一本化（社員 channel の SSM 凭证）。
"""
import base64
import os

import boto3

from jinji.common import assist
from jinji.common import authlib
from jinji.common import business as jbiz
from jinji.common import config as jconfig
from jinji.common import line as jline
from jinji.handlers import line_webhook as jinji_web
from kenshu.common import business as kbiz
from kenshu.common import db as kdb
from kenshu.common.timeutils import iso_utc
from kenshu.handlers import webhook as kenshu_web

KENSHU, JINJI = "kenshu", "jinji"

KENSHU_WORDS = {"研修", "研修メニュー", "研修モード", "研修アシスタント", "けんしゅう", "📚研修"}
JINJI_WORDS = {"人事", "人事メニュー", "人事モード", "勤怠", "通勤費", "通勤费", "経費", "交通費", "交通费", "提出", "🗂️人事"}
# 人事(提出)系ボタン：人事モードへ固定しつつ原文を jinji に委譲（メニュー表示ではなく実処理）
SHAIN_SUBMIT_WORDS = {"勤怠提出", "経費提出", "交通費提出", "作業時間記録簿提出",
                      "その他経費", "履歴", "テンプレ"}

CHOOSER = (
    "本日のご利用メニューを選んでください👇\n"
    "・「研修」… 研修の受講・お申込み\n"
    "・「人事」… 勤怠・通勤費の提出 / テンプレ / 履歴\n"
    "────────\n"
    "请选择今天要用的功能：\n"
    "・发「研修」… 研修报名\n"
    "・发「人事」… 考勤·通勤费提交/模板/履历"
)

SHAIN_INTENTS = dict(assist.COMMON_INTENTS)
SHAIN_INTENTS.update({
    "kenshu": set(KENSHU_WORDS) | {"training", "study", "培训", "연수", "교육"},
    "jinji": set(JINJI_WORDS) | {"hr", "attendance", "考勤", "인사", "근태"},
})


def _shain_chooser(name, lang):
    if lang == "ja":
        txt = ((("%s さん\n" % name) if name else "")
               + "本日のご利用メニューを選んでください👇\n"
                 "・研修：受講・お申込み\n・人事：勤怠・通勤費の提出 / テンプレ / 履歴")
    else:
        txt = ((("%s\n" % name) if name else "")
               + "请选择今天要用的功能👇\n・研修：报名/咨询\n・人事：考勤·通勤费提交/模板/履历")
    return assist.quick_reply(txt, [("📚 研修", "研修"), ("🗂️ 人事", "人事"), ("❓ヘルプ", "ヘルプ")])


def _shain_help(lang):
    title = "■ 社員メニュー（ボタンをタップ）" if lang == "ja" else "■ 社员菜单（点按钮）"
    entries = [
        ("研修 … 研修の受講・お申込み", "研修 … 报名/咨询", "📚 研修", "研修"),
        ("人事 … 勤怠・通勤費の提出", "人事 … 考勤·通勤费提交", "🗂️ 人事", "人事"),
        ("Excel を送る → 人事へ提出（再提出も可）", "发 Excel → 人事提交（可重复）", None, None),
        ("登録解除 … 別人で登録し直す", "登録解除 … 换人重新登记", None, None),
    ]
    return assist.help_message(lang, title, entries)


_ddb = None
SESSION_TABLE = os.environ.get("SHAIN_SESSION_TABLE", "")


# ---------------- セッション（mode + 日付） ----------------
def _table():
    global _ddb
    if _ddb is None:
        _ddb = boto3.resource("dynamodb", region_name=jconfig.REGION)
    return _ddb.Table(SESSION_TABLE)


def _get_session(uid):
    try:
        return _table().get_item(Key={"userId": uid}).get("Item")
    except Exception as e:  # noqa: BLE001
        print("get_session error:", repr(e))
        return None


def _set_session(uid, mode, date):
    try:
        _table().put_item(Item={"userId": uid, "mode": mode, "modeDate": date})
    except Exception as e:  # noqa: BLE001
        print("set_session error:", repr(e))


# ---------------- 研修学员の自動プロビジョニング ----------------
def _ensure_kenshu_student(uid, name):
    s = kbiz.get_student(uid)
    if s and s.get("status") == "active":
        return
    kdb.students().put_item(Item={
        "openid": uid, "status": "active", "role": "student",
        "lang": "ja", "name": name or "", "createdAt": iso_utc(),
    })


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
    if _method(event) == "GET":                 # 人事 添付/テンプレ DL
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

    # ---- ブロック/削除(unfollow)・再追加(follow) を記録（返信なし）----
    if mtype == "event":
        evt = ev.get("event")
        if evt == "unsubscribe":
            nm = authlib.mark_blocked(uid, True)
            print("[UNFOLLOW] %s name=%s ブロック/削除" % (uid, nm))
            return
        if evt == "subscribe":
            authlib.mark_blocked(uid, False)      # 再追加でブロック解除

    # ---- ⓪ 登録解除（別の社員で登録し直す／誤登録のリセット）----
    if mtype == "text" and text in authlib.RESET_WORDS:
        authlib.unbind(uid)                       # roster.lineUserId + 認証行クリア
        try:
            jbiz.db.employees().delete_item(Key={"userId": uid})
        except Exception:  # noqa: BLE001
            pass
        try:
            _table().delete_item(Key={"userId": uid})   # mode セッション
        except Exception:  # noqa: BLE001
            pass
        jline.reply(rt, "登録を解除しました。もう一度「所属部署 お名前」を送って登録してください。\n"
                        "例：開発部 社員テスト\n────────\n已解除登记，请重新发「部门 姓名」登记。")
        return

    # ---- ① 初回本人確認（花名册 dept+name）。未登録なら登録フローが会話を占有 ----
    emp = jbiz.get_employee(uid)
    if not (emp and emp.get("status") == "active"):
        text_in = text if mtype == "text" else ""
        _, reply = jbiz.handle_registration(uid, text_in)
        emp2 = jbiz.get_employee(uid)
        if emp2 and emp2.get("status") == "active":
            # 登録成功直後 → 使い方より先に「言語選択」を出す（選択後にメニュー）
            jline.reply_messages(rt, [assist.lang_chooser(emp2.get("name") or "")])
        else:
            jline.reply(rt, reply or jbiz.i18n.T("ask_dept_name"))
        return

    name = emp.get("name") or ""
    today = authlib.today_jst()

    # ---- ② 言語：選択ワード → 設定 → モードチューザー ----
    if mtype == "text":
        lw = assist.detect_lang_word(text)
        if lw:
            assist.set_lang("shain", uid, lw)
            jline.reply_messages(rt, [_shain_chooser(name, lw)])
            return

    # ---- ③ 毎日初回 → 言語チューザーを最優先 ----
    if assist.needs_lang_today("shain", uid):
        jline.reply_messages(rt, [assist.lang_chooser(name)])
        return

    lang = assist.get_lang("shain", uid)
    canon = assist.resolve(text, SHAIN_INTENTS) if mtype == "text" else None

    # ---- ④ ヘルプ ----
    if canon == "help":
        jline.reply_messages(rt, [_shain_help(lang)])
        return

    # ---- ④.5 人事(提出)系ボタン → 人事モードに固定して原文を委譲 ----
    if mtype == "text" and text in SHAIN_SUBMIT_WORDS:
        _set_session(uid, JINJI, today)
        jinji_web._route(ev, base)
        return

    # ---- ⑤ 明示モード切替（多言語）----
    if canon == "kenshu" or (mtype == "text" and text in KENSHU_WORDS):
        _set_session(uid, KENSHU, today)
        _ensure_kenshu_student(uid, name)
        jline.reply(rt, kenshu_web._route(_kenshu_msg(ev, as_event=True)))
        return
    if canon == "jinji" or (mtype == "text" and text in JINJI_WORDS):
        _set_session(uid, JINJI, today)
        _jinji_entry(ev, base)
        return

    # ---- ⑥ ファイル/画像 → 人事(提出)固定 ----
    if mtype in ("file", "image"):
        _set_session(uid, JINJI, today)
        jinji_web._route(ev, base)
        return

    # ---- ⑦ 当日まだモード未選択 → モードチューザー（Quick Reply）----
    sess = _get_session(uid)
    if not sess or sess.get("modeDate") != today or not sess.get("mode"):
        jline.reply_messages(rt, [_shain_chooser(name, lang)])
        return

    # ---- ⑧ 当日のモードに従って振り分け ----
    if sess["mode"] == KENSHU:
        _ensure_kenshu_student(uid, name)
        jline.reply(rt, kenshu_web._route(_kenshu_msg(ev)))
        return
    jinji_web._route(ev, base)


def _jinji_entry(ev, base):
    """人事モード突入直後：メニュー表示（subscribe 相当）。"""
    fake = dict(ev)
    fake["msgType"] = "event"
    fake["event"] = "subscribe"
    jinji_web._route(fake, base)
