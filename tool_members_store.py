"""
ツールカテゴリのメンバー＆トーク種類＆割当を Google Sheets に永続化するストア。

- 保存先: ikusei用スプレッドシート内の `tool_members_data` ワークシートのA1セル
- 全ユーザー共有（st.cache_resource）
- 5秒スロットリング
- soft-delete: active=false で非表示にしてインデックス安定性を保つ

データ構造:
{
  "members": [
    {"name": "室谷 慧", "assignments": ["fc1week"], "active": true},
    ...
  ],
  "boards": [
    {"suffix": "fc1week", "label": "1週間後FCトーク"},
    ...
  ]
}
"""

import json
import time

import streamlit as st
import gspread

from talk_template_store import (
    _get_writable_client,
    _IKUSEI_SHEET_ID_FALLBACK,
)

WORKSHEET_NAME = "tool_members_data"
CELL = "A1"

# 初期トーク種類
_DEFAULT_BOARDS = [
    {"suffix": "fc1week", "label": "1週間後FCトーク"},
    {"suffix": "shiryou", "label": "1週間後FC 資料"},
]

# 初期メンバー（初回デプロイ時のシード）
_DEFAULT_MEMBERS = [
    {"name": "室谷 慧", "assignments": ["fc1week"], "active": True},
    {"name": "原田 綾子", "assignments": ["fc1week"], "active": True},
    {"name": "金澤 駿平", "assignments": ["fc1week"], "active": True},
    {"name": "吉本 将吾", "assignments": ["fc1week"], "active": True},
    {"name": "大滝 紀香", "assignments": ["fc1week"], "active": True},
    {"name": "堀田 輝斗", "assignments": ["fc1week"], "active": True},
    {"name": "角田 心華", "assignments": ["fc1week"], "active": True},
    {"name": "佐々木 彩乃", "assignments": ["fc1week"], "active": True},
    {"name": "葛西 翼", "assignments": ["fc1week"], "active": True},
    {"name": "雨貝 一生", "assignments": ["fc1week"], "active": True},
    {"name": "半田 さくら", "assignments": ["fc1week"], "active": True},
    {"name": "菊地 隆真", "assignments": ["fc1week"], "active": True},
    {"name": "栗田 優衣", "assignments": ["fc1week"], "active": True},
    {"name": "高橋 真友香", "assignments": ["fc1week"], "active": True},
]


def _get_ws():
    client = _get_writable_client()
    try:
        sheet_id = st.secrets["ikusei"]["spreadsheet_id"]
    except Exception:
        sheet_id = _IKUSEI_SHEET_ID_FALLBACK
    sh = client.open_by_key(sheet_id)
    try:
        return sh.worksheet(WORKSHEET_NAME)
    except gspread.exceptions.WorksheetNotFound:
        return sh.add_worksheet(title=WORKSHEET_NAME, rows=2, cols=2)


@st.cache_resource
def _shared_cache() -> dict:
    """全ユーザー共有キャッシュ。"""
    try:
        ws = _get_ws()
        raw = ws.acell(CELL).value
        if raw:
            data = json.loads(raw)
            if isinstance(data, dict):
                return {
                    "members": data.get("members"),
                    "boards": data.get("boards"),
                }
    except Exception:
        pass
    return {"members": None, "boards": None}


# --- ボード（トーク種類）管理 ---

def get_boards() -> list[dict]:
    """トーク種類リストを返す。未保存ならデフォルト。不足分は自動追加。"""
    cached = _shared_cache().get("boards")
    if cached is None:
        return [b.copy() for b in _DEFAULT_BOARDS]
    # マイグレーション: _DEFAULT_BOARDS に存在するが cached にないボードを追加
    existing = {b["suffix"] for b in cached}
    for db in _DEFAULT_BOARDS:
        if db["suffix"] not in existing:
            cached.append(db.copy())
    return cached


def get_boards_as_tuples() -> list[tuple[str, str]]:
    """(suffix, label) のタプルリスト。metrics.py の TALK_SCRIPT_BOARDS 互換。"""
    return [(b["suffix"], b["label"]) for b in get_boards()]


def next_board_suffix() -> str:
    """新規トーク種類用のサフィックスを自動生成。"""
    boards = get_boards()
    idx = 1
    existing = {b["suffix"] for b in boards}
    while f"board{idx:02d}" in existing:
        idx += 1
    return f"board{idx:02d}"


# --- メンバー管理 ---

def get_members() -> list[dict]:
    """全メンバー（非アクティブ含む）を返す。未保存ならデフォルト。"""
    cached = _shared_cache().get("members")
    if cached is None:
        return [m.copy() for m in _DEFAULT_MEMBERS]
    return cached


def get_active_members() -> list[dict]:
    """アクティブなメンバーのみ返す。"""
    return [m for m in get_members() if m.get("active", True)]


def get_member_names() -> list[str]:
    """アクティブなメンバー名のリスト（サイドバー表示用）。"""
    return [m["name"] for m in get_active_members()]


def get_all_member_names() -> list[str]:
    """全メンバー名のリスト（インデックス対応、非アクティブ含む）。"""
    return [m["name"] for m in get_members()]


def get_member_assignments(name: str) -> list[str]:
    """指定メンバーのトーク割当サフィックスリストを返す。"""
    for m in get_members():
        if m["name"] == name:
            return m.get("assignments", [])
    return []


# --- 保存 ---

_last_save = {"t": 0.0}


def _save_data(members: list[dict], boards: list[dict]) -> tuple[bool, str]:
    """メンバー＋ボードをGoogle Sheetsに保存（5秒スロットリング）。"""
    now = time.time()
    if now - _last_save["t"] < 5:
        return False, "保存スキップ（5秒以内の連続保存）"
    try:
        ws = _get_ws()
        payload = {"members": members, "boards": boards}
        ws.update_acell(CELL, json.dumps(payload, ensure_ascii=False))
        cache = _shared_cache()
        cache["members"] = members
        cache["boards"] = boards
        _last_save["t"] = now
        return True, "保存しました"
    except Exception as e:
        return False, f"保存エラー: {e}"


def save_members(members: list[dict]) -> tuple[bool, str]:
    """メンバーリストを保存（ボードは現在値を維持）。"""
    return _save_data(members, get_boards())


def save_boards(boards: list[dict]) -> tuple[bool, str]:
    """ボードリストを保存（メンバーは現在値を維持）。"""
    return _save_data(get_members(), boards)


def save_all(members: list[dict], boards: list[dict]) -> tuple[bool, str]:
    """メンバー＋ボードを一括保存。"""
    return _save_data(members, boards)


def clear_members_cache():
    """共有キャッシュをクリア。"""
    _shared_cache.clear()
