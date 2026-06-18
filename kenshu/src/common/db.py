"""DynamoDB 资源与表句柄。

本地 demo / 离线测试时，可设置环境变量：
    DYNAMODB_ENDPOINT=http://localhost:8000
指向 DynamoDB Local。
"""
import os

import boto3

from . import config

_kwargs = {}
_endpoint = os.environ.get("DYNAMODB_ENDPOINT")
if _endpoint:
    _kwargs["endpoint_url"] = _endpoint

_resource = boto3.resource("dynamodb", **_kwargs)


def students():
    return _resource.Table(config.STUDENTS_TABLE)


def courses():
    return _resource.Table(config.COURSES_TABLE)


def enrollments():
    return _resource.Table(config.ENROLLMENTS_TABLE)


def groups():
    return _resource.Table(config.GROUPS_TABLE)


def results():
    return _resource.Table(config.RESULTS_TABLE)


def knowledge():
    return _resource.Table(config.KNOWLEDGE_TABLE)
