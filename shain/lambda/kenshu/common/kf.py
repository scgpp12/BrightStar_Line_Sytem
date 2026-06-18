"""微信客服(kf) 接入：经中转服务器调企业微信 kf API。

Lambda → relay(POST /kf/xxx, 带 _corp_id/_secret/_method) → 企业微信 /cgi-bin/kf/xxx
中转服务器 IP 在企业可信IP白名单内，故 Lambda 自身 IP 不受限。

回调只收到「有新消息」事件(含 token+open_kfid)，需主动 sync_msg 拉取，再 send_msg 回复。
游标 next_cursor 持久化在 Students 表的保留键里，避免重复处理。
"""
import json
import logging
import time
import urllib.request

from botocore.exceptions import ClientError

from . import config, db

log = logging.getLogger()

_CURSOR_PK = "__kfcursor__"  # Students 表保留键前缀
_MSG_PK = "__kfmsg__"        # 已处理 msgid 去重键前缀


def claim_msgid(msgid: str) -> bool:
    """首次见到该 msgid 返回 True；重复(并发或重试)返回 False。条件写入保证幂等。"""
    if not msgid:
        return True
    try:
        db.students().put_item(
            Item={"openid": _MSG_PK + msgid, "ts": int(time.time())},
            ConditionExpression="attribute_not_exists(openid)",
        )
        return True
    except ClientError as e:
        if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
            return False
        raise


def _relay(api_path: str, payload: dict = None, method: str = "POST") -> dict:
    body = dict(payload or {})
    body["_corp_id"] = config.WECOM_CORP_ID
    body["_secret"] = config.WECOM_SECRET
    body["_method"] = method
    data = json.dumps(body).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if config.WECOM_RELAY_AUTH:
        headers["X-Relay-Auth"] = config.WECOM_RELAY_AUTH
    req = urllib.request.Request(
        config.WECOM_RELAY_URL.rstrip("/") + api_path,
        data=data,
        headers=headers,
        method="POST",
    )
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=15) as r:  # noqa: S310
            out = json.loads(r.read().decode("utf-8"))
        log.info("kf relay %s: %.0fms", api_path, (time.time() - t0) * 1000)
        return out
    except Exception as e:  # noqa: BLE001
        log.error("kf relay %s error after %.0fms: %s", api_path, (time.time() - t0) * 1000, e)
        return {"errcode": -1, "errmsg": str(e)}


# ------------------------------- 游标 -------------------------------
def _cursor_key(open_kfid: str) -> str:
    return _CURSOR_PK + open_kfid


def get_cursor(open_kfid: str) -> str:
    item = db.students().get_item(Key={"openid": _cursor_key(open_kfid)}).get("Item")
    return (item or {}).get("cursor", "")


def set_cursor(open_kfid: str, cursor: str):
    db.students().put_item(Item={"openid": _cursor_key(open_kfid), "cursor": cursor})


# ------------------------------- API -------------------------------
def sync_msg(token: str, cursor: str, open_kfid: str, limit: int = 100) -> dict:
    payload = {"token": token, "limit": limit, "open_kfid": open_kfid}
    if cursor:
        payload["cursor"] = cursor
    return _relay("/kf/sync_msg", payload)


def send_text(open_kfid: str, external_userid: str, text: str) -> dict:
    payload = {
        "touser": external_userid,
        "open_kfid": open_kfid,
        "msgtype": "text",
        "text": {"content": text},
    }
    return _relay("/kf/send_msg", payload)
