"""
汎用置換表マスタ

トーク本文の任意のトリガー文字列を置換後の文言に差し替える、シンプルな対応表。
商流別名乗りマスタ（条件付き置換）とは別系統で、条件なしで全トーク描画に適用される。

保存先: ikusei用スプレッドシート内の `replace_master_data` ワークシートA1セルにJSON。
全ユーザー共有（st.cache_resource）。
"""

import json
import time

import streamlit as st
import gspread

from talk_template_store import (
    _get_writable_client,
    _IKUSEI_SHEET_ID_FALLBACK,
)


WORKSHEET_NAME = "replace_master_data"
STORAGE_CELL = "A1"


def _get_storage_worksheet():
    client = _get_writable_client()
    try:
        sheet_id = st.secrets["ikusei"]["spreadsheet_id"]
    except Exception:
        sheet_id = _IKUSEI_SHEET_ID_FALLBACK
    spreadsheet = client.open_by_key(sheet_id)
    try:
        return spreadsheet.worksheet(WORKSHEET_NAME)
    except gspread.exceptions.WorksheetNotFound:
        return spreadsheet.add_worksheet(title=WORKSHEET_NAME, rows=2, cols=2)


def _normalize_rows(rows) -> list[dict]:
    out = []
    if not isinstance(rows, list):
        return out
    for r in rows:
        if not isinstance(r, dict):
            continue
        out.append({
            "トリガー": str(r.get("トリガー", "")),
            "置換後": str(r.get("置換後", "")),
        })
    return out


@st.cache_resource
def _shared_master() -> dict:
    try:
        ws = _get_storage_worksheet()
        raw = ws.acell(STORAGE_CELL).value
        if raw:
            data = json.loads(raw)
            return {"rows": _normalize_rows(data.get("rows", []))}
    except Exception:
        pass
    return {"rows": []}


def get_rows() -> list[dict]:
    return _shared_master()["rows"]


def set_rows(rows: list[dict]):
    _shared_master()["rows"] = _normalize_rows(rows)


_last_save = {"t": 0.0}


def save_master() -> tuple[bool, str]:
    now = time.time()
    if now - _last_save["t"] < 5:
        return False, "連続保存はできません（5秒間隔）"
    try:
        data = {"rows": _normalize_rows(get_rows())}
        ws = _get_storage_worksheet()
        ws.update_acell(STORAGE_CELL, json.dumps(data, ensure_ascii=False))
        _last_save["t"] = now
        return True, "保存しました"
    except Exception as e:
        return False, f"保存エラー: {e}"


def clear_cache():
    _shared_master.clear()


def apply_replace_substitution(body: str) -> str:
    """置換表の全行をそのまま文字列置換する。トリガーが空の行はスキップ。"""
    if not body:
        return body
    for r in get_rows():
        trigger = r.get("トリガー", "")
        if not trigger:
            continue
        body = body.replace(trigger, r.get("置換後", ""))
    return body
