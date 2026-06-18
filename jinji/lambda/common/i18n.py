"""文案（日本語デフォルト、中文併記）。社内ツールにつき簡潔に。"""
from . import config

# 提交类型显示名
TYPE_LABELS = {
    "kintai": {"ja": "作業時間記録簿", "zh": "考勤（作业时间）"},
    "commute": {"ja": "経費", "zh": "经费"},
}

_T = {
    "welcome": {
        "ja": "ようこそ BrightStar 社内アシスタントへ。\n毎月「作業時間記録簿」と「経費」のExcelをこのトークに送って提出してください。",
        "zh": "欢迎使用 BrightStar 社内助手。\n每月把「作业时间记录簿（考勤）」和「经费」Excel 发到这里即可提交。",
    },
    "ask_name": {
        "ja": "お名前（フルネーム）を入力してください。\n※社員名簿と一致する必要があります。",
        "zh": "请输入姓名（全名）。\n※需与员工花名册一致。",
    },
    "ask_dept": {"ja": "所属部署を入力してください。", "zh": "请输入部门。"},
    "not_in_roster": {
        "ja": "「{name}」さんは社員名簿に見つかりませんでした。お名前を再確認するか、人事にご連絡ください。",
        "zh": "花名册里没有找到「{name}」。请核对姓名，或联系人事添加。",
    },
    "dup_name": {
        "ja": "「{name}」が名簿に複数います。人事にご連絡ください。",
        "zh": "花名册里有多个「{name}」，请联系人事处理。",
    },
    "line_unregistered": {"ja": "※Line未登録", "zh": "※Line未登录"},
    "registered": {
        "ja": "登録完了：{name}（{dept}）\n\n{menu}",
        "zh": "注册完成：{name}（{dept}）\n\n{menu}",
    },
    "menu_employee": {
        "ja": "■ 使い方\n・「作業時間記録簿」「経費」のExcelを送る → 提出（再提出も可）\n・テンプレ → 空白様式\n・履歴 → 自分の提出（ダウンロード可）",
        "zh": "■ 用法\n・发「作业时间记录簿」「经费」Excel → 提交（可重复提交）\n・模板 → 取空白样式\n・履历 → 看自己的提交（可下载）",
    },
    "menu_hr": {
        "ja": "■ 人事メニュー\n・未提出確認[202606] → 未提出者（月省略で当月）\n・一覧[202606] → 全員の提出状況\n・催促[202606] → 未提出者に督促\n・一括DL → 当月の提出を一括ダウンロード\n（個人提出も可：Excel／テンプレ／履歴）",
        "zh": "■ 人事菜单\n・未提出確認[202606] → 未提交者（省略月份=当月）\n・一覧[202606] → 全员提交情况\n・催促[202606] → 给未提交者发提醒\n・一括DL → 打包下载当月全部提交\n（也可个人提交：Excel／模板／履历）",
    },
    "ask_type": {
        "ja": "これは「作業時間記録簿（勤怠）」ですか「経費」ですか？ どちらか送信してください。",
        "zh": "这是「作业时间记录簿（考勤）」还是「经费」？请回复其一。",
    },
    "submit_ok": {
        "ja": "✅ {period} の{label}を受け付けました。",
        "zh": "✅ 已收到 {period} 的{label}。",
    },
    "submit_fail": {
        "ja": "❌ ファイルの取得に失敗しました。もう一度お試しください。",
        "zh": "❌ 文件获取失败，请重试。",
    },
    "period_mismatch": {
        "ja": "❌ アップロードできません。\n{label}の年月（{found}）が提出月（{expected}）と一致しません。\n{expected} 分のファイルをご提出ください。",
        "zh": "❌ 无法上传。\n{label}内的年月（{found}）与提交月份（{expected}）不一致。\n请提交 {expected} 月份的文件。",
    },
    "period_unreadable": {
        "ja": "❌ アップロードできません。\nファイルから年月を確認できませんでした（{label}）。所定セルに年月が入った正しいテンプレートをご利用ください。",
        "zh": "❌ 无法上传。\n无法从文件读取年月（{label}）。请使用所定单元格填有年月的正确模板。",
    },
    "name_mismatch": {
        "ja": "❌ アップロードできません。\nファイルの氏名（{found}）が登録氏名（{want}）と一致しません。\nご自身の勤務表かご確認のうえ、再提出してください。",
        "zh": "❌ 无法上传。\n文件中的姓名（{found}）与登记姓名（{want}）不一致。\n请确认是否为本人的勤务表后再次提交。",
    },
    "holiday_work_warn": {
        "ja": "⚠️ 休日に勤務時間が入力されています：{dates}\nお間違いなければそのままで結構です。誤りの場合は、修正のうえ再提出してください。",
        "zh": "⚠️ 以下休息日填写了工时：{dates}\n如无误请忽略；若有误请修正后重新提交。",
    },
    "date_incomplete_warn": {
        "ja": "⚠️ 5 行目の日付が揃っていません（未入力）：{days}\n1 日〜月末まで日付をご確認ください。誤りの場合は、修正のうえ再提出してください。",
        "zh": "⚠️ 第5行日期不完整（未填写）：{days}\n请确认从1号到月末的日期。若有误请修正后重新提交。",
    },
    "templates": {
        "ja": "空白様式（{ttl}分有効）：\n作業時間記録簿：{kintai}\n経費：{commute}",
        "zh": "空白样式（{ttl}分钟有效）：\n作业时间记录簿：{kintai}\n经费：{commute}",
    },
    "no_history": {"ja": "提出履歴はまだありません。", "zh": "暂无提交记录。"},
    "hr_only": {"ja": "この操作は人事のみ可能です。", "zh": "该操作仅限人事。"},
    "remind_text": {
        "ja": "【リマインド】{period} の{labels}が未提出です。期日までにご提出ください。",
        "zh": "【提醒】{period} 的{labels}尚未提交，请按期提交。",
    },
    "remind_sent": {"ja": "未提出者 {n} 名に督促を送信しました。", "zh": "已向 {n} 名未提交者发送提醒。"},
    "all_submitted": {"ja": "全員提出済みです 🎉", "zh": "全员已提交 🎉"},
    "resubmit_alert": {
        "ja": "⚠️【再提出確認】{name} さんが {period} の{label}を再提出しました。内容のご確認をお願いします。",
        "zh": "⚠️【重复提交确认】{name} 重新提交了 {period} 的{label}，请人事确认内容。",
    },
    "fallback": {
        "ja": "コマンドが分かりませんでした。「テンプレ」「履歴」などをお試しください。",
        "zh": "未识别的指令。可试「模板」「履历」等。",
    },
    # ---- 花名册 CRUD（人事） ----
    "roster_added": {
        "ja": "✅ 名簿に追加：{eid} {name}（{dept}／{role}）",
        "zh": "✅ 已加入花名册：{eid} {name}（{dept}／{role}）",
    },
    "roster_updated": {
        "ja": "✅ 名簿変更：{eid} {name} の{field}を「{value}」に",
        "zh": "✅ 已修改花名册：{eid} {name} 的{field} → 「{value}」",
    },
    "roster_deleted": {
        "ja": "🗑 名簿から削除：{eid} {name}",
        "zh": "🗑 已从花名册删除：{eid} {name}",
    },
    "roster_not_found": {
        "ja": "対象が見つかりません：{q}", "zh": "未找到对象：{q}",
    },
    "roster_change_alert": {
        "ja": "📢【名簿変更通知】{who} が変更しました：\n{detail}",
        "zh": "📢【花名册变更通知】{who} 做了修改：\n{detail}",
    },
    "crud_usage": {
        "ja": ("社員名簿の操作：\n"
               "・名簿 → 一覧\n"
               "・社員追加 山田太郎 開発部 [hr]\n"
               "・社員変更 E003 部署 営業部（部署/名前/role）\n"
               "・社員削除 E003"),
        "zh": ("花名册操作：\n"
               "・名簿 → 列表\n"
               "・社員追加 山田太郎 開発部 [hr]\n"
               "・社員変更 E003 部署 営業部（部署/名前/role）\n"
               "・社員削除 E003"),
    },
}


def T(key, lang=None, **kw):
    lang = lang or config.DEFAULT_LANG
    s = _T.get(key, {}).get(lang) or _T.get(key, {}).get("ja", key)
    return s.format(**kw) if kw else s


def type_label(type_, lang=None):
    lang = lang or config.DEFAULT_LANG
    return TYPE_LABELS.get(type_, {}).get(lang, type_)
