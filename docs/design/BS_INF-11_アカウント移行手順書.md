# BrightStar 統合LINEアシスタント アカウント移行手順書

| 項目 | 内容 |
|---|---|
| 文書番号 | BS_INF-11 |
| 文書名 | アカウント移行手順書 |
| 移行元 | AWS アカウント 603319838936（個人） |
| 移行先 | 会社 AWS アカウント |
| リージョン | ap-northeast-1（変更なし） |
| LINE チャネル | 変更なし（5チャネルとも移行しない） |

移行対象：CloudFormation スタック5本・Lambda 11本・DynamoDB 15表・S3 1本・SSM SecureString 10件・SNS 1本・CloudWatch アラーム10件。

以降、PowerShell のプロファイル名は移行元 `bs-old`、移行先 `bs-new` とする。

---

## 1. 必要ソフトウェア

| ソフト | バージョン | 確認コマンド |
|---|---|---|
| AWS CLI | v2 | `aws --version` |
| Node.js | 20 以上 | `node --version` |
| npm | 10 以上 | `npm --version` |
| Python | 3.12 以上 | `python --version` |
| Git | 任意 | `git --version` |

未導入のものを入れる。

```powershell
winget install --id Amazon.AWSCLI -e
winget install --id OpenJS.NodeJS.LTS -e
winget install --id Python.Python.3.12 -e
winget install --id Git.Git -e
```

インストール後に PowerShell を開き直す。

```powershell
python -m pip install --upgrade pip
python -m pip install boto3
```

---

## 2. 事前確認（移行元）

### 2-1. プロファイル設定

```powershell
aws configure --profile bs-old
```

```powershell
$env:AWS_REGION = "ap-northeast-1"
$env:PYTHONUTF8 = "1"
aws sts get-caller-identity --profile bs-old --output table
```

`Account` が `603319838936` であること。

### 2-2. 現構成の記録

```powershell
$REPO = "C:\Users\sons\Downloads\aws-test\BrightStar_Line_System"
Set-Location $REPO
New-Item -ItemType Directory -Force "MIGRATION\before" | Out-Null
```

```powershell
aws cloudformation describe-stacks --profile bs-old --region ap-northeast-1 --query "Stacks[?contains(StackName,'rightstar')||contains(StackName,'kiCommute')].{Name:StackName,Status:StackStatus}" --output json | Out-File -Encoding utf8 MIGRATION\before\stacks.json
```

```powershell
aws lambda list-functions --profile bs-old --region ap-northeast-1 --query "Functions[?contains(FunctionName,'rightstar')||contains(FunctionName,'kiCommute')].{Name:FunctionName,Runtime:Runtime,Arch:Architectures[0],Mem:MemorySize,Timeout:Timeout}" --output json | Out-File -Encoding utf8 MIGRATION\before\lambda.json
```

```powershell
aws dynamodb list-tables --profile bs-old --region ap-northeast-1 --output json | Out-File -Encoding utf8 MIGRATION\before\tables.json
```

```powershell
aws ssm describe-parameters --profile bs-old --region ap-northeast-1 --query "Parameters[?starts_with(Name,'/brightstar')||starts_with(Name,'/eki-commute')].{Name:Name,Type:Type}" --output json | Out-File -Encoding utf8 MIGRATION\before\ssm.json
```

```powershell
aws cloudwatch describe-alarms --profile bs-old --region ap-northeast-1 --query "MetricAlarms[].AlarmName" --output json | Out-File -Encoding utf8 MIGRATION\before\alarms.json
```

### 2-3. 現 Webhook URL の記録

```powershell
foreach ($s in @("BrightstarHr-dev","BrightstarSoumu-dev","brightstar-shain-dev","EkiCommute-dev","brightstar-kenshu-dev")) {
  aws cloudformation describe-stacks --stack-name $s --profile bs-old --region ap-northeast-1 --query "Stacks[0].Outputs[?contains(OutputKey,'Url')].[OutputKey,OutputValue]" --output text
}
```

出力を `MIGRATION\before\urls.txt` に保存する。

```powershell
foreach ($s in @("BrightstarHr-dev","BrightstarSoumu-dev","brightstar-shain-dev","EkiCommute-dev","brightstar-kenshu-dev")) {
  "[$s]"
  aws cloudformation describe-stacks --stack-name $s --profile bs-old --region ap-northeast-1 --query "Stacks[0].Outputs[?contains(OutputKey,'Url')].[OutputKey,OutputValue]" --output text
} | Out-File -Encoding utf8 MIGRATION\before\urls.txt
```

### 2-4. 研修チャネルの環境変数（企業微信ほか）の退避

```powershell
$FN = aws cloudformation list-stack-resources --stack-name brightstar-kenshu-dev --profile bs-old --region ap-northeast-1 --query "StackResourceSummaries[?ResourceType=='AWS::Lambda::Function'&&contains(PhysicalResourceId,'LineWebhook')].PhysicalResourceId" --output text
aws lambda get-function-configuration --function-name $FN --profile bs-old --region ap-northeast-1 --query "Environment.Variables" --output json | Out-File -Encoding utf8 MIGRATION\before\kenshu_env.json
$FN
```

`MIGRATION\` は機密を含むため Git 管理外であること。

```powershell
Select-String -Path "$REPO\.gitignore" -Pattern "^MIGRATION/" -Quiet
```

`False` の場合は追記する。

```powershell
Add-Content -Path "$REPO\.gitignore" -Value "`nMIGRATION/"
```

### 2-5. リポジトリとデプロイの一致確認

```powershell
git -C $REPO status --short
git -C $REPO log origin/main..HEAD --oneline
python tools\verify_deploy_sync.py --profile bs-old
```

`RESULT: ALL IN SYNC` であること。

---

## 3. 事前準備（移行先）

### 3-1. 会社アカウントへ依頼する内容

| 項目 | 必要な内容 |
|---|---|
| IAM 主体 | CDK デプロイ可能な権限（CloudFormation / IAM / Lambda / DynamoDB / S3 / SSM / KMS / Events / Logs / SNS / CloudWatch / APIGateway） |
| アクセス方法 | IAM Identity Center（SSO）または IAM ユーザーのアクセスキー |
| リージョン | ap-northeast-1 の利用許可 |
| SCP | 上記サービスが SCP で禁止されていないこと |
| Bedrock | 利用する場合のみモデルアクセス有効化（現状は未使用） |

### 3-2. プロファイル設定

SSO の場合。

```powershell
aws configure sso --profile bs-new
aws sso login --profile bs-new
```

アクセスキーの場合。

```powershell
aws configure --profile bs-new
```

```powershell
aws sts get-caller-identity --profile bs-new --output table
aws configure get region --profile bs-new
```

`Account` が会社アカウント、`region` が `ap-northeast-1` であること。

```powershell
$NEWACC = aws sts get-caller-identity --profile bs-new --query Account --output text
$NEWACC
```

### 3-3. CDK ブートストラップ

```powershell
Set-Location "$REPO\jinji\cdk"
npm install
npx cdk bootstrap "aws://$NEWACC/ap-northeast-1" --profile bs-new
```

```powershell
aws cloudformation describe-stacks --stack-name CDKToolkit --profile bs-new --region ap-northeast-1 --query "Stacks[0].StackStatus" --output text
```

`CREATE_COMPLETE` または `UPDATE_COMPLETE` であること。

### 3-4. SSM SecureString の作成

移行元から読み出してそのまま移行先へ書き込む。値はファイルに落とさない。

```powershell
$PARAMS = @(
  "/brightstar-kenshu/dev/line/secret",
  "/brightstar-kenshu/dev/line/token",
  "/brightstar-hr/dev/line/secret",
  "/brightstar-hr/dev/line/token",
  "/brightstar-soumu/dev/line/secret",
  "/brightstar-soumu/dev/line/token",
  "/brightstar-shain/dev/line/secret",
  "/brightstar-shain/dev/line/token",
  "/eki-commute/dev/line/channel-secret",
  "/eki-commute/dev/line/channel-access-token"
)
foreach ($p in $PARAMS) {
  $v = aws ssm get-parameter --name $p --with-decryption --profile bs-old --region ap-northeast-1 --query "Parameter.Value" --output text
  aws ssm put-parameter --name $p --value $v --type SecureString --overwrite --profile bs-new --region ap-northeast-1 --output text | Out-Null
  "$p  OK"
  Remove-Variable v
}
```

```powershell
aws ssm describe-parameters --profile bs-new --region ap-northeast-1 --query "length(Parameters[?starts_with(Name,'/brightstar')||starts_with(Name,'/eki-commute')])" --output text
```

`10` であること。

### 3-5. 研修スタックの context 引数を組み立てる

```powershell
$env:KENSHU_ENV = Get-Content "$REPO\MIGRATION\before\kenshu_env.json" -Raw
$e = $env:KENSHU_ENV | ConvertFrom-Json
$map = [ordered]@{
  weComCorpId="WECOM_CORP_ID"; weComToken="WECOM_TOKEN"; weComAesKey="WECOM_AES_KEY"
  weComAgentId="WECOM_AGENT_ID"; weComSecret="WECOM_SECRET"; weComRelayUrl="WECOM_RELAY_URL"
  weComRelayAuth="WECOM_RELAY_AUTH"; weComKfOpenKfId="WECOM_KF_OPEN_KFID"
  teacherOpenids="TEACHER_OPENIDS"; teacherSignupCode="TEACHER_SIGNUP_CODE"
  cancelDeadlineHours="CANCEL_DEADLINE_HOURS"; loginCodeTtlDays="LOGIN_CODE_TTL_DAYS"
  bedrockEnabled="BEDROCK_ENABLED"; bedrockModelId="BEDROCK_MODEL_ID"
  bedrockChatModelId="BEDROCK_CHAT_MODEL_ID"; bedrockEmbedModelId="BEDROCK_EMBED_MODEL_ID"
  bedrockEmbedDim="BEDROCK_EMBED_DIM"; zoomEnabled="ZOOM_ENABLED"
}
$KCTX = @()
foreach ($k in $map.Keys) { $v = $e.($map[$k]); if ($v) { $KCTX += @("-c","$k=$v") } }
$KCTX.Count / 2
```

### 3-6. masterHrPrefix の扱いを決める

`masterHrPrefix` は `prefix + YYYYMMDD` を送ると人事/総務権限が付与されるテスト用バックドアである。
**2026-09-19 に移行元で無効化済**（context を渡さない＝空文字＝無効）。移行先でも渡さないこと。

| 方針 | デプロイ時の指定 |
|---|---|
| 無効化（既定・推奨） | 何も渡さない |
| 継続利用 | `-c masterHrPrefix=<新しい文字列>`。値はリポジトリに置かない |

```powershell
foreach ($f in @("brightstar-hr-dev-webhook","brightstar-hr-dev-reconcile","brightstar-soumu-dev-webhook","brightstar-soumu-dev-reminder")) {
  $v = aws lambda get-function-configuration --function-name $f --profile bs-new --region ap-northeast-1 --query "Environment.Variables.MASTER_HR_PREFIX" --output text
  "{0,-34} = '{1}'" -f $f, $v
}
```

すべて空であること。

---

## 4. 当日実行

### 4-1. 移行元を書き込み停止にする

LINE Developers の各チャネルで Webhook を一時的に無効化する（Messaging API 設定 → Webhook の利用 → オフ）。対象は 5 チャネルすべて。

### 4-2. データ書き出し（移行元）

```powershell
Set-Location $REPO
python tools\migrate_data.py export --profile bs-old --out MIGRATION\data
```

### 4-3. スタックのデプロイ（移行先）

人事スタックを最初に実行する。共有テーブルと S3 バケットを作成するため。

```powershell
Set-Location "$REPO\jinji\cdk"
npm install
npx cdk deploy BrightstarHr-dev --require-approval never --profile bs-new -c mailProofreadUrl="https://sons02-relay.tail5a0084.ts.net:8443/"
```

```powershell
Set-Location "$REPO\kenshu\cdk"
python -m pip install aws-cdk-lib constructs
npx cdk deploy brightstar-kenshu-dev --require-approval never --profile bs-new @KCTX
```

```powershell
Set-Location "$REPO\soumu\cdk"
npm install
npx cdk deploy BrightstarSoumu-dev --require-approval never --profile bs-new
```

```powershell
Set-Location "$REPO\shain\cdk"
npx cdk deploy brightstar-shain-dev --require-approval never --profile bs-new
```

```powershell
Set-Location "$REPO\eigyo\infra"
npm install
python build.py
npx cdk deploy EkiCommute-dev --require-approval never --profile bs-new
```

### 4-4. 新しい URL の取得

```powershell
Set-Location $REPO
New-Item -ItemType Directory -Force "MIGRATION\after" | Out-Null
foreach ($s in @("BrightstarHr-dev","BrightstarSoumu-dev","brightstar-shain-dev","EkiCommute-dev","brightstar-kenshu-dev")) {
  "[$s]"
  aws cloudformation describe-stacks --stack-name $s --profile bs-new --region ap-northeast-1 --query "Stacks[0].Outputs[?contains(OutputKey,'Url')].[OutputKey,OutputValue]" --output text
} | Tee-Object -FilePath MIGRATION\after\urls.txt
```

### 4-5. LINE Developers の Webhook URL 変更

https://developers.line.biz/console/ で各チャネルの Messaging API 設定を開き、Webhook URL を `MIGRATION\after\urls.txt` の値に差し替える。

| チャネル | 設定する値 |
|---|---|
| BS社員管理 | `brightstar-shain-dev` の LineWebhookUrl |
| BS総務 | `BrightstarSoumu-dev` の LineWebhookUrl |
| BS人事 | `BrightstarHr-dev` の LineWebhookUrl |
| BS営業 | `EkiCommute-dev` の LineWebhookUrl |
| BS研修 | `brightstar-kenshu-dev` の LineWebhookUrl（API Gateway の `/line`） |

各チャネルで「検証」を実行し成功すること。Webhook の利用をオンに戻す。

企業微信を使う場合は、企業微信の管理画面のコールバック URL を `brightstar-kenshu-dev` の WeChatWebhookUrl に差し替える。

### 4-6. SNS 通知先の登録

```powershell
$TOPIC = aws cloudformation describe-stacks --stack-name BrightstarHr-dev --profile bs-new --region ap-northeast-1 --query "Stacks[0].Outputs[?OutputKey=='AlertTopicArn'].OutputValue" --output text
aws sns subscribe --topic-arn $TOPIC --protocol email --notification-endpoint "<通知先メールアドレス>" --profile bs-new --region ap-northeast-1
```

届いた確認メールの `Confirm subscription` を開く。

```powershell
aws sns list-subscriptions-by-topic --topic-arn $TOPIC --profile bs-new --region ap-northeast-1 --query "Subscriptions[].{P:Protocol,E:Endpoint,A:SubscriptionArn}" --output table
```

`SubscriptionArn` が `PendingConfirmation` でないこと。

---

## 5. データ移行

### 5-1. 投入

```powershell
Set-Location $REPO
python tools\migrate_data.py import --profile bs-new --src MIGRATION\data
```

### 5-2. 件数照合

```powershell
python tools\migrate_data.py verify --profile bs-new --src MIGRATION\data
```

`RESULT: ALL OK` であること。

移行対象外のテーブル：`auth`（当日限り・TTL）、`session`（当日限り）、`CacheTable`（再取得可能）。

### 5-3. テンプレートの確認

```powershell
$BUCKET = aws cloudformation describe-stacks --stack-name BrightstarHr-dev --profile bs-new --region ap-northeast-1 --query "Stacks[0].Outputs[?OutputKey=='BucketName'].OutputValue" --output text
aws s3 ls "s3://$BUCKET/hr/template/" --profile bs-new --region ap-northeast-1
```

`勤務表.xlsx` と `交通費経費.xlsx` が存在すること。

---

## 6. 動作確認

### 6-1. リソース照合

```powershell
Set-Location $REPO
aws cloudformation describe-stacks --profile bs-new --region ap-northeast-1 --query "length(Stacks[?contains(StackName,'rightstar')||contains(StackName,'kiCommute')])" --output text
aws lambda list-functions --profile bs-new --region ap-northeast-1 --query "length(Functions[?contains(FunctionName,'rightstar')||contains(FunctionName,'kiCommute')])" --output text
aws dynamodb list-tables --profile bs-new --region ap-northeast-1 --query "length(TableNames)" --output text
aws cloudwatch describe-alarms --profile bs-new --region ap-northeast-1 --query "length(MetricAlarms)" --output text
```

期待値：スタック 5、DynamoDB 15、アラーム 10。Lambda は CDK 補助関数を含むため `MIGRATION\before\lambda.json` と突き合わせる。

### 6-2. コード同期の検証

```powershell
python tools\verify_deploy_sync.py --profile bs-new
```

`RESULT: ALL IN SYNC` であること。

### 6-3. ログ保持期間

```powershell
aws logs describe-log-groups --profile bs-new --region ap-northeast-1 --query "length(logGroups[?retentionInDays==null&&(contains(logGroupName,'brightstar')||contains(logGroupName,'Eki'))])" --output text
```

`0` であること。

### 6-4. リッチメニューの再登録

```powershell
Set-Location $REPO
$env:AWS_PROFILE = "bs-new"
python tools\richmenu.py
Remove-Item Env:\AWS_PROFILE
```

### 6-5. LINE 実機テスト

| # | チャネル | 操作 | 期待結果 |
|---|---|---|---|
| 1 | BS社員管理 | 「所属部署 お名前」を送信 | 本人として認証され言語選択が出る |
| 2 | BS社員管理 | 「テンプレ」 | 勤務表・交通費のテンプレートが届く |
| 3 | BS社員管理 | 記入済み Excel を送信 | 受付完了が返る |
| 4 | BS総務 | 「一覧」 | 提出状況に上記の提出が反映されている |
| 5 | BS総務 | 「未提出確認」 | 未提出者一覧が出る |
| 6 | BS総務 | 「一括DL」 | ZIP のダウンロードリンクが開ける |
| 7 | BS総務 | 「催促」 | BS社員管理に催促が届き「確認しました」ボタンが押せる |
| 8 | BS総務 | 「確認状況」 | 確認済/未確認の件数が出る |
| 9 | BS人事 | 「名簿」 | 移行した社員名簿が表示される |
| 10 | BS営業 | 「検索路線：新宿」 | 通勤コスト比較が返る |
| 11 | BS研修 | 「本日認証」 | 講師として認証される |

### 6-6. 定期実行の確認

```powershell
aws events list-rules --profile bs-new --region ap-northeast-1 --query "Rules[?contains(Name,'brightstar')].{Name:Name,Sched:ScheduleExpression,State:State}" --output table
```

4 ルートすべて `ENABLED` であること。

```powershell
$RFN = "brightstar-soumu-dev-reminder"
aws lambda invoke --function-name $RFN --payload '{\"trigger\":\"poll\"}' --profile bs-new --region ap-northeast-1 MIGRATION\after\poll.json --output text
Get-Content MIGRATION\after\poll.json
```

エラーにならないこと。

### 6-7. アラームの状態

```powershell
aws cloudwatch describe-alarms --profile bs-new --region ap-northeast-1 --query "MetricAlarms[].{Name:AlarmName,State:StateValue}" --output table
```

`ALARM` のものが無いこと。`brightstar-soumu-dev-scheduler-dead` は 10分間隔ポーラーが2回動くまで `INSUFFICIENT_DATA` のままでよい。

---

## 7. 切り戻し

移行先で問題が出た場合。

1. LINE Developers の Webhook URL を `MIGRATION\before\urls.txt` の値に戻す。
2. 移行元のスタックは削除していないため、そのまま再開する。
3. 移行先で投入したデータは、移行元には影響しない。

移行元の Webhook を再度オンにする。

---

## 8. 移行元の後始末

動作確認がすべて完了し、数日運用して問題がないことを確認してから実行する。

### 8-1. データの最終バックアップ

```powershell
Set-Location $REPO
python tools\migrate_data.py export --profile bs-old --out MIGRATION\final_backup
```

`MIGRATION\final_backup` を社内の保管先へ退避する。

### 8-2. 削除対象の確認

```powershell
aws cloudformation describe-stacks --profile bs-old --region ap-northeast-1 --query "Stacks[?contains(StackName,'rightstar')||contains(StackName,'kiCommute')].StackName" --output table
aws s3 ls --profile bs-old | Select-String brightstar
aws dynamodb list-tables --profile bs-old --region ap-northeast-1 --output table
```

### 8-3. 削除

スタック削除で DynamoDB と S3 の中身も消える（`RemovalPolicy.DESTROY`）。8-1 のバックアップを取得済みであることを確認してから実行する。

```powershell
foreach ($s in @("brightstar-shain-dev","BrightstarSoumu-dev","EkiCommute-dev","brightstar-kenshu-dev","BrightstarHr-dev")) {
  aws cloudformation delete-stack --stack-name $s --profile bs-old --region ap-northeast-1
  aws cloudformation wait stack-delete-complete --stack-name $s --profile bs-old --region ap-northeast-1
  "$s deleted"
}
```

人事スタックは他4スタックが参照しているため最後に削除する。

```powershell
foreach ($p in $PARAMS) { aws ssm delete-parameter --name $p --profile bs-old --region ap-northeast-1 }
```

```powershell
aws logs describe-log-groups --profile bs-old --region ap-northeast-1 --query "logGroups[?contains(logGroupName,'brightstar')||contains(logGroupName,'Eki')].logGroupName" --output text
```

残ったロググループを削除する。

```powershell
$LG = aws logs describe-log-groups --profile bs-old --region ap-northeast-1 --query "logGroups[?contains(logGroupName,'brightstar')||contains(logGroupName,'Eki')].logGroupName" --output text
foreach ($g in ($LG -split "\s+" | Where-Object {$_})) { aws logs delete-log-group --log-group-name $g --profile bs-old --region ap-northeast-1; "$g deleted" }
```

### 8-4. 最終確認

```powershell
aws cloudformation describe-stacks --profile bs-old --region ap-northeast-1 --query "length(Stacks[?contains(StackName,'rightstar')||contains(StackName,'kiCommute')])" --output text
aws dynamodb list-tables --profile bs-old --region ap-northeast-1 --query "length(TableNames)" --output text
```

いずれも `0` であること。

---

## 9. 移行に伴う変更点

| 項目 | 移行前 | 移行後 |
|---|---|---|
| Lambda Function URL | `https://<旧ID>.lambda-url.ap-northeast-1.on.aws/` | 新規発行され変わる |
| API Gateway URL | `https://2xqgja49x8.execute-api…` | 新規発行され変わる |
| S3 バケット名 | `brightstar-hr-dev-603319838936` | `brightstar-hr-dev-<新アカウントID>` |
| EkiCommute のテーブル名 | `EkiCommute-dev-StaffTable11B9C6C0-…` | ハッシュ部分が変わる |
| DynamoDB テーブル名（上記以外） | 変更なし | 変更なし |
| SSM パラメータ名 | 変更なし | 変更なし |
| LINE チャネル ID / シークレット / トークン | 変更なし | 変更なし |
| リッチメニュー | LINE 側に保持 | 変更なし（6-4 は再登録のみ） |

---

## 10. 関連文書

- [BS_INF-10 CDK構成管理・デプロイ設計書](BS_INF-10_CDK構成管理・デプロイ設計書.md)
- [BS_INF-03 DynamoDB設計書](BS_INF-03_DynamoDB設計書.md)
- [BS_INF-08 SSM・暗号化設計書](BS_INF-08_SSM・暗号化設計書.md)
