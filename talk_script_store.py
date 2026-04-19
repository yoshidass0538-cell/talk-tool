"""
トークスクリプト用 Google Sheets リーダー

電話番号で顧客情報を引き当て、商材別のトークスクリプト本文を取得する。
（既存のスプレッドシート運用を Streamlit に取り込んだもの）
"""

import os
import re
import pandas as pd
import streamlit as st
import gspread
from google.oauth2.service_account import Credentials


SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets.readonly",
    "https://www.googleapis.com/auth/drive.readonly",
]

# トークスクリプト用スプレッドシート（トーク本文ソース）
TALK_SCRIPT_SHEET_ID = "15kqCJoZYQSrkvqwecmLgeS9aJBlJAVdoSOP1j822zS0"

# 顧客データシート（sync_report.pyで自動同期される先）
LOOKUP_SHEET_ID = "1iNtEakg4U4C3p7uQlVcJIzojnUd8uW5Ykl8swQRQD5U"
LOOKUP_SHEET = "1週間後FC該当案件"

# 商材種別 → トークシート名
SCRIPT_SHEETS = {
    "Sonet": "1週間後FCトーク0314",
    "NURO": "NURO1週間後FCトーク0402",
}

# ローカル開発用フォールバック JSON
_LOCAL_KEY_FILE = "yoshida0538-f46ce1eea153.json"


@st.cache_resource
def _get_gspread_client():
    """gspread認証クライアントを返す。st.secrets優先、ローカルJSONフォールバック。"""
    try:
        creds_dict = dict(st.secrets["gcp_service_account"])
        creds = Credentials.from_service_account_info(creds_dict, scopes=SCOPES)
    except Exception:
        # ローカル: JSONファイル直読み
        if not os.path.exists(_LOCAL_KEY_FILE):
            raise RuntimeError(
                "Google Sheets認証情報が見つかりません。"
                "st.secrets['gcp_service_account'] か "
                f"{_LOCAL_KEY_FILE} を用意してください。"
            )
        creds = Credentials.from_service_account_file(_LOCAL_KEY_FILE, scopes=SCOPES)
    return gspread.authorize(creds)


def normalize_phone(phone: str) -> str:
    """電話番号を正規化（数字以外を除去）。"""
    if phone is None:
        return ""
    return re.sub(r"[^0-9]", "", str(phone))


@st.cache_data(ttl=1800, show_spinner="顧客データを取得中...")
def load_customer_data() -> pd.DataFrame:
    """1週間後FC該当案件シートを丸ごとDataFrameで読み込み、電話番号正規化列を付与。
    TTL 30分（API制限回避のため長め）。
    """
    import time as _time
    from talk_template_store import _get_writable_client
    try:
        client = _get_writable_client()
    except Exception:
        client = _get_gspread_client()

    # リトライ付きで取得（429/レート制限対策）
    last_err = None
    for attempt in range(4):
        try:
            sh = client.open_by_key(LOOKUP_SHEET_ID)
            ws = sh.worksheet(LOOKUP_SHEET)
            values = ws.get_all_values()
            break
        except Exception as e:
            last_err = e
            msg = str(e)
            if "429" in msg or "quota" in msg.lower() or "rate" in msg.lower() or "limit" in msg.lower():
                _time.sleep(2 ** attempt)  # 1, 2, 4, 8秒
                continue
            raise
    else:
        raise last_err

    if not values or len(values) < 2:
        return pd.DataFrame()
    header = values[0]
    rows = values[1:]
    # 列数差吸収（行ごとに長さが違う場合）
    width = len(header)
    rows = [r + [""] * (width - len(r)) if len(r) < width else r[:width] for r in rows]
    df = pd.DataFrame(rows, columns=header)
    if "取引先名" in df.columns:
        df["_phone_normalized"] = df["取引先名"].map(normalize_phone)
    else:
        df["_phone_normalized"] = ""
    return df


def get_lookup_columns() -> list[str]:
    """顧客lookupシートのヘッダー列名一覧を返す（内部列を除外）。"""
    df = load_customer_data()
    if df.empty:
        return []
    return [c for c in df.columns if not c.startswith("_")]


def lookup_customer(phone: str) -> dict | None:
    """
    電話番号で顧客情報を引き当て。複数ヒット時は申込日（案件進捗管理: エントリ日）が
    最も新しい1件を返す。
    """
    phone_n = normalize_phone(phone)
    if not phone_n:
        return None
    df = load_customer_data()
    if df.empty:
        return None
    hit = df[df["_phone_normalized"] == phone_n]
    if hit.empty:
        return None
    # エントリ日で降順ソート（不正値は最後）
    date_col = "案件進捗管理: エントリ日"
    if date_col in hit.columns:
        hit = hit.copy()
        hit["_entry_dt"] = pd.to_datetime(hit[date_col], errors="coerce")
        hit = hit.sort_values("_entry_dt", ascending=False, na_position="last")
    return hit.iloc[0].to_dict()


@st.cache_data(ttl=600, show_spinner="トークスクリプトを取得中...")
def load_talk_script(kind: str) -> list[str]:
    """商材種別のトークスクリプト本文（B列）を行ごとのリストで取得。"""
    sheet_name = SCRIPT_SHEETS.get(kind)
    if not sheet_name:
        return []
    client = _get_gspread_client()
    sh = client.open_by_key(TALK_SCRIPT_SHEET_ID)
    ws = sh.worksheet(sheet_name)
    return ws.col_values(2)  # B列


@st.cache_data(ttl=600, show_spinner="LINEテンプレを取得中...")
def load_line_templates(kind: str) -> dict[str, str]:
    """
    LINEテンプレ（完了LINE / 留守LINE / 留守完了LINE）を取得。
    Sonet: D/E/F列に分かれている
    NURO:  B列の末尾にインライン格納（完了LINE / 留守LINE / 留守完了LINE のヘッダーで区切り）
    """
    sheet_name = SCRIPT_SHEETS.get(kind)
    if not sheet_name:
        return {}
    client = _get_gspread_client()
    sh = client.open_by_key(TALK_SCRIPT_SHEET_ID)
    ws = sh.worksheet(sheet_name)

    if kind == "Sonet":
        col_d = ws.col_values(4)
        col_e = ws.col_values(5)
        col_f = ws.col_values(6)

        def _extract_col(col_values: list[str], header: str) -> str:
            try:
                start = col_values.index(header) + 1
            except ValueError:
                return ""
            body = col_values[start:]
            while body and not body[-1].strip():
                body.pop()
            return "\n".join(body)

        return {
            "完了LINE": _extract_col(col_d, "完了LINE"),
            "留守LINE": _extract_col(col_e, "留守LINE"),
            "留守完了LINE": _extract_col(col_f, "留守完了LINE"),
        }

    # NURO: B列内のヘッダー区切り
    col_b = ws.col_values(2)
    headers = ["完了LINE", "留守LINE", "留守完了LINE"]
    positions: list[tuple[str, int]] = []
    for i, v in enumerate(col_b):
        if v.strip() in headers:
            positions.append((v.strip(), i))

    result: dict[str, str] = {h: "" for h in headers}
    for idx, (h, start) in enumerate(positions):
        end = positions[idx + 1][1] if idx + 1 < len(positions) else len(col_b)
        body = col_b[start + 1:end]
        while body and not body[-1].strip():
            body.pop()
        while body and not body[0].strip():
            body.pop(0)
        result[h] = "\n".join(body)
    return result


def detect_kind(shozai: str) -> str:
    """取次商材情報からトーク種別を判定。"""
    s = (shozai or "").upper()
    if "NURO" in s:
        return "NURO"
    return "Sonet"


def clear_caches():
    """キャッシュクリア（サイドバーの🔄ボタンから呼ぶ用）。"""
    load_customer_data.clear()
    load_talk_script.clear()
