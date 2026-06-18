"""Zoom 集成。

⚠️ DEMO 说明 ⚠️
本次为 demo，未提供 Zoom 凭证（account_id / client_id / client_secret）。
默认 ZOOM_ENABLED=false → create_meeting() 返回【假数据】空壳，
但函数签名、返回结构与真实 Zoom 一致，日后接入只需：
  1) 在 Secrets Manager 写入 secret（名字见 config.ZOOM_SECRET_NAME），
     JSON: {"account_id": "...", "client_id": "...", "client_secret": "..."}
  2) 把环境变量 ZOOM_ENABLED 设为 "true"
即可切换为真实调用，业务代码无需改动。

真实流程（已写好，被 ZOOM_ENABLED 开关保护）：
  - Server-to-Server OAuth 取 token（account_credentials grant）
  - POST /users/{userId}/meetings  type=2(scheduled), timezone=Asia/Tokyo
  - 返回 id / join_url / start_url（start_url 仅老师可见）
"""
import base64
import json
import urllib.parse
import urllib.request
import uuid

from . import config


def _stub_meeting(course_id: str, topic: str) -> dict:
    """空壳：返回结构与真实 Zoom 一致的假会议。"""
    fake = uuid.uuid4().hex[:10]
    return {
        "meetingId": f"DEMO-{fake}",
        "joinUrl": f"https://zoom.example.com/j/{fake}?demo=1",
        "startUrl": f"https://zoom.example.com/s/{fake}?role=host&demo=1",
        "isStub": True,  # 前端/日志可据此标识为 demo
    }


# --------------------------- 真实实现（上线启用） ---------------------------
def _get_zoom_credentials() -> dict:
    import boto3

    sm = boto3.client("secretsmanager")
    resp = sm.get_secret_value(SecretId=config.ZOOM_SECRET_NAME)
    return json.loads(resp["SecretString"])


def _get_access_token(creds: dict) -> str:
    basic = base64.b64encode(
        f"{creds['client_id']}:{creds['client_secret']}".encode()
    ).decode()
    data = urllib.parse.urlencode(
        {"grant_type": "account_credentials", "account_id": creds["account_id"]}
    ).encode()
    req = urllib.request.Request(
        "https://zoom.us/oauth/token",
        data=data,
        headers={
            "Authorization": f"Basic {basic}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as r:  # noqa: S310
        return json.loads(r.read())["access_token"]


def _create_real_meeting(token: str, topic: str, start_time_jst: str, duration_min: int) -> dict:
    payload = json.dumps(
        {
            "topic": topic,
            "type": 2,  # scheduled meeting（一次性）
            "start_time": start_time_jst,  # 形如 2026-06-10T14:00:00
            "duration": duration_min,
            "timezone": config.TIMEZONE,  # Asia/Tokyo
            "settings": {"join_before_host": False, "waiting_room": True},
        }
    ).encode()
    url = f"https://api.zoom.us/v2/users/{config.ZOOM_USER_ID}/meetings"
    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as r:  # noqa: S310
        m = json.loads(r.read())
    return {
        "meetingId": str(m["id"]),
        "joinUrl": m["join_url"],
        "startUrl": m["start_url"],
        "isStub": False,
    }


# ------------------------------- 公共入口 -------------------------------
def create_meeting(course_id: str, topic: str, start_time_jst: str, duration_min: int) -> dict:
    """创建一次性会议。返回 {meetingId, joinUrl, startUrl, isStub}。

    start_time_jst: JST 本地时间字符串，形如 "2026-06-10T14:00:00"
    """
    if not config.ZOOM_ENABLED:
        return _stub_meeting(course_id, topic)
    creds = _get_zoom_credentials()
    token = _get_access_token(creds)
    return _create_real_meeting(token, topic, start_time_jst, duration_min)
