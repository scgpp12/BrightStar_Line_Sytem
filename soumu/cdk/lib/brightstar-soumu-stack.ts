import * as path from "path";
import * as cdk from "aws-cdk-lib";
import { Construct } from "constructs";
import * as dynamodb from "aws-cdk-lib/aws-dynamodb";
import * as s3 from "aws-cdk-lib/aws-s3";
import * as lambda from "aws-cdk-lib/aws-lambda";
import * as logs from "aws-cdk-lib/aws-logs";
import * as iam from "aws-cdk-lib/aws-iam";
import * as events from "aws-cdk-lib/aws-events";
import * as targets from "aws-cdk-lib/aws-events-targets";
import * as sns from "aws-cdk-lib/aws-sns";
import * as cloudwatch from "aws-cdk-lib/aws-cloudwatch";
import * as cwActions from "aws-cdk-lib/aws-cloudwatch-actions";

export interface BrightstarSoumuStackProps extends cdk.StackProps {
  stage: string;
}

/**
 * 総務（soumu）チャネル。勤怠(勤務表)・通勤費(経費)の「提出管理・催促」を担当。
 *
 * 重要（既存格局を壊さない方針）：
 * - データ（roster / submissions / auth / employees / S3）は **brightstar-hr 栈が所有**。
 *   本栈は新規作成せず **既存リソースを名前で参照** し、Lambda ロールに読み書き権限のみ付与する。
 * - 従業員の提出は「社員(shain)」のまま。人事(jinji)からは提出管理・催促を移管。
 */
export class BrightstarSoumuStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props: BrightstarSoumuStackProps) {
    super(scope, id, props);

    const stage = props.stage;
    const appName = "brightstar-soumu";
    // データは brightstar-hr 栈のものを共有する
    const hrPrefix = `brightstar-hr-${stage}`;

    cdk.Tags.of(this).add("Project", appName);
    cdk.Tags.of(this).add("ManagedBy", "cdk");
    cdk.Tags.of(this).add("Stage", stage);

    const reminderCron =
      this.node.tryGetContext("reminderCron") || "cron(0 0 25,28 * ? *)";
    const hrUserIds = this.node.tryGetContext("hrUserIds") || "";

    // 総務チャネル専用の LINE 凭证（SSM SecureString、値は deploy 外で put、git に入れない）
    const lineSecretParam = `/${appName}/${stage}/line/secret`;
    const lineTokenParam = `/${appName}/${stage}/line/token`;
    // 催促 push は「社員(shain)」channel の token で送る（社員アシスタント側に届ける）
    const shainTokenParam = `/brightstar-shain/${stage}/line/token`;

    // ---------------- 既存リソースを参照（新規作成しない） ----------------
    const employees = dynamodb.Table.fromTableName(
      this, "EmployeesTable", `${hrPrefix}-employees`);
    const roster = dynamodb.Table.fromTableName(
      this, "RosterTable", `${hrPrefix}-roster`);
    const auth = dynamodb.Table.fromTableName(
      this, "AuthTable", `${hrPrefix}-auth`);
    // submissions は GSI1 を使うため grantIndexPermissions を有効化
    const submissions = dynamodb.Table.fromTableAttributes(this, "SubmissionsTable", {
      tableName: `${hrPrefix}-submissions`,
      grantIndexPermissions: true,
    });
    const bucket = s3.Bucket.fromBucketName(
      this, "DataBucket", `${hrPrefix}-${this.account}`);

    // ---------------- 催促予約テーブル（本栈所有・唯一の新規データ） ----------------
    const bookings = new dynamodb.Table(this, "BookingsTable", {
      tableName: `${appName}-${stage}-bookings`,
      partitionKey: { name: "bookingId", type: dynamodb.AttributeType.STRING },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      encryption: dynamodb.TableEncryption.AWS_MANAGED,
      removalPolicy: stage === "prod" ? cdk.RemovalPolicy.RETAIN : cdk.RemovalPolicy.DESTROY,
    });

    // ---------------- Lambda ----------------
    const code = lambda.Code.fromAsset(path.join(__dirname, "../../lambda"));
    // ---------------- 配信バッチ/既読確認テーブル（#10） ----------------
    const broadcasts = new dynamodb.Table(this, "BroadcastsTable", {
      tableName: `${appName}-${stage}-broadcasts`,
      partitionKey: { name: "bcastId", type: dynamodb.AttributeType.STRING },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      encryption: dynamodb.TableEncryption.AWS_MANAGED,
      removalPolicy: stage === "prod" ? cdk.RemovalPolicy.RETAIN : cdk.RemovalPolicy.DESTROY,
    });

    const commonEnv: Record<string, string> = {
      APP_NAME: appName,
      STAGE: stage,
      EMPLOYEES_TABLE: `${hrPrefix}-employees`,
      ROSTER_TABLE: `${hrPrefix}-roster`,
      AUTH_TABLE: `${hrPrefix}-auth`,
      SUBMISSIONS_TABLE: `${hrPrefix}-submissions`,
      SUBMISSIONS_GSI1: "GSI1",
      BOOKINGS_TABLE: `${appName}-${stage}-bookings`,
      BROADCASTS_TABLE: `${appName}-${stage}-broadcasts`,
      BUCKET_NAME: `${hrPrefix}-${this.account}`,
      PRESIGN_TTL: "3600",
      HR_USERIDS: hrUserIds,
      LINE_SECRET_PARAM: lineSecretParam,
      LINE_TOKEN_PARAM: lineTokenParam,
      // テスト用バックドア：「sonsik+YYYYMMDD」で1時間だけ総務権限（-c masterHrPrefix=sonsik）
      MASTER_HR_PREFIX: this.node.tryGetContext("masterHrPrefix") || "",
      BEDROCK_ENABLED: "false",
      TZ: "Asia/Tokyo",
    };

    const reminderFn = new lambda.Function(this, "ReminderFunction", {
      functionName: `${appName}-${stage}-reminder`,
      runtime: lambda.Runtime.PYTHON_3_12,
      architecture: lambda.Architecture.ARM_64,
      handler: "handlers.reminder.handler",
      code,
      memorySize: 256,
      timeout: cdk.Duration.seconds(60),
      // ログに氏名等が出るため無期限保持にしない（月次業務1周期を追える30日）
      logRetention: logs.RetentionDays.ONE_MONTH,
      // 催促は社員(shain)のtokenで送る → リマインドは社員アシスタントに届く
      environment: { ...commonEnv, PUSH_TOKEN_PARAM: shainTokenParam },
    });

    const webhookFn = new lambda.Function(this, "WebhookFunction", {
      functionName: `${appName}-${stage}-webhook`,
      runtime: lambda.Runtime.PYTHON_3_12,
      architecture: lambda.Architecture.ARM_64,
      handler: "handlers.line_webhook.handler",
      code,
      memorySize: 512,                                 // 一括DL の zip 生成に余裕
      timeout: cdk.Duration.seconds(29),
      logRetention: logs.RetentionDays.ONE_MONTH,
      environment: { ...commonEnv, REMINDER_FUNCTION_NAME: reminderFn.functionName },
    });

    // ---------------- 権限（最小化・既存テーブル/バケットへ付与） ----------------
    employees.grantReadWriteData(webhookFn);
    roster.grantReadWriteData(webhookFn);              // 認証で lineUserId バインド
    submissions.grantReadWriteData(webhookFn);
    auth.grantReadWriteData(webhookFn);                // 日次認証状態
    bucket.grantReadWrite(webhookFn);                  // 一括DL zip
    employees.grantReadData(reminderFn);
    roster.grantReadData(reminderFn);
    submissions.grantReadData(reminderFn);
    bookings.grantReadWriteData(webhookFn);            // 催促予約の登録/取消
    bookings.grantReadWriteData(reminderFn);           // ポーラーが実行済みへ更新
    broadcasts.grantReadWriteData(webhookFn);          // 確認状況の参照
    broadcasts.grantReadWriteData(reminderFn);         // 配信バッチの作成
    reminderFn.grantInvoke(webhookFn);

    // 読 SSM SecureString（2参数）+ SSM 経由 KMS 解密
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
    // reminderFn は社員(shain)のtokenも読む（催促を社員アシスタントから送るため）
    reminderFn.addToRolePolicy(new iam.PolicyStatement({
      actions: ["ssm:GetParameter"],
      resources: [`arn:aws:ssm:${this.region}:${this.account}:parameter${shainTokenParam}`],
    }));

    // ---------------- Function URL（LINE webhook 入口） ----------------
    const fnUrl = webhookFn.addFunctionUrl({
      authType: lambda.FunctionUrlAuthType.NONE,
    });

    // ---------------- EventBridge 定时催促 ----------------
    new events.Rule(this, "ReminderSchedule", {
      ruleName: `${appName}-${stage}-reminder-schedule`,
      schedule: events.Schedule.expression(reminderCron),
      targets: [
        new targets.LambdaFunction(reminderFn, {
          event: events.RuleTargetInput.fromObject({ trigger: "schedule" }),
        }),
      ],
    });

    // ---------------- 催促予約ポーラー（10分間隔で期限到来分を実行） ----------------
    new events.Rule(this, "BookingPoller", {
      ruleName: `${appName}-${stage}-booking-poller`,
      schedule: events.Schedule.rate(cdk.Duration.minutes(10)),
      targets: [
        new targets.LambdaFunction(reminderFn, {
          event: events.RuleTargetInput.fromObject({ trigger: "poll" }),
        }),
      ],
    });

    // ---------------- アラーム ----------------
    // 通知先トピックは人事スタックが所有。ARN 文字列で参照する（依存を作らない）
    const alertTopic = sns.Topic.fromTopicArn(
      this, "AlertTopic",
      `arn:aws:sns:${this.region}:${this.account}:brightstar-ops-${stage}-alerts`);

    const alarm = (
      id: string, name: string, metric: cloudwatch.Metric,
      threshold: number, evalPeriods: number, desc: string,
      cmp: cloudwatch.ComparisonOperator = cloudwatch.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
      missing: cloudwatch.TreatMissingData = cloudwatch.TreatMissingData.NOT_BREACHING,
    ) => {
      const a = new cloudwatch.Alarm(this, id, {
        alarmName: name, alarmDescription: desc, metric, threshold,
        evaluationPeriods: evalPeriods, comparisonOperator: cmp, treatMissingData: missing,
      });
      a.addAlarmAction(new cwActions.SnsAction(alertTopic));
      return a;
    };

    // 最優先：催促が送れていない＝業務が止まる
    alarm("ReminderErrorAlarm", `${appName}-${stage}-reminder-errors`,
      reminderFn.metricErrors({ period: cdk.Duration.minutes(5) }), 1, 1,
      "催促/予約催促/一斉送信の送信処理が失敗");

    alarm("SoumuWebhookErrorAlarm", `${appName}-${stage}-webhook-errors`,
      webhookFn.metricErrors({ period: cdk.Duration.minutes(5) }), 5, 1,
      "総務チャネルの webhook が継続的にエラー");

    alarm("SoumuWebhookThrottleAlarm", `${appName}-${stage}-webhook-throttles`,
      webhookFn.metricThrottles({ period: cdk.Duration.minutes(5) }), 1, 1,
      "総務チャネルの webhook が同時実行数上限に到達");

    // 定時催促のルールが「起動に失敗」した場合の検知
    alarm("ReminderRuleFailedAlarm", `${appName}-${stage}-reminder-rule-failed`,
      new cloudwatch.Metric({
        namespace: "AWS/Events", metricName: "FailedInvocations",
        dimensionsMap: { RuleName: `${appName}-${stage}-reminder-schedule` },
        statistic: "Sum", period: cdk.Duration.hours(1),
      }), 1, 1, "定時催促のEventBridgeルールがLambda起動に失敗");

    // 生存確認：10分間隔のポーラーが2時間動いていない＝定期実行系そのものが停止
    // （月2回しか動かない催促は「動かなかったこと」を直接は検知できないため、
    //   同じ EventBridge を使う高頻度ルールをカナリアにする）
    alarm("SchedulerDeadAlarm", `${appName}-${stage}-scheduler-dead`,
      new cloudwatch.Metric({
        namespace: "AWS/Events", metricName: "Invocations",
        dimensionsMap: { RuleName: `${appName}-${stage}-booking-poller` },
        statistic: "Sum", period: cdk.Duration.hours(1),
      }), 1, 2,
      "定期実行が2時間停止（EventBridgeルール無効化・削除等）。月次の自動催促も動かない",
      cloudwatch.ComparisonOperator.LESS_THAN_THRESHOLD,
      cloudwatch.TreatMissingData.BREACHING);

    // ---------------- 输出 ----------------
    new cdk.CfnOutput(this, "LineWebhookUrl", {
      value: fnUrl.url,
      description: "总务 channel の Webhook URL（LINE Developers > Messaging API、末尾の余分なスラッシュ不要）",
    });
    new cdk.CfnOutput(this, "LineSecretParam", { value: lineSecretParam });
    new cdk.CfnOutput(this, "LineTokenParam", { value: lineTokenParam });
    new cdk.CfnOutput(this, "Region", { value: this.region });
  }
}
