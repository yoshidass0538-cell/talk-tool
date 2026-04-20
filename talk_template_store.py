"""
トークスクリプトのセクション別テンプレートを保存するストア。

- 保存先: ikusei用スプレッドシート内の `talk_template_data` ワークシートのA1セルにJSON
- 初回はソース（1週間後FCトーク0314 / NURO1週間後FCトーク0402）のB列から
  デフォルトテンプレートをパースして使う
- マスタ画面で編集 → save_templates() で永続化
- 全ユーザー共有（st.cache_resource）
"""

import json
import os
import time

import streamlit as st
import gspread
from google.oauth2.service_account import Credentials

from talk_script_store import (
    TALK_SCRIPT_SHEET_ID,
    SCRIPT_SHEETS,
    _LOCAL_KEY_FILE,
)


WRITE_SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

# ikusei用スプレッドシート（書き込み可能）に同居させる
_IKUSEI_SHEET_ID_FALLBACK = "1aXKoCL_bppzw60ddYmtaGjqHHYCRRLyVU6z3ZxB7JbY"
TEMPLATE_WORKSHEET = "talk_template_data"
TEMPLATE_CELL = "A1"


# セクション順（デフォルト値。Google Sheetsに保存があればそちらを優先）
_DEFAULT_SONET_SECTIONS = [
    "アプローチ",
    "現時点の状況確認",
    "契約書の説明",
    "決済未登録",  # 決済登録日が空のときのみ表示
    "今後の流れ",
    "不備解消",
    "締め",
]

_DEFAULT_NURO_SECTIONS = [
    "アプローチ",
    "現時点の状況確認",
    "契約書の説明",
    "今後の流れ",
    "締め",
]

_DEFAULT_SECTIONS_BY_KIND = {
    "Sonet": _DEFAULT_SONET_SECTIONS,
    "NURO": _DEFAULT_NURO_SECTIONS,
}

# 後方互換: 既存コードが SECTIONS_BY_KIND を参照している箇所用
# get_sections_by_kind() を使うのが正しいが、初期値としてデフォルトを設定
SONET_SECTIONS = list(_DEFAULT_SONET_SECTIONS)
NURO_SECTIONS = list(_DEFAULT_NURO_SECTIONS)
SECTIONS_BY_KIND = {
    "Sonet": SONET_SECTIONS,
    "NURO": NURO_SECTIONS,
}

# 不備解消セクションの9種テンプレートキー（ダイコンステータス値）
SONET_FUBI_KEYS = [
    "工事日決定済み",
    "工事取得",
    "番ポ不備",
    "現地調査必要",
    "住所確認",
    "事前解約",
    "有派遣へ変更必要",
    "オーナー確認",
    "詳細確認待ち",
]

# 促進用トーク（代コン不備解消用）の5種テンプレートキー
SONET_SOKUSHIN_KEYS = [
    "工事取得3者間",
    "番ポ不備FC",
    "住所確認FC",
    "現地調査3者間",
    "有派遣変更3者間",
]

# 促進用トーク デフォルト（空テンプレ。マスタで編集する想定）
DEFAULT_SONET_SOKUSHIN: dict[str, str] = {k: "" for k in SONET_SOKUSHIN_KEYS}

# 締めセクションのバリエーションキー（Sonetのみ）
SONET_CLOSING_KEYS = [
    "利用回線あり",
    "利用回線不明",
]

# LINEテンプレのキー（Sonet/NURO共通）
LINE_TEMPLATE_KEYS = [
    "完了LINE",
    "留守LINE",
    "留守完了LINE",
]

# Sonet 締め デフォルトテンプレ
DEFAULT_SONET_CLOSING: dict[str, str] = {
    "利用回線あり": """また、工事完了後は弊社よりご利用頂いている○○光と2重契約にならないよう解約のご誘導いたしますのでそれまでお待ち頂ければと思います！
もし気になる点やご心配な点が出てきましたらお電話やLINEでお気軽にお問合せ頂ければと思います！！！
それでは今後もしっかりサポートさせて頂きますのでよろしくお願いいたします！！

本日はお時間ありがとうございました！失礼致します！""",

    "利用回線不明": """※利用回線不明の場合
また、現在ご利用頂いております光回線についてはどちらでご利用でしたでしょうか？
○○光→ありがとうございます。
今わからない→かしこまりました。
それではまた、工事終了したころにサポートのお電話も致しますのでそれまでにお調べ頂けますようお願いいたします。
それでは今後もしっかりサポートさせて頂きますのでよろしくお願いいたします！！

本日はお時間ありがとうございました！失礼致します！""",
}

# GASから移植: 不備解消9種のデフォルトテンプレート
DEFAULT_SONET_FUBI: dict[str, str] = {
    "工事日決定済み": """status大区分：40 工事日決定済み

まず工事日についてですが、現在○○/○○で申請進めさせて頂いておりまして、ご都合の方は問題なさそうでしょうか？

🙍：問題無いよ
↓
ありがとうございます！
工事の１週間前くらいになりましたらSMSにて詳細なお時間など通知されますのでご確認頂くようお願いします！
ーーーーーーーーーーーーーーーーーーーーーーーーーーーーーーー

status大区分：30 異動情報紐付済み、20 申込送付済み

まず工事日についてですが、現在○○/○○で申請進めさせて頂いておりまして、ご都合の方は問題なさそうでしょうか？

🙍：問題無いよ
↓
ありがとうございます！
また、万が一今回のお手続きで不備などがあった際弊社担当より再度お客様宛にご連絡をさせて頂きます。
その際今回決めて頂いた工事日が一度白紙になって取り直しになってしまうお客様もいらっしゃいますので
必ずしもこちらの日程で確定ではない旨だけご理解、ご了承頂けますようお願いいたします。
なお、問題無く取得した工事日で施工が可能な場合は工事の5日～3日前にはソネット本体からお客様宛にSMSにてご連絡も入りますのでよろしくお願いいたします。

■変更希望は三者間通話
※まずは家族の立ち合いなども勧める！
※直近過ぎる日にちに変更は出来ないのと工事日を変更する場合
今の確定している工事日は白紙になるから変更しようと思ったけど変更の希望日が空いてなかったから
元の取っていた工事日に戻すっていうのは出来ない

ソネット光工事調整窓口
1840120004016
ガイダンス　1
営業時間：10：00～19：00


【1週間後FC】
対話者：男性/女性
工事日決定：○○/○○
LINE送付：済み/未
送りバント案内：有/無
アウト：""",

    "工事取得": """今回お申込みいただいたソネット光の工事日の日程調整を私の方から窓口におつなぎさせていただき、
工事日の調整お願いしたいのですが、お時間ご問題ないでしょうか？

🙍：問題無いよ
↓
ありがとうございます。
では、担当のオペレーターにつながりましたら、工事日の調整をしたいですとお伝えお願いします。

固定電話利用なしの人↓
その際、先ほどもお伝えしましたが、今回、キャンペーンの適用条件で固定電話の契約が含まれてるお申し込みになりますので、
光電話の契約と光回線の契約ですねというような形でお伝えある場合ございますが、問題ないですとお答えお願いします。

ソネット光工事調整窓口
1840120004016
ガイダンス　1
営業時間：10：00～19：00

■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■
ーーーーーーーーーーー三者間通話ーーーーーーーーーーーーーー
■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■

ありがとうございます。
それでは○月〇日　AMorPM　の工事となりますので当日はお立合いの程よろしくお願いいたします。

ーーーーーーーーーーーーーーーーーーーーーーーーーーーーーーー

🙍：今あんまり時間ない
↓
かしこまりました。
そうしましたら工事日の調整については
再度お時間のご都合問題ないタイミングでお電話改めさせて頂きたいのですが
お電話いつ頃ですとご都合問題無いでしょうか？

🙍：◯◯/◯◯　◯◯：◯◯です
↓
かしこまりました。
それでは工事日の取得については再度◯◯/◯◯　◯◯：◯◯頃にお電話改めさせて頂きます。

※次回コール設定＆折り返し希望(工事取得)を入れる

【1週間後FC】
対話者：男性/女性
取得した工事日：○○/○○
LINE送付：済み/未
送りバント案内：有/無
アウト：
工事日決めれなかった理由：
工事日決めれそうなタイミング""",

    "番ポ不備": """ご利用頂いております固定電話についてだったのですが
今回固定電話の番号継続でお手配していたんですけど
NTTさんの方で手続き進めた際にエラー出ちゃっているようなので確認なんですけど
今ご利用頂いているインターネット、WI-FIって○○光でお間違いないでしょうか？

🙍：はい
↓
ありがとうございます。
固定電話って今のその○○光とセット契約をしていて○○光と一緒に請求来ている状態で間違いないですよね？

🙍：NTTから別で来ているよ
↓
かしこまりました。
ありがとうございます。
そうしましたら今回のインターネットのご変更に固定電話は含めれないので
今のまま固定電話はご利用が頂けますのでご安心ください
また、ソネット光では今回CPの適用条件で固定電話の番号だけ発行はされますが
こちらで新しく発行した電話番号は特にご利用はしないで今までご使用いただいていた
番号で固定電話はご利用頂けますようお願いいたします。

🙍：ソフトバンクから請求来ているよ
↓
かしこまりました。
ありがとうございます。
そうしましたら今回のインターネットのご変更にはなりますが
固定電話につきましてはそのままソフトバンクのおうちのでんわでご利用となりますので
ご認識の程よろしくお願いいたします。
また、ソネット光では今回CPの適用条件で固定電話の番号だけ新しく発行はされますが
こちらで新しく発行した電話番号は特にご利用はしないで番号については今まで利用頂いていた
番号でご利用となりますのでご安心ください
工事完了後こちらにつきましてもサポート入りますのでご安心頂ければと思います。
※他のオプション解除時に一緒に外してもOK
開通後ホーム電話案内：おうちの電話を選択

🙍：○○から請求来ているよ(AU系列)
↓
かしこまりました。
ありがとうございます。
そうしましたら今回のインターネットのご変更にはなりますが
固定電話につきましてはそのままAUのホームプラス電話でご利用となりますので
ご認識の程よろしくお願いいたします。
また、ソネット光では今回携帯とのセット割の適用条件で固定電話の番号だけ新しく発行はされますが
こちらで新しく発行した電話番号は特にご利用はしないで番号については今まで利用頂いていた
番号でご利用となりますのでご安心ください
工事完了後こちらにつきましてもサポート入りますのでご安心頂ければと思います。
※AU、もしくはUQのスマホとセット割引が使える(スマートバリュー)
携帯台数×プランに応じて1,100円か550円の割引が携帯電話が割引される
https://x.gd/mNGtM
携帯のMyページから手続き必要
開通後ホーム電話案内：ホームプラスを選択

【1週間後FC】
対話者：男性/女性
取得している工事日：○○/○○　なし
LINE送付：済み/未
送りバント案内：有/無
アウト：
固定電話案内：NTT別契約/おでん/ホムプラ""",

    "現地調査必要": """今回お申込みいただきましたソネット光ですが工事業者より工事をする前に一度現地の調査をしてから工事をしたいと連携頂いておりまして
お時間差し支えなければこのまま日程の調整が出来る窓口にお電話お繋ぎさせて頂きたいのですが
ご都合問題無いでしょうか？

🙍：問題無いよ
↓
ありがとうございます。
担当が出ましたら現地調査の日程を調整したいとお伝えお願いいたします。
またお話しが終わるましたらこちらでお電話お切り致しますので
お客様の方では電話を切ったりの操作はしないでそのままお待ちください
それではお電話お繋ぎ致しますので少々お待ちくださいませ

ソネット光工事調整窓口
1840120004016
ガイダンス　1
営業時間：10：00～19：00

■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■
ーーーーーーーーーーー三者間通話ーーーーーーーーーーーーーー
■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■

ありがとうございます。
それでは○月〇日　AMorPM　に現地調査となりますので当日はよろしくお願いいたします。
また、現地調査が終わり次第ご問題無ければ工事日の日程調整となりますので再度ご調整をお願いしております。
再度工事日については担当から調整の連絡が入りますのでご対応の程お願い致します。

ダイコンステータス：現調待ちに変更
次回コールを現調日に変更　時間は0：00
折り返し希望(新設FC)を入れる

ーーーーーーーーーーーーーーーーーーーーーーーーーーーーーーー

🙍：今あんまり時間ない
↓
かしこまりました。
そうしましたら現地調査の日程調整については
再度お客様のご都合問題ないタイミングでお電話改めさせて頂きたいのですが
お電話いつ頃ですとご都合問題無いでしょうか？

🙍：◯◯/○○　◯◯：○○です
↓
かしこまりました。
それでは工事日の取得については再度◯◯/○○　◯◯：○○頃にお電話改めさせて頂きます。

※次回コール設定＆折り返し希望(新設FC)を入れる

【1週間後FC】
対話者：男性/女性
現地調査予定日：○○/○○　なし
LINE送付：済み/未
送りバント案内：有/無
アウト：""",

    "住所確認": """今回ソネット光の工事をするにあたりNTTの方から
物件の住所登録が無いため数点確認をしてほしいと連携を頂いていたので何点か確認だったのですが
まずお住まいの自宅の屋根の色は何色になりますでしょうか？
↓
🙍：○○です。
↓
ありがとうございます。
次に自宅の外壁の色は何色になりますか？
↓
ありがとうございます。
次に自宅は表札はつけておりますでしょうか？
↓
🙍：ついてないです。orついてます。
↓
ありがとうございます。
次に自宅の物件の階数は何階建てのおうちになりますでしょうか？
↓
🙍：○○です。
↓
ありがとうございます。
後はわかればいいのですが築年数はどれくらいになりますでしょうか？
↓
🙍：○○年です。
↓
ありがとうございます。
後は最後に正確な位置確認のため緯度と経度お伺いしたいのですが
さすがに自宅の緯度経度ってわからないですよね。。。？
↓
🙍：わからないです。
↓
そうですよね
それであれば先日弊社のサポートLINEの登録して頂いたと思うんですけど
自宅にいらっしゃるタイミングで位置情報だけこちらのLINEに送信して頂いても宜しいでしょうか？

ーーーーーーーーーーーーーーーーーーーーーーーーーーーーーーーーーーーーーー
※LINEで位置情報を送信するには
まずトーク画面を開き、左下の「+」ボタンをタップします。次に「位置情報」を選択し、地図上で共有したい場所を選んで「送信」をタップします。
現在地を送信する場合は、そのまま「送信」をタップするだけで完了致します。
ーーーーーーーーーーーーーーーーーーーーーーーーーーーーーーーーーーーーーー

🙍：わかりました
↓
ありがとうございます。
こちらを送信頂いた後に住所登録の申請をNTTに上げさせて頂いて
ＮＴＴの方で登録完了後に再度工事日の調整が可能になりますので
工事日の調整が可能になり次第再度ご連絡改めさせて頂ければと思いますのでよろしくお願いします。

【1週間後FC】
対話者：男性/女性
緯度経度：　○○/LINE待ち
屋根の色：
外壁の色：
築年数：
表札の有無：
建物階数：
LINE送付：済み/未
送りバント案内：有/無
アウト：""",

    "事前解約": """■申し込みプラン1Gの場合
こちらでお手続きした場合ネットやWi-Fiが使えない期間が空いてしまうため
お手続き方法のご変更をさせて頂ければと思います。

・事業変案内
まずはご利用頂いているインターネット環境につきましては○○光でお間違いないでしょうか？

🙍：間違いないです。
↓
ありがとうございます。
そうしましたら後ほどLINEでもお送りいたしますが今からお伝えするお電話番号にご連絡を頂き
「事業者変更承諾番号」の発行をしたいとお伝えをお願いいたします。
それではお電話番号をお伝えいたします。

📞 ソフトバンク光
📱 電話番号：    0800-111-6710
音声ガイダンス：1→3→2→2
受付時間：10:00〜19:00（※日曜・祝日・年末年始を除く

📞 ドコモ光
📱 電話番号：151(ドコモ携帯から)
📱 電話番号：0120-800-000 (ドコモ以外の電話から)
音声ガイダンス：7→1→3
受付時間：10:00〜19:00（※日曜・祝日・年末年始を除く）

📞 OCN光
📱 電話番号：0120-506-506
受付時間：10:00〜19:00（※日曜・祝日・年末年始を除く）

📞 ニフティ光（@nifty光）
📱 電話番号：03-6625-3265
受付時間：10:00〜17:00

📞 BIGLOBE光
📱 電話番号：0120-86-0962（固定電話のみ）／03-6385-0962（固定以外）
受付時間：9:00〜18:00（年中無休）

📞 T-COM光
📱 電話番号：0120-805-633
受付時間：平日10:00〜20:00／土日祝10:00〜18:00

📞  楽天光
📱 電話番号：0120-987-300
受付時間：9:00〜18:00

上記以外の回線の場合は調べる

こちらにご連絡を頂き事業者変更承諾番号が発行出来ましたら
番号自体に有効期限が御座いますので
弊社まで急ぎにてLINEかお電話をお願いいたします。
また、数日空けてご連絡がなかった場合はこちらからもお電話改めますのでよろしくお願いいたします。


■申し込みプラン10Gの場合
現在利用頂いている光回線に解約のご連絡を頂いて現在入線している1Gの線の回線撤去日の調整をお願いいたします。
設備の撤去日が決まりましたら次になるべく近いお日にちで工事日の調整をさせて頂ければと思いますので
撤去日が決まり次第弊社にお電話、もしくはLINEでお知らせを頂けますようお願いいたします。
また、数日空けてご連絡がなかった場合はこちらからもお電話改めますのでよろしくお願いいたします。

【1週間後FC】
対話者：男性/女性
案内方法：事前解約案内/事業変案内
LINE送付：済み/未
送りバント案内：有/無
アウト：""",

    "有派遣へ変更必要": """今回お申込みいただきましたソネット光ですが工事業者より当初お客様のお立合いが必要ない工事をご連携頂いていたのですが
配線作業の兼ね合いでやはり一度ご自宅に伺って工事をしたいとご連絡が入りまして
大変申し訳ないのですが再度工事日の調整をしたくご連絡だったのですが
お時間差し支えなければこのまま日程の調整が出来る窓口にお電話お繋ぎ致しますので
担当が出ましたら工事日の日程を調整したいとお伝えお願いいたします。

いいよ
↓
ありがとうございます。
お話し終わりましたらこちらでお電話お切り致しますのでお客様はお電話切らずにそのままお待ちください
それではお電話お繋ぎ致しますのでこのまま少々お待ちください。

ソネット光工事調整窓口
1840120004016
ガイダンス　1
営業時間：10：00～19：00

■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■
ーーーーーーーーーーー三者間通話ーーーーーーーーーーーーーー
■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■

※こちらから事務局の電話だけ切電　事務局とお客さんが話している間はこちらはミュートにする
ありがとうございます。
それでは○月〇日　AMorPM　に工事業者が自宅に伺いますので当日よろしくお願いいたします。

【1週間後FC】
対話者：男性/女性
有派遣工事日：
LINE送付：済み/未
送りバント案内：有/無
アウト：""",

    "オーナー確認": """今回ソネット光のお申込みにあたり
物件のオーナー様に一言ご挨拶のご連絡だけ必要となりますので
物件の管理会社のお名前とお電話番号をお伺いしても宜しいでしょうか

🙍：○○です。
↓
ありがとうございます。
もしオーナー様にご連絡した際にもし特記事項などあればご報告いたしますのでよろしくお願いいたします。

ーーーーーーーーーーーーーーーーーーーーーーーーーーーーーーーーーーーーーー

🙍：管理会社の名前だけわかる)
↓
ありがとうございます。
それではこちらでお電話番号お調べ致しますので少々お待ちください

WEB検索

電話番号見つかった
↓
お調べ出来ました。
ありがとうございます。
それではこちらでご連絡いたしますので
もし特記事項などあればご報告いたしますのでよろしくお願いいたします。
※+3日後に次回コール設定
折り返し希望(新設FC)を入れる
備考に代コンDXに投入して工事日調整可能になったか確認お願いします。って備考に残しておく


電話番号見つからなかった
↓
申し訳ございません
こちらでお調べしてみたのですが該当する情報が無かったので
一度ご自宅に戻られた際にでもお調べ頂いても宜しいでしょうか？
わかり次第LINEかお電話にてご報告をお願いしたいのですがいつ頃にはご確認頂けそうでしょうか？

🙍：○○には調べられる
↓
かしこまりました。
それでは再度ご連絡お待ちいたしますが
ご連絡なかった場合は再度○○日以降にお電話こちらからも改めますので
ご対応の程よろしくお願いいたします。
※申告の日にち+3日後に次回コール設定の上折り返し希望(新設FC)を入れる

ーーーーーーーーーーーーーーーーーーーーーーーーーーーーーーーーーーーーーー

🙍：今わからない。(管理会社の名前だけわかる)
↓
かしこまりました。
それでは一度ご自宅に戻られた際にでもお調べ頂いても宜しいでしょうか？
わかり次第LINEかお電話にてご報告をお願いしたいのですがいつ頃にはご確認頂けそうでしょうか？

🙍：○○には調べられる
↓
かしこまりました。
それでは再度ご連絡お待ちいたしますが
ご連絡なかった場合は再度○○日以降にお電話こちらからも改めますので
ご対応の程よろしくお願いいたします。
※申告の日にち+3日後に次回コール設定の上折り返し希望(新設FC)を入れる

【1週間後FC】
対話者：男性/女性
管理会社名：
管理会社電話番号：
穴開けビス止め：可/不可
図面提出：必要/不要
原状復帰：必要/不要
LINE送付：済み/未
送りバント案内：有/無
アウト：""",

    "詳細確認待ち": """今回ソネット光のお申込みにあたりNTTからの情報連携待ちの状態になっております。
お客様への特記事項のお伝えがあるみたいでこちらでも内容の確認をNTTさんに行っておりますので
わかり次第お客様へ再度ご連絡をさせて頂きますので今一度お待ち頂ければと思います。

代コンDXに連携して内容の確認をしてもらう

折り返し希望(新設FC)入れて
次回コールを+3日後においておく

【1週間後FC】
対話者：男性/女性
代コンDX投入：済み/未
LINE送付：済み/未
送りバント案内：有/無
アウト：""",
}


# ソースB列上のマーカー（前方一致）
_MARKERS_BY_KIND = {
    "Sonet": {
        "アプローチ": "【アプローチ】",
        "現時点の状況確認": "【お客様の現時点の状況確認】",
        "契約書の説明": "【契約書面の説明】",
        "決済未登録": "【決済未登録】",
        "今後の流れ": "【今後の流れ】",
        "不備解消": "・不備解消",
        "締め": "【締め】",
    },
    "NURO": {
        "アプローチ": "【アプローチ】",
        "現時点の状況確認": "【お客様の現時点の状況確認】",
        "契約書の説明": "【契約書面の説明】",
        "今後の流れ": "【今後の流れ】",
        "締め": "【締め】",
    },
}


# ----------------------------------------------------------------------
# 認証
# ----------------------------------------------------------------------
@st.cache_resource
def _get_writable_client():
    """書き込み可能なgspreadクライアント。st.secrets優先、ローカルJSONフォールバック。"""
    try:
        creds_dict = dict(st.secrets["gcp_service_account"])
        creds = Credentials.from_service_account_info(creds_dict, scopes=WRITE_SCOPES)
    except Exception:
        if not os.path.exists(_LOCAL_KEY_FILE):
            raise RuntimeError(
                "Google Sheets認証情報が見つかりません。"
                "st.secrets['gcp_service_account'] か "
                f"{_LOCAL_KEY_FILE} を用意してください。"
            )
        creds = Credentials.from_service_account_file(_LOCAL_KEY_FILE, scopes=WRITE_SCOPES)
    return gspread.authorize(creds)


def _get_storage_worksheet():
    """保存先ワークシートを取得（無ければ作成）。"""
    client = _get_writable_client()
    try:
        sheet_id = st.secrets["ikusei"]["spreadsheet_id"]
    except Exception:
        sheet_id = _IKUSEI_SHEET_ID_FALLBACK
    spreadsheet = client.open_by_key(sheet_id)
    try:
        return spreadsheet.worksheet(TEMPLATE_WORKSHEET)
    except gspread.exceptions.WorksheetNotFound:
        return spreadsheet.add_worksheet(title=TEMPLATE_WORKSHEET, rows=2, cols=2)


# ----------------------------------------------------------------------
# デフォルト抽出
# ----------------------------------------------------------------------
def _extract_sections(col_b: list[str], kind: str) -> dict[str, str]:
    """B列をマーカーで分割してセクション別本文に変換。"""
    markers = _MARKERS_BY_KIND[kind]
    boundaries: list[tuple[str, int]] = []
    used = set()
    for i, line in enumerate(col_b):
        text = (line or "").strip()
        for sec_name, marker in markers.items():
            if sec_name in used:
                continue
            if text.startswith(marker):
                boundaries.append((sec_name, i))
                used.add(sec_name)
                break
    boundaries.sort(key=lambda x: x[1])

    result: dict[str, str] = {}
    for idx, (sec_name, start) in enumerate(boundaries):
        end = boundaries[idx + 1][1] if idx + 1 < len(boundaries) else len(col_b)
        body_lines = col_b[start + 1:end]
        # 前後の空行をトリム
        while body_lines and not body_lines[0].strip():
            body_lines.pop(0)
        while body_lines and not body_lines[-1].strip():
            body_lines.pop()
        result[sec_name] = "\n".join(body_lines)

    # 見つからなかったセクションは空で埋める
    for sec in SECTIONS_BY_KIND[kind]:
        result.setdefault(sec, "")
    return result


def _parse_defaults_from_source() -> dict[str, dict[str, str]]:
    """既存トークシートのB列からデフォルトテンプレを構築。Sonet_fubiも初期化。"""
    from talk_script_store import _get_gspread_client  # readonlyでOK
    client = _get_gspread_client()
    sh = client.open_by_key(TALK_SCRIPT_SHEET_ID)
    out: dict[str, dict[str, str]] = {}
    for kind, sheet_name in SCRIPT_SHEETS.items():
        try:
            ws = sh.worksheet(sheet_name)
            col_b = ws.col_values(2)
            # NUROはB列内に LINEテンプレ(完了LINE/留守LINE/留守完了LINE)が
            # インラインで含まれているため、最初のLINEヘッダーで打ち切る
            if kind == "NURO":
                line_headers = {"完了LINE", "留守LINE", "留守完了LINE"}
                cut_idx = None
                for i, v in enumerate(col_b):
                    if v.strip() in line_headers:
                        cut_idx = i
                        break
                if cut_idx is not None:
                    col_b = col_b[:cut_idx]
            out[kind] = _extract_sections(col_b, kind)
        except Exception:
            out[kind] = {sec: "" for sec in SECTIONS_BY_KIND[kind]}
    # 不備解消の9種は GAS から移植したハードコードを使う
    out["Sonet_fubi"] = dict(DEFAULT_SONET_FUBI)
    # 締めの2種（利用回線あり/不明）もハードコードのデフォルトを使う
    out["Sonet_closing"] = dict(DEFAULT_SONET_CLOSING)
    # 促進用5種（代コン不備解消用）もハードコードのデフォルト
    out["Sonet_sokushin"] = dict(DEFAULT_SONET_SOKUSHIN)
    # LINEテンプレ（Sonet/NUROそれぞれ3種）はソースシートから取得
    from talk_script_store import load_line_templates
    try:
        sonet_line = load_line_templates.__wrapped__("Sonet")
    except Exception:
        sonet_line = {}
    try:
        nuro_line = load_line_templates.__wrapped__("NURO")
    except Exception:
        nuro_line = {}
    out["Sonet_line"] = {k: sonet_line.get(k, "") for k in LINE_TEMPLATE_KEYS}
    out["NURO_line"] = {k: nuro_line.get(k, "") for k in LINE_TEMPLATE_KEYS}
    return out


# ----------------------------------------------------------------------
# 動的処理（GAS移植）
# ----------------------------------------------------------------------
def select_fubi_key(daikon_status: str, koji_yotei_hi: str) -> str:
    """ダイコンステータスと工事予定日から不備解消テンプレキーを決定（GASのC59式相当）。"""
    s = (daikon_status or "").strip()
    if not s:
        return "工事日決定済み" if (koji_yotei_hi or "").strip() else "工事取得"
    if s == "工事日調整希望":
        return "工事取得"
    return s


_SOKUSHIN_MAPPING = {
    "工事日調整希望": "工事取得3者間",
    "API工事取得": "工事取得3者間",
    "番ポ不備": "番ポ不備FC",
    "住所確認": "住所確認FC",
    "現地調査必要": "現地調査3者間",
    "有派遣へ変更必要": "有派遣変更3者間",
}


def select_sokushin_key(daikon_status: str) -> str:
    """ダイコンステータスから促進用トークのテンプレキーを決定。未対応値は空文字。"""
    s = (daikon_status or "").strip()
    return _SOKUSHIN_MAPPING.get(s, "")


def apply_furigana_substitution(body: str, furigana: str) -> str:
    """
    「こちら○○様のお電話でお間違いないでしょうか？」の行で、
    お客様のフリガナを差し込む。
    """
    name = (furigana or "").strip()
    if not name:
        # フリガナ未取得の場合は空白のまま
        name = ""
    base = f"こちら{name}様のお電話でお間違いないでしょうか？"
    new_lines = []
    for line in body.split("\n"):
        if "様のお電話でお間違いないでしょうか" in line:
            new_lines.append(base)
        else:
            new_lines.append(line)
    return "\n".join(new_lines)


def apply_kaisen_substitution(body: str, riyou_kaisen: str) -> str:
    """
    ①「また、工事完了後は弊社より～」の行を、利用回線を埋め込んだ正規テキストに置換。
    利用回線が空/不明なら何もしない（その後 filter_kaisen で行ごと隠す）。
    """
    s = (riyou_kaisen or "").strip()
    if not s or s == "不明":
        return body
    base = (
        f"また、工事完了後は弊社よりご利用頂いている{s}と"
        f"2重契約にならないよう解約のご誘導いたしますのでそれまでお待ち頂ければと思います！"
    )
    new_lines = []
    for line in body.split("\n"):
        if "また、工事完了後は弊社より" in line:
            new_lines.append(base)
        else:
            new_lines.append(line)
    return "\n".join(new_lines)


def apply_line_sms_substitution(body: str, has_line_touroku: bool) -> str:
    """
    ②「もし気になる点や～」を含む行で、LINE登録の有無により LINE/SMS を入れ替える。
    """
    new_lines = []
    for line in body.split("\n"):
        if "もし気になる点やご心配な点が出てきましたらお電話や" in line:
            if has_line_touroku:
                line = line.replace("SMS", "LINE")
            else:
                line = line.replace("LINE", "SMS")
        new_lines.append(line)
    return "\n".join(new_lines)


def filter_kaisen_unknown(body: str, has_kaisen: bool) -> str:
    """
    ④ 利用回線の有無により行を隠す:
    - has_kaisen: 「※利用回線不明の場合」から始まる5行を非表示
    - 不明:        「また、工事完了後は弊社より」を含む行＋次の1行 を非表示（複数箇所すべて）
    """
    lines = body.split("\n")
    out: list[str] = []
    skip = 0
    if has_kaisen:
        for line in lines:
            if skip > 0:
                skip -= 1
                continue
            if line.strip() == "※利用回線不明の場合":
                skip = 4  # 自身 + 後ろ4行 = 計5行を非表示
                continue
            out.append(line)
    else:
        for line in lines:
            if skip > 0:
                skip -= 1
                continue
            if "また、工事完了後は弊社より" in line:
                skip = 1  # 自身 + 次1行 = 計2行を非表示
                continue
            out.append(line)
    return "\n".join(out)


def apply_dynamic_processing(body: str, info: dict) -> str:
    """
    Sonetの動的処理を一括適用（①～⑤）。
    info: lookup_customer の戻り値
    """
    riyou_kaisen = (info.get("利用回線") or "").strip()
    has_kaisen = bool(riyou_kaisen) and riyou_kaisen != "不明"
    has_line_touroku = bool((info.get("【Lｽﾃｯﾌﾟ】突合完了日（引用）") or "").strip())
    furigana = (info.get("申込者氏名（フリガナ）") or "").strip()

    body = apply_furigana_substitution(body, furigana)
    body = apply_kaisen_substitution(body, riyou_kaisen)
    body = apply_line_sms_substitution(body, has_line_touroku)
    body = filter_kaisen_unknown(body, has_kaisen)
    return body


# ----------------------------------------------------------------------
# 永続化
# ----------------------------------------------------------------------
def _serialize(templates: dict[str, dict[str, str]]) -> str:
    return json.dumps(templates, ensure_ascii=False)


def _deserialize(raw: str) -> dict:
    data = json.loads(raw)
    # セクション構成がなければデフォルトで初期化
    if "_sections" not in data:
        data["_sections"] = {k: list(v) for k, v in _DEFAULT_SECTIONS_BY_KIND.items()}
    # 欠けセクションをデフォルトでマージ
    sections_map = data["_sections"]
    for kind in _DEFAULT_SECTIONS_BY_KIND:
        if kind not in sections_map:
            sections_map[kind] = list(_DEFAULT_SECTIONS_BY_KIND[kind])
        if kind not in data:
            data[kind] = {}
        for sec in sections_map[kind]:
            data[kind].setdefault(sec, "")
    # 不備解消9種テンプレ（Sonet_fubi）も欠けてればデフォルトで埋める
    if "Sonet_fubi" not in data:
        data["Sonet_fubi"] = {}
    for key in SONET_FUBI_KEYS:
        data["Sonet_fubi"].setdefault(key, DEFAULT_SONET_FUBI.get(key, ""))
    # 締め2種（Sonet_closing）も欠けてればデフォルトで埋める
    if "Sonet_closing" not in data:
        data["Sonet_closing"] = {}
    for key in SONET_CLOSING_KEYS:
        data["Sonet_closing"].setdefault(key, DEFAULT_SONET_CLOSING.get(key, ""))
    # 促進用5種（Sonet_sokushin）も欠けてればデフォルトで埋める
    if "Sonet_sokushin" not in data:
        data["Sonet_sokushin"] = {}
    for key in SONET_SOKUSHIN_KEYS:
        data["Sonet_sokushin"].setdefault(key, DEFAULT_SONET_SOKUSHIN.get(key, ""))
    # LINEテンプレ（Sonet_line / NURO_line）も欠けてればソースシートから補完
    for store_key in ("Sonet_line", "NURO_line"):
        if store_key not in data:
            data[store_key] = {}
        # 欠けキーがあればソースから補完
        missing = [k for k in LINE_TEMPLATE_KEYS if k not in data[store_key] or not data[store_key][k]]
        if missing:
            kind = "Sonet" if store_key == "Sonet_line" else "NURO"
            try:
                from talk_script_store import load_line_templates
                src = load_line_templates.__wrapped__(kind)
            except Exception:
                src = {}
            for k in LINE_TEMPLATE_KEYS:
                data[store_key].setdefault(k, src.get(k, ""))
    return data


@st.cache_resource
def _shared_templates() -> dict:
    """全ユーザー共有のテンプレートストア。"""
    try:
        ws = _get_storage_worksheet()
        raw = ws.acell(TEMPLATE_CELL).value
        if raw:
            return _deserialize(raw)
    except Exception:
        pass
    # 初回 or 失敗時 → ソースB列から構築
    try:
        result = _parse_defaults_from_source()
        if "_sections" not in result:
            result["_sections"] = {k: list(v) for k, v in _DEFAULT_SECTIONS_BY_KIND.items()}
        return result
    except Exception:
        result = {kind: {sec: "" for sec in secs} for kind, secs in _DEFAULT_SECTIONS_BY_KIND.items()}
        result["_sections"] = {k: list(v) for k, v in _DEFAULT_SECTIONS_BY_KIND.items()}
        return result


def get_templates() -> dict:
    """共有テンプレートを取得（編集可能な参照を返す）。"""
    return _shared_templates()


def get_sections(kind: str) -> list[str]:
    """指定商材のセクション構成を取得。Google Sheets保存値優先、なければデフォルト。"""
    templates = _shared_templates()
    sections_map = templates.get("_sections", {})
    return sections_map.get(kind, list(_DEFAULT_SECTIONS_BY_KIND.get(kind, [])))


def get_sections_by_kind() -> dict[str, list[str]]:
    """全商材のセクション構成を返す。"""
    templates = _shared_templates()
    sections_map = templates.get("_sections")
    if sections_map:
        return sections_map
    return {k: list(v) for k, v in _DEFAULT_SECTIONS_BY_KIND.items()}


def update_sections(kind: str, sections: list[str]):
    """セクション構成を更新（メモリ上のみ。save_templatesで永続化）。"""
    templates = _shared_templates()
    if "_sections" not in templates:
        templates["_sections"] = {k: list(v) for k, v in _DEFAULT_SECTIONS_BY_KIND.items()}
    templates["_sections"][kind] = sections


def _ensure_default_section_rules(templates: dict):
    """既存の「決済未登録」ハードコード挙動を初回のみルールとして自動登録。"""
    if "_section_rules" in templates:
        return
    templates["_section_rules"] = {
        "Sonet": {"決済未登録": {"field": "決済登録日（引用）", "op": "empty"}},
        "NURO": {"決済未登録": {"field": "決済登録日（引用）", "op": "empty"}},
    }


def get_section_rule(kind: str, section_name: str) -> dict:
    """
    セクションの表示ルールを取得。
    返り値: {"field": "引用フィールド名", "op": "empty"|"not_empty"} または空 {}（常に表示）
    """
    templates = _shared_templates()
    _ensure_default_section_rules(templates)
    rules = templates.get("_section_rules", {})
    return dict(rules.get(kind, {}).get(section_name, {}))


def update_section_rule(kind: str, section_name: str, rule: dict):
    """
    セクションの表示ルールを更新。rule が空 {} なら削除（常に表示に戻す）。
    """
    templates = _shared_templates()
    _ensure_default_section_rules(templates)
    kind_rules = templates["_section_rules"].setdefault(kind, {})
    if rule:
        kind_rules[section_name] = rule
    else:
        kind_rules.pop(section_name, None)


def evaluate_section_rule(rule: dict, info: dict) -> bool:
    """
    ルールを顧客lookup辞書に適用し、表示するか判定。
    rule が空ならTrue（常に表示）。

    サポート演算子:
      - empty, not_empty: 値の有無のみで判定
      - eq, ne: 文字列完全一致 / 不一致
      - contains, not_contains: 部分一致
      - starts_with: 前方一致
      - lt, gt, le, ge: 大小比較（数値優先、失敗時は文字列比較）
    """
    if not rule:
        return True
    field = rule.get("field")
    op = rule.get("op")
    if not field or not op:
        return True

    raw_val = info.get(field)
    val_str = "" if raw_val is None else str(raw_val).strip()
    has_val = val_str != ""

    if op == "empty":
        return not has_val
    if op == "not_empty":
        return has_val

    cmp_str = str(rule.get("value", "")).strip()

    if op == "eq":
        return val_str == cmp_str
    if op == "ne":
        return val_str != cmp_str
    if op == "contains":
        return cmp_str in val_str
    if op == "not_contains":
        return cmp_str not in val_str
    if op == "starts_with":
        return val_str.startswith(cmp_str)

    if op in ("lt", "gt", "le", "ge"):
        # 数値比較を試み、失敗したら文字列比較にフォールバック
        try:
            a = float(val_str)
            b = float(cmp_str)
        except (ValueError, TypeError):
            a, b = val_str, cmp_str
        if op == "lt":
            return a < b
        if op == "gt":
            return a > b
        if op == "le":
            return a <= b
        if op == "ge":
            return a >= b

    return True


_last_save = {"t": 0.0}


def save_templates() -> tuple[bool, str]:
    """共有テンプレートをGoogle Sheetsへ保存（5秒スロットリング）。"""
    now = time.time()
    if now - _last_save["t"] < 5:
        return False, "連続保存はできません（5秒間隔）"
    try:
        templates = _shared_templates()
        ws = _get_storage_worksheet()
        ws.update_acell(TEMPLATE_CELL, _serialize(templates))
        _last_save["t"] = now
        return True, "保存しました"
    except Exception as e:
        return False, f"保存エラー: {e}"


def reset_to_default() -> tuple[bool, str]:
    """ソースB列から再パースして上書き保存。"""
    try:
        defaults = _parse_defaults_from_source()
        templates = _shared_templates()
        # セクション構成もデフォルトに戻す
        templates["_sections"] = {k: list(v) for k, v in _DEFAULT_SECTIONS_BY_KIND.items()}
        # 既存ストアを上書き
        for kind in _DEFAULT_SECTIONS_BY_KIND:
            templates[kind] = defaults.get(kind, {sec: "" for sec in _DEFAULT_SECTIONS_BY_KIND[kind]})
        # 即時保存（スロットリング回避）
        ws = _get_storage_worksheet()
        ws.update_acell(TEMPLATE_CELL, _serialize(templates))
        _last_save["t"] = time.time()
        return True, "デフォルトに戻しました"
    except Exception as e:
        return False, f"リセットエラー: {e}"


def clear_template_cache():
    """共有キャッシュをクリア（次回読み込みで再取得）。"""
    _shared_templates.clear()
