"""
集計指標の定義（talk-tool 版）

このビルドではツールタブ（トークスクリプト）と資料ボードのみを扱う。
サイドバーの集計系ボード（FC/CX/開通進捗/シフト/育成KPI 等）は含めない。
"""

from dataclasses import dataclass
from typing import Callable, Optional, Union
import pandas as pd
from simple_salesforce import Salesforce

FetchResult = Union[pd.DataFrame, dict[str, pd.DataFrame]]


@dataclass
class Metric:
    key: str
    label: str
    description: str
    fetch: Callable[[Salesforce], FetchResult]
    group_col: Optional[str] = None
    value_col: Optional[str] = None
    category: str = "活動"
    list_label: str = "一覧"


# ======================================================================
# 資料ボード（ツール内） — Google Sheets から読み出し
# ======================================================================
_SHIRYOU_SHEET_ID = "1E-bMWswznqU8GZBA-3cUy9FAYKO0oYWGsf4tk6w4ryY"


def fetch_fc_shiryou(sf: Salesforce) -> dict[str, pd.DataFrame]:
    """
    シート2（1週間後FC 基本手順）とシート3（不備対応手順）を読み取り、
    セクションごとに整形して返す。
    戻り値は {"__shiryou__": [section, ...]} の特殊形式。
    app.py 側でカスタム描画する。
    """
    from talk_script_store import _get_gspread_client

    try:
        client = _get_gspread_client()
        sp = client.open_by_key(_SHIRYOU_SHEET_ID)
    except Exception as e:
        return {"エラー": pd.DataFrame({"メッセージ": [f"シート取得失敗: {e}"]})}

    def _read_sheet(name: str) -> list[list[str]]:
        ws = sp.worksheet(name)
        return ws.get_all_values()

    try:
        raw2 = _read_sheet("シート2")
        raw3 = _read_sheet("シート3")
    except Exception as e:
        return {"エラー": pd.DataFrame({"メッセージ": [f"シート読み込み失敗: {e}"]})}

    sections = []
    sections.append(_parse_sheet2(raw2))
    sections.append(_parse_sheet3(raw3))

    return {"__shiryou__": sections}


def _cell(row: list[str], idx: int) -> str:
    if idx < len(row):
        return (row[idx] or "").strip()
    return ""


def _collect(raw, rows, col) -> list[str]:
    """指定行範囲・列から空でないセルを収集。"""
    return [_cell(raw[i], col) for i in range(rows[0], min(rows[1], len(raw))) if _cell(raw[i], col)]


def _parse_sheet2(raw: list[list[str]]) -> dict:
    """シート2をフロー図向けに構造化。"""
    return {
        "title": "1週間後FC 基本手順",
        "scope": _cell(raw[2], 1) if len(raw) > 2 else "",
        "task": _cell(raw[4], 1) if len(raw) > 4 else "",
        "confirm": _collect(raw, (6, 14), 1),
        "after_call": [
            {"label": "架電　留守",         "icon": "phone_rusu",    "items": _collect(raw, (15, 30), 1)},
            {"label": "架電　留守（7日目）", "icon": "phone_rusu7",   "items": _collect(raw, (15, 30), 3)},
            {"label": "架電　完了",         "icon": "phone_kanryou", "items": _collect(raw, (15, 30), 5)},
        ],
        "callback": [
            {"label": "折り返し対応（再コール）", "items": _collect(raw, (30, 41), 1)},
            {"label": "折り返し対応（完了時）",   "items": _collect(raw, (30, 41), 4)},
        ],
    }


def _parse_sheet3(raw: list[list[str]]) -> dict:
    """シート3を不備カテゴリ別に構造化。"""
    categories = []

    # --- 番ポ不備 ---
    categories.append({
        "name": "番ポ不備", "color": "#E67E22",
        "desc": _cell(raw[4], 1) if len(raw) > 4 else "",
        "steps": _collect(raw, (6, 11), 1),
        "complete": _collect(raw, (17, 28), 1),
        "absent": _collect(raw, (17, 28), 6),
        "flow": _collect(raw, (30, 36), 1),
    })

    # --- 住所不備 ---
    categories.append({
        "name": "住所不備", "color": "#2980B9",
        "desc": _cell(raw[4], 10) if len(raw) > 4 else "",
        "steps": _collect(raw, (6, 11), 10),
        "complete": _collect(raw, (17, 28), 10),
        "absent": _collect(raw, (17, 28), 15),
        "flow": _collect(raw, (31, 36), 10),
    })

    # --- 事業変 ---
    categories.append({
        "name": "事業変", "color": "#8E44AD",
        "desc": _cell(raw[60], 1) if len(raw) > 60 else "",
        "steps": _collect(raw, (64, 67), 1),
        "notes": _collect(raw, (69, 73), 1),
        "complete": _collect(raw, (78, 93), 1),
        "absent": _collect(raw, (78, 93), 6),
        "flow": _collect(raw, (95, 101), 1),
    })

    # --- 事前解約 ---
    categories.append({
        "name": "事前解約", "color": "#C0392B",
        "desc": _cell(raw[60], 11) if len(raw) > 60 else "",
        "steps": _collect(raw, (64, 67), 11),
        "notes": _collect(raw, (69, 73), 11),
        "complete": _collect(raw, (78, 93), 11),
        "absent": _collect(raw, (78, 93), 15),
        "flow": [],
    })

    # --- 豆知識 ---
    knowledge = _collect(raw, (37, 54), 1)

    return {
        "title": "不備対応手順",
        "categories": categories,
        "knowledge": knowledge,
    }


# ======================================================================
# METRICS — talk-tool 版ではツールのみ
# ======================================================================
METRICS: list[Metric] = []


# --- ツール: メンバー別トークスクリプト（動的管理） ---
from tool_members_store import get_members, get_all_member_names, get_member_names, get_boards_as_tuples

# 全メンバー名（非アクティブ含む、インデックス安定用）
TALK_SCRIPT_MEMBERS_ALL: list[str] = get_all_member_names()
# アクティブメンバー名（サイドバー表示用）
TALK_SCRIPT_MEMBERS: list[str] = get_member_names()

# 各メンバーが持てるボード一覧（ストアから動的取得）
# (suffix, label) のタプルリスト
TALK_SCRIPT_BOARDS: list[tuple[str, str]] = get_boards_as_tuples()


def _build_talk_script_metrics() -> list[Metric]:
    """メンバー×割当済みボードから Metric リストを動的生成。"""
    result = []
    members = get_members()
    boards = get_boards_as_tuples()
    for _i, _m in enumerate(members):
        if not _m.get("active", True):
            continue
        for _suffix, _board_label in boards:
            if _suffix in _m.get("assignments", []):
                result.append(Metric(
                    key=f"talk_script_{_i:02d}_{_suffix}",
                    label=_board_label,
                    description=f"{_m['name']} の {_board_label}",
                    fetch=lambda sf: pd.DataFrame(),
                    category="ツール",
                ))
    return result


METRICS.extend(_build_talk_script_metrics())


def reload_talk_script_metrics():
    """メンバー/ボード変更後にMETRICSを再構築（マスタ画面から呼ばれる）。"""
    global TALK_SCRIPT_MEMBERS, TALK_SCRIPT_MEMBERS_ALL, TALK_SCRIPT_BOARDS
    from tool_members_store import clear_members_cache
    clear_members_cache()
    TALK_SCRIPT_MEMBERS_ALL = get_all_member_names()
    TALK_SCRIPT_MEMBERS = get_member_names()
    TALK_SCRIPT_BOARDS = get_boards_as_tuples()
    METRICS[:] = [m for m in METRICS if not m.key.startswith("talk_script_")]
    METRICS.extend(_build_talk_script_metrics())


def parse_talk_script_key(key: str) -> tuple[str, str] | None:
    """
    talk_script_NN_xxx 形式のキーから (メンバー名, ボードラベル) を返す。
    パースできなければ None。
    """
    if not key.startswith("talk_script_"):
        return None
    parts = key.split("_", 3)
    # ['talk', 'script', 'NN', 'suffix']
    if len(parts) < 4:
        return None
    try:
        idx = int(parts[2])
    except ValueError:
        return None
    if idx >= len(TALK_SCRIPT_MEMBERS_ALL):
        return None
    suffix = parts[3]
    label = next((lbl for sfx, lbl in TALK_SCRIPT_BOARDS if sfx == suffix), suffix)
    return TALK_SCRIPT_MEMBERS_ALL[idx], label


def get_metric(key: str) -> Metric:
    for m in METRICS:
        if m.key == key:
            return m
    raise KeyError(key)
