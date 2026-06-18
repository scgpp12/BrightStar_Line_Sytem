"""学员业务逻辑（基于微信 openid）。每个函数返回要回复给用户的文案。

双语：每个学员在注册时选择语言（默认日语），存于档案 lang 字段。
面向学员的所有回复均按该语言输出（见 i18n.py）。
"""
import uuid as _uuid
from datetime import timedelta

from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

from . import config, db
from .i18n import DEFAULT_LANG, T
from .timeutils import fmt_jst_span, iso_utc, now_utc, parse_iso

NUM_TO_INTENT = {"1": "list_courses", "2": "my_courses", "3": "next_class"}


def get_lang(student) -> str:
    """从学员档案取语言；缺省日语。"""
    if isinstance(student, dict):
        return student.get("lang") or DEFAULT_LANG
    return DEFAULT_LANG


def menu(lang: str = DEFAULT_LANG) -> str:
    return T(lang, "menu")


# ------------------------------- 学员档案 -------------------------------
def get_student(openid: str):
    return db.students().get_item(Key={"openid": openid}).get("Item")


def start_registration(openid: str) -> str:
    """新用户：建占位档案，先让其选择语言（默认日语）。附跨平台关联提示。"""
    db.students().put_item(
        Item={
            "openid": openid,
            "status": "awaiting_lang",
            "role": "student",
            "lang": DEFAULT_LANG,
            "createdAt": iso_utc(),
        }
    )
    return T(DEFAULT_LANG, "lang_choose") + "\n\n" + T(DEFAULT_LANG, "link_hint")


def _set_lang_field(openid: str, lang: str):
    db.students().update_item(
        Key={"openid": openid},
        UpdateExpression="SET lang = :l",
        ExpressionAttributeValues={":l": lang},
    )


def set_language(openid: str, text: str) -> str:
    """注册第二步：识别语言选择 → 进入「等待姓名」。

    无法识别时默认日语；若这句话本身就是合法姓名，则直接完成注册（日语）。
    """
    from .i18n import norm_lang
    lang = norm_lang(text)
    if lang is None:
        # 默认日语；若用户直接发了姓名，顺势完成注册
        _set_lang_field(openid, DEFAULT_LANG)
        if valid_name(text):
            db.students().update_item(
                Key={"openid": openid},
                UpdateExpression="SET #s = :a",
                ExpressionAttributeNames={"#s": "status"},
                ExpressionAttributeValues={":a": "awaiting_name"},
            )
            return complete_registration(openid, text)
        db.students().update_item(
            Key={"openid": openid},
            UpdateExpression="SET #s = :a",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={":a": "awaiting_name"},
        )
        return T(DEFAULT_LANG, "welcome_name")
    db.students().update_item(
        Key={"openid": openid},
        UpdateExpression="SET lang = :l, #s = :a",
        ExpressionAttributeNames={"#s": "status"},
        ExpressionAttributeValues={":l": lang, ":a": "awaiting_name"},
    )
    return T(lang, "welcome_name")


def switch_language(openid: str, lang: str) -> str:
    """已注册学员切换语言。"""
    _set_lang_field(openid, lang)
    return T(lang, "lang_switched") + menu(lang)


# ------------------------------- AI 问答开关 -------------------------------
def get_ai_mode(student) -> bool:
    """学员是否开启了「人工智能回复」。"""
    return bool(student.get("aiMode")) if isinstance(student, dict) else False


def set_ai_mode(openid: str, on: bool):
    db.students().update_item(
        Key={"openid": openid},
        UpdateExpression="SET aiMode = :v",
        ExpressionAttributeValues={":v": bool(on)},
    )


_LOGINCODE_PK = "__logincode__"  # 反向索引：登录码 -> openid


def _expired(exp_iso) -> bool:
    """exp_iso 为空视为未过期（兼容历史无过期码）；否则按 UTC 比较。"""
    if not exp_iso:
        return False
    try:
        return parse_iso(exp_iso) < now_utc()
    except Exception:  # noqa: BLE001
        return False


def get_or_create_login_code(openid: str):
    """返回学员的有效网页登录码（无/已过期则重新生成）。未注册返回 None。

    有效期 config.LOGIN_CODE_TTL_DAYS 天（默认 7）。过期会换发新码、删除旧反向索引。
    """
    s = get_student(openid)
    if not s or s.get("status") != "active":
        return None
    code = s.get("loginCode")
    exp = s.get("loginCodeExp")
    if code and not _expired(exp):
        return code
    # 换发：删旧码反向索引（如有），生成新码 + 新有效期
    if code:
        db.students().delete_item(Key={"openid": _LOGINCODE_PK + code})
    code = _uuid.uuid4().hex[:8].upper()
    new_exp = iso_utc(now_utc() + timedelta(days=config.LOGIN_CODE_TTL_DAYS))
    db.students().update_item(
        Key={"openid": openid},
        UpdateExpression="SET loginCode = :c, loginCodeExp = :e",
        ExpressionAttributeValues={":c": code, ":e": new_exp},
    )
    db.students().put_item(
        Item={"openid": _LOGINCODE_PK + code, "ref": openid, "exp": new_exp}
    )
    return code


def find_by_login_code(code: str):
    """登录码 → 学员记录；无效或已过期返回 None。"""
    code = (code or "").strip().upper()
    if not code:
        return None
    m = db.students().get_item(Key={"openid": _LOGINCODE_PK + code}).get("Item")
    if not m or not m.get("ref"):
        return None
    if _expired(m.get("exp")):
        return None
    return get_student(m["ref"])


# ------------------------------- 跨平台账号关联 -------------------------------
def resolve_openid(openid: str) -> str:
    """身份解析：若该 openid 是已关联的「次账号」，返回其指向的主账号 openid；否则原样返回。

    LINE / 微信等不同平台的同一个人，可用「绑定 登录码」把次账号并到主账号；
    之后该次账号的所有消息都按主账号处理（注册/课程/老师权限/登录码全部共用）。
    """
    s = db.students().get_item(Key={"openid": openid}).get("Item")
    if s and s.get("linkedTo"):
        return s["linkedTo"]
    return openid


def get_channels(openid: str) -> list:
    """返回主账号的全部推送通道 openid（自身 + 已关联的各平台次账号），去重。

    供开课提醒「多平台同时触达」：一个人若微信+LINE都绑了，两边都收到提醒。
    """
    s = get_student(openid) or {}
    chans = [openid] + [c for c in (s.get("channels") or []) if c != openid]
    seen, out = set(), []
    for c in chans:
        if c not in seen:
            seen.add(c)
            out.append(c)
    return out


def link_by_login_code(openid: str, code: str) -> str:
    """把当前(次)账号 openid 关联到「登录码」对应的主账号。返回回复文案。"""
    cur = get_student(openid)
    lang = get_lang(cur)
    code = (code or "").strip()
    if not code:
        return T(lang, "link_need_code")
    canonical = find_by_login_code(code)
    if not canonical:
        return T(lang, "link_bad_code")
    canonical_openid = canonical["openid"]
    if canonical_openid == openid:
        return T(lang, "link_self")
    # 次账号 → 别名记录，指向主账号
    db.students().put_item(Item={
        "openid": openid,
        "linkedTo": canonical_openid,
        "status": "linked",
        "createdAt": (cur or {}).get("createdAt") or iso_utc(),
    })
    # 主账号登记该通道（供提醒多平台触达），去重写回
    chans = [c for c in (canonical.get("channels") or []) if c != openid]
    chans.append(openid)
    db.students().update_item(
        Key={"openid": canonical_openid},
        UpdateExpression="SET channels = :c",
        ExpressionAttributeValues={":c": chans},
    )
    return T(lang, "link_ok", name=canonical.get("name") or "") + menu(get_lang(canonical))


def promote_teacher(openid: str):
    """把已注册学员升级为老师（兼具学员身份）。"""
    db.students().update_item(
        Key={"openid": openid},
        UpdateExpression="SET #r = :t",
        ExpressionAttributeNames={"#r": "role"},
        ExpressionAttributeValues={":t": "teacher"},
    )


# 不能当姓名的问候语/指令词（中日）
_NAME_BLOCKLIST = {
    "你好", "您好", "hi", "hello", "在吗", "注册", "重新注册", "报名", "取消",
    "课程", "课程列表", "有哪些课", "下节课", "我的课", "我的课程", "菜单",
    "帮助", "help", "老师", "老师认证", "老师帮助", "改名", "语言",
    "こんにちは", "はい", "いいえ", "メニュー", "ヘルプ", "申込", "キャンセル",
    "言語", "名前変更", "次の講座", "講座一覧",
}


def valid_name(name: str) -> bool:
    name = (name or "").strip()
    if not (2 <= len(name) <= 20):
        return False
    if name.lower() in _NAME_BLOCKLIST or name in _NAME_BLOCKLIST:
        return False
    if name.isdigit():
        return False
    return True


def complete_registration(openid: str, name: str) -> str:
    name = (name or "").strip()
    s = get_student(openid)
    lang = get_lang(s)
    if not valid_name(name):
        return T(lang, "name_invalid")
    db.students().update_item(
        Key={"openid": openid},
        UpdateExpression="SET #n = :n, #s = :a",
        ExpressionAttributeNames={"#n": "name", "#s": "status"},
        ExpressionAttributeValues={":n": name, ":a": "active"},
    )
    return T(lang, "reg_success", name=name) + menu(lang)


def rename(openid: str, new_name: str) -> str:
    new_name = (new_name or "").strip()
    lang = get_lang(get_student(openid))
    if not valid_name(new_name):
        return T(lang, "rename_format")
    db.students().update_item(
        Key={"openid": openid},
        UpdateExpression="SET #n = :n",
        ExpressionAttributeNames={"#n": "name"},
        ExpressionAttributeValues={":n": new_name},
    )
    return T(lang, "rename_ok", name=new_name)


# ------------------------------- 课程 -------------------------------
def _published_courses():
    items = [c for c in db.courses().scan().get("Items", []) if c.get("status") == "published"]
    items.sort(key=lambda c: c.get("startTime", ""))
    return items


def list_courses(lang: str = DEFAULT_LANG) -> str:
    courses = _published_courses()
    if not courses:
        return T(lang, "courses_none")
    lines = [T(lang, "courses_header")]
    for i, c in enumerate(courses, 1):
        left = int(c.get("capacity", 0)) - int(c.get("enrolledCount", 0))
        span = fmt_jst_span(c.get("startTime", ""), c.get("durationMin"), lang)
        lines.append(T(lang, "courses_line", i=i, title=c["title"], span=span, left=left))
    lines.append(T(lang, "courses_enroll_hint"))
    return "\n".join(lines)


def _find_published_by_kw(kw: str):
    kw = (kw or "").strip()
    courses = _published_courses()
    if not kw:
        return courses, None
    matches = [c for c in courses if kw in c.get("title", "")]
    return matches, kw


# ------------------------------- 报名 -------------------------------
def enroll(openid: str, course_kw: str, lang: str = DEFAULT_LANG) -> str:
    matches, kw = _find_published_by_kw(course_kw)
    if not kw:
        return T(lang, "enroll_need_name") + list_courses(lang)
    if not matches:
        return T(lang, "enroll_not_found", kw=kw) + list_courses(lang)
    if len(matches) > 1:
        names = "、".join(c["title"] for c in matches[:5])
        return T(lang, "enroll_multi", names=names)

    course = matches[0]
    course_id = course["courseId"]

    ex = db.enrollments().get_item(Key={"openid": openid, "courseId": course_id}).get("Item")
    if ex and ex.get("status") == "enrolled":
        return T(lang, "enroll_already", title=course["title"])

    try:
        db.courses().update_item(
            Key={"courseId": course_id},
            UpdateExpression="SET enrolledCount = enrolledCount + :one",
            ConditionExpression="enrolledCount < #cap",  # capacity 是 DynamoDB 保留字
            ExpressionAttributeNames={"#cap": "capacity"},
            ExpressionAttributeValues={":one": 1},
        )
    except ClientError as e:
        if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
            return T(lang, "enroll_full", title=course["title"])
        raise

    db.enrollments().put_item(
        Item={
            "openid": openid,
            "courseId": course_id,
            "enrolledAt": iso_utc(),
            "status": "enrolled",
        }
    )
    join = course.get("zoomJoinUrl") or T(lang, "zoom_tbd")
    span = fmt_jst_span(course.get("startTime", ""), course.get("durationMin"), lang)
    return T(lang, "enroll_ok", title=course["title"], span=span, join=join)


# ------------------------------- 取消 -------------------------------
def cancel(openid: str, course_kw: str, lang: str = DEFAULT_LANG) -> str:
    kw = (course_kw or "").strip()
    mine = _my_enrolled(openid)
    if not mine:
        return T(lang, "cancel_none")
    if kw:
        mine = [x for x in mine if kw in x["course"].get("title", "")]
    if not mine:
        return T(lang, "cancel_not_found", kw=kw)
    if len(mine) > 1:
        names = "、".join(x["course"]["title"] for x in mine[:5])
        return T(lang, "cancel_multi", names=names)

    item = mine[0]
    course = item["course"]
    course_id = course["courseId"]

    if course.get("startTime"):
        hours_left = (parse_iso(course["startTime"]) - now_utc()).total_seconds() / 3600.0
        if hours_left < config.CANCEL_DEADLINE_HOURS:
            return T(lang, "cancel_deadline", hours=config.CANCEL_DEADLINE_HOURS)

    db.enrollments().update_item(
        Key={"openid": openid, "courseId": course_id},
        UpdateExpression="SET #s = :c",
        ExpressionAttributeNames={"#s": "status"},
        ExpressionAttributeValues={":c": "cancelled"},
    )
    try:
        db.courses().update_item(
            Key={"courseId": course_id},
            UpdateExpression="SET enrolledCount = enrolledCount - :one",
            ConditionExpression="enrolledCount > :zero",
            ExpressionAttributeValues={":one": 1, ":zero": 0},
        )
    except ClientError as e:
        if e.response["Error"]["Code"] != "ConditionalCheckFailedException":
            raise
    return T(lang, "cancel_ok", title=course["title"])


# ------------------------------- 我的课/下节课 -------------------------------
def _my_enrolled(openid: str):
    rows = db.enrollments().query(
        KeyConditionExpression=Key("openid").eq(openid)
    ).get("Items", [])
    out = []
    for e in rows:
        if e.get("status") != "enrolled":
            continue
        c = db.courses().get_item(Key={"courseId": e["courseId"]}).get("Item")
        if c:
            out.append({"enrollment": e, "course": c})
    out.sort(key=lambda x: x["course"].get("startTime", ""))
    return out


def my_courses(openid: str, lang: str = DEFAULT_LANG) -> str:
    mine = _my_enrolled(openid)
    if not mine:
        return T(lang, "my_none")
    lines = [T(lang, "my_header")]
    for i, x in enumerate(mine, 1):
        c = x["course"]
        span = fmt_jst_span(c.get("startTime", ""), c.get("durationMin"), lang)
        lines.append(T(lang, "my_line", i=i, title=c["title"], span=span))
    return "\n".join(lines)


def next_class(openid: str, lang: str = DEFAULT_LANG) -> str:
    mine = _my_enrolled(openid)
    upcoming = [x for x in mine if x["course"].get("startTime") and parse_iso(x["course"]["startTime"]) > now_utc()]
    if not upcoming:
        return T(lang, "next_none")
    c = upcoming[0]["course"]
    join = c.get("zoomJoinUrl") or T(lang, "zoom_tbd")
    span = fmt_jst_span(c.get("startTime", ""), c.get("durationMin"), lang)
    return T(lang, "next_ok", title=c["title"], span=span, join=join)
