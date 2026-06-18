# BrightStar 基础设施 —— CDK（Python）

与 `../template.yaml`（SAM）**功能等价**的另一套 IaC。资源由 CDK 自动命名，
Lambda 环境变量自动接到真实表名；机密通过 `-c`（context）传入，不写死。

> ⚠️ **与线上 SAM 栈的关系**：线上正在跑的是 SAM 栈 `brightstar-dev`。
> 本 CDK 默认 `stage=cdk`（栈名 `brightstar-cdk`），是**独立的另一套环境**，
> 不会与 SAM 撞名。两者都用 DynamoDB 按需 + 同一份 `../src` Lambda 代码。
> 想用 CDK 取代 SAM，请部署 CDK 后把数据/配置迁过去，再删 SAM 栈——不要同时用两套管同一批资源。

## 前置
- Node.js ≥ 18 与 AWS CDK CLI：`npm i -g aws-cdk`
- Python 3.12，AWS 凭证（`aws configure` 或环境变量），区域建议 `ap-northeast-1`

## 安装
```bash
cd brightstar/cdk
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

## 首次需 bootstrap（每账号每区域一次）
```bash
cdk bootstrap aws://<account-id>/ap-northeast-1
```

## 部署（机密用 -c 传入，不入库）
```bash
cdk deploy brightstar-kenshu-dev --require-approval never \
  -c weComCorpId=<CORP_ID> \
  -c weComToken=<TOKEN>  -c weComAesKey=<AES_KEY>  -c weComAgentId=<AGENT_ID> \
  -c weComSecret=<SECRET>  \
  -c weComRelayUrl=<RELAY_URL> \
  -c weComRelayAuth=<RELAY_AUTH>  \
  -c weComKfOpenKfId=<OPEN_KFID> \
  -c teacherSignupCode=<SIGNUP_CODE> \
  -c bedrockChatModelId=jp.anthropic.claude-haiku-4-5-20251001-v1:0
```
部署完看输出 `WeChatWebhookUrl` / `WebLoginApiUrl` / `KnowledgeTableName`。

> 机密更稳妥的做法：放进 SSM Parameter Store / Secrets Manager，再在 stack 里读；
> 当前为与 SAM 对齐用 context，**切勿把带机密的命令提交进 git**。

## 灌入 RAG 演示文档（CDK 表名是自动生成的）
```bash
cd ..
AWS_DEFAULT_REGION=ap-northeast-1 KNOWLEDGE_TABLE=<上面输出的 KnowledgeTableName> \
  python tools/seed_knowledge.py
```

## 常用命令
```bash
cdk synth      # 生成 CloudFormation 模板（不部署，检查用）
cdk diff       # 与已部署的差异
cdk deploy     # 部署/更新
cdk destroy    # 拆除（DynamoDB 默认 RETAIN，需手动删表，防误删数据）
```

## 与 SAM 的对应
| SAM (template.yaml) | CDK (brightstar_stack.py) |
|---|---|
| `AWS::Serverless::Function` | `aws_lambda.Function`（同一份 src/） |
| `AWS::Serverless::HttpApi` + CORS | `aws_apigatewayv2.HttpApi`（CORS 自动处理 OPTIONS） |
| `DynamoDBCrudPolicy` 等 | `table.grant_read_write_data(fn)`（含 index 权限） |
| NoEcho 参数 | `-c`（context）/ 建议 SSM |
| `rate(10 minutes)` Schedule | `aws_events.Rule` |
