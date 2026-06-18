import * as path from "path";
import * as cdk from "aws-cdk-lib";
import { Construct } from "constructs";
import * as dynamodb from "aws-cdk-lib/aws-dynamodb";
import * as lambda from "aws-cdk-lib/aws-lambda";
import * as logs from "aws-cdk-lib/aws-logs";
import * as iam from "aws-cdk-lib/aws-iam";

/**
 * 要員通勤コストツールの最小構成:
 *   - DynamoDB: staff（要員。PK=staff_id=BrightStar empId）/ cache（通勤キャッシュ。TTL付き）
 *   - Lambda(Python3.12): ekitan スクレイパ + 一括比較。依存(requests/bs4)はレイヤ。
 *   - Function URL: 認証は AWS_IAM（公開すると外部からスクレイピングを誘発できてしまうため）。
 * 規模が小さいので DynamoDB は従量(オンデマンド)課金。
 */
export class CommuteStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props?: cdk.StackProps) {
    super(scope, id, props);

    // --- DynamoDB: 要員テーブル（BrightStar roster と同じ empId をキーに） ---
    const staffTable = new dynamodb.Table(this, "StaffTable", {
      partitionKey: { name: "staff_id", type: dynamodb.AttributeType.STRING },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      removalPolicy: cdk.RemovalPolicy.DESTROY, // 検証用。本番は RETAIN を検討
    });

    // --- DynamoDB: 通勤キャッシュ（30日 TTL で自動失効） ---
    const cacheTable = new dynamodb.Table(this, "CacheTable", {
      partitionKey: { name: "cacheKey", type: dynamodb.AttributeType.STRING },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      timeToLiveAttribute: "ttl",
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    });

    // --- 依存レイヤ（requests / beautifulsoup4。infra/build.py で作成済み） ---
    const depsLayer = new lambda.LayerVersion(this, "DepsLayer", {
      code: lambda.Code.fromAsset(path.join(__dirname, "..", "build", "layer")),
      compatibleRuntimes: [lambda.Runtime.PYTHON_3_12],
      compatibleArchitectures: [lambda.Architecture.X86_64],
      description: "requests + beautifulsoup4",
    });

    // --- API Lambda ---
    const apiFn = new lambda.Function(this, "ApiFn", {
      runtime: lambda.Runtime.PYTHON_3_12,
      architecture: lambda.Architecture.X86_64,
      handler: "handler.handler",
      code: lambda.Code.fromAsset(path.join(__dirname, "..", "build", "lambda")),
      layers: [depsLayer],
      timeout: cdk.Duration.minutes(5), // スクレイピングは sleep を挟むため長め
      memorySize: 256,
      logRetention: logs.RetentionDays.TWO_WEEKS,
      environment: {
        STAFF_TABLE: staffTable.tableName,
        CACHE_TABLE: cacheTable.tableName,
        REQUEST_DELAY_SEC: "3", // 責任あるクロール（リクエスト間スリープ）
        USE_BEDROCK_NORMALIZE: "1", // 中国語等を Bedrock で日本語駅名へ正規化（兜底）
        BEDROCK_MODEL_ID: "jp.anthropic.claude-haiku-4-5-20251001-v1:0",
      },
    });

    staffTable.grantReadWriteData(apiFn);
    cacheTable.grantReadWriteData(apiFn);

    // Bedrock 呼び出し権限（推論プロファイル経由なので model と profile 両方を許可）
    const bedrockPolicy = new iam.PolicyStatement({
      actions: ["bedrock:InvokeModel"],
      resources: [
        "arn:aws:bedrock:*::foundation-model/anthropic.claude-haiku-4-5-*",
        `arn:aws:bedrock:*:${this.account}:inference-profile/*anthropic.claude-haiku-4-5-*`,
      ],
    });
    apiFn.addToRolePolicy(bedrockPolicy);

    // Amazon Location（住所→最寄駅）。Places API はアカウントレベル・リソース不要。
    apiFn.addToRolePolicy(
      new iam.PolicyStatement({
        actions: ["geo-places:Geocode", "geo-places:SearchText", "geo-places:SearchNearby"],
        resources: ["*"],
      })
    );

    // --- Function URL（AWS_IAM 認証。SigV4 署名が必要） ---
    const fnUrl = apiFn.addFunctionUrl({
      authType: lambda.FunctionUrlAuthType.AWS_IAM,
    });

    new cdk.CfnOutput(this, "FunctionUrl", { value: fnUrl.url });
    new cdk.CfnOutput(this, "StaffTableName", { value: staffTable.tableName });
    new cdk.CfnOutput(this, "CacheTableName", { value: cacheTable.tableName });

    // --- LINE Webhook 用 Lambda（別関数。Function URL は公開だが署名検証で守る） ---
    // 認証情報は SSM SecureString から読む（値はコード/環境変数に置かない）。
    const lineSecretParam = "/eki-commute/dev/line/channel-secret";
    const lineTokenParam = "/eki-commute/dev/line/channel-access-token";

    const lineFn = new lambda.Function(this, "LineWebhookFn", {
      runtime: lambda.Runtime.PYTHON_3_12,
      architecture: lambda.Architecture.X86_64,
      handler: "line_handler.handler",
      code: lambda.Code.fromAsset(path.join(__dirname, "..", "build", "lambda")),
      layers: [depsLayer],
      timeout: cdk.Duration.minutes(5),
      memorySize: 256,
      logRetention: logs.RetentionDays.TWO_WEEKS,
      environment: {
        STAFF_TABLE: staffTable.tableName,
        CACHE_TABLE: cacheTable.tableName,
        REQUEST_DELAY_SEC: "3",
        LINE_SECRET_PARAM: lineSecretParam,
        LINE_TOKEN_PARAM: lineTokenParam,
        USE_BEDROCK_NORMALIZE: "1",
        BEDROCK_MODEL_ID: "jp.anthropic.claude-haiku-4-5-20251001-v1:0",
      },
    });

    staffTable.grantReadWriteData(lineFn);
    cacheTable.grantReadWriteData(lineFn);
    lineFn.addToRolePolicy(bedrockPolicy);

    // 非同期ワーカーとして自分自身を Event 呼び出しするための権限
    // （関数名は自動生成なのでスタック名プレフィクスのワイルドカードで循環参照を回避）
    lineFn.addToRolePolicy(
      new iam.PolicyStatement({
        actions: ["lambda:InvokeFunction"],
        resources: [`arn:aws:lambda:${this.region}:${this.account}:function:${this.stackName}-*`],
      })
    );

    // SSM SecureString 2件の読み取り権限（値が無い間はデプロイ可・実行時に取得）
    lineFn.addToRolePolicy(
      new iam.PolicyStatement({
        actions: ["ssm:GetParameter"],
        resources: [
          `arn:aws:ssm:${this.region}:${this.account}:parameter${lineSecretParam}`,
          `arn:aws:ssm:${this.region}:${this.account}:parameter${lineTokenParam}`,
        ],
      })
    );

    // LINE はこの URL に署名付き POST を送る（authType=NONE、署名検証はコード側）
    const lineUrl = lineFn.addFunctionUrl({
      authType: lambda.FunctionUrlAuthType.NONE,
    });
    new cdk.CfnOutput(this, "LineWebhookUrl", { value: lineUrl.url });
  }
}
