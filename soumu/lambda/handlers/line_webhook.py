"""LINE webhook 入口（総務チャネル）：勤怠(勤務表)・通勤費(経費)の提出管理・催促。

人事(jinji)から「一覧/未提出/催促/一括DL」をこのチャネルへ移管したもの。
データは brightstar-hr の submissions/roster/auth テーブル・S3 を**共有**して読む
（従業員の提出は「社員アシスタント(shain)」のまま。ここは管理・督促に専念）。

Function URL は2用途：
- POST：LINE webhook（検証後ルーティング）
- GET /dl?key=<s3key>&sig=<hmac>：一括DL用の短縮リンク（HMAC署名で越権防止）→ 302
"""
import base64
import hashlib
import hmac
import json
import os
import urllib.parse

import boto3

from common import assist, authlib, business, config, line, s3util
from common.i18n import T, type_label

CHANNEL = "soumu"

# 多言語キーワード（英/日/韓/中）→ canonical。COMMON_INTENTS(help/auth/reset) もマージ。
INTENTS = dict(assist.COMMON_INTENTS)
INTENTS.update({
    "roster_status": {"一覧", "一覧確認", "提出状況", "list", "status", "一览", "提交情况", "現況", "목록", "제출현황"},
    "missing": {"未提出確認", "未提出", "未提出者", "missing", "未提交", "谁没交", "誰が未提出", "미제출"},
    "remind": {"催促", "リマインド", "督促", "remind", "催办", "提醒", "독촉"},
    "bulk_dl": {"一括DL", "一括ダウンロード", "一括", "bulkdl", "download", "打包下载", "打包", "일괄다운로드"},
})

# ヘルプ項目: (説明 ja, 説明 zh, ボタン名, 送信キーワード)
_HELP_ENTRIES = [
    ("一覧[202606] … 全員の提出状況", "一覧[202606] … 全员提交情况", "一覧", "一覧"),
    ("未提出確認[202606] … 未提出者", "未提出確認[202606] … 未提交者", "未提出確認", "未提出確認"),
    ("催促[202606] … 未提出者に督促", "催促[202606] … 给未提交者发提醒", "催促", "催促"),
    ("済 E003 … メール受領を手動で提出済みに", "済 E003 … 手动标为已交(邮件收到)", None, None),
    ("一括DL … 当月の提出を一括DL", "一括DL … 打包下载当月提交", "一括DL", "一括DL"),
    ("本日認証 … 当日の本人確認", "本日認証 … 当天本人确认", "認証", "認証"),
    ("登録解除 … 別人で登録し直す", "登録解除 … 换人重新登记", None, None),
]


def _help(lang):
    title = "■ 総務メニュー（ボタンをタップで実行）" if lang == "ja" else "■ 总务菜单（点按钮即执行）"
    return assist.help_message(lang, title, _HELP_ENTRIES)


def _auth_ok_with_help(uid, name, dept):
    """認証直後：言語選択を反映した認証OK + ヘルプ。"""
    lang = assist.get_lang(CHANNEL, uid)
    head = ("✅ 認証OK：%s（%s）" % (name, dept)) if lang == "ja" else ("✅ 认证通过：%s（%s）" % (name, dept))
    h = _help(lang)
    h["text"] = head + "\n\n" + h["text"]
    return h


def _soumu_pred(item):
    """総務チャネルの権限：花名册 role=hr または role=soumu。
    既存の人事(hr)はそのまま使え、将来 role=soumu を付与すれば総務専任も可。"""
    return item.get("role") in ("hr", "soumu")


def _auth_reply(rt, action, item):
    """非認証態の統一応答（gate の action に対応）。"""
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


_lambda = None


def _lambda_client():
    global _lambda
    if _lambda is None:
        _lambda = boto3.client("lambda", region_name=config.REGION)
    return _lambda


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


def _base_url(event):
    host = _header(event, "host") or \
        (event.get("requestContext", {}) or {}).get("domainName", "")
    return "https://" + host


def handler(event, context):
    # GET /dl?key=... → 一括DL短縮リンク（検証不要、HMACで保護）
    if _method(event) == "GET":
        return _handle_download(event)

    body_bytes = _raw_body(event)
    sig = _header(event, "x-line-signature")
    if not line.verify_signature(body_bytes, sig):
        return {"statusCode": 403, "body": "bad signature"}

    base = _base_url(event)
    for ev in line.parse_events(body_bytes.decode("utf-8")):
        try:
            _route(ev, base)
        except Exception as e:  # noqa: BLE001  単一失敗が他に波及しない / 全体200
            print("route error:", repr(e))
    return {"statusCode": 200, "body": "OK"}


def _sign_key(key):
    return hmac.new(config.line_secret().encode("utf-8"),
                    key.encode("utf-8"), hashlib.sha256).hexdigest()[:16]


def _dl_link(base, key):
    return "%s/dl?key=%s&sig=%s" % (base, urllib.parse.quote(key, safe=""), _sign_key(key))


def _redirect(url):
    return {"statusCode": 302, "headers": {"Location": url}, "body": ""}


def _handle_download(event):
    params = urllib.parse.parse_qs(event.get("rawQueryString", "") or "")
    key = params.get("key", [None])[0]                   # 一括DL（署名検証）
    sig = params.get("sig", [None])[0]
    if key and sig and hmac.compare_digest(_sign_key(key), sig):
        name = key.rsplit("/", 1)[-1] or "submission.xlsx"
        if name.endswith(".zip"):
            return _redirect(s3util.presign_get(
                key, download_name=name, ascii_name="export.zip",
                content_type="application/zip"))
        return _redirect(s3util.presign_get(key, download_name=name))
    return {"statusCode": 404, "body": "not found"}


# ---------------- 路由 ----------------

def _fmt_period(p):
    return "%s-%s" % (p[:4], p[4:])


def _route(ev, base=""):
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

    # ---- テスト用バックドア：「<prefix><YYYYMMDD>」で 1 時間だけ 総務 権限（紐付けは変えない）----
    mcode = authlib.master_code_today(config.MASTER_HR_PREFIX)
    if mcode and mtype == "text" and (ev.get("content", "") or "").strip().lower() == mcode.lower():
        authlib.grant_temp(CHANNEL, uid, name="テスト総務", role="soumu", seconds=3600)
        line.reply(rt, "✅ テスト用 総務 権限を 1 時間付与しました。\n"
                       "已临时授予 总务 权限 1 小时（不影响你的花名册绑定）。\n" + T("menu_hr"))
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
        action, item = authlib.gate(CHANNEL, uid, gate_text, _soumu_pred)
        if action not in ("ok", "pass"):
            _auth_reply(rt, action, item)
            return

    # ================= 認証済み 総務 =================
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

    # 日時ピッカーの応答（催促予約の確定）
    if mtype == "postback":
        if ev.get("data") == "book_remind":
            dt_str = (ev.get("params") or {}).get("datetime")
            if dt_str:
                bid, _ = business.booking_add(dt_str, created_by=business.emp_name(uid) or uid)
                line.reply(rt, T("book_done", when=dt_str.replace("T", " "), bid=bid))
            return
        return

    if mtype == "text":
        t = (ev.get("content", "") or "").strip()
        canon = assist.resolve(t, INTENTS)
        if canon == "help":
            line.reply_messages(rt, [_help(lang)])
            return
        # 催促予約（「催促」より先に判定：語が包含されるため）
        if t.startswith(("催促予約", "予約催促", "预约催促")):
            line.reply_messages(rt, [_book_picker()])
            return
        if t.startswith(("予約確認", "予約一覧", "预约确认")):
            line.reply(rt, _book_list())
            return
        if t.startswith(("予約取消", "予約キャンセル", "取消预约")):
            line.reply(rt, _book_cancel(t))
            return
        if t.startswith(("済", "済解除")):            # 手動「済」＝催促除外（メール等で受領）
            line.reply(rt, _handle_manual_ok(uid, t))
            return
        if canon == "missing" or any(k in t for k in ("未提出", "未提交", "谁没交", "誰が出して", "未提出者")):
            line.reply_messages(rt, _hr_missing_messages(t))   # 済ボタン付き
            return
        if canon == "roster_status" or _is_roster_cmd(t):
            line.reply_messages(rt, _hr_roster_messages(business.normalize_period(t)))
            return
        if canon == "bulk_dl" or _is_bulk_cmd(t):
            line.reply_messages(rt, [_bulk_download_msg(business.normalize_period(t), base)])
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
    if canon == "remind" or any(k in t for k in ("催促", "催办", "リマインド", "提醒", "督促")):
        return _hr_remind(uid, t)
    return T("fallback", lang=lang)


# ---------------- 催促予約（日時ピッカー） ----------------

def _book_picker():
    from datetime import datetime, timezone, timedelta
    jst = timezone(timedelta(hours=9))
    now = datetime.now(jst)
    initial = (now + timedelta(days=1)).replace(hour=9, minute=0)
    return line.datetime_picker_message(
        alt_text="催促予約",
        text=T("book_pick"),
        label="日時を選ぶ",
        data="book_remind",
        initial=initial.strftime("%Y-%m-%dT%H:%M"),
        min_dt=now.strftime("%Y-%m-%dT%H:%M"),
        max_dt=(now + timedelta(days=60)).strftime("%Y-%m-%dT%H:%M"),
    )


def _book_list():
    rows = business.bookings_pending()
    if not rows:
        return T("book_list_empty")
    lines = ["⏰ 予約中の催促（%d件）" % len(rows)]
    for b in rows:
        lines.append("・%s  ID:%s（by %s）" % (b.get("runAtJst", ""),
                                              b.get("bookingId", ""), b.get("createdBy", "")))
    lines.append("")
    lines.append("取消：予約取消 <ID>")
    return "\n".join(lines)


def _book_cancel(t):
    import re
    m = re.search(r"([0-9a-f]{6})", t)
    if not m:
        return _book_list()
    b = business.booking_cancel(m.group(1))
    if not b:
        return T("book_not_found", bid=m.group(1))
    return T("book_cancelled", bid=m.group(1), when=b.get("runAtJst", ""))


# ---------------- 手動「済」（メール等で受領 → 催促除外） ----------------

def _handle_manual_ok(uid, raw):
    """「済 E003 [勤怠|経費]」＝手動で提出済み扱い／「済解除 …」＝取り消し。"""
    import re
    parts = [p for p in re.split(r"[\s　]+", (raw or "").strip()) if p]
    clear = parts[0].startswith("済解除")
    if len(parts) < 2:
        return T("manual_ok_usage")
    r = business.roster_resolve(parts[1])
    if not r:
        return T("roster_not_found", q=parts[1])
    rest = " ".join(parts[2:])
    only = business.infer_type(rest, rest) if rest else None
    types = [only] if only else list(business.config.SUBMISSION_TYPES)
    period = business.normalize_period(rest)
    by = business.emp_name(uid) or "総務"
    for tp in types:
        if clear:
            business.manual_ok_clear(r["empId"], period, tp)
        else:
            business.manual_ok_set(r["empId"], period, tp, by_name=by)
    labels = "、".join(type_label(tp) for tp in types)
    key = "manual_ok_cleared" if clear else "manual_ok_done"
    return T(key, name=r.get("name", r["empId"]), period=_fmt_period(period), labels=labels)


# ---------------- 集計・表示（勤怠・通勤費の提出管理） ----------------

def _mark_unreg(has_line):
    return "" if has_line else "  " + T("line_unregistered")


def _hr_missing(text):
    """未提出一覧のテキスト。行頭に社員番号（済 コマンド／ボタンで使う）。"""
    period = business.normalize_period(text)
    only = business.infer_type(text, text)
    lines = ["■ 未提出（%s）" % _fmt_period(period)]
    if only:
        miss = business.missing(period, only)
        if not miss:
            return T("all_submitted")
        lines.append("【%s】" % type_label(only))
        for e in miss:
            lines.append("・%s %s（%s）%s" % (e.get("empId", ""),
                                            e.get("name", ""),
                                            e.get("department", ""),
                                            _mark_unreg(e.get("lineUserId"))))
        lines.append("")
        lines.append(T("manual_hint"))
        return "\n".join(lines)
    mm = business.missing_all_types(period)
    if not mm:
        return T("all_submitted")
    for eid in sorted(mm):
        v = mm[eid]
        e = v["emp"]
        labels = "、".join(type_label(t) for t in v["missing_types"])
        lines.append("・%s %s（%s）— 未: %s%s" % (eid, e.get("name", ""),
                                                e.get("department", ""), labels,
                                                _mark_unreg(v.get("linked"))))
    lines.append("")
    lines.append(T("manual_hint"))
    return "\n".join(lines)


def _hr_missing_messages(text):
    """未提出一覧＋「済」ワンタップボタン（最大13名分）。押すと「済 E00X」を送信。"""
    body = _hr_missing(text)
    period = business.normalize_period(text)
    mm = business.missing_all_types(period)
    items = []
    for eid in sorted(mm):
        if len(items) >= 13:
            break
        nm = mm[eid]["emp"].get("name", eid)
        items.append(("済 " + nm[:17], "済 " + eid))
    if not items:
        return [{"type": "text", "text": body}]
    if len(mm) > 13:
        body += "\n" + T("manual_more")
    return [assist.quick_reply(body, items)]


ROSTER_CMDS_KW = ("一覧", "一览", "全員", "全员", "提出状況", "提交情况")
BULK_CMDS_KW = ("一括dl", "一括ダウンロード", "一括ＤＬ", "一括", "打包下载", "打包", "bulkdl", "zip")


def _is_roster_cmd(t):
    return any(k in t for k in ROSTER_CMDS_KW)


def _is_bulk_cmd(t):
    tl = t.lower()
    return any(k in tl for k in BULK_CMDS_KW)


def _emp_line(r):
    e = r["emp"]
    def _mk(it):
        if not it:
            return "✗"
        return "✓(手)" if isinstance(it, dict) and it.get("manual") else "✓"
    marks = " ".join("%s%s" % (type_label(t), _mk(it)) for t, it in r["types"].items())
    tail = "" if r.get("linked") else "  " + T("line_unregistered")
    return "・%s %s（%s） %s%s" % (e.get("empId", ""), e.get("name", ""),
                                 e.get("department", ""), marks, tail)


def _hr_roster_messages(period):
    """未提出置顶 + 提出済后置，纯 ✓/✗ 状态。人多时按 ~4500 字分多条，最多 5 条。"""
    rows = business.roster_status(period)
    if not rows:
        return [{"type": "text", "text": "（社員未登録 / 暂无员工）"}]
    pending = [r for r in rows if any(it is None for it in r["types"].values())]
    done = [r for r in rows if all(it is not None for it in r["types"].values())]

    lines = ["■ 提出状況（%s）" % _fmt_period(period), ""]
    lines.append("【未提出 %d名】" % len(pending))
    lines += [_emp_line(r) for r in pending] if pending else ["（なし）"]
    lines.append("")
    lines.append("【提出済 %d名】" % len(done))
    lines += [_emp_line(r) for r in done] if done else ["（なし）"]
    lines.append("")
    lines.append("💾 全件まとめてDL →「一括DL」と送信")

    msgs, cur = [], ""
    for ln in lines:
        if len(cur) + len(ln) + 1 > 4500:
            msgs.append(cur)
            cur = ""
            if len(msgs) >= 5:
                break
        cur = (cur + "\n" + ln) if cur else ln
    if cur and len(msgs) < 5:
        msgs.append(cur)
    return [{"type": "text", "text": m} for m in msgs[:5]]


def _bulk_download_msg(period, base):
    """打包当月全部提交 → 一个「全件ダウンロード」按钮。"""
    zipkey, n = s3util.build_month_zip(period)
    if not n:
        return {"type": "text", "text": "%s の提出はまだありません。" % _fmt_period(period)}
    return line.buttons_message(
        alt_text="全件ダウンロード",
        text="%s の提出 %d 件をまとめました" % (_fmt_period(period), n),
        actions=[{"label": "全件ダウンロード(zip)", "uri": _dl_link(base, zipkey)}],
    )


def _hr_remind(uid, text):
    period = business.normalize_period(text)
    mm = business.missing_all_types(period)
    if not mm:
        return T("all_submitted")
    fn = config.REMINDER_FUNCTION_NAME or os.environ.get("REMINDER_FUNCTION_NAME")
    if fn:
        _lambda_client().invoke(
            FunctionName=fn,
            InvocationType="Event",
            Payload=json.dumps({"trigger": "manual", "period": period, "by": uid}).encode(),
        )
    pushable = sum(1 for v in mm.values() if v.get("linked"))
    return T("remind_sent", n=pushable)
