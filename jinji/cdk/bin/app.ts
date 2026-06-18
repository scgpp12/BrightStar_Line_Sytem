#!/usr/bin/env node
import * as cdk from "aws-cdk-lib";
import { BrightstarHrStack } from "../lib/brightstar-hr-stack";

const app = new cdk.App();
const stage = app.node.tryGetContext("stage") || "dev";

new BrightstarHrStack(app, `BrightstarHr-${stage}`, {
  stage,
  env: {
    account: process.env.CDK_DEFAULT_ACCOUNT,
    region: process.env.CDK_DEFAULT_REGION || "ap-northeast-1",
  },
  description: "BrightStar 社内システム（勤怠・通勤費 LINE Bot）",
});
