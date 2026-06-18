#!/usr/bin/env node
// CDK アプリのエントリ。東京リージョン(ap-northeast-1)に1スタックをデプロイする。
import * as cdk from "aws-cdk-lib";
import { CommuteStack } from "../lib/commute-stack";

const app = new cdk.App();

new CommuteStack(app, "EkiCommute-dev", {
  env: {
    account: process.env.CDK_DEFAULT_ACCOUNT,
    region: process.env.AWS_REGION || "ap-northeast-1",
  },
  description: "要員通勤コスト調査ツール（Lambda + DynamoDB + Function URL）",
});

// 後片付けしやすいよう全リソースに統一タグを付与
cdk.Tags.of(app).add("Project", "eki-commute");
cdk.Tags.of(app).add("Env", "dev");
