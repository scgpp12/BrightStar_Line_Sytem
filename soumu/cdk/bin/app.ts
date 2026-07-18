#!/usr/bin/env node
import * as cdk from "aws-cdk-lib";
import { BrightstarSoumuStack } from "../lib/brightstar-soumu-stack";

const app = new cdk.App();
const stage = app.node.tryGetContext("stage") || "dev";

new BrightstarSoumuStack(app, `BrightstarSoumu-${stage}`, {
  stage,
  env: {
    account: process.env.CDK_DEFAULT_ACCOUNT,
    region: process.env.CDK_DEFAULT_REGION || "ap-northeast-1",
  },
  description: "BrightStar 総務アシスタント（勤怠・通勤費の提出管理・催促）",
});
