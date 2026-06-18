#!/usr/bin/env python3
"""社員アシスタント CDK 入口。

  cdk deploy            # 既定 stage=dev
依存テーブル/バケットは kenshu / jinji スタックが作成したものを名前で参照する。
"""
import os

import aws_cdk as cdk

from shain_stack import ShainStack

app = cdk.App()

app_name = app.node.try_get_context("appName") or "brightstar-shain"
stage = app.node.try_get_context("stage") or "dev"
kenshu_app = app.node.try_get_context("kenshuApp") or "brightstar-kenshu"
jinji_app = app.node.try_get_context("jinjiApp") or "brightstar-hr"

ShainStack(
    app, f"{app_name}-{stage}",
    app_name=app_name,
    stage=stage,
    kenshu_app=kenshu_app,
    jinji_app=jinji_app,
    env=cdk.Environment(
        account=os.environ.get("CDK_DEFAULT_ACCOUNT"),
        region=os.environ.get("CDK_DEFAULT_REGION", "ap-northeast-1"),
    ),
)

app.synth()
