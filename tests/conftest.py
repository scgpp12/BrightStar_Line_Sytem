"""authlib テストの土台：jinji の authlib を import path に追加し、moto で
roster/auth テーブルをモックする。moto 起動後に authlib の boto3 キャッシュを
リセットして、モック上のリソースを使わせる。
"""
import os
import pathlib
import sys

import boto3
import pytest
from moto import mock_aws

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "jinji" / "lambda" / "common"))

os.environ.setdefault("AWS_REGION", "ap-northeast-1")
os.environ.setdefault("AWS_DEFAULT_REGION", "ap-northeast-1")
os.environ.setdefault("AWS_ACCESS_KEY_ID", "testing")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "testing")
os.environ["ROSTER_TABLE"] = "test-roster"
os.environ["AUTH_TABLE"] = "test-auth"


@pytest.fixture()
def ddb():
    with mock_aws():
        res = boto3.resource("dynamodb", region_name="ap-northeast-1")
        res.create_table(
            TableName="test-roster",
            KeySchema=[{"AttributeName": "empId", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "empId", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )
        res.create_table(
            TableName="test-auth",
            KeySchema=[{"AttributeName": "pk", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "pk", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )
        import authlib
        authlib._ddb = None          # モック上で作り直させる
        yield res
