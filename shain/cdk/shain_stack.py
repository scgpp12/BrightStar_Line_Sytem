"""BrightStar 社員アシスタント —— AWS 基础设施（CDK / Python）。

研修(kenshu)・人事(jinji) スタックが作った DynamoDB / S3 を**名前参照で共有**し、
社員むけ統合 webhook（Function URL）と mode 保存テーブルだけを新規に作る。
LINE 凭证は SSM SecureString（/{app}/{stage}/line/{secret,token}）。
"""
from aws_cdk import (
    Stack,
    Tags,
    Duration,
    RemovalPolicy,
    CfnOutput,
    aws_dynamodb as ddb,
    aws_lambda as lambda_,
    aws_iam as iam,
)
from constructs import Construct


class ShainStack(Stack):
    def __init__(self, scope, cid, *, app_name, stage, kenshu_app, jinji_app, **kw):
        super().__init__(scope, cid, **kw)

        Tags.of(self).add("Project", app_name)
        Tags.of(self).add("ManagedBy", "cdk")
        Tags.of(self).add("Stage", stage)

        prefix = f"{app_name}-{stage}"
        kprefix = f"{kenshu_app}-{stage}"     # 研修テーブル接頭辞
        jprefix = f"{jinji_app}-{stage}"      # 人事テーブル接頭辞
        acct, region = self.account, self.region

        line_secret_param = f"/{app_name}/{stage}/line/secret"
        line_token_param = f"/{app_name}/{stage}/line/token"

        # ---------- mode 保存テーブル（新規） ----------
        session = ddb.Table(
            self, "SessionTable",
            table_name=f"{prefix}-session",
            partition_key=ddb.Attribute(name="userId", type=ddb.AttributeType.STRING),
            billing_mode=ddb.BillingMode.PAY_PER_REQUEST,
            encryption=ddb.TableEncryption.AWS_MANAGED,
            removal_policy=RemovalPolicy.DESTROY,
        )

        # ---------- 共有リソース名（kenshu / jinji が作成済み） ----------
        bucket_name = f"{jprefix}-{acct}"
        env = {
            "APP_NAME": app_name,
            "STAGE": stage,
            "TZ": "Asia/Tokyo",
            # 研修テーブル（共有）
            "STUDENTS_TABLE": f"{kprefix}-students",
            "COURSES_TABLE": f"{kprefix}-courses",
            "ENROLLMENTS_TABLE": f"{kprefix}-enrollments",
            "GROUPS_TABLE": f"{kprefix}-groups",
            "RESULTS_TABLE": f"{kprefix}-results",
            "KNOWLEDGE_TABLE": f"{kprefix}-knowledge",
            "ENROLLMENTS_GSI1": "GSI1",
            "CANCEL_DEADLINE_HOURS": "2",
            "LOGIN_CODE_TTL_DAYS": "7",
            "TEACHER_OPENIDS": "",
            "TEACHER_SIGNUP_CODE": "",
            "BEDROCK_ENABLED": "false",
            "BEDROCK_MODEL_ID": "anthropic.claude-3-haiku-20240307-v1:0",
            "BEDROCK_CHAT_MODEL_ID": "jp.anthropic.claude-haiku-4-5-20251001-v1:0",
            "BEDROCK_EMBED_MODEL_ID": "amazon.titan-embed-text-v2:0",
            "BEDROCK_EMBED_DIM": "256",
            # 人事テーブル / バケット（共有）
            "EMPLOYEES_TABLE": f"{jprefix}-employees",
            "ROSTER_TABLE": f"{jprefix}-roster",
            "AUTH_TABLE": f"{jprefix}-auth",
            "SUBMISSIONS_TABLE": f"{jprefix}-submissions",
            "SUBMISSIONS_GSI1": "GSI1",
            "BUCKET_NAME": bucket_name,
            "PRESIGN_TTL": "3600",
            "HR_USERIDS": "",
            # LINE 凭证（kenshu/jinji config 共に同じ env 名を読む）
            "LINE_SECRET_PARAM": line_secret_param,
            "LINE_TOKEN_PARAM": line_token_param,
            # 社員ルーター
            "SHAIN_SESSION_TABLE": session.table_name,
        }

        webhook = lambda_.Function(
            self, "ShainWebhookFunction",
            function_name=f"{prefix}-webhook",
            runtime=lambda_.Runtime.PYTHON_3_12,
            architecture=lambda_.Architecture.ARM_64,
            handler="handler.handler",
            code=lambda_.Code.from_asset("../lambda"),
            memory_size=512,                       # 添付DLの zip 余裕 + 起動高速化
            timeout=Duration.seconds(29),
            environment=env,
        )

        session.grant_read_write_data(webhook)

        # ---------- 共有テーブル/バケットへのアクセス（名前→ARN を明示付与） ----------
        def tarn(name):
            return f"arn:aws:dynamodb:{region}:{acct}:table/{name}"

        rw_tables = [
            f"{kprefix}-students", f"{kprefix}-courses", f"{kprefix}-enrollments",
            f"{kprefix}-groups",
            f"{jprefix}-employees", f"{jprefix}-roster", f"{jprefix}-submissions",
            f"{jprefix}-auth",
        ]
        ro_tables = [f"{kprefix}-knowledge"]
        table_resources = [tarn(n) for n in rw_tables + ro_tables]
        # GSI も含める
        index_resources = [
            tarn(f"{kprefix}-enrollments") + "/index/*",
            tarn(f"{jprefix}-submissions") + "/index/*",
        ]
        webhook.add_to_role_policy(iam.PolicyStatement(
            actions=[
                "dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:UpdateItem",
                "dynamodb:DeleteItem", "dynamodb:Query", "dynamodb:Scan",
                "dynamodb:BatchGetItem", "dynamodb:BatchWriteItem",
            ],
            resources=table_resources + index_resources,
        ))

        # 人事の提出物バケット（提出保存・テンプレ・履歴DL・zip）
        webhook.add_to_role_policy(iam.PolicyStatement(
            actions=["s3:GetObject", "s3:PutObject", "s3:DeleteObject",
                     "s3:ListBucket", "s3:GetObjectTagging", "s3:PutObjectTagging"],
            resources=[f"arn:aws:s3:::{bucket_name}", f"arn:aws:s3:::{bucket_name}/*"],
        ))

        # LINE 凭证（SSM SecureString）+ KMS 解密
        webhook.add_to_role_policy(iam.PolicyStatement(
            actions=["ssm:GetParameter"],
            resources=[
                f"arn:aws:ssm:{region}:{acct}:parameter{line_secret_param}",
                f"arn:aws:ssm:{region}:{acct}:parameter{line_token_param}",
            ],
        ))
        webhook.add_to_role_policy(iam.PolicyStatement(
            actions=["kms:Decrypt"], resources=["*"],
            conditions={"StringEquals": {"kms:ViaService": f"ssm.{region}.amazonaws.com"}},
        ))
        # Bedrock（研修 RAG/意図；既定 off だが将来用）
        webhook.add_to_role_policy(iam.PolicyStatement(
            actions=["bedrock:InvokeModel"], resources=["*"],
        ))

        # ---------- Function URL（LINE webhook 入口） ----------
        fn_url = webhook.add_function_url(auth_type=lambda_.FunctionUrlAuthType.NONE)

        CfnOutput(self, "LineWebhookUrl", value=fn_url.url,
                  description="填到 LINE Developers > Messaging API > Webhook URL（社員）")
        CfnOutput(self, "LineSecretParam", value=line_secret_param)
        CfnOutput(self, "LineTokenParam", value=line_token_param)
        CfnOutput(self, "SessionTableName", value=session.table_name)
