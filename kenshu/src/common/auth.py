"""老师身份判定（基于微信 openid）。

老师 = openid 在白名单 (TEACHER_OPENIDS) 中，或 Students 表里 role == 'teacher'。
"""
from . import config, db


def is_teacher(openid: str) -> bool:
    if openid in config.TEACHER_OPENIDS:
        return True
    item = db.students().get_item(Key={"openid": openid}).get("Item")
    return bool(item and item.get("role") == "teacher")
