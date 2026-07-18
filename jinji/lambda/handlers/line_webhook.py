"""LINE webhook 入口（人事チャネル）：名簿(花名册) CRUD + メール校正。

※ 勤怠(勤務表)・通勤費(経費)の「提出管理・一覧・未提出・催促・一括DL」は
   「総務アシスタント(soumu)」へ移管した（責務分離）。本チャネルは人事業務に専念。
   従業員の提出は引き続き「社員アシスタント(shain)」。
"""
import base64

from common import assist, authlib, business, config, line, messaging
from common.i18n import T

CHANNEL = "jinji"

# 多言語キーワード（英/日/韓/中）→ canonical。COMMON_INTENTS(help/auth/reset) もマージ。
INTENTS = dict(assist.COMMON_INTENTS)
INTENTS.update({
    "roster_admin": {"名簿", "社員名簿", "roster", "花名册", "员工一览", "名부", "명부"},
    "proofread": {"メール校正", "校正", "proofread", "邮件校正", "校对", "교정"},
})

# ヘルプ項目: (説明 ja, 説明 zh, ボタン名, 送信キーワード)
_HELP_ENTRIES = [
    ("名簿 / 社員追加 / 社員変更 / 社員削除", "名簿 / 添加 / 变更 / 删除 员工", "名簿", "名簿"),
    ("メール校正 … メール自動校正ツール", "メール校正 … 邮件自动校正工具", "メール校正", "メール校正"),
    ("本日認証 … 当日の本人確認", "本日認証 … 当天本人确认", "認証", "認証"),
    ("登録解除 … 別人で登録し直す", "登録解除 … 换人重新登记", None, None),
    ("※ 勤怠・通勤費の管理/催促は「総務アシスタント」へ",
     "※ 考勤·通勤费的管理/催促请用「総務アシスタント」", None, None),
]


def _help(lang):
    title = "■ 人事メニュー（ボタンをタップで実行）" if lang == "ja" else "■ 人事菜单（点按钮即执行）"
    return assist.help_message(lang, title, _HELP_ENTRIES)


def _auth_ok_with_help(uid, name, dept):
    """認証直後：言語選択を反映した認証OK + ヘルプ。"""
    lang = assist.get_lang(CHANNEL, uid)
    head = ("✅ 認証OK：%s（%s）" % (name, dept)) if lang == "ja" else ("✅ 认证通过：%s（%s）" % (name, dept))
    h = _help(lang)
    h["text"] = head + "\n\n" + h["text"]
    return h


def _hr_pred(item):
    """认证人是否有人事权限：花名册 role=hr。"""
    return item.get("role") == "hr"


def _auth_reply(rt, action, item):
    """非认证态下，统一的认证应答（gate の action に対応）。"""
    if action == "ok":
        line.reply(rt, T("auth_ok_hr", name=item.get("name", ""),
                         dept=item.get("department", ""), menu=T("menu_hr")))
    elif action == "tap":
        line.reply(rt, T("auth_tap"))
    elif action == "wrong_role":
        line.reply(rt, T("wrong_role_hr"))
    elif action == "not_found":
        line.reply(rt, T("not_in_roster", name="—"))
    elif action == "ambiguous":
        line.reply(rt, T("dup_name_id"))
    elif action == "taken":
        line.reply(rt, T("auth_taken"))
    else:  # need_bind
        line.reply(rt, T("ask_dept_name"))


# ---------------- HTTP ----------------

def _raw_body(event):
    body = event.get("body") or ""
    if event.get("isBase64Encoded"):
        return base64.b64decode(body)
    return body.encode("utf-8")


def _header(event, name):
    headers = event.get("headers") or {}
    for k, v in headers.items():
        if k.lower() == name.lower():
            return v
    return None


def _method(event):
    return ((event.get("requestContext", {}) or {}).get("http", {}) or {}).get("method", "")


def handler(event, context):
    # 人事チャネルはダウンロード短縮リンクを持たない（一括DL等は総務へ移管）
    if _method(event) == "GET":
        return {"statusCode": 404, "body": "not found"}

    body_bytes = _raw_body(event)
    sig = _header(event, "x-line-signature")
    if not line.verify_signature(body_bytes, sig):
        return {"statusCode": 403, "body": "bad signature"}

    for ev in line.parse_events(body_bytes.decode("utf-8")):
        try:
            _route(ev)
        except Exception as e:  # noqa: BLE001  単条失败不影响其他事件 / 整体 200
            print("route error:", repr(e))
    return {"statusCode": 200, "body": "OK"}


# ---------------- 路由 ----------------

def _route(ev):
    uid = ev.get("fromUser")
    if not uid:
        return
    rt = ev.get("replyToken")
    mtype = ev.get("msgType")

    # ---- ブロック/削除(unfollow)・再追加(follow) を記録（返信なし）----
    if mtype == "event":
        evt = ev.get("event")
        if evt == "unsubscribe":
            nm = authlib.mark_blocked(uid, True)
            print("[UNFOLLOW] %s name=%s ブロック/削除" % (uid, nm))
            return
        if evt == "subscribe":
            authlib.mark_blocked(uid, False)      # 再追加でブロック解除

    # ---- テスト用バックドア：「<prefix><YYYYMMDD>」で 1 時間だけ HR 権限（紐付けは変えない）----
    mcode = authlib.master_code_today(config.MASTER_HR_PREFIX)
    if mcode and mtype == "text" and (ev.get("content", "") or "").strip().lower() == mcode.lower():
        authlib.grant_temp(CHANNEL, uid, name="テストHR", seconds=3600)
        line.reply(rt, "✅ テスト用 HR 権限を 1 時間付与しました。\n"
                       "已临时授予 HR 权限 1 小时（不影响你的花名册绑定）。\n" + T("menu_hr"))
        return

    # ---- 登録解除：別人で認証し直す／誤紐付けのリセット ----
    if mtype == "text" and (ev.get("content", "") or "").strip() in authlib.RESET_WORDS:
        authlib.unbind(uid)
        line.reply(rt, "認証の紐付けを解除しました。次回「所属部署 お名前」で認証してください。\n"
                       "已解除认证绑定，下次请用「部门 姓名」认证。")
        return

    # ---- 日次認証ゲート：初回は「部门 姓名」、以降は「認証」ワンタップ（HR_USERIDS 白名单は免除）----
    bare = business._strip_prefix(uid)
    whitelisted = uid in config.HR_USERIDS or bare in config.HR_USERIDS
    if not whitelisted:
        gate_text = ev.get("content", "") if mtype == "text" else ""
        action, item = authlib.gate(CHANNEL, uid, gate_text, _hr_pred)
        if action not in ("ok", "pass"):
            _auth_reply(rt, action, item)
            return

    # ================= 認証済み HR =================
    name = business.emp_name(uid) or ""

    # ① 言語選択ワード → 設定 → 認証OK + ヘルプ（選んだ言語で）
    if mtype == "text":
        lw = assist.detect_lang_word(ev.get("content", ""))
        if lw:
            assist.set_lang(CHANNEL, uid, lw)
            r = business.roster_of(uid) or {}
            line.reply_messages(rt, [_auth_ok_with_help(uid, name, r.get("department", ""))])
            return

    # ② 毎日初回（認証直後 / 未選択）→ 言語チューザーを最優先で表示
    if assist.needs_lang_today(CHANNEL, uid):
        line.reply_messages(rt, [assist.lang_chooser(name)])
        return

    lang = assist.get_lang(CHANNEL, uid)

    # ③ イベント
    if mtype == "event":
        if ev.get("event") == "subscribe":
            line.reply_messages(rt, [_help(lang)])
        return

    if mtype == "text":
        t = (ev.get("content", "") or "").strip()
        canon = assist.resolve(t, INTENTS)
        if canon == "help":
            line.reply_messages(rt, [_help(lang)])
            return
        if canon == "roster_admin" or _is_roster_admin_cmd(t):
            line.reply(rt, _handle_roster_admin(uid, ev.get("content", "")))
            return
        line.reply(rt, _route_text(uid, ev.get("content", ""), lang, canon))
        return

    if mtype in ("file", "image"):
        line.reply(rt, "📄 勤怠・通勤費の提出は「社員アシスタント」をご利用ください。\n"
                       "考勤·通勤费的提交请用「社員アシスタント」。")
        return


def _route_text(uid, text, lang="ja", canon=None):
    t = (text or "").strip()
    if canon == "help" or t in ("メニュー", "菜单", "menu", "help", "?", "？"):
        return T("menu_hr", lang=lang)
    if canon == "proofread" or any(k in t for k in ("メール校正", "メール", "校正", "邮件校正", "邮件", "校对")):
        url = config.MAIL_PROOFREAD_URL
        if not url:
            return "メール校正ツールのURLが未設定です（管理者にご連絡ください）。"
        return (("✉️ メール自動校正ツール（社内）\n%s\n宛先や敬語・誤字をチェックできます。" % url)
                if lang == "ja" else
                ("✉️ 邮件自动校正工具（社内）\n%s\n可检查收件人/敬语/错字。" % url))
    return T("fallback", lang=lang)


# ---------------- 花名册增删改查（人事） ----------------

ROSTER_ADMIN_PREFIXES = (
    "名簿", "花名册", "社員一覧", "员工一览", "社員名簿",
    "社員追加", "員工追加", "添加员工",
    "社員変更", "員工変更", "修改员工",
    "社員削除", "員工削除", "删除员工",
)


def _is_roster_admin_cmd(t):
    return any(t.startswith(p) for p in ROSTER_ADMIN_PREFIXES)


def _roster_list_msg():
    rows = business.roster_scan()
    if not rows:
        return "（社員名簿は空です / 花名册为空）"
    lines = ["■ 社員名簿（%d名）" % len(rows)]
    for r in rows:
        linked = "✓Line" if r.get("lineUserId") else T("line_unregistered")
        if r.get("blocked"):
            linked += " 🚫ブロック中"
        lines.append("・%s %s（%s／%s）%s" % (
            r.get("empId", ""), r.get("name", ""), r.get("department", ""),
            r.get("role", ""), linked))
    return "\n".join(lines)


def _broadcast_roster_change(actor_uid, detail):
    who = business.emp_name(actor_uid) or "人事"
    msg = T("roster_change_alert", who=who, detail=detail)
    for hid in business.list_hr():
        if hid != actor_uid:
            messaging.send(hid, msg)


def _handle_roster_admin(uid, raw):
    import re
    parts = [p for p in re.split(r"[\s　]+", (raw or "").strip()) if p]
    if not parts:
        return T("crud_usage")
    cmd = parts[0]

    if cmd in ("名簿", "花名册", "社員一覧", "员工一览", "社員名簿"):
        return _roster_list_msg()

    if cmd in ("社員追加", "員工追加", "添加员工"):
        if len(parts) < 3:
            return T("crud_usage")
        role = parts[3] if len(parts) >= 4 else "employee"
        item = business.roster_add(parts[1], parts[2], role)
        detail = T("roster_added", eid=item["empId"], name=item["name"],
                   dept=item["department"], role=item["role"])
        _broadcast_roster_change(uid, detail)
        return detail

    if cmd in ("社員変更", "員工変更", "修改员工"):
        if len(parts) < 4:
            return T("crud_usage")
        r = business.roster_resolve(parts[1])
        if not r:
            return T("roster_not_found", q=parts[1])
        field, val = business.roster_update_field(r["empId"], parts[2], " ".join(parts[3:]))
        if not field:
            return T("crud_usage")
        detail = T("roster_updated", eid=r["empId"], name=r.get("name", ""),
                   field=parts[2], value=val)
        _broadcast_roster_change(uid, detail)
        return detail

    if cmd in ("社員削除", "員工削除", "删除员工"):
        if len(parts) < 2:
            return T("crud_usage")
        r = business.roster_resolve(parts[1])
        if not r:
            return T("roster_not_found", q=parts[1])
        business.roster_delete(r["empId"])
        detail = T("roster_deleted", eid=r["empId"], name=r.get("name", ""))
        _broadcast_roster_change(uid, detail)
        return detail

    return T("crud_usage")
