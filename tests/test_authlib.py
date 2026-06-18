"""authlib.gate の状態遷移を網羅（一次绑定 / ワンタップ / 占用ロック / TTL / 役割）。"""
import authlib


def HR(item):
    return item.get("role") == "hr"


def TEACHER(item):
    return item.get("role") == "teacher"


def seed(ddb, items):
    t = ddb.Table("test-roster")
    for it in items:
        t.put_item(Item=it)


HRMAN = {"empId": "E1", "name": "孫成功", "department": "人事部", "role": "hr"}


def test_need_bind_for_unknown(ddb):
    seed(ddb, [HRMAN])
    assert authlib.gate("jinji", "U1", "こんにちは", HR) == ("need_bind", None)


def test_bind_then_pass(ddb):
    seed(ddb, [HRMAN])
    act, item = authlib.gate("jinji", "U1", "人事部 孫成功", HR)
    assert act == "ok" and item["empId"] == "E1"
    # roster に lineUserId が紐付く
    assert ddb.Table("test-roster").get_item(Key={"empId": "E1"})["Item"]["lineUserId"] == "U1"
    # auth 行 + TTL(expireAt)
    a = ddb.Table("test-auth").get_item(Key={"pk": "jinji#U1"})["Item"]
    assert a["authedDate"] == authlib.today_jst()
    assert int(a["expireAt"]) > 0
    # 当日2回目は pass（再認証不要）
    assert authlib.gate("jinji", "U1", "未提出", HR)[0] == "pass"


def test_one_tap_next_day(ddb):
    seed(ddb, [HRMAN])
    authlib.gate("jinji", "U1", "人事部 孫成功", HR)            # 初回紐付け
    ddb.Table("test-auth").delete_item(Key={"pk": "jinji#U1"})  # 翌日（認証失効）を模倣
    act, item = authlib.gate("jinji", "U1", "認証", HR)         # 名前を打たずワンタップ
    assert act == "ok" and item["empId"] == "E1"


def test_taken_lock(ddb):
    seed(ddb, [HRMAN])
    authlib.gate("jinji", "U1", "人事部 孫成功", HR)            # U1 が占有
    act, _ = authlib.gate("jinji", "U2", "人事部 孫成功", HR)   # 別アカウントが冒名
    assert act == "taken"
    # 紐付けは U1 のまま
    assert ddb.Table("test-roster").get_item(Key={"empId": "E1"})["Item"]["lineUserId"] == "U1"


def test_wrong_role(ddb):
    seed(ddb, [{"empId": "E2", "name": "拉拉", "department": "営業部", "role": "employee"}])
    act, _ = authlib.gate("jinji", "U3", "営業部 拉拉", HR)     # HR ではない
    assert act == "wrong_role"
    assert "jinji#U3" not in [i["pk"] for i in ddb.Table("test-auth").scan()["Items"]]


def test_ambiguous_needs_empid(ddb):
    seed(ddb, [
        {"empId": "E3", "name": "田中", "department": "開発部", "role": "employee"},
        {"empId": "E4", "name": "田中", "department": "開発部", "role": "employee"},
    ])
    assert authlib.gate("jinji", "U4", "開発部 田中", HR)[0] == "ambiguous"
    # 社員番号で確定（ただし役割は employee なので wrong_role）
    assert authlib.gate("jinji", "U4", "開発部 田中 E3", HR)[0] == "wrong_role"


def test_not_found(ddb):
    seed(ddb, [HRMAN])
    assert authlib.gate("jinji", "U5", "営業部 山田", HR)[0] == "not_found"


def test_teacher_role_separate_channel(ddb):
    seed(ddb, [{"empId": "E9", "name": "講師太郎", "department": "研修部", "role": "teacher"}])
    assert authlib.gate("kenshu", "U6", "研修部 講師太郎", TEACHER)[0] == "ok"
    # 同じ人でも人事 channel は role 不一致で弾く
    ddb.Table("test-auth").delete_item(Key={"pk": "kenshu#U6"})
    assert authlib.gate("jinji", "U6", "研修部 講師太郎", HR)[0] == "wrong_role"
