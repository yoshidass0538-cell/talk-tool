"""
サイドバーのボード並び順を Google Sheets に永続化するストア。

- 保存先: ikusei用スプレッドシート内の `ui_order_data` ワークシートのA1セル
- 全ユーザー共有（st.cache_resource）
- マスタで並び順を変更するたびに保存される
- ロード時、現在の METRICS と整合を取る（追加削除されたカテゴリ/アイテムを補正）
"""

import json
import time

import streamlit as st
import gspread

from talk_template_store import (
    _get_writable_client,
    _IKUSEI_SHEET_ID_FALLBACK,
)


UI_ORDER_WORKSHEET = "ui_order_data"
UI_ORDER_CELL = "A1"

# デフォルトのカテゴリ表示順
DEFAULT_CATEGORY_ORDER = ["TOTAL", "ツール", "1週間後FC", "促進", "責任者用"]


def _get_ws():
    client = _get_writable_client()
    try:
        sheet_id = st.secrets["ikusei"]["spreadsheet_id"]
    except Exception:
        sheet_id = _IKUSEI_SHEET_ID_FALLBACK
    sh = client.open_by_key(sheet_id)
    try:
        return sh.worksheet(UI_ORDER_WORKSHEET)
    except gspread.exceptions.WorksheetNotFound:
        return sh.add_worksheet(title=UI_ORDER_WORKSHEET, rows=2, cols=2)


@st.cache_resource
def _shared_order_cache() -> dict:
    """全ユーザー共有のboard_orderキャッシュ。dictで包んで参照を共有。"""
    try:
        ws = _get_ws()
        raw = ws.acell(UI_ORDER_CELL).value
        if raw:
            return {"order": json.loads(raw)}
    except Exception:
        pass
    return {"order": None}  # 未保存


def get_saved_order() -> list | None:
    """保存済みのboard_orderを返す。未保存なら None。"""
    return _shared_order_cache().get("order")


def build_initial_board_order(metrics_list) -> list:
    """
    現在のMETRICSから board_order を構築。
    保存済み順があればそれを尊重し、不足分はデフォルトで補完する。
    metrics_list: METRICS のリスト (Metric オブジェクト)
    ※ ツール カテゴリの items は メンバー名 一覧として扱う（並び替え対象）
    """
    from tool_members_store import get_member_names  # 動的メンバー名

    # 現在のカテゴリ→ラベル一覧
    current_cats: dict[str, list[str]] = {}
    for m in metrics_list:
        if m.category == "ツール" and m.key.startswith("talk_script_"):
            continue  # ツール配下のメンバー別ボードは別管理
        current_cats.setdefault(m.category, []).append(m.label)
    # ツールはアクティブなメンバー名一覧で管理
    current_cats["ツール"] = get_member_names()

    saved = get_saved_order()

    if not saved:
        # 初回 or 失敗時 → デフォルト順で構築
        order = []
        for cat in DEFAULT_CATEGORY_ORDER:
            if cat in current_cats:
                order.append({"header": cat, "items": current_cats[cat]})
        for cat, items in current_cats.items():
            if cat not in DEFAULT_CATEGORY_ORDER:
                order.append({"header": cat, "items": items})
        return order

    # 保存済み順をベースに、現在のMETRICSと整合を取る
    order = []
    used_cats = set()
    for entry in saved:
        cat = entry.get("header")
        if cat is None or cat not in current_cats:
            continue
        saved_items = entry.get("items", []) or []
        current_items = current_cats[cat]
        # 既存順序を保ちつつ、新規アイテムは末尾に追加
        merged = [i for i in saved_items if i in current_items]
        merged += [i for i in current_items if i not in merged]
        order.append({"header": cat, "items": merged})
        used_cats.add(cat)
    # 保存されていない新カテゴリは末尾に追加
    for cat, items in current_cats.items():
        if cat not in used_cats:
            order.append({"header": cat, "items": items})

    return order


_last_save = {"t": 0.0}


def save_order(board_order: list) -> tuple[bool, str]:
    """board_orderをGoogle Sheetsに保存（5秒スロットリング）。"""
    now = time.time()
    if now - _last_save["t"] < 5:
        return False, "保存スキップ（5秒以内の連続保存）"
    try:
        ws = _get_ws()
        ws.update_acell(UI_ORDER_CELL, json.dumps(board_order, ensure_ascii=False))
        # キャッシュも更新
        cache = _shared_order_cache()
        cache["order"] = board_order
        _last_save["t"] = now
        return True, "並び順を保存しました"
    except Exception as e:
        return False, f"並び順保存エラー: {e}"


def clear_order_cache():
    """共有キャッシュをクリア（次回読み込みで再取得）。"""
    _shared_order_cache.clear()
