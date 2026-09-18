# -*- coding: utf-8 -*-
"""デプロイ済み Lambda とリポジトリの一致を検証する。

  python tools/verify_deploy_sync.py --profile <profile>

各関数の Code.Location から配布 zip を取得・展開し、リポジトリ側と md5 比較する。
営業は build/ が .gitignore のため、build.py のコピー規則で git 管理下の元ファイルへ読み替える。
"""
import argparse
import hashlib
import io
import os
import subprocess
import sys
import urllib.request
import zipfile

import boto3

REPO = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
SKIP_DIRS = {"__pycache__", ".pytest_cache", "cdk.out", "node_modules", ".venv"}

STACK_SRC = {
    "brightstar-kenshu-dev": "kenshu/src",
    "BrightstarHr-dev": "jinji/lambda",
    "BrightstarSoumu-dev": "soumu/lambda",
    "brightstar-shain-dev": "shain/lambda",
}
EIGYO_STACK = "EkiCommute-dev"
EIGYO_MAP = {
    "handler.py": "eigyo/infra/lambda_app/handler.py",
    "line_handler.py": "eigyo/infra/lambda_app/line_handler.py",
    "authlib.py": "eigyo/infra/lambda_app/authlib.py",
    "assist.py": "eigyo/infra/lambda_app/assist.py",
}
EIGYO_DIRS = {"transit/": "eigyo/transit/", "staff/": "eigyo/staff/"}


def md5(b):
    return hashlib.md5(b).hexdigest()


def local_map(root):
    base = os.path.join(REPO, root.replace("/", os.sep))
    if not os.path.isdir(base):
        return None
    out = {}
    for dp, dn, fn in os.walk(base):
        dn[:] = [d for d in dn if d not in SKIP_DIRS]
        for f in fn:
            if f.endswith(".py"):
                p = os.path.join(dp, f)
                with open(p, "rb") as fh:
                    out[os.path.relpath(p, base).replace(os.sep, "/")] = md5(fh.read())
    return out


def deployed_map(lam, fn):
    url = lam.get_function(FunctionName=fn)["Code"]["Location"]
    data = urllib.request.urlopen(url, timeout=180).read()
    out = {}
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        for n in z.namelist():
            if n.endswith(".py") and "__pycache__" not in n:
                out[n] = md5(z.read(n))
    return out


def tracked(rel):
    return subprocess.run(["git", "-C", REPO, "ls-files", "--error-unmatch", rel],
                          capture_output=True).returncode == 0


def main(profile, region):
    s = boto3.Session(profile_name=profile, region_name=region)
    cfn, lam = s.client("cloudformation"), s.client("lambda")
    overall, total = True, 0

    def funcs(stack):
        out = []
        for p in cfn.get_paginator("list_stack_resources").paginate(StackName=stack):
            for r in p["StackResourceSummaries"]:
                if r["ResourceType"] == "AWS::Lambda::Function":
                    n = r["PhysicalResourceId"]
                    if "Custom" not in n and "LogRetention" not in n:
                        out.append(n)
        return sorted(out)

    print("=" * 74)
    print("Lambda (deployed)  vs  Repository   [profile=%s]" % profile)
    print("=" * 74)
    for stack, root in STACK_SRC.items():
        loc = local_map(root)
        for fn in funcs(stack):
            dep = deployed_map(lam, fn)
            diff = sorted(k for k in set(dep) & set(loc) if dep[k] != loc[k])
            only_d, only_l = sorted(set(dep) - set(loc)), sorted(set(loc) - set(dep))
            st = "IN SYNC" if not (diff or only_d or only_l) else "DRIFT"
            overall = overall and st == "IN SYNC"
            total += len(dep)
            print("\n%-52s %s  (%d files)" % (fn[:52], st, len(dep)))
            for k in diff:
                print("    ! differs : %s" % k)
            for k in only_d:
                print("    + only in Lambda : %s" % k)
            for k in only_l:
                print("    - only in repo   : %s" % k)

    for fn in funcs(EIGYO_STACK):
        dep_raw = lam.get_function(FunctionName=fn)["Code"]["Location"]
        data = urllib.request.urlopen(dep_raw, timeout=180).read()
        same = diff = miss = 0
        unmapped = []
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            names = [n for n in z.namelist() if n.endswith(".py") and "__pycache__" not in n]
            for n in sorted(names):
                rel = EIGYO_MAP.get(n)
                if rel is None:
                    for pre, rp in EIGYO_DIRS.items():
                        if n.startswith(pre):
                            rel = rp + n[len(pre):]
                            break
                if rel is None:
                    unmapped.append(n)
                    continue
                p = os.path.join(REPO, rel.replace("/", os.sep))
                if not os.path.isfile(p) or not tracked(rel):
                    print("    - not tracked in repo : %s" % rel)
                    miss += 1
                    continue
                with open(p, "rb") as fh:
                    same += 1 if md5(fh.read()) == md5(z.read(n)) else 0
                    diff += 0
        ok = (miss == 0 and not unmapped and same == len(names))
        overall = overall and ok
        total += len(names)
        print("\n%-52s %s  (%d files)" % (fn[:52], "IN SYNC" if ok else "DRIFT", len(names)))
        for n in unmapped:
            print("    ? unmapped : %s" % n)

    print("\n" + "=" * 74)
    print("RESULT: %s   (%d files compared)" % ("ALL IN SYNC" if overall else "DRIFT FOUND", total))
    sys.exit(0 if overall else 1)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", required=True)
    ap.add_argument("--region", default="ap-northeast-1")
    a = ap.parse_args()
    main(a.profile, a.region)
