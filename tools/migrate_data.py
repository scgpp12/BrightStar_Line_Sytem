# -*- coding: utf-8 -*-
"""アカウント間のデータ移行（DynamoDB / S3）。

  python tools/migrate_data.py export --profile <旧> --out <DIR>
  python tools/migrate_data.py import --profile <新> --src <DIR>
  python tools/migrate_data.py verify --profile <新> --src <DIR>

テーブル名・バケット名は CloudFormation から解決するため、
自動生成名（EkiCommute-dev-StaffTable…）やアカウントIDを含むバケット名にも追従する。
"""
import argparse
import decimal
import json
import os
import sys

import boto3
from botocore.exceptions import ClientError

REGION = "ap-northeast-1"

STACKS = {
    "hr": "BrightstarHr-dev",
    "soumu": "BrightstarSoumu-dev",
    "shain": "brightstar-shain-dev",
    "kenshu": "brightstar-kenshu-dev",
    "eigyo": "EkiCommute-dev",
}

# 移行するテーブル（論理名で指定。物理名は CFN から解決）
TABLES = [
    ("hr", "RosterTable"), ("hr", "EmployeesTable"), ("hr", "SubmissionsTable"),
    ("soumu", "BookingsTable"), ("soumu", "BroadcastsTable"),
    ("kenshu", "Courses"), ("kenshu", "Enrollments"), ("kenshu", "Groups"),
    ("kenshu", "Students"), ("kenshu", "Results"), ("kenshu", "Knowledge"),
    ("eigyo", "StaffTable"),
]
# 移行しない（当日限り / 再取得可能）
SKIP_NOTE = ["AuthTable", "SessionTable", "CacheTable"]


class DecEncoder(json.JSONEncoder):
    def default(self, o):
        if isinstance(o, decimal.Decimal):
            return int(o) if o % 1 == 0 else float(o)
        if isinstance(o, (bytes, bytearray)):
            return {"__b64__": __import__("base64").b64encode(o).decode()}
        if isinstance(o, set):
            return {"__set__": list(o)}
        return super().default(o)


def dec_hook(d):
    if "__b64__" in d:
        return __import__("base64").b64decode(d["__b64__"])
    if "__set__" in d:
        return set(d["__set__"])
    return d


def sess(profile):
    return boto3.Session(profile_name=profile, region_name=REGION)


def resolve(s):
    """CFN から {論理ID接頭辞: 物理名} と バケット名 を解決する。"""
    cfn = s.client("cloudformation")
    out = {"tables": {}, "bucket": None}
    for key, stack in STACKS.items():
        try:
            pages = cfn.get_paginator("list_stack_resources").paginate(StackName=stack)
        except ClientError as e:
            print("  [WARN] %s: %s" % (stack, e.response["Error"]["Code"]))
            continue
        for p in pages:
            for r in p["StackResourceSummaries"]:
                if r["ResourceType"] == "AWS::DynamoDB::Table":
                    out["tables"][(key, r["LogicalResourceId"])] = r["PhysicalResourceId"]
                if r["ResourceType"] == "AWS::S3::Bucket" and key == "hr":
                    out["bucket"] = r["PhysicalResourceId"]
    return out


def find_table(res, key, logical_prefix):
    for (k, logical), phys in res["tables"].items():
        if k == key and logical.startswith(logical_prefix):
            return phys
    return None


def do_export(profile, outdir):
    s = sess(profile)
    res = resolve(s)
    ddb = s.resource("dynamodb")
    s3 = s.client("s3")
    os.makedirs(os.path.join(outdir, "ddb"), exist_ok=True)
    os.makedirs(os.path.join(outdir, "s3"), exist_ok=True)

    manifest = {"bucket": res["bucket"], "tables": {}, "s3": []}
    print("DynamoDB")
    for key, logical in TABLES:
        phys = find_table(res, key, logical)
        if not phys:
            print("  [SKIP] %-12s %s (見つからない)" % (key, logical))
            continue
        items, kw = [], {}
        while True:
            r = ddb.Table(phys).scan(**kw)
            items += r.get("Items", [])
            if "LastEvaluatedKey" not in r:
                break
            kw["ExclusiveStartKey"] = r["LastEvaluatedKey"]
        fn = os.path.join(outdir, "ddb", "%s__%s.json" % (key, logical))
        with open(fn, "w", encoding="utf-8") as f:
            json.dump(items, f, cls=DecEncoder, ensure_ascii=False)
        manifest["tables"]["%s__%s" % (key, logical)] = {"source": phys, "count": len(items)}
        print("  %-42s %4d 件" % (phys, len(items)))

    print("S3  %s" % res["bucket"])
    n = 0
    tok = {}
    while True:
        r = s3.list_objects_v2(Bucket=res["bucket"], **tok)
        for o in r.get("Contents", []):
            k = o["Key"]
            body = s3.get_object(Bucket=res["bucket"], Key=k)
            meta = {"key": k, "contentType": body.get("ContentType", ""), "tags": []}
            try:
                meta["tags"] = s3.get_object_tagging(Bucket=res["bucket"], Key=k)["TagSet"]
            except ClientError:
                pass
            local = os.path.join(outdir, "s3", "%05d.bin" % n)
            with open(local, "wb") as f:
                f.write(body["Body"].read())
            meta["file"] = "%05d.bin" % n
            manifest["s3"].append(meta)
            n += 1
        if not r.get("IsTruncated"):
            break
        tok = {"ContinuationToken": r["NextContinuationToken"]}
    print("  %d オブジェクト" % n)

    with open(os.path.join(outdir, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=1)
    print("\n出力: %s" % os.path.abspath(outdir))
    print("移行対象外: %s" % ", ".join(SKIP_NOTE))


def do_import(profile, srcdir):
    s = sess(profile)
    res = resolve(s)
    ddb = s.resource("dynamodb")
    s3 = s.client("s3")
    with open(os.path.join(srcdir, "manifest.json"), encoding="utf-8") as f:
        man = json.load(f)

    print("DynamoDB")
    for name, info in man["tables"].items():
        key, logical = name.split("__", 1)
        phys = find_table(res, key, logical)
        if not phys:
            print("  [NG] %-24s 移行先テーブルが見つからない" % name)
            continue
        with open(os.path.join(srcdir, "ddb", name + ".json"), encoding="utf-8") as f:
            items = json.load(f, object_hook=dec_hook)
        t = ddb.Table(phys)
        with t.batch_writer() as bw:
            for it in items:
                bw.put_item(Item=it)
        print("  %-42s %4d 件" % (phys, len(items)))

    print("S3  %s" % res["bucket"])
    for m in man["s3"]:
        kw = {}
        if m.get("contentType"):
            kw["ContentType"] = m["contentType"]
        if m.get("tags"):
            kw["Tagging"] = "&".join("%s=%s" % (t["Key"], t["Value"]) for t in m["tags"])
        with open(os.path.join(srcdir, "s3", m["file"]), "rb") as f:
            s3.put_object(Bucket=res["bucket"], Key=m["key"], Body=f.read(), **kw)
    print("  %d オブジェクト" % len(man["s3"]))


def do_verify(profile, srcdir):
    s = sess(profile)
    res = resolve(s)
    ddb = s.client("dynamodb")
    s3 = s.client("s3")
    with open(os.path.join(srcdir, "manifest.json"), encoding="utf-8") as f:
        man = json.load(f)
    ok = True
    print("%-44s %8s %8s" % ("対象", "移行元", "移行先"))
    for name, info in man["tables"].items():
        key, logical = name.split("__", 1)
        phys = find_table(res, key, logical)
        if not phys:
            print("%-44s %8d %8s  NG" % (name, info["count"], "-"))
            ok = False
            continue
        cnt, kw = 0, {}
        while True:
            r = ddb.scan(TableName=phys, Select="COUNT", **kw)
            cnt += r["Count"]
            if "LastEvaluatedKey" not in r:
                break
            kw["ExclusiveStartKey"] = r["LastEvaluatedKey"]
        mark = "OK" if cnt == info["count"] else "NG"
        ok = ok and cnt == info["count"]
        print("%-44s %8d %8d  %s" % (phys, info["count"], cnt, mark))
    cnt, tok = 0, {}
    while True:
        r = s3.list_objects_v2(Bucket=res["bucket"], **tok)
        cnt += len(r.get("Contents", []))
        if not r.get("IsTruncated"):
            break
        tok = {"ContinuationToken": r["NextContinuationToken"]}
    mark = "OK" if cnt == len(man["s3"]) else "NG"
    ok = ok and cnt == len(man["s3"])
    print("%-44s %8d %8d  %s" % (res["bucket"], len(man["s3"]), cnt, mark))
    print("\nRESULT:", "ALL OK" if ok else "MISMATCH")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["export", "import", "verify"])
    ap.add_argument("--profile", required=True)
    ap.add_argument("--out", default="MIGRATION_DATA")
    ap.add_argument("--src", default="MIGRATION_DATA")
    ap.add_argument("--region", default=REGION)
    a = ap.parse_args()
    REGION = a.region
    if a.mode == "export":
        do_export(a.profile, a.out)
    elif a.mode == "import":
        do_import(a.profile, a.src)
    else:
        do_verify(a.profile, a.src)
