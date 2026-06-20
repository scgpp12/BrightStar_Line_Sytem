"""业务逻辑。

两张身份表：
- roster（花名册，主数据）：empId → name/department/role/lineUserId。人事维护，决定"谁要交"。
- employees（LINE 绑定/会话）：userId(line:xxx) → status/empId/name。LINE 登入只是"激活"花名册中的人。

员工初次登入：只输入姓名 → 在花名册按姓名匹配 → 绑定该 empId（部门取自花名册）。
submissions 仍以 lineUserId 为键；花名册的人通过 roster.lineUserId 关联其提交。
period 形如 '202606'。
"""
import calendar
import re
import unicodedata
from datetime import datetime, timezone

from boto3.dynamodb.conditions import Key

from . import config, db, i18n, jpholiday, s3util, xlsx

# 内容チェック対象セル
PERIOD_CELL = {"kintai": "B5", "commute": "A1"}   # 年月
NAME_CELL = {"kintai": "B3"}                       # 氏名（勤務表のみ）
_WD = "月火水木金土日"

# ---------------- 时间 ----------------

def current_period():
    return datetime.now().strftime("%Y%m")


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def normalize_period(text):
    """从用户文本里抽月份；'6月'/'202606'/'2026-06'/'[202606]' → '202606'，否则当月。"""
    import re
    if not text:
        return current_period()
    m = re.search(r"(20\d{2})\D?(\d{1,2})", text)
    if m:
        return "%s%02d" % (m.group(1), int(m.group(2)))
    m = re.search(r"(\d{1,2})\s*月", text)
    if m:
        return current_period()[:4] + "%02d" % int(m.group(1))
    return current_period()


def _strip_prefix(uid):
    from .line import USER_PREFIX
    return uid[len(USER_PREFIX):] if uid.startswith(USER_PREFIX) else uid


# ---------------- 花名册（主数据） ----------------

def roster_scan():
    items, kwargs = [], {}
    while True:
        r = db.roster().scan(**kwargs)
        items += r.get("Items", [])
        if "LastEvaluatedKey" not in r:
            break
        kwargs["ExclusiveStartKey"] = r["LastEvaluatedKey"]
    return sorted(items, key=lambda x: x.get("empId", ""))


def roster_get(emp_id):
    return db.roster().get_item(Key={"empId": emp_id}).get("Item")


def roster_find_by_name(name):
    name = (name or "").strip()
    return [r for r in roster_scan() if (r.get("name") or "").strip() == name]


def _name_variants(item):
    """本名 + 別名/読み(aliases) の正規化集合。中文(簡/繁)・かな・ハングル・ローマ字対応。"""
    vals = [item.get("name")]
    al = item.get("aliases")
    if al:
        vals += re.split(r"[,，、|;；/\s　]+", al)
    return {_norm_name(v) for v in vals if v}


def roster_find_by_dept_name(dept, name):
    """部门+姓名 同时匹配（去空白／表記ゆれ吸収／別名対応）。重名靠部门区分。"""
    dn, nn = _norm_name(dept), _norm_name(name)
    return [r for r in roster_scan()
            if nn in _name_variants(r) and _norm_name(r.get("department")) == dn]


def _parse_dept_name(text):
    """'部门 姓名' / '部门 姓名 E001' / 'E001' → 匹配的花名册条目列表。"""
    toks = [t for t in re.split(r"[\s　]+", (text or "").strip()) if t]
    if not toks:
        return None  # 形式不符
    if len(toks) == 1:
        if re.match(r"^[Ee]\d+$", toks[0]):
            r = roster_get(toks[0].upper())
            return [r] if r else []
        return None  # 单词且非编号 → 不当作认证输入
    if re.match(r"^[Ee]\d+$", toks[-1]):
        r = roster_get(toks[-1].upper())
        return [r] if r else []
    return roster_find_by_dept_name(toks[0], " ".join(toks[1:]))


def roster_people():
    """需要提交的对象：花名册全员（含人事；如需排除 hr 在此过滤）。"""
    return roster_scan()


def _next_emp_id():
    mx = 0
    for r in roster_scan():
        eid = r.get("empId", "")
        if eid.startswith("E") and eid[1:].isdigit():
            mx = max(mx, int(eid[1:]))
    return "E%03d" % (mx + 1)


# 角色规范化（含中日别名）。可选值：employee / hr / teacher(讲师) / sales(营业)
_ROLE_ALIAS = {
    "employee": "employee", "社員": "employee", "员工": "employee", "社员": "employee",
    "hr": "hr", "人事": "hr",
    "teacher": "teacher", "講師": "teacher", "讲师": "teacher", "教員": "teacher", "教师": "teacher",
    "sales": "sales", "営業": "sales", "营业": "sales", "営業部": "sales",
}


def norm_role(role):
    return _ROLE_ALIAS.get((role or "").strip().lower(), _ROLE_ALIAS.get((role or "").strip(), "employee"))


def roster_add(name, department, role="employee"):
    emp_id = _next_emp_id()
    item = {
        "empId": emp_id,
        "name": (name or "").strip(),
        "department": (department or "").strip(),
        "role": norm_role(role),
        "createdAt": _now_iso(),
    }
    db.roster().put_item(Item=item)
    return item


def roster_resolve(id_or_name):
    """按 empId 或 姓名 定位花名册条目；姓名多条返回 None（需消歧）。"""
    if id_or_name and roster_get(id_or_name):
        return roster_get(id_or_name)
    matches = roster_find_by_name(id_or_name)
    return matches[0] if len(matches) == 1 else None


_FIELD_ALIAS = {
    "name": "name", "姓名": "name", "名前": "name", "名字": "name",
    "dept": "department", "department": "department", "部署": "department",
    "部门": "department", "所属": "department",
    "role": "role", "役割": "role", "角色": "role",
    "aliases": "aliases", "alias": "aliases", "別名": "aliases", "别名": "aliases",
    "カナ": "aliases", "かな": "aliases", "フリガナ": "aliases", "ふりがな": "aliases",
    "読み": "aliases", "読み方": "aliases", "读音": "aliases", "yomi": "aliases", "別称": "aliases",
}


def roster_update_field(emp_id, field_key, value):
    field = _FIELD_ALIAS.get(field_key.lower()) if field_key else None
    if not field:
        return None, None
    value = (value or "").strip()
    if field == "role":
        value = norm_role(value)
    db.roster().update_item(
        Key={"empId": emp_id},
        UpdateExpression="SET #f=:v, updatedAt=:u",
        ExpressionAttributeNames={"#f": field},
        ExpressionAttributeValues={":v": value, ":u": _now_iso()},
    )
    return field, value


def roster_delete(emp_id):
    item = roster_get(emp_id)
    if not item:
        return None
    db.roster().delete_item(Key={"empId": emp_id})
    # 解除其 LINE 绑定（如有）
    line_uid = item.get("lineUserId")
    if line_uid:
        try:
            db.employees().delete_item(Key={"userId": line_uid})
        except Exception:  # noqa: BLE001
            pass
    return item


# ---------------- LINE 绑定 / 权限 ----------------

def get_employee(user_id):
    """LINE 绑定/会话行（employees 表）。"""
    return db.employees().get_item(Key={"userId": user_id}).get("Item")


def roster_of(user_id):
    """该 LINE 用户对应的花名册条目（未绑定返回 None）。
    认证(roster.lineUserId)を最優先＝1人=1社員番号の正本。employees は補助。"""
    for r in roster_scan():
        if r.get("lineUserId") == user_id:
            return r
    link = get_employee(user_id)
    if link and link.get("empId"):
        return roster_get(link["empId"])
    return None


def is_hr(user_id):
    r = roster_of(user_id)
    if r and r.get("role") == "hr":
        return True
    bare = _strip_prefix(user_id)
    return user_id in config.HR_USERIDS or bare in config.HR_USERIDS


def emp_name(user_id):
    r = roster_of(user_id)
    if r:
        return r.get("name")
    link = get_employee(user_id)
    return link.get("name") if link else None


def list_hr():
    """全部人事的 lineUserId（花名册 role=hr 且已绑定 + 白名单 HR_USERIDS）。"""
    ids = {r["lineUserId"] for r in roster_scan()
           if r.get("role") == "hr" and r.get("lineUserId")}
    for h in config.HR_USERIDS:
        ids.add(h)
    return list(ids)


# ---------------- 注册（仅姓名 → 花名册匹配绑定） ----------------

def handle_registration(user_id, text):
    """返回 (handled, reply_text)。未绑定/绑定中时接管对话。
    本人确认＝输入「部门 姓名」(重名时再加 社員番号)，与花名册一致才绑定。"""
    link = get_employee(user_id)
    if link and link.get("status") == "active":
        return False, None

    t = (text or "").strip()
    if not link:
        db.employees().put_item(Item={
            "userId": user_id, "status": "awaiting_id", "createdAt": _now_iso(),
        })
        # 初回でも「部门 姓名」が来ていればその場で照合（follow の空文字なら案内のみ）

    if (link is None) or link.get("status") in ("awaiting_id", "awaiting_name"):
        if not t:
            return True, i18n.T("ask_dept_name")
        matches = _parse_dept_name(t)
        if matches is None:                       # 形式不符 → 再提示
            return True, i18n.T("ask_dept_name")
        if len(matches) == 0:
            return True, i18n.T("not_in_roster", name=t)
        if len(matches) > 1:
            return True, i18n.T("dup_name_id")    # 要求加社員番号消歧
        r = matches[0]
        existing = r.get("lineUserId")            # 占用ロック：別アカウント登録済み
        if existing and existing != user_id:
            return True, i18n.T("auth_taken")
        db.employees().update_item(
            Key={"userId": user_id},
            UpdateExpression="SET #s=:s, empId=:e, #n=:n",
            ExpressionAttributeNames={"#s": "status", "#n": "name"},
            ExpressionAttributeValues={":s": "active", ":e": r["empId"], ":n": r["name"]},
        )
        # 排他バインド（1人=1社員番号）：同 userId が他エントリに付いていれば外す
        for other in roster_scan():
            if other.get("lineUserId") == user_id and other.get("empId") != r["empId"]:
                db.roster().update_item(Key={"empId": other["empId"]},
                                        UpdateExpression="REMOVE lineUserId")
        db.roster().update_item(
            Key={"empId": r["empId"]},
            UpdateExpression="SET lineUserId=:u",
            ExpressionAttributeValues={":u": user_id},
        )
        menu = i18n.T("menu_hr") if r.get("role") == "hr" else i18n.T("menu_employee")
        return True, i18n.T("registered", name=r.get("name", ""),
                            dept=r.get("department", ""), menu=menu)
    return False, None


# ---------------- 提交 ----------------

def infer_type(file_name, text=""):
    s = ((file_name or "") + " " + (text or "")).lower()
    if any(k in s for k in ["作業時間", "作業", "記録簿", "時間記録",
                            "勤怠", "考勤", "kintai", "勤務", "出勤", "工時", "工时"]):
        return "kintai"
    if any(k in s for k in ["経費", "費用", "费用", "通勤", "commute",
                            "交通", "keihi", "経费", "经费"]):
        return "commute"
    return None


def check_file_period(type_, data, period):
    """提出ファイルの年月セルが period(yyyymm) と一致するか。
    返り値 (ok: bool, found: 'YYYY-MM' | None)。"""
    ref = PERIOD_CELL.get(type_)
    ym = xlsx.cell_year_month(data, ref) if ref else None
    if ym is None:
        return False, None
    want = (int(period[:4]), int(period[4:]))
    found = "%04d-%02d" % ym
    return (ym == want), found


def _norm_name(s):
    """表記ゆれ吸収：NFKC(全/半角)＋空白除去＋小文字化＋カタカナ→ひらがな。"""
    s = unicodedata.normalize("NFKC", s or "")
    s = re.sub(r"[\s　]+", "", s).strip().lower()
    return "".join(chr(ord(c) - 0x60) if "ァ" <= c <= "ヶ" else c for c in s)


def check_name(type_, data, user_id):
    """勤務表 B3 の氏名が登録氏名と一致するか。返り値 (ok, found_name)。
    対象外の種別は常に ok。"""
    ref = NAME_CELL.get(type_)
    if not ref:
        return True, None
    found = xlsx.read_cell(data, ref)
    want = emp_name(user_id)
    if not found or not want:
        return False, found
    return (_norm_name(found) == _norm_name(want)), found


def _fmt_day(d):
    tag = _WD[d.weekday()] + ("・祝" if jpholiday.holiday_name(d) else "")
    return "%d/%d(%s)" % (d.month, d.day, tag)


def holiday_work_warnings(type_, data):
    """勤務表で『休日（土日・祝日）なのに時間（6行/7行）が入力されている』日を列挙。
    返り値：['12/7(土)', '12/23(月・祝)', ...]"""
    if type_ != "kintai":
        return []
    m = xlsx.cell_map(data)
    days = []
    for ref, val in m.items():
        if not re.match(r"^[A-Z]+5$", ref):
            continue
        col = xlsx.col_of(ref)
        d = xlsx.to_date(val)
        if not d or not jpholiday.is_rest_day(d):
            continue
        if xlsx.is_number(m.get(col + "6")) or xlsx.is_number(m.get(col + "7")):
            days.append(d)
    days.sort()
    return [_fmt_day(d) for d in days]


def missing_dates(type_, data, period):
    """勤務表 5 行目の日付が 1 日〜月末まで揃っているか。欠落日（int）のリストを返す。"""
    if type_ != "kintai":
        return []
    y, mo = int(period[:4]), int(period[4:])
    last = calendar.monthrange(y, mo)[1]
    m = xlsx.cell_map(data)
    present = set()
    for ref, val in m.items():
        if not re.match(r"^[A-Z]+5$", ref):
            continue
        d = xlsx.to_date(val)
        if d and d.year == y and d.month == mo:
            present.add(d.day)
    return [day for day in range(1, last + 1) if day not in present]


def pending_bytes(user_id):
    link = get_employee(user_id)
    if not link or not link.get("pendingKey"):
        return None
    return s3util.read_object(link["pendingKey"])


def clear_pending(user_id):
    """pending ポインタを解除（S3 の実体は pending/ ライフサイクルで自動削除）。"""
    link = get_employee(user_id)
    if link and link.get("pendingKey"):
        db.employees().update_item(
            Key={"userId": user_id},
            UpdateExpression="REMOVE pendingKey, pendingFile",
        )


def _has_submission(user_id, period, type_):
    r = db.submissions().get_item(Key={"userId": user_id, "sk": "%s#%s" % (period, type_)})
    return "Item" in r


def is_late_resubmit(period):
    """重复提交且已跨月（为上个/更早月份补交）→ 需要人事再确认。"""
    return current_period() > period


def record_submission(user_id, type_, period, s3_key, file_name):
    pk_gsi = "%s#%s" % (period, type_)
    db.submissions().put_item(Item={
        "userId": user_id,
        "sk": "%s#%s" % (period, type_),
        "gsi1pk": pk_gsi,
        "gsi1sk": user_id,
        "period": period,
        "type": type_,
        "s3Key": s3_key,
        "fileName": file_name or "",
        "submittedAt": _now_iso(),
    })


def save_submission(user_id, type_, data, period=None):
    """存提交物（允许重复提交）。返回 (period, key, resubmit)。"""
    period = period or current_period()
    resubmit = _has_submission(user_id, period, type_)
    key, fname = s3util.put_submission(period, type_, emp_name(user_id), data)
    record_submission(user_id, type_, period, key, fname)
    return period, key, resubmit


# ---------------- 待分类暂存 ----------------

def stash_pending(user_id, file_name, data):
    key = s3util.put_pending(user_id, file_name, data)
    db.employees().update_item(
        Key={"userId": user_id},
        UpdateExpression="SET pendingKey=:k, pendingFile=:f",
        ExpressionAttributeValues={":k": key, ":f": file_name or ""},
    )


def resolve_pending(user_id, type_, period=None):
    """用户随后说明类型 → 转正。返回 (period, resubmit)；无暂存返回 None。"""
    link = get_employee(user_id)
    if not link or not link.get("pendingKey"):
        return None
    period = period or current_period()
    resubmit = _has_submission(user_id, period, type_)
    key, fname = s3util.copy_to_submission(link["pendingKey"], period, type_, emp_name(user_id))
    record_submission(user_id, type_, period, key, fname)
    db.employees().update_item(
        Key={"userId": user_id},
        UpdateExpression="REMOVE pendingKey, pendingFile",
    )
    return period, resubmit


def has_pending(user_id):
    link = get_employee(user_id)
    return bool(link and link.get("pendingKey"))


def my_submissions(user_id):
    r = db.submissions().query(KeyConditionExpression=Key("userId").eq(user_id))
    return r.get("Items", [])


# ---------------- 未提交者差集（基于花名册） ----------------

def submitters(period, type_):
    """某月某类型的提交者 lineUserId 集合。"""
    pk = "%s#%s" % (period, type_)
    ids, kwargs = set(), {
        "IndexName": config.SUBMISSIONS_GSI1,
        "KeyConditionExpression": Key("gsi1pk").eq(pk),
    }
    while True:
        r = db.submissions().query(**kwargs)
        for it in r.get("Items", []):
            ids.add(it["gsi1sk"])
        if "LastEvaluatedKey" not in r:
            break
        kwargs["ExclusiveStartKey"] = r["LastEvaluatedKey"]
    return ids


def _submitted(person, done_ids):
    luid = person.get("lineUserId")
    return bool(luid and luid in done_ids)


def missing(period, type_):
    """未提交该类型的花名册成员列表。"""
    done = submitters(period, type_)
    return [p for p in roster_people() if not _submitted(p, done)]


def missing_all_types(period):
    """{empId: {emp, missing_types, lineUserId, linked}}，缺任一必交类型即在内。"""
    done = {t: submitters(period, t) for t in config.SUBMISSION_TYPES}
    out = {}
    for p in roster_people():
        miss = [t for t in config.SUBMISSION_TYPES if not _submitted(p, done[t])]
        if miss:
            out[p["empId"]] = {"emp": p, "missing_types": miss,
                               "lineUserId": p.get("lineUserId"),
                               "linked": bool(p.get("lineUserId"))}
    return out


def roster_status(period):
    """花名册 × 类型 提交矩阵：[{emp, types:{type:item|None}, linked}]"""
    rows = []
    for p in roster_people():
        luid = p.get("lineUserId")
        items = {}
        if luid:
            items = {it["type"]: it for it in my_submissions(luid)
                     if it.get("period") == period}
        rows.append({
            "emp": p,
            "types": {t: items.get(t) for t in config.SUBMISSION_TYPES},
            "linked": bool(luid),
        })
    return rows
