"""LINE webhook 入口：验签 → 解析事件 → 路由（注册 / 提交 / 查询 / 催办）。

Function URL 同时承担两类请求：
- POST：LINE webhook（验签后路由）
- GET /dl?type=kintai|commute      ：空白模板短链，302 跳转预签名 URL
- GET /dl?key=<s3key>&sig=<hmac>    ：提交文件短链（HMAC 签名防越权），302 跳转预签名
  （Lambda 角色临时凭证使预签名 URL 超过 LINE 按钮 1000 字上限，故一律走短链）
"""
import base64
import hashlib
import hmac
import json
import os
import urllib.parse

import boto3

from common import authlib, business, config, line, messaging, s3util
from common.i18n import T, type_label

CHANNEL = "jinji"


def _hr_pred(item):
    """认证人是否有人事权限：花名册 role=hr 或 在 HR_USERIDS 白名单。"""
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
    # GET /dl?type=... → 下载短链跳转（无需验签）
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
        except Exception as e:  # noqa: BLE001  单条失败不影响其他事件 / 整体 200
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
    t = (params.get("type", [None])[0])
    if t in config.SUBMISSION_TYPES:                     # 空白模板
        return _redirect(s3util.presign_template(t))
    key = params.get("key", [None])[0]                   # 提交文件（签名校验）
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


# 人事 channel ＝ HR 専用。社員（一般従業員）の提出・テンプレ・履歴・登録は
# 「BrightStar 社員アシスタント」へ誘導する（責務分離・低結合のため）。
SHAIN_REDIRECT = (
    "🔔 こちらは「人事担当者」専用アカウントです。\n"
    "勤怠・通勤費の提出、テンプレート、提出履歴は\n"
    "👉「BrightStar 社員アシスタント」をご利用ください。\n"
    "────────\n"
    "此账号为「人事担当」专用。\n"
    "考勤·通勤费的提交、模板下载、履历查询，\n"
    "请使用「BrightStar 社員アシスタント」。"
)


def _route(ev, base=""):
    uid = ev.get("fromUser")
    if not uid:
        return
    rt = ev.get("replyToken")
    mtype = ev.get("msgType")

    # ---- 日次認証ゲート：初回は「部门 姓名」、以降は「認証」ワンタップ（HR_USERIDS 白名单は免除）----
    bare = business._strip_prefix(uid)
    whitelisted = uid in config.HR_USERIDS or bare in config.HR_USERIDS
    if not whitelisted:
        gate_text = ev.get("content", "") if mtype == "text" else ""
        action, item = authlib.gate(CHANNEL, uid, gate_text, _hr_pred)
        if action != "pass":
            _auth_reply(rt, action, item)
            return

    # ================= 以下 認証済み HR のみ =================
    if mtype == "event":
        if ev.get("event") == "subscribe":
            line.reply(rt, T("menu_hr"))
        return

    if mtype == "text":
        t = (ev.get("content", "") or "").strip()
        if _is_roster_cmd(t):                        # 人事一覧：未提出置顶，纯状态无链接
            line.reply_messages(rt, _hr_roster_messages(business.normalize_period(t)))
            return
        if _is_bulk_cmd(t):                          # 一括DL：打包全月提交，回一个按钮
            line.reply_messages(rt, [_bulk_download_msg(business.normalize_period(t), base)])
            return
        if _is_roster_admin_cmd(t):                  # 人事：花名册增删改查
            line.reply(rt, _handle_roster_admin(uid, ev.get("content", "")))
            return
        # 未提出確認 / 催促 / メニュー など
        line.reply(rt, _route_text(uid, ev.get("content", "")))
        return

    # HR はこの channel ではファイル提出しない（提出は社員アシスタント）
    if mtype in ("file", "image"):
        line.reply(rt, "📄 勤怠・通勤費の提出は「社員アシスタント」をご利用ください。")
        return


def _route_text(uid, text):
    t = (text or "").strip()

    # 通用指令（テンプレ / 履歴 / 一覧 / 一括DL / pending転正 は _route で処理済）
    if t in ("メニュー", "菜单", "menu", "help", "?", "？"):
        return _menu(uid)

    # 3) 人事指令
    if any(k in t for k in ("未提出", "未提交", "谁没交", "誰が出して", "未提出者")):
        return _hr_guard(uid) or _hr_missing(t)
    if any(k in t for k in ("催促", "催办", "リマインド", "提醒", "督促")):
        return _hr_guard(uid) or _hr_remind(uid, t)

    # 4) メール自動校正ツール（独立 Web へのリンク）
    if any(k in t for k in ("メール校正", "メール", "校正", "邮件校正", "邮件", "校对")):
        url = config.MAIL_PROOFREAD_URL
        if not url:
            return "メール校正ツールのURLが未設定です（管理者にご連絡ください）。"
        return ("✉️ メール自動校正ツール（社内）\n%s\n"
                "宛先や敬語・誤字をチェックできます。" % url)

    return T("fallback")


# ---------------- 文件提交 ----------------

def _handle_file(uid, ev, rt):
    data = line.download_content(ev.get("messageId"))
    if not data:
        line.reply(rt, T("submit_fail"))
        return
    fname = ev.get("fileName") or "file.xlsx"
    type_ = business.infer_type(fname, "")
    if type_:
        _do_submit(uid, type_, data, rt)
    else:
        business.stash_pending(uid, fname, data)
        line.reply(rt, T("ask_type"))


def _do_submit(uid, type_, data, rt):
    """提出の検証→保存→返信（年月／氏名チェック、休日勤務の注意）。"""
    # 1) 年月チェック（不一致は保存しない）
    period = business.current_period()
    ok, found = business.check_file_period(type_, data, period)
    if not ok:
        lbl = type_label(type_)
        exp = _fmt_period(period)
        line.reply(rt, T("period_unreadable", label=lbl) if found is None
                   else T("period_mismatch", label=lbl, found=found, expected=exp))
        return
    # 2) 氏名チェック（勤務表のみ・不一致は保存しない）
    ok_name, fname_in = business.check_name(type_, data, uid)
    if not ok_name:
        line.reply(rt, T("name_mismatch", found=fname_in or "—",
                         want=business.emp_name(uid) or "—"))
        return
    # 3) 保存
    period, _, resubmit = business.save_submission(uid, type_, data, period)
    msgs = [{"type": "text",
             "text": T("submit_ok", period=_fmt_period(period), label=type_label(type_))}]
    # 4) 注意（保存はする＝警告のみ）：休日勤務 / 日付の欠落
    warns = business.holiday_work_warnings(type_, data)
    if warns:
        msgs.append({"type": "text",
                     "text": T("holiday_work_warn", dates="、".join(warns))})
    miss = business.missing_dates(type_, data, period)
    if miss:
        days = "、".join("%d日" % d for d in miss)
        msgs.append({"type": "text", "text": T("date_incomplete_warn", days=days)})
    line.reply_messages(rt, msgs[:5])
    _maybe_notify_resubmit(uid, period, type_, resubmit)


def _maybe_notify_resubmit(uid, period, type_, resubmit):
    """跨月重复提交 → 广播给全体人事再确认。"""
    if not (resubmit and business.is_late_resubmit(period)):
        return
    name = business.emp_name(uid) or uid
    msg = T("resubmit_alert", name=name, period=_fmt_period(period), label=type_label(type_))
    for hid in business.list_hr():
        if hid != uid:
            messaging.send(hid, msg)


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


# ---------------- 文案构造 ----------------

def _menu(uid):
    return T("menu_hr") if business.is_hr(uid) else T("menu_employee")


TEMPLATE_CMDS = ("テンプレ", "テンプレート", "模板", "样式", "様式", "template", "テンプレート様式")


def _template_buttons(base=""):
    """空白样式：两个下载按钮，指向本服务短链 /dl?type=...（再 302 跳预签名）。"""
    return line.buttons_message(
        alt_text="空白様式のダウンロード",
        text="空白様式をタップでダウンロード",
        actions=[
            {"label": "作業時間記録簿", "uri": base + "/dl?type=kintai"},
            {"label": "経費", "uri": base + "/dl?type=commute"},
        ],
    )


HISTORY_CMDS = ("履歴", "履历", "我的提交", "履歴確認", "個人履歴", "个人履历", "my")


def _history_messages(uid, base):
    """個人履歴：文字清单 + carousel（每条最新提交一张卡、一个下载按钮）。"""
    items = business.my_submissions(uid)
    if not items:
        return [{"type": "text", "text": T("no_history")}]
    items.sort(key=lambda x: x.get("sk", ""), reverse=True)
    lines = ["■ 提出履歴"]
    cols = []
    for it in items[:30]:
        lines.append("・%s %s ✓" % (_fmt_period(it.get("period", "")),
                                    type_label(it.get("type", ""))))
    for it in items[:10]:                                # 最新 10 条带下载按钮
        cols.append({
            "title": "%s %s" % (_fmt_period(it.get("period", "")),
                                type_label(it.get("type", ""))),
            "text": (it.get("fileName") or " ")[:60] or " ",
            "actions": [{"label": "ダウンロード", "uri": _dl_link(base, it["s3Key"])}],
        })
    msgs = [{"type": "text", "text": "\n".join(lines)}]
    for i in range(0, len(cols), 10):
        msgs.append(line.carousel_message("提出履歴", cols[i:i + 10]))
    return msgs[:5]


def _hr_guard(uid):
    return None if business.is_hr(uid) else T("hr_only")


def _mark_unreg(has_line):
    return "" if has_line else "  " + T("line_unregistered")


def _hr_missing(text):
    period = business.normalize_period(text)
    only = business.infer_type(text, text)
    lines = ["■ 未提出（%s）" % _fmt_period(period)]
    if only:
        miss = business.missing(period, only)
        if not miss:
            return T("all_submitted")
        lines.append("【%s】" % type_label(only))
        for e in miss:
            lines.append("・%s（%s）%s" % (e.get("name", e.get("empId", "")),
                                         e.get("department", ""),
                                         _mark_unreg(e.get("lineUserId"))))
        return "\n".join(lines)
    mm = business.missing_all_types(period)
    if not mm:
        return T("all_submitted")
    for v in mm.values():
        e = v["emp"]
        labels = "、".join(type_label(t) for t in v["missing_types"])
        lines.append("・%s（%s）— 未: %s%s" % (e.get("name", e.get("empId", "")),
                                             e.get("department", ""), labels,
                                             _mark_unreg(v.get("linked"))))
    return "\n".join(lines)


ROSTER_CMDS_KW = ("一覧", "一览", "全員", "全员", "提出状況", "提交情况")
BULK_CMDS_KW = ("一括dl", "一括ダウンロード", "一括ＤＬ", "一括", "打包下载", "打包", "bulkdl", "zip")


def _is_roster_cmd(t):
    return any(k in t for k in ROSTER_CMDS_KW)


def _is_bulk_cmd(t):
    tl = t.lower()
    return any(k in tl for k in BULK_CMDS_KW)


def _emp_line(r):
    e = r["emp"]
    marks = " ".join("%s%s" % (type_label(t), "✓" if it else "✗")
                     for t, it in r["types"].items())
    tail = "" if r.get("linked") else "  " + T("line_unregistered")
    return "・%s（%s） %s%s" % (e.get("name", e.get("empId", "")),
                              e.get("department", ""), marks, tail)


def _hr_roster_messages(period):
    """未提出置顶 + 提出済后置，纯 ✓/✗ 状态，无下载链接（下载用「一括DL」）。
    人多时按 ~4500 字分多条，最多 5 条。"""
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

    # 按 ~4500 字分条
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
