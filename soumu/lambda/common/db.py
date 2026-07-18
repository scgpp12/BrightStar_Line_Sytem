"""DynamoDB 资源与表句柄。

本地离线测试可设 DYNAMODB_ENDPOINT=http://localhost:8000 指向 DynamoDB Local。
"""
import os

import boto3

from . import config

_kwargs = {"region_name": config.REGION}
_endpoint = os.environ.get("DYNAMODB_ENDPOINT")
if _endpoint:
    _kwargs["endpoint_url"] = _endpoint

_resource = boto3.resource("dynamodb", **_kwargs)


def employees():
    """LINE 登入/绑定状态表：userId → empId/status（不是员工花名册）。"""
    return _resource.Table(config.EMPLOYEES_TABLE)


def roster():
    """员工花名册（主数据，人事维护）：empId → name/department/role/lineUserId。"""
    return _resource.Table(config.ROSTER_TABLE)


def submissions():
    return _resource.Table(config.SUBMISSIONS_TABLE)


def bookings():
    """催促予約（総務が日時ピッカーで登録）。"""
    return _resource.Table(config.BOOKINGS_TABLE)
