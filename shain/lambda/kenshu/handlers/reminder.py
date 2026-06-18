"""开课前 1 小时提醒：定时扫描，给报名学员推送提醒（微信客服 / LINE 自动按平台路由）。

由 EventBridge 每 10 分钟触发。命中开课时间落在 [now+55min, now+65min] 的已发布课程，
对其报名学员发提醒，并把课程标记 remind1hSent 防重复。
发送统一走 messaging.send()，据 openid 前缀选择渠道（企业微信 / LINE push）。

⚠️ 微信客服限制：只能给「最近 48 小时内有发过消息」的用户主动推送（企业微信平台规则）；
   LINE push 无此限制，但计入 push 配额。超窗/失败均记日志。
"""
import logging
from datetime import timedelta

from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

from kenshu.common import business, config, db, i18n, messaging
from kenshu.common.timeutils import fmt_jst, now_utc, parse_iso

log = logging.getLogger()
log.setLevel(logging.INFO)


def handler(event, context):
    now = now_utc()
    lo, hi = now + timedelta(minutes=55), now + timedelta(minutes=65)
    sent = failed = 0

    for c in db.courses().scan().get("Items", []):
        if c.get("status") != "published" or not c.get("startTime"):
            continue
        try:
            start = parse_iso(c["startTime"])
        except Exception:  # noqa: BLE001
            continue
        if not (lo <= start <= hi) or c.get("remind1hSent"):
            continue

        # 幂等占位：抢到才发（防多实例/多次触发重复）
        try:
            db.courses().update_item(
                Key={"courseId": c["courseId"]},
                UpdateExpression="SET remind1hSent = :t",
                ConditionExpression="attribute_not_exists(remind1hSent) OR remind1hSent = :f",
                ExpressionAttributeValues={":t": True, ":f": False},
            )
        except ClientError:
            continue

        when = fmt_jst(c["startTime"])
        join = c.get("zoomJoinUrl") or "(see course details)"
        rows = db.enrollments().query(
            IndexName=config.ENROLLMENTS_GSI1,
            KeyConditionExpression=Key("courseId").eq(c["courseId"]),
        ).get("Items", [])
        for e in rows:
            if e.get("status") != "enrolled":
                continue
            lang = business.get_lang(business.get_student(e["openid"]))
            msg = i18n.T(lang, "reminder", title=c["title"], when=when, join=join)
            # 多平台扇出：主账号 + 已关联的各平台通道都推送
            for ch in business.get_channels(e["openid"]):
                r = messaging.send(ch, msg)
                if r.get("errcode") == 0:
                    sent += 1
                else:
                    failed += 1
                    log.warning("reminder send failed uid=%s: %s", ch, r)

    log.info("reminder tick: sent=%s failed=%s", sent, failed)
    return {"sent": sent, "failed": failed}
