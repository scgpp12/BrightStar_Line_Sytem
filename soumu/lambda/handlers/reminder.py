"""提醒 Lambda。

触发来源：
- EventBridge 定时（cron）：detail 为空 → 当月、所有必交类型
- 人事手动（webhook 异步 invoke）：payload {trigger:'manual', period, type?}

仅向未提交者 push；已提交者自动排除（靠 submissions 差集）。
"""
from common import assist, business, config, line
from common.i18n import T, type_label


def handler(event, context):
    event = event or {}

    # 一斉送信（管理者が総務webhookから起動。社員botのtokenで全社員へ）
    if event.get("trigger") == "broadcast":
        text = (event.get("text") or "").strip()
        by = event.get("by") or ""
        if not text:
            return {"sent": 0}
        targets = business.broadcast_targets()
        push_tok = config.push_token()
        tinfo = []
        for p in business.roster_people():
            if p.get("lineUserId") in targets:
                tinfo.append({"uid": p["lineUserId"], "eid": p.get("empId", ""),
                              "name": p.get("name", "")})
        bid = business.batch_create("一斉送信", by, tinfo, text)
        msg = assist.quick_reply(text + "\n\n" + T("confirm_hint"),
                                 [(T("confirm_btn"), "確認 %s" % bid)])
        sent = fail = 0
        for luid in targets:
            r = line.push_message(luid, msg, token=push_tok)
            if r.get("errcode") == 0:
                sent += 1
            else:
                fail += 1
                print("bcast push fail:", luid, r)
        skipped = sum(1 for p in business.roster_people()
                      if not p.get("lineUserId") or p.get("blocked"))
        print("broadcast sent=%d fail=%d skipped=%d by=%s" % (sent, fail, skipped, by))
        if by:                                        # 実行結果を発起人（総務チャネル）へ
            line.push(by, T("bcast_report", sent=sent, fail=fail, skipped=skipped)
                      + T("batch_report_id", bid=bid))
        return {"sent": sent, "fail": fail, "skipped": skipped}

    # 単発通知（総務→本人。社員botのtokenで届ける）
    if event.get("trigger") == "notify":
        to, text = event.get("to"), event.get("text") or ""
        if to and text:
            r = line.push(to, text, token=config.push_token())
            print("notify:", to, r)
        return {"notified": bool(to and text)}

    # ポーラー：期限到来の「催促予約」を実行（10分間隔）
    if event.get("trigger") == "poll":
        import time
        due = business.bookings_due(int(time.time()))
        for b in due:
            try:
                res = _send_reminders(b.get("period") or business.current_period(), None)
                business.booking_mark(b["bookingId"], "sent")
                print("booking %s executed: %s" % (b["bookingId"], res))
            except Exception as e:  # noqa: BLE001
                print("booking %s failed: %r" % (b.get("bookingId"), e))
        return {"polled": len(due)}

    period = event.get("period") or business.current_period()
    only = event.get("type")
    return _send_reminders(period, only, event.get("by", ""))


def _send_reminders(period, only, by=""):

    if only:
        targets = {e["empId"]: {"emp": e, "missing_types": [only],
                                "lineUserId": e.get("lineUserId")}
                   for e in business.missing(period, only)}
    else:
        targets = business.missing_all_types(period)

    # リマインドは「社員アシスタント」のbotから届ける（PUSH_TOKEN_PARAM=社員token）。
    # 未設定なら従来どおり自channelのtoken。
    push_tok = config.push_token()

    sent = 0
    skipped = 0
    pj = "%s-%s" % (period[:4], period[4:])
    tinfo = [{"uid": v.get("lineUserId"), "eid": eid,
              "name": (v.get("emp") or {}).get("name", "")}
             for eid, v in targets.items() if v.get("lineUserId")]
    bid = business.batch_create("催促", by, tinfo, "催促 %s" % pj) if tinfo else None
    for v in targets.values():
        luid = v.get("lineUserId")
        if not luid:                                     # 未绑定 LINE → 无法推送（総務手动联系）
            skipped += 1
            continue
        labels = "、".join(type_label(t) for t in v["missing_types"])
        msg = T("remind_text", period=pj, labels=labels)
        m = assist.quick_reply(msg + "\n\n" + T("confirm_hint"),
                               [(T("confirm_btn"), "確認 %s" % bid)]) if bid else None
        r = line.push_message(luid, m, token=push_tok) if m else line.push(luid, msg, token=push_tok)
        if r.get("errcode") == 0:
            sent += 1
        else:
            print("push fail:", luid, r)
    print("reminder skipped(unlinked)=%d" % skipped)

    print("reminder sent=%d period=%s type=%s bid=%s" % (sent, period, only or "all", bid))
    if by and bid:
        line.push(by, T("batch_report_id", bid=bid).strip())
    return {"sent": sent, "period": period, "type": only or "all", "bid": bid}
