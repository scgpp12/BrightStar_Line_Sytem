#!/usr/bin/env python3
"""CDK 应用入口。stage / app 名与机密通过 context 传入：

  cdk deploy -c stage=cdk -c weComCorpId=... -c weComRelayUrl=... ...

默认 stage=cdk（刻意区别于线上 SAM 的 dev，避免撞名）。region 由
CDK_DEFAULT_REGION / 环境决定（建议 ap-northeast-1）。
"""
import os

import aws_cdk as cdk

from brightstar_stack import BrightStarStack

app = cdk.App()

app_name = app.node.try_get_context("appName") or "brightstar-kenshu"
stage = app.node.try_get_context("stage") or "dev"

BrightStarStack(
    app, f"{app_name}-{stage}",
    app_name=app_name,
    stage=stage,
    env=cdk.Environment(
        account=os.environ.get("CDK_DEFAULT_ACCOUNT"),
        region=os.environ.get("CDK_DEFAULT_REGION", "ap-northeast-1"),
    ),
)

app.synth()
