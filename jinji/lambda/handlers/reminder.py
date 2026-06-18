"""提醒 Lambda。

触发来源：
- EventBridge 定时（cron）：detail 为空 → 当月、所有必交类型
- 人事手动（webhook 异步 invoke）：payload {trigger:'manual', period, type?}

仅向未提交者 push；已提交者自动排除（靠 submissions 差集）。
"""
from common import business, messaging
from common.i18n import T, type_label


def handler(event, context):
    event = event or {}
    period = event.get("period") or business.current_period()
    only = event.get("type")

    if only:
        targets = {e["empId"]: {"emp": e, "missing_types": [only],
                                "lineUserId": e.get("lineUserId")}
                   for e in business.missing(period, only)}
    else:
        targets = business.missing_all_types(period)

    sent = 0
    skipped = 0
    pj = "%s-%s" % (period[:4], period[4:])
    for v in targets.values():
        luid = v.get("lineUserId")
        if not luid:                                     # 未绑定 LINE → 无法推送（人事手动联系）
            skipped += 1
            continue
        labels = "、".join(type_label(t) for t in v["missing_types"])
        msg = T("remind_text", period=pj, labels=labels)
        r = messaging.send(luid, msg)
        if r.get("errcode") == 0:
            sent += 1
        else:
            print("push fail:", luid, r)
    print("reminder skipped(unlinked)=%d" % skipped)

    print("reminder sent=%d period=%s type=%s" % (sent, period, only or "all"))
    return {"sent": sent, "period": period, "type": only or "all"}
