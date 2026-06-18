"""LINE Webhook 入口：验签 → 解析事件 → 日次「講師」認証 → webhook._route → reply。

研修 LINE channel ＝「講師」専用。全社花名册(roster)で **每日「部门 姓名」本人确认**し、
role=講師(teacher) のみ通す。受講者は「BrightStar 社員アシスタント」へ誘導。
※ 微信(kf/企業微信)側は従来どおり受講者・講師を兼ねる（本ゲートは LINE のみ）。
"""
import base64
import logging

from common import assist, authlib, business, line
from handlers import webhook

log = logging.getLogger()
log.setLevel(logging.INFO)

CHANNEL = "kenshu"

KENSHU_INTENTS = dict(assist.COMMON_INTENTS)

_HELP_ENTRIES = [
    ("建课 … 新規コース作成", "建课 … 新建课程", None, None),
    ("发布 … コース公開(Zoom)", "发布 … 发布课程(Zoom)", None, None),
    ("改课 / 删课 … 変更 / 削除", "改课 / 删课 … 修改 / 删除", None, None),
    ("学员列表 … 受講者一覧", "学员列表 … 学员列表", "学员列表", "学员列表"),
    ("名单 / 分组 … 名簿 / ランダム分組", "名单 / 分组 … 名单 / 随机分组", None, None),
    ("講師ヘルプ … 管理コマンド一覧", "讲师帮助 … 管理命令一览", "講師ヘルプ", "老师帮助"),
    ("本日認証 … 当日の本人確認", "本日認证 … 当天本人确认", "認証", "認証"),
    ("登録解除 … 別人で認証し直す", "登録解除 … 换人重新认证", None, None),
]


def _kenshu_help(lang):
    title = "■ 講師メニュー（ボタンをタップ）" if lang == "ja" else "■ 讲师菜单（点按钮）"
    return assist.help_message(lang, title, _HELP_ENTRIES)


def _auth_ok_help(lang, name):
    h = _kenshu_help(lang)
    head = ("✅ 認証OK：%s（講師）" % name) if lang == "ja" else ("✅ 认证通过：%s（讲师）" % name)
    h["text"] = head + "\n\n" + h["text"]
    return h

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
MSG_TAP = ("本日の本人確認をお願いします。\nメニューの「本日認証」を押すか、「認証」と送信してください。\n"
           "请进行今日本人确认：点「本日認証」或发送「認証」。")
MSG_TAKEN = ("この社員番号は別の LINE アカウントで登録済みです。人事にご連絡ください。\n"
             "该员工编号已绑定别的 LINE 账号，请联系人事。")


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


def _handle_event(ev):
    """str（テキスト返信）または dict（Quick Reply 等の message）を返す。"""
    raw = ev.get("fromUser")
    if not raw:
        return ""
    openid = business.resolve_openid(raw)
    text = (ev.get("content") or "").strip() if ev.get("msgType") == "text" else ""

    # 登録解除
    if text in authlib.RESET_WORDS:
        authlib.unbind(raw)
        return ("認証の紐付けを解除しました。次回「所属部署 お名前」で認証してください。\n"
                "已解除认证绑定，下次请用「部门 姓名」认证。")

    # 認証（TEACHER_OPENIDS 白名单は免除）
    if not _is_whitelisted(openid):
        action, item = authlib.gate(CHANNEL, raw, text, _teacher_pred)
        if action not in ("ok", "pass"):
            return {"tap": MSG_TAP, "wrong_role": MSG_WRONG_ROLE, "not_found": MSG_NOT_FOUND,
                    "ambiguous": MSG_AMBIGUOUS, "taken": MSG_TAKEN}.get(action, AUTH_PROMPT)

    # === 認証済み講師 ===
    nm = (authlib.find_by_line(raw) or {}).get("name", "")
    lw = assist.detect_lang_word(text)
    if lw:
        assist.set_lang(CHANNEL, raw, lw)
        return _auth_ok_help(lw, nm)                 # dict
    if assist.needs_lang_today(CHANNEL, raw):
        return assist.lang_chooser(nm)               # dict
    lang = assist.get_lang(CHANNEL, raw)
    if assist.resolve(text, KENSHU_INTENTS) == "help":
        return _kenshu_help(lang)                    # dict
    return webhook._route(ev)                         # 講師ルーティング（text）


def handler(event, context):
    headers = {k.lower(): v for k, v in (event.get("headers") or {}).items()}
    signature = headers.get("x-line-signature", "")
    body_bytes = _raw_body(event)

    if not line.verify_signature(body_bytes, signature):
        return {"statusCode": 403, "body": "invalid signature"}

    for ev in line.parse_events(body_bytes.decode("utf-8")):
        try:
            out = _handle_event(ev)
            rt = ev.get("replyToken", "")
            if isinstance(out, dict):
                line.reply_messages(rt, [out])      # Quick Reply 等
            elif out:
                line.reply(rt, out)
        except Exception:  # noqa: BLE001
            log.exception("line route error")
    return {"statusCode": 200, "body": ""}
