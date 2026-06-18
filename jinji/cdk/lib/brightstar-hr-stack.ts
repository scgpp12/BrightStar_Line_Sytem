import * as path from "path";
import * as cdk from "aws-cdk-lib";
import { Construct } from "constructs";
import * as dynamodb from "aws-cdk-lib/aws-dynamodb";
import * as s3 from "aws-cdk-lib/aws-s3";
import * as s3deploy from "aws-cdk-lib/aws-s3-deployment";
import * as lambda from "aws-cdk-lib/aws-lambda";
import * as iam from "aws-cdk-lib/aws-iam";
import * as events from "aws-cdk-lib/aws-events";
import * as targets from "aws-cdk-lib/aws-events-targets";

export interface BrightstarHrStackProps extends cdk.StackProps {
  stage: string;
}

export class BrightstarHrStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props: BrightstarHrStackProps) {
    super(scope, id, props);

    const stage = props.stage;
    const appName = "brightstar-hr";
    const prefix = `${appName}-${stage}`;
    const isProd = stage === "prod";

    // 数据资源：prod 保留，dev/staging 便于清理（统一打标签）
    const removalPolicy = isProd
      ? cdk.RemovalPolicy.RETAIN
      : cdk.RemovalPolicy.DESTROY;

    cdk.Tags.of(this).add("Project", appName);
    cdk.Tags.of(this).add("ManagedBy", "cdk");
    cdk.Tags.of(this).add("Stage", stage);

    const reminderCron =
      this.node.tryGetContext("reminderCron") || "cron(0 0 25,28 * ? *)";
    const hrUserIds = this.node.tryGetContext("hrUserIds") || "";

    // SSM 参数名（值为 SecureString，部署外单独写入，不入 git）
    const lineSecretParam = `/${appName}/${stage}/line/secret`;
    const lineTokenParam = `/${appName}/${stage}/line/token`;

    // ---------------- S3：提交物 + 空白模板 ----------------
    const bucket = new s3.Bucket(this, "DataBucket", {
      bucketName: `${prefix}-${this.account}`,
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      encryption: s3.BucketEncryption.S3_MANAGED,
      enforceSSL: true,
      versioned: true,
      removalPolicy,
      autoDeleteObjects: !isProd,
      lifecycleRules: [
        { id: "expire-pending", prefix: "pending/", expiration: cdk.Duration.days(7) },
        { id: "expire-exports", prefix: "exports/", expiration: cdk.Duration.days(3) },
        {
          // 提交物（按标签 lifecycle=managed 命中，模板/导出不受影响）：
          // 前 2 个月标准存储=即时下载；60 天后转 Deep Archive（最便宜，下载需先 restore）；
          // 1 年后删除。
          id: "submissions-archive-expire",
          tagFilters: { lifecycle: "managed" },
          transitions: [{
            storageClass: s3.StorageClass.DEEP_ARCHIVE,
            transitionAfter: cdk.Duration.days(60),
          }],
          expiration: cdk.Duration.days(365),
          // 重复提交产生的旧版本：同样归档并最终清理
          noncurrentVersionTransitions: [{
            storageClass: s3.StorageClass.DEEP_ARCHIVE,
            transitionAfter: cdk.Duration.days(60),
          }],
          noncurrentVersionExpiration: cdk.Duration.days(365),
        },
      ],
    });

    // 把本地 templates/ 目录同步到 s3://bucket/hr/template/
    new s3deploy.BucketDeployment(this, "TemplatesDeploy", {
      sources: [s3deploy.Source.asset(path.join(__dirname, "../../templates"))],
      destinationBucket: bucket,
      destinationKeyPrefix: "hr/template/",
      exclude: ["README.md"],
      prune: false,
    });

    // ---------------- DynamoDB ----------------
    const employees = new dynamodb.Table(this, "EmployeesTable", {
      tableName: `${prefix}-employees`,
      partitionKey: { name: "userId", type: dynamodb.AttributeType.STRING },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      encryption: dynamodb.TableEncryption.AWS_MANAGED,
      pointInTimeRecoverySpecification: { pointInTimeRecoveryEnabled: true },
      removalPolicy,
    });

    const roster = new dynamodb.Table(this, "RosterTable", {
      tableName: `${prefix}-roster`,
      partitionKey: { name: "empId", type: dynamodb.AttributeType.STRING },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      encryption: dynamodb.TableEncryption.AWS_MANAGED,
      pointInTimeRecoverySpecification: { pointInTimeRecoveryEnabled: true },
      removalPolicy,
    });

    const submissions = new dynamodb.Table(this, "SubmissionsTable", {
      tableName: `${prefix}-submissions`,
      partitionKey: { name: "userId", type: dynamodb.AttributeType.STRING },
      sortKey: { name: "sk", type: dynamodb.AttributeType.STRING },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      encryption: dynamodb.TableEncryption.AWS_MANAGED,
      pointInTimeRecoverySpecification: { pointInTimeRecoveryEnabled: true },
      removalPolicy,
    });
    submissions.addGlobalSecondaryIndex({
      indexName: "GSI1",
      partitionKey: { name: "gsi1pk", type: dynamodb.AttributeType.STRING },
      sortKey: { name: "gsi1sk", type: dynamodb.AttributeType.STRING },
      projectionType: dynamodb.ProjectionType.ALL,
    });

    // 全 channel 共有：日次認証状態（pk='<channel>#<userId>'）。研修/营业/社員 も跨栈で読み書き。
    const auth = new dynamodb.Table(this, "AuthTable", {
      tableName: `${prefix}-auth`,
      partitionKey: { name: "pk", type: dynamodb.AttributeType.STRING },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      encryption: dynamodb.TableEncryption.AWS_MANAGED,
      timeToLiveAttribute: "expireAt",   // 認証行は2日後に自動失効・削除
      removalPolicy,
    });

    // ---------------- Lambda ----------------
    const code = lambda.Code.fromAsset(path.join(__dirname, "../../lambda"));
    const commonEnv: Record<string, string> = {
      APP_NAME: appName,
      STAGE: stage,
      EMPLOYEES_TABLE: employees.tableName,
      ROSTER_TABLE: roster.tableName,
      AUTH_TABLE: auth.tableName,
      SUBMISSIONS_TABLE: submissions.tableName,
      SUBMISSIONS_GSI1: "GSI1",
      BUCKET_NAME: bucket.bucketName,
      PRESIGN_TTL: "3600",
      HR_USERIDS: hrUserIds,
      LINE_SECRET_PARAM: lineSecretParam,
      LINE_TOKEN_PARAM: lineTokenParam,
      MAIL_PROOFREAD_URL: this.node.tryGetContext("mailProofreadUrl") || "",
      BEDROCK_ENABLED: "false",
      TZ: "Asia/Tokyo",
    };

    const reminderFn = new lambda.Function(this, "ReminderFunction", {
      functionName: `${prefix}-reminder`,
      runtime: lambda.Runtime.PYTHON_3_12,
      architecture: lambda.Architecture.ARM_64,
      handler: "handlers.reminder.handler",
      code,
      memorySize: 256,
      timeout: cdk.Duration.seconds(60),
      environment: commonEnv,
    });

    const webhookFn = new lambda.Function(this, "WebhookFunction", {
      functionName: `${prefix}-webhook`,
      runtime: lambda.Runtime.PYTHON_3_12,
      architecture: lambda.Architecture.ARM_64,
      handler: "handlers.line_webhook.handler",
      code,
      memorySize: 512,                                 // 打包 zip 需要内存余量
      timeout: cdk.Duration.seconds(29),               // 含一括DL 打包
      environment: { ...commonEnv, REMINDER_FUNCTION_NAME: reminderFn.functionName },
    });

    // ---------------- 权限（最小化） ----------------
    employees.grantReadWriteData(webhookFn);
    roster.grantReadWriteData(webhookFn);              // 人事 CRUD 花名册
    submissions.grantReadWriteData(webhookFn);
    auth.grantReadWriteData(webhookFn);                // 日次認証状態
    bucket.grantReadWrite(webhookFn);
    employees.grantReadData(reminderFn);
    roster.grantReadData(reminderFn);
    submissions.grantReadData(reminderFn);
    reminderFn.grantInvoke(webhookFn);

    // 读 SSM SecureString（两个参数）+ 经 SSM 调用的 KMS 解密
    const ssmStmt = new iam.PolicyStatement({
      actions: ["ssm:GetParameter"],
      resources: [
        `arn:aws:ssm:${this.region}:${this.account}:parameter${lineSecretParam}`,
        `arn:aws:ssm:${this.region}:${this.account}:parameter${lineTokenParam}`,
      ],
    });
    const kmsStmt = new iam.PolicyStatement({
      actions: ["kms:Decrypt"],
      resources: ["*"],
      conditions: { StringEquals: { "kms:ViaService": `ssm.${this.region}.amazonaws.com` } },
    });
    webhookFn.addToRolePolicy(ssmStmt);
    webhookFn.addToRolePolicy(kmsStmt);
    reminderFn.addToRolePolicy(ssmStmt);
    reminderFn.addToRolePolicy(kmsStmt);

    // ---------------- Function URL（LINE webhook 入口） ----------------
    const fnUrl = webhookFn.addFunctionUrl({
      authType: lambda.FunctionUrlAuthType.NONE,
    });

    // ---------------- EventBridge 定时提醒 ----------------
    new events.Rule(this, "ReminderSchedule", {
      ruleName: `${prefix}-reminder-schedule`,
      schedule: events.Schedule.expression(reminderCron),
      targets: [
        new targets.LambdaFunction(reminderFn, {
          event: events.RuleTargetInput.fromObject({ trigger: "schedule" }),
        }),
      ],
    });

    // ---------------- 输出 ----------------
    new cdk.CfnOutput(this, "LineWebhookUrl", {
      value: fnUrl.url,
      description: "填到 LINE Developers > Messaging API > Webhook URL（末尾不要多斜杠）",
    });
    new cdk.CfnOutput(this, "BucketName", { value: bucket.bucketName });
    new cdk.CfnOutput(this, "LineSecretParam", { value: lineSecretParam });
    new cdk.CfnOutput(this, "LineTokenParam", { value: lineTokenParam });
    new cdk.CfnOutput(this, "Region", { value: this.region });
  }
}
