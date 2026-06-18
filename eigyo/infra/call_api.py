#!/usr/bin/env python3
"""AWS_IAM 認証の Function URL を SigV4 署名付きで叩く呼び出しツール.

Function URL は authType=AWS_IAM。ブラウザや素の curl では呼べないので、
default プロファイルの認証情報で SigV4 署名して呼ぶ。

使い方:
    python call_api.py --url <FunctionUrl> GET  "/staff"
    python call_api.py --url <FunctionUrl> POST "/seed"
    python call_api.py --url <FunctionUrl> GET  "/query?from=池袋&to=東京"
    python call_api.py --url <FunctionUrl> GET  "/site?site=東京&strategy=cheapest"

--url は環境変数 COMMUTE_API_URL でも可。
"""

from __future__ import annotations

import argparse
import os
import sys
import urllib.parse

import boto3
import requests
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest

REGION = os.environ.get("AWS_REGION", "ap-northeast-1")


def _encode_path(path: str) -> str:
    """パス＋クエリの値だけを安全に percent-encode（署名と送信で同一文字列にするため）."""
    if "?" not in path:
        return path
    base, query = path.split("?", 1)
    pairs = []
    for kv in query.split("&"):
        if "=" in kv:
            k, v = kv.split("=", 1)
            pairs.append(f"{k}={urllib.parse.quote(v, safe='')}")
        else:
            pairs.append(kv)
    return base + "?" + "&".join(pairs)


def call(method: str, path: str, url: str, data: bytes | None = None) -> int:
    creds = boto3.Session().get_credentials().get_frozen_credentials()
    full = url.rstrip("/") + _encode_path(path)

    # POST ボディは署名対象（ペイロードハッシュ）。同じ bytes を署名と送信に使う。
    req = AWSRequest(method=method, url=full, data=data)
    if data is not None:
        req.headers["Content-Type"] = "application/json"
    SigV4Auth(creds, "lambda", REGION).add_auth(req)

    resp = requests.request(method, full, headers=dict(req.headers), data=data, timeout=310)
    print(f"HTTP {resp.status_code}")
    ct = resp.headers.get("content-type", "")
    if "json" in ct:
        import json
        print(json.dumps(resp.json(), ensure_ascii=False, indent=2))
    else:
        print(resp.text)
    return 0 if resp.ok else 1


def main(argv: list[str] | None = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    p = argparse.ArgumentParser(description="Function URL(AWS_IAM) を SigV4 で呼ぶ")
    p.add_argument("method", choices=["GET", "POST"])
    p.add_argument("path", help='例: "/query?from=池袋&to=東京"')
    p.add_argument("--url", default=os.environ.get("COMMUTE_API_URL"))
    p.add_argument("--body-file", default=None,
                   help="POST ボディ(UTF-8 JSON ファイル)。日本語は argv で壊れるのでファイル渡し推奨")
    args = p.parse_args(argv)
    if not args.url:
        print("--url か 環境変数 COMMUTE_API_URL が必要です", file=sys.stderr)
        return 2
    data = None
    if args.body_file:
        with open(args.body_file, "rb") as f:
            data = f.read()
    return call(args.method, args.path, args.url, data=data)


if __name__ == "__main__":
    raise SystemExit(main())
