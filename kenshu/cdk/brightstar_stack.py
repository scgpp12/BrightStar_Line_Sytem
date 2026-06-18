"""BrightStar 研修アシスタント —— AWS 基础设施（CDK / Python）。

与线上 SAM(template.yaml.ref) 等价，并补齐 LINE Webhook：
  6 张 DynamoDB 表 + HTTP API(/wechat,/line,/web/*) + 4 个 Lambda + 定时提醒。

責務分離後の本 channel ＝「講師」専用（LINE）：line_webhook が講師ゲートを行う。
LINE 凭证走 SSM SecureString（/{app}/{stage}/line/{secret,token}）；企业微信机密走 context。
表名は明示（旧 SAM ブランド brightstar-dev-* と衝突しない）。テスト段階のため RemovalPolicy=DESTROY。
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
    aws_apigatewayv2 as apigw,
    aws_events as events,
    aws_events_targets as targets,
)
from aws_cdk.aws_apigatewayv2 import HttpMethod, CorsHttpMethod, CorsPreflightOptions
from aws_cdk.aws_apigatewayv2_integrations import HttpLambdaIntegration
from constructs import Construct


class BrightStarStack(Stack):
    def __init__(self, scope: Construct, cid: str, *, app_name: str, stage: str, **kw):
        super().__init__(scope, cid, **kw)

        ctx = self.node.try_get_context

        def c(key, default=""):
            v = ctx(key)
            return v if v is not None else default

        prefix = f"{app_name}-{stage}"
        # 全社花名册 / 日次認証テーブルは人事(jinji)スタックが作成。名前で参照する。
        jinji_app = c("jinjiApp", "brightstar-hr")
        jinji_prefix = f"{jinji_app}-{stage}"
        roster_table = f"{jinji_prefix}-roster"
        auth_table = f"{jinji_prefix}-auth"
        Tags.of(self).add("Project", app_name)
        Tags.of(self).add("ManagedBy", "cdk")
        Tags.of(self).add("Stage", stage)

        # LINE 凭证 SSM 参数名（值为 SecureString，部署外单独写入，不入 git）
        line_secret_param = f"/{app_name}/{stage}/line/secret"
        line_token_param = f"/{app_name}/{stage}/line/token"

        # ---------- DynamoDB（按需计费 + 静态加密；测试段 DESTROY）----------
        def table(logical, pk, sk=None, name=None):
            kwargs = dict(
                table_name=name,
                partition_key=ddb.Attribute(name=pk, type=ddb.AttributeType.STRING),
                billing_mode=ddb.BillingMode.PAY_PER_REQUEST,
                encryption=ddb.TableEncryption.AWS_MANAGED,
                removal_policy=RemovalPolicy.DESTROY,
            )
            if sk:
                kwargs["sort_key"] = ddb.Attribute(name=sk, type=ddb.AttributeType.STRING)
            return ddb.Table(self, logical, **kwargs)

        students = table("Students", "openid", name=f"{prefix}-students")
        courses = table("Courses", "courseId", name=f"{prefix}-courses")
        enrollments = table("Enrollments", "openid", "courseId", name=f"{prefix}-enrollments")
        enrollments.add_global_secondary_index(  # courseId -> openid（查某课名单）
            index_name="GSI1",
            partition_key=ddb.Attribute(name="courseId", type=ddb.AttributeType.STRING),
            sort_key=ddb.Attribute(name="openid", type=ddb.AttributeType.STRING),
        )
        groups = table("Groups", "courseId", "groupId", name=f"{prefix}-groups")
        results = table("Results", "openid", "itemKey", name=f"{prefix}-results")
        results.add_global_secondary_index(  # itemKey -> openid（老师导出成绩）
            index_name="GSI1",
            partition_key=ddb.Attribute(name="itemKey", type=ddb.AttributeType.STRING),
            sort_key=ddb.Attribute(name="openid", type=ddb.AttributeType.STRING),
        )
        knowledge = table("Knowledge", "docId", "chunkId", name=f"{prefix}-knowledge")  # RAG

        # ---------- 公共环境变量 ----------
        env = {
            "APP_NAME": app_name,
            "STAGE": stage,
            "TZ": "Asia/Tokyo",
            "STUDENTS_TABLE": students.table_name,
            "COURSES_TABLE": courses.table_name,
            "ENROLLMENTS_TABLE": enrollments.table_name,
            "GROUPS_TABLE": groups.table_name,
            "RESULTS_TABLE": results.table_name,
            "KNOWLEDGE_TABLE": knowledge.table_name,
            "ENROLLMENTS_GSI1": "GSI1",
            "CANCEL_DEADLINE_HOURS": c("cancelDeadlineHours", "2"),
            # 企业微信 / 中转（机密走 context）
            "WECOM_CORP_ID": c("weComCorpId"),
            "WECOM_TOKEN": c("weComToken"),
            "WECOM_AES_KEY": c("weComAesKey"),
            "WECOM_AGENT_ID": c("weComAgentId"),
            "WECOM_SECRET": c("weComSecret"),
            "WECOM_RELAY_URL": c("weComRelayUrl"),
            "WECOM_RELAY_AUTH": c("weComRelayAuth"),
            "WECOM_KF_OPEN_KFID": c("weComKfOpenKfId"),
            "TEACHER_OPENIDS": c("teacherOpenids"),
            "TEACHER_SIGNUP_CODE": c("teacherSignupCode"),
            "LOGIN_CODE_TTL_DAYS": c("loginCodeTtlDays", "7"),
            # LINE 凭证（SSM 懒加载）
            "LINE_SECRET_PARAM": line_secret_param,
            "LINE_TOKEN_PARAM": line_token_param,
            # 日次認証（花名册 + auth テーブルは人事スタック所有・名前参照）
            "ROSTER_TABLE": roster_table,
            "AUTH_TABLE": auth_table,
            # Bedrock：意图 / RAG 生成 / 向量
            "BEDROCK_ENABLED": c("bedrockEnabled", "false"),
            "BEDROCK_MODEL_ID": c("bedrockModelId", "anthropic.claude-3-haiku-20240307-v1:0"),
            "BEDROCK_CHAT_MODEL_ID": c("bedrockChatModelId", "jp.anthropic.claude-haiku-4-5-20251001-v1:0"),
            "BEDROCK_EMBED_MODEL_ID": c("bedrockEmbedModelId", "amazon.titan-embed-text-v2:0"),
            "BEDROCK_EMBED_DIM": c("bedrockEmbedDim", "256"),
            # Zoom（demo 桩）
            "ZOOM_ENABLED": c("zoomEnabled", "false"),
            "ZOOM_SECRET_NAME": f"{app_name}/{stage}/zoom",
            "ZOOM_USER_ID": "me",
        }

        def fn(name, handler):
            return lambda_.Function(
                self, name,
                runtime=lambda_.Runtime.PYTHON_3_12,
                architecture=lambda_.Architecture.ARM_64,
                handler=handler,
                code=lambda_.Code.from_asset("../src"),  # 无第三方依赖，直接打包 src/
                memory_size=1024,
                timeout=Duration.seconds(20),
                environment=env,
            )

        bedrock_policy = iam.PolicyStatement(
            actions=["bedrock:InvokeModel"], resources=["*"]
        )
        zoom_secret_policy = iam.PolicyStatement(
            actions=["secretsmanager:GetSecretValue"],
            resources=[f"arn:aws:secretsmanager:{self.region}:{self.account}:secret:{app_name}/{stage}/zoom-*"],
        )
        ssm_policy = iam.PolicyStatement(
            actions=["ssm:GetParameter"],
            resources=[
                f"arn:aws:ssm:{self.region}:{self.account}:parameter{line_secret_param}",
                f"arn:aws:ssm:{self.region}:{self.account}:parameter{line_token_param}",
            ],
        )
        kms_policy = iam.PolicyStatement(
            actions=["kms:Decrypt"], resources=["*"],
            conditions={"StringEquals": {"kms:ViaService": f"ssm.{self.region}.amazonaws.com"}},
        )

        # ---------- Lambda：webhook（微信入口；受講者・講師兼用＝据来不动）----------
        webhook = fn("WebhookFunction", "handlers.webhook.handler")
        for t in (students, courses, enrollments, groups):
            t.grant_read_write_data(webhook)
        knowledge.grant_read_data(webhook)
        webhook.add_to_role_policy(bedrock_policy)
        webhook.add_to_role_policy(zoom_secret_policy)

        # ---------- Lambda：line-webhook（LINE 入口，講師専用门）----------
        line_webhook = fn("LineWebhookFunction", "handlers.line_webhook.handler")
        for t in (students, courses, enrollments, groups):
            t.grant_read_write_data(line_webhook)
        knowledge.grant_read_data(line_webhook)
        line_webhook.add_to_role_policy(bedrock_policy)
        line_webhook.add_to_role_policy(zoom_secret_policy)
        line_webhook.add_to_role_policy(ssm_policy)
        line_webhook.add_to_role_policy(kms_policy)
        # 跨栈：花名册(读 + lineUserId 紐付け) + 认证表(读写)
        line_webhook.add_to_role_policy(iam.PolicyStatement(
            actions=["dynamodb:GetItem", "dynamodb:Scan", "dynamodb:UpdateItem"],
            resources=[f"arn:aws:dynamodb:{self.region}:{self.account}:table/{roster_table}"],
        ))
        line_webhook.add_to_role_policy(iam.PolicyStatement(
            actions=["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:UpdateItem"],
            resources=[f"arn:aws:dynamodb:{self.region}:{self.account}:table/{auth_table}"],
        ))

        # ---------- Lambda：web（网站登录/成绩）----------
        web = fn("WebFunction", "handlers.web.handler")
        students.grant_read_data(web)
        results.grant_read_write_data(web)

        # ---------- Lambda：reminder（开课前 1h 提醒，多平台扇出含 LINE）----------
        reminder = fn("ReminderFunction", "handlers.reminder.handler")
        courses.grant_read_write_data(reminder)
        enrollments.grant_read_data(reminder)
        students.grant_read_data(reminder)
        reminder.add_to_role_policy(bedrock_policy)
        reminder.add_to_role_policy(ssm_policy)   # LINE push 需读凭证
        reminder.add_to_role_policy(kms_policy)
        events.Rule(
            self, "ReminderTick",
            schedule=events.Schedule.rate(Duration.minutes(10)),
            targets=[targets.LambdaFunction(reminder)],
        )

        # ---------- HTTP API（CORS 自动处理 OPTIONS）----------
        http = apigw.HttpApi(
            self, "HttpApi",
            cors_preflight=CorsPreflightOptions(
                allow_origins=["*"],
                allow_headers=["content-type"],
                allow_methods=[CorsHttpMethod.GET, CorsHttpMethod.POST, CorsHttpMethod.OPTIONS],
            ),
        )
        http.add_routes(
            path="/wechat", methods=[HttpMethod.GET, HttpMethod.POST],
            integration=HttpLambdaIntegration("WebhookInt", webhook),
        )
        http.add_routes(
            path="/line", methods=[HttpMethod.POST],
            integration=HttpLambdaIntegration("LineInt", line_webhook),
        )
        for p in ("/web/login", "/web/submit", "/web/results", "/web/my-results"):
            http.add_routes(
                path=p, methods=[HttpMethod.POST],
                integration=HttpLambdaIntegration(f"WebInt{p.replace('/', '-')}", web),
            )

        CfnOutput(self, "WeChatWebhookUrl", value=f"{http.api_endpoint}/wechat",
                  description="填到企业微信回调的 URL")
        CfnOutput(self, "LineWebhookUrl", value=f"{http.api_endpoint}/line",
                  description="填到 LINE Developers > Messaging API > Webhook URL（研修=講師専用）")
        CfnOutput(self, "WebLoginApiUrl", value=f"{http.api_endpoint}/web/login",
                  description="课程网站登录接口")
        CfnOutput(self, "LineSecretParam", value=line_secret_param)
        CfnOutput(self, "LineTokenParam", value=line_token_param)
        CfnOutput(self, "StudentsTableName", value=students.table_name)
        CfnOutput(self, "CoursesTableName", value=courses.table_name)
        CfnOutput(self, "EnrollmentsTableName", value=enrollments.table_name)
        CfnOutput(self, "GroupsTableName", value=groups.table_name)
        CfnOutput(self, "KnowledgeTableName", value=knowledge.table_name)
