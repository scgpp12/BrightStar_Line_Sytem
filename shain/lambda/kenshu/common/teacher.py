"""老师业务（openid 白名单）。通过文本命令在 agent 内操作，返回回复文本。

命令格式（中文）：
  建课 标题|开课时间|时长|容量|简介      例: 建课 Python入门|2026-06-10 14:00|90|20|零基础
  发布 课程名
  删课 课程名
  改课 课程名|容量=30                     (支持 标题/简介/容量/时长/开课时间)
  学员列表
  名单 课程名
  分组 课程名|组数=4      或   分组 课程名|每组=5
  老师帮助
"""
import uuid
from datetime import datetime

from boto3.dynamodb.conditions import Key

from . import db, zoom_client
from .timeutils import JST, fmt_jst, iso_utc, parse_iso, to_jst

HELP = (
    "🧑‍🏫 老师命令：\n"
    "建课 标题|开课时间|时长|容量|简介\n"
    "  例：建课 Python入门|2026-06-10 14:00|90|20|零基础\n"
    "发布 课程名\n删课 课程名\n改课 课程名|容量=30\n"
    "学员列表\n名单 课程名\n分组 课程名|组数=4（或 每组=5）"
)


def handle(text: str) -> str:
    parts = text.strip().split(maxsplit=1)
    cmd = parts[0]
    rest = parts[1] if len(parts) > 1 else ""
    if cmd == "建课":
        return create_course(rest)
    if cmd == "发布":
        return publish_course(rest)
    if cmd == "删课":
        return delete_course(rest)
    if cmd == "改课":
        return update_course(rest)
    if cmd == "学员列表":
        return list_students()
    if cmd == "名单":
        return list_enrollments(rest)
    if cmd == "分组":
        return random_groups(rest)
    return HELP


# ------------------------------- 课程 CRUD -------------------------------
def _find_course_by_name(name: str):
    name = (name or "").strip()
    courses = db.courses().scan().get("Items", [])
    return [c for c in courses if name and name in c.get("title", "")]


def _pick_one(name: str):
    """返回 (course, errmsg)。"""
    if not name.strip():
        return None, "请提供课程名。"
    matches = _find_course_by_name(name)
    if not matches:
        return None, f"没找到包含「{name}」的课程。"
    if len(matches) > 1:
        return None, "匹配到多门课：" + "、".join(c["title"] for c in matches[:5]) + "，请更具体。"
    return matches[0], None


def create_course(arg: str) -> str:
    fields = [f.strip() for f in arg.split("|")]
    if len(fields) < 4:
        return "格式：建课 标题|开课时间|时长|容量|简介\n例：建课 Python入门|2026-06-10 14:00|90|20|零基础"
    title, start_str, dur_str, cap_str = fields[0], fields[1], fields[2], fields[3]
    desc = fields[4] if len(fields) > 4 else ""
    try:
        dt_jst = datetime.strptime(start_str, "%Y-%m-%d %H:%M").replace(tzinfo=JST)
        start_iso = iso_utc(dt_jst)
    except ValueError:
        return "开课时间格式应为 YYYY-MM-DD HH:MM（JST），例：2026-06-10 14:00"
    try:
        duration, capacity = int(dur_str), int(cap_str)
    except ValueError:
        return "时长和容量必须是数字。"
    if capacity <= 0:
        return "容量必须为正整数。"

    course_id = str(uuid.uuid4())
    now = iso_utc()
    db.courses().put_item(
        Item={
            "courseId": course_id,
            "title": title,
            "description": desc,
            "startTime": start_iso,
            "durationMin": duration,
            "capacity": capacity,
            "enrolledCount": 0,
            "status": "draft",
            "createdAt": now,
            "updatedAt": now,
        }
    )
    return f"✅ 已创建草稿：{title}｜{fmt_jst(start_iso)}\n用「发布 {title}」创建 Zoom 会议并发布。"


def publish_course(name: str) -> str:
    course, err = _pick_one(name)
    if err:
        return err
    start_jst = to_jst(parse_iso(course["startTime"])).strftime("%Y-%m-%dT%H:%M:%S")
    meeting = zoom_client.create_meeting(
        course_id=course["courseId"],
        topic=course["title"],
        start_time_jst=start_jst,
        duration_min=int(course.get("durationMin", 60)),
    )
    db.courses().update_item(
        Key={"courseId": course["courseId"]},
        UpdateExpression=(
            "SET #s = :p, zoomMeetingId = :mid, zoomJoinUrl = :ju, "
            "zoomStartUrl = :su, updatedAt = :u"
        ),
        ExpressionAttributeNames={"#s": "status"},
        ExpressionAttributeValues={
            ":p": "published",
            ":mid": meeting["meetingId"],
            ":ju": meeting["joinUrl"],
            ":su": meeting["startUrl"],
            ":u": iso_utc(),
        },
    )
    stub = "（demo：模拟 Zoom 会议）" if meeting.get("isStub") else ""
    return (
        f"🚀 已发布：{course['title']}{stub}\n"
        f"主持人链接(仅你可见)：{meeting['startUrl']}\n"
        f"学员 Zoom：{meeting['joinUrl']}"
    )


def delete_course(name: str) -> str:
    course, err = _pick_one(name)
    if err:
        return err
    db.courses().delete_item(Key={"courseId": course["courseId"]})
    return f"🗑️ 已删除课程：{course['title']}"


def update_course(arg: str) -> str:
    seg = arg.split("|", 1)
    if len(seg) < 2:
        return "格式：改课 课程名|字段=值（字段：标题/简介/容量/时长/开课时间）"
    course, err = _pick_one(seg[0])
    if err:
        return err
    keymap = {
        "标题": ("title", str),
        "简介": ("description", str),
        "容量": ("capacity", int),
        "时长": ("durationMin", int),
    }
    sets, names, vals = [], {}, {":u": iso_utc()}
    i = 0
    for pair in seg[1].split(";"):
        if "=" not in pair:
            continue
        k, v = (x.strip() for x in pair.split("=", 1))
        if k == "开课时间":
            try:
                dt = datetime.strptime(v, "%Y-%m-%d %H:%M").replace(tzinfo=JST)
            except ValueError:
                return "开课时间格式应为 YYYY-MM-DD HH:MM"
            sets.append("startTime = :startTime")
            vals[":startTime"] = iso_utc(dt)
        elif k in keymap:
            field, caster = keymap[k]
            sets.append(f"#{field} = :v{i}")
            names[f"#{field}"] = field
            vals[f":v{i}"] = caster(v)
            i += 1
    if not sets:
        return "没有可更新的字段。"
    expr = "SET updatedAt = :u, " + ", ".join(sets)
    kwargs = {
        "Key": {"courseId": course["courseId"]},
        "UpdateExpression": expr,
        "ExpressionAttributeValues": vals,
    }
    if names:
        kwargs["ExpressionAttributeNames"] = names
    db.courses().update_item(**kwargs)
    return f"✏️ 已更新：{course['title']}"


# ------------------------------- 学员 / 名单 / 分组 -------------------------------
def list_students() -> str:
    items = db.students().scan().get("Items", [])
    active = [
        s for s in items
        if s.get("status") not in ("awaiting_name", "awaiting_lang")
        and not str(s.get("openid", "")).startswith("__")
        and not s.get("linkedTo")  # 跨平台已关联的别名记录，不重复计入名单
    ]
    if not active:
        return "暂无学员。"
    lines = [f"👥 学员（{len(active)}）："]
    for s in active[:50]:
        role = "老师" if s.get("role") == "teacher" else "学员"
        lines.append(f"· {s.get('name','(未填名)')}｜{role}")
    return "\n".join(lines)


def _course_enrolled(course_id):
    rows = db.enrollments().query(
        IndexName="GSI1", KeyConditionExpression=Key("courseId").eq(course_id)
    ).get("Items", [])
    return [e for e in rows if e.get("status") == "enrolled"]


def list_enrollments(name: str) -> str:
    course, err = _pick_one(name)
    if err:
        return err
    rows = _course_enrolled(course["courseId"])
    if not rows:
        return f"「{course['title']}」暂无报名。"
    lines = [f"📋 {course['title']} 报名名单（{len(rows)}）："]
    for e in rows:
        s = db.students().get_item(Key={"openid": e["openid"]}).get("Item")
        lines.append(f"· {(s or {}).get('name', e['openid'][:8])}")
    return "\n".join(lines)


def _shuffle(seq):
    keyed = [(uuid.uuid4().hex, x) for x in seq]
    keyed.sort(key=lambda kv: kv[0])
    return [x for _, x in keyed]


def random_groups(arg: str) -> str:
    seg = arg.split("|", 1)
    course, err = _pick_one(seg[0])
    if err:
        return err
    group_count = per_group = None
    if len(seg) > 1:
        spec = seg[1]
        if "组数=" in spec:
            group_count = int(spec.split("组数=", 1)[1].split(";")[0])
        elif "每组=" in spec:
            per_group = int(spec.split("每组=", 1)[1].split(";")[0])
    if not group_count and not per_group:
        return "请指定分组方式：分组 课程名|组数=4 或 分组 课程名|每组=5"

    members = [e["openid"] for e in _course_enrolled(course["courseId"])]
    if not members:
        return f"「{course['title']}」暂无报名学员。"
    members = _shuffle(members)
    n = len(members)
    k = max(1, min(group_count, n)) if group_count else max(1, (n + per_group - 1) // per_group)

    base, r = divmod(n, k)
    chunks, idx = [], 0
    for i in range(k):
        size = base + (1 if i < r else 0)
        chunks.append(members[idx : idx + size])
        idx += size

    table = db.groups()
    old = table.query(KeyConditionExpression=Key("courseId").eq(course["courseId"])).get("Items", [])
    with table.batch_writer() as bw:
        for o in old:
            bw.delete_item(Key={"courseId": course["courseId"], "groupId": o["groupId"]})
    now = iso_utc()
    lines = [f"🎲 {course['title']} 随机分组（{k} 组）："]
    with table.batch_writer() as bw:
        for i, chunk in enumerate(chunks, 1):
            gid = f"G{i}"
            bw.put_item(
                Item={
                    "courseId": course["courseId"],
                    "groupId": gid,
                    "groupName": f"第 {i} 组",
                    "members": chunk,
                    "createdAt": now,
                }
            )
            names = []
            for oid in chunk:
                s = db.students().get_item(Key={"openid": oid}).get("Item")
                names.append((s or {}).get("name", oid[:6]))
            lines.append(f"第 {i} 组：{'、'.join(names)}")
    return "\n".join(lines)
