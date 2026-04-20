"""
Salesforce 集計ダッシュボード（Streamlit）

ローカル実行:
    py -m streamlit run app.py

新しい集計を追加するには metrics.py に Metric を追記するだけ。
"""

import pandas as pd
try:
    pd.options.future.infer_string = False
except Exception:
    pass
import streamlit as st
from st_aggrid import AgGrid, GridOptionsBuilder, JsCode
from streamlit_sortables import sort_items

from sf_client import get_sf
from metrics import METRICS, get_metric, TALK_SCRIPT_MEMBERS, TALK_SCRIPT_BOARDS, parse_talk_script_key, reload_talk_script_metrics

st.set_page_config(page_title="トークスクリプトツール", page_icon="📞", layout="wide")

# ブラウザ自動翻訳を無効化 & フォント設定
st.markdown(
    """
    <script>
    document.documentElement.setAttribute('translate', 'no');
    document.documentElement.setAttribute('lang', 'ja');
    document.documentElement.classList.add('notranslate');
    </script>
    <meta name="google" content="notranslate">
    """,
    unsafe_allow_html=True,
)
st.markdown(
    """
    <style>
    html, body, [class*="css"], .stMarkdown, .stDataFrame, th, td,
    .ag-theme-balham, .ag-cell, .ag-header-cell-text {
        font-family: 'メイリオ', Meiryo, 'Hiragino Sans', 'Yu Gothic', sans-serif !important;
    }
    /* サイドバー背景グラデーション: ライトモード */
    html.light-mode [data-testid="stSidebar"] {
        background: linear-gradient(180deg, #e8eaf6 0%, #c5cae9 30%, #9fa8da 70%, #b39ddb 100%) !important;
        border-right: 2px solid rgba(0, 0, 0, 0.15) !important;
    }
    html.light-mode [data-testid="stSidebar"] *,
    html.light-mode [data-testid="stSidebar"] button {
        color: #1a1a2e !important;
    }
    /* サイドバー背景グラデーション: ダークモード */
    html.dark-mode [data-testid="stSidebar"] {
        background: linear-gradient(180deg, #1a1a2e 0%, #16213e 30%, #0f3460 70%, #533483 100%) !important;
        border-right: 2px solid rgba(255, 255, 255, 0.2) !important;
    }
    html.dark-mode [data-testid="stSidebar"] *,
    html.dark-mode [data-testid="stSidebar"] button,
    html.dark-mode [data-testid="stSidebar"] h3,
    html.dark-mode [data-testid="stSidebar"] p,
    html.dark-mode [data-testid="stSidebar"] span,
    html.dark-mode [data-testid="stSidebar"] label,
    html.dark-mode [data-testid="stSidebar"] summary,
    html.dark-mode [data-testid="stSidebar"] summary p,
    html.dark-mode [data-testid="stSidebar"] summary svg {
        color: #ffffff !important;
        fill: #ffffff !important;
    }
    html.dark-mode [data-testid="stSidebar"] button {
        background: rgba(255, 255, 255, 0.12) !important;
        border: 1px solid rgba(255, 255, 255, 0.25) !important;
    }
    html.dark-mode [data-testid="stSidebar"] button:hover {
        background: rgba(255, 255, 255, 0.22) !important;
    }
    /* メインエリア背景: ライトモード */
    html.light-mode [data-testid="stAppViewContainer"] {
        background: linear-gradient(180deg, #e8eaf6 0%, #c5cae9 30%, #9fa8da 70%, #b39ddb 100%) !important;
    }
    html.light-mode [data-testid="stMain"],
    html.light-mode [data-testid="stMainBlockContainer"],
    html.light-mode [data-testid="stVerticalBlock"],
    html.light-mode .main .block-container,
    html.light-mode section[data-testid="stMain"] {
        background: transparent !important;
    }
    html.light-mode [data-testid="stHeader"] {
        background: rgba(255, 255, 255, 0.8) !important;
        backdrop-filter: blur(10px) !important;
    }
    /* メインエリア背景: ダークモード */
    html.dark-mode [data-testid="stAppViewContainer"] {
        background: linear-gradient(180deg, #1a1a2e 0%, #16213e 30%, #0f3460 70%, #533483 100%) !important;
    }
    html.dark-mode [data-testid="stMain"] {
        background: transparent !important;
    }
    /* +タブを右端に寄せる */
    [data-testid="stTabs"] [role="tablist"] {
        display: flex;
        width: 100%;
    }
    [data-testid="stTabs"] [role="tablist"] button:last-child {
        margin-left: auto;
        margin-right: 0;
        position: absolute;
        right: 0;
    }
    </style>
    """,
    unsafe_allow_html=True,
)


# ----------------------------------------------------------------------
# 接続 & データ取得（キャッシュ）
# ----------------------------------------------------------------------
@st.cache_resource
def _sf():
    return get_sf()


# talk-tool 版: 集計系ボードは持たないため、_load は未使用。
# トークスクリプト/資料ボード内で個別にキャッシュ戦略を設けている。


# ----------------------------------------------------------------------
# サイドバー: 指標選択
# ----------------------------------------------------------------------
st.sidebar.markdown(
    '<div style="margin-bottom:10px;">'
    '<span style="font-size:1.5rem;font-weight:bold;">営業トークスクリプト</span>'
    '</div>',
    unsafe_allow_html=True,
)

# カテゴリでグルーピング
# ツール配下の talk_script_* メトリクスは「サイドバーでネスト描画」するため、
# board_order の通常アイテムからは除外する
categories: dict[str, list] = {}
for m in METRICS:
    if m.category == "ツール" and m.key.startswith("talk_script_"):
        continue
    categories.setdefault(m.category, []).append(m)
categories.setdefault("ツール", [])  # 空でも存在を保証

label_to_key = {m.label: m.key for m in METRICS}

# セッションに並び順を保持（Google Sheetsから読み込み、無ければデフォルト）
from ui_order_store import build_initial_board_order, save_order as _save_board_order
if "board_order" not in st.session_state:
    st.session_state["board_order"] = build_initial_board_order(METRICS)
else:
    # 旧形式（ツールカテゴリが空 or 存在しない）を検出して再構築
    _bo = st.session_state["board_order"]
    _tool_entry = next((c for c in _bo if c.get("header") == "ツール"), None)
    if _tool_entry is None or not _tool_entry.get("items"):
        del st.session_state["board_order"]
        st.rerun()

# カテゴリ別配色
_CAT_COLORS = {
    "1週間後FC": {"bg": "#4A6FA5", "fg": "#ffffff"},
    "促進":      {"bg": "#2E8B57", "fg": "#ffffff"},
    "ツール":    {"bg": "#D4850A", "fg": "#ffffff"},
}

# サイドバー: TOTAL はそのまま表示、他カテゴリはトグル式
for container in st.session_state["board_order"]:
    cat = container["header"]
    if cat == "TOTAL":
        st.sidebar.subheader(cat)
        for label in container["items"]:
            mkey = label_to_key.get(label)
            if mkey and st.sidebar.button(label, key=f"btn_{mkey}", use_container_width=True):
                st.session_state["selected"] = mkey
    else:
        colors = _CAT_COLORS.get(cat, {"bg": "#555", "fg": "#fff"})
        toggle_key = f"cat_open_{cat}"
        if toggle_key not in st.session_state:
            st.session_state[toggle_key] = False
        is_open = st.session_state[toggle_key]
        arrow = "▼" if is_open else "▶"
        css_id = f"cat-{cat}"
        with st.sidebar.container(key=css_id):
            if st.button(f"{arrow}  {cat}", key=f"toggle_{cat}", use_container_width=True):
                st.session_state[toggle_key] = not is_open
                st.rerun()
        if is_open:
            # ツールは「メンバー → ボード」の2階層ネスト描画
            # メンバー順は board_order の保存済みitemsを使用
            if cat == "ツール":
                from tool_members_store import get_member_assignments, get_all_member_names
                _all_names = get_all_member_names()
                for _member_name in container["items"]:
                    if _member_name not in _all_names:
                        continue
                    _mem_idx = _all_names.index(_member_name)
                    _mem_assignments = get_member_assignments(_member_name)
                    if not _mem_assignments:
                        continue  # トーク割当なし → 非表示
                    _mem_toggle_key = f"_member_open_{_mem_idx}"
                    if _mem_toggle_key not in st.session_state:
                        st.session_state[_mem_toggle_key] = False
                    _mem_open = st.session_state[_mem_toggle_key]
                    _mem_arrow = "▼" if _mem_open else "▶"
                    if st.sidebar.button(
                        f"　{_mem_arrow} {_member_name}",
                        key=f"toggle_member_{_mem_idx}",
                        use_container_width=True,
                    ):
                        st.session_state[_mem_toggle_key] = not _mem_open
                        st.rerun()
                    if _mem_open:
                        for _suffix, _board_label in TALK_SCRIPT_BOARDS:
                            if _suffix not in _mem_assignments:
                                continue  # 未割当のトークはスキップ
                            _bkey = f"talk_script_{_mem_idx:02d}_{_suffix}"
                            _icon = "📖" if _suffix == "shiryou" else "📋"
                            if st.sidebar.button(
                                f"　　{_icon} {_board_label}",
                                key=f"btn_{_bkey}",
                                use_container_width=True,
                            ):
                                st.session_state["selected"] = _bkey
            else:
                for label in container["items"]:
                    mkey = label_to_key.get(label)
                    if mkey and st.sidebar.button(label, key=f"btn_{mkey}", use_container_width=True):
                        st.session_state["selected"] = mkey
valid_keys = {m.key for m in METRICS} | {"_master"}
_sel = st.session_state.get("selected")
# talk_script_* は動的生成のため、キャッシュ未更新でも有効とみなす
if _sel not in valid_keys and not (_sel and _sel.startswith("talk_script_")):
    st.session_state["selected"] = METRICS[0].key if METRICS else "_master"

selected_key = st.session_state["selected"]

if st.sidebar.button("🔄 キャッシュ更新", width="stretch"):
    from tool_members_store import clear_members_cache
    from talk_template_store import clear_template_cache
    from talk_script_store import clear_caches as _clear_ts_caches
    clear_members_cache()
    clear_template_cache()
    _clear_ts_caches()
    reload_talk_script_metrics()
    st.rerun()

st.sidebar.caption("データは5分間キャッシュされます")

# カテゴリトグルボタンの配色をJSで適用
import streamlit.components.v1 as components
components.html("""
<script>
// 親ドキュメント側の自動翻訳を無効化（components.html内で確実に実行）
try {
    const pdoc = window.parent.document;
    pdoc.documentElement.setAttribute('translate', 'no');
    pdoc.documentElement.setAttribute('lang', 'ja');
    pdoc.documentElement.classList.add('notranslate');
    if (!pdoc.querySelector('meta[name="google"][content="notranslate"]')) {
        const m = pdoc.createElement('meta');
        m.name = 'google';
        m.content = 'notranslate';
        pdoc.head.appendChild(m);
    }
    // 描画後の動的要素にも notranslate を強制付与
    function forceNoTranslate() {
        pdoc.querySelectorAll('body, body *').forEach(el => {
            if (!el.classList.contains('notranslate')) {
                el.classList.add('notranslate');
                el.setAttribute('translate', 'no');
            }
        });
    }
    forceNoTranslate();
    new MutationObserver(forceNoTranslate).observe(pdoc.body, {childList: true, subtree: true});
} catch (e) {}

const colorMap = {
    '1週間後FC': {bg: '#4A6FA5', hover: '#3A5F95'},
    '促進':      {bg: '#2E8B57', hover: '#257A4A'},
    '責任者用':  {bg: '#8B5CF6', hover: '#7C3AED'},
    'ツール':    {bg: '#D4850A', hover: '#B8730A'},
};
function styleCatButtons() {
    const sidebar = window.parent.document.querySelector('[data-testid="stSidebar"]');
    if (!sidebar) return;
    const buttons = sidebar.querySelectorAll('button');
    buttons.forEach(btn => {
        const text = btn.textContent.trim().replace(/^[▶▼]\\s*/, '');
        const c = colorMap[text];
        if (c) {
            btn.style.cssText = 'background:'+c.bg+' !important;color:#fff !important;font-weight:700 !important;font-size:1.05rem !important;border:none !important;border-radius:8px !important;';
            btn.onmouseenter = () => btn.style.background = c.hover;
            btn.onmouseleave = () => btn.style.background = c.bg;
        }
    });
}
styleCatButtons();
const obs = new MutationObserver(styleCatButtons);
obs.observe(window.parent.document.body, {childList: true, subtree: true});

// Streamlitのテーマ検出 → html にクラス付与
// stHeaderの背景色はグラデーション適用外なので安定して検出できる
function detectTheme() {
    const doc = window.parent.document;
    const el = doc.querySelector('[data-testid="stHeader"]');
    if (!el) return;
    const bg = window.getComputedStyle(el).backgroundColor;
    const match = bg.match(/\d+/g);
    if (match) {
        const brightness = (parseInt(match[0]) + parseInt(match[1]) + parseInt(match[2])) / 3;
        if (brightness < 128) {
            doc.documentElement.classList.add('dark-mode');
            doc.documentElement.classList.remove('light-mode');
        } else {
            doc.documentElement.classList.add('light-mode');
            doc.documentElement.classList.remove('dark-mode');
        }
    }
}
detectTheme();
setInterval(detectTheme, 2000);
</script>
""", height=0)

if st.sidebar.button("🔒 マスタ", key="btn_master", width="stretch"):
    st.session_state["selected"] = "_master"


# ----------------------------------------------------------------------
# メイン
# ----------------------------------------------------------------------
if selected_key == "_master":
    st.title("⚙ マスタ")
    if not st.session_state.get("master_auth"):
        pw = st.text_input("パスワードを入力してください", type="password", key="master_pw")
        if pw:
            if pw == "nakagawa":
                st.session_state["master_auth"] = True
                st.rerun()
            else:
                st.error("パスワードが違います")
        st.stop()
    # --- 📋 ボード並び順の変更（トグル方式：sort_itemsはexpander非対応） ---
    if "master_order_open" not in st.session_state:
        st.session_state["master_order_open"] = False
    _order_arrow = "▼" if st.session_state["master_order_open"] else "▶"
    if st.button(f"{_order_arrow}  📋 ボード並び順の変更", key="toggle_master_order", use_container_width=True):
        st.session_state["master_order_open"] = not st.session_state["master_order_open"]
        st.rerun()
    if st.session_state["master_order_open"]:
        st.caption("ドラッグ＆ドロップで並び替えてください。「💾 並び順を保存」で全ユーザーに反映されます。")
        _sig = "_".join(
            f"{c['header']}:{len(c.get('items', []))}"
            for c in st.session_state["board_order"]
        )
        new_order = sort_items(
            st.session_state["board_order"],
            multi_containers=True,
            direction="vertical",
            key=f"board_sort_{_sig}",
        )
        st.session_state["board_order"] = new_order
        if st.button("💾 並び順を保存", key="save_board_order", type="primary"):
            ok, msg = _save_board_order(new_order)
            st.session_state["selected"] = "_master"
            st.toast(msg, icon="✅" if ok else "⚠️")

    st.divider()

    # --- 📝 トーク種類管理 ---
    with st.expander("📝 トーク種類管理", expanded=False):
        from tool_members_store import (
            get_members, save_members, clear_members_cache,
            get_boards, save_boards, next_board_suffix,
        )

        _tool_boards = get_boards()
        st.caption("トーク種類の追加・削除ができます。追加した種類はメンバー割当で選択可能になります。")

        # 追加フォーム
        _bc1, _bc2 = st.columns([4, 1])
        _new_board_label = _bc1.text_input("新しいトーク種類名", key="master_new_board", placeholder="例: 新設FCトーク")
        if _bc2.button("➕ 追加", key="master_add_board", use_container_width=True):
            _new_board_label = _new_board_label.strip()
            if not _new_board_label:
                st.warning("種類名を入力してください。")
            elif any(b["label"] == _new_board_label for b in _tool_boards):
                st.warning("同名のトーク種類が既に存在します。")
            else:
                _new_suffix = next_board_suffix()
                _tool_boards.append({"suffix": _new_suffix, "label": _new_board_label})
                ok, msg = save_boards(_tool_boards)
                reload_talk_script_metrics()
                st.toast(f"「{_new_board_label}」を追加しました（ID: {_new_suffix}）", icon="✅")
                st.rerun()

        # 一覧＋削除
        for _bi, _b in enumerate(_tool_boards):
            _bc_name, _bc_del = st.columns([5, 1])
            _bc_name.markdown(f"**{_b['label']}**　`{_b['suffix']}`")
            if len(_tool_boards) > 1:  # 最低1つは残す
                if _bc_del.button("✕", key=f"del_board_{_bi}", help=f"{_b['label']} を削除"):
                    _removed_suffix = _b["suffix"]
                    _tool_boards.pop(_bi)
                    # メンバーの割当からも除去
                    _members_for_cleanup = get_members()
                    for _m in _members_for_cleanup:
                        if _removed_suffix in _m.get("assignments", []):
                            _m["assignments"].remove(_removed_suffix)
                    from tool_members_store import save_all
                    ok, msg = save_all(_members_for_cleanup, _tool_boards)
                    reload_talk_script_metrics()
                    st.toast(f"「{_b['label']}」を削除しました", icon="✅")
                    st.rerun()

    st.divider()

    # --- 👥 ツールメンバー管理 ---
    with st.expander("👥 ツールメンバー管理", expanded=False):
        from tool_members_store import get_members, save_members, clear_members_cache

        _tool_members = get_members()
        _tool_boards_for_assign = get_boards()

        st.caption("メンバーの追加・削除、トーク割当の変更ができます。変更後は「💾 保存」を押してください。")

        # --- メンバー追加 ---
        _add_col1, _add_col2 = st.columns([4, 1])
        _new_name = _add_col1.text_input("新しいメンバー名", key="master_new_member", placeholder="例: 山田 太郎")
        if _add_col2.button("➕ 追加", key="master_add_member", use_container_width=True):
            _new_name = _new_name.strip()
            if not _new_name:
                st.warning("名前を入力してください。")
            elif any(m["name"] == _new_name and m.get("active", True) for m in _tool_members):
                st.warning("同名のメンバーが既に存在します。")
            else:
                _all_suffixes = [b["suffix"] for b in _tool_boards_for_assign]
                # 非アクティブで同名がいれば再有効化
                _reactivated = False
                for m in _tool_members:
                    if m["name"] == _new_name and not m.get("active", True):
                        m["active"] = True
                        m["assignments"] = _all_suffixes
                        _reactivated = True
                        break
                if not _reactivated:
                    _tool_members.append({
                        "name": _new_name,
                        "assignments": _all_suffixes,
                        "active": True,
                    })
                ok, msg = save_members(_tool_members)
                reload_talk_script_metrics()
                # board_orderにも新メンバーを追加
                for entry in st.session_state.get("board_order", []):
                    if entry.get("header") == "ツール":
                        if _new_name not in entry.get("items", []):
                            entry["items"].append(_new_name)
                            _save_board_order(st.session_state["board_order"])
                st.toast(f"「{_new_name}」を追加しました", icon="✅")
                st.rerun()

        # --- メンバー一覧＋トーク割当＋削除 ---
        _member_changed = False
        for _mi, _m in enumerate(_tool_members):
            if not _m.get("active", True):
                continue
            _n_boards = max(len(_tool_boards_for_assign), 1)
            _c_name, _c_talks, _c_del = st.columns([3, _n_boards * 2, 1])
            _c_name.markdown(f"**{_m['name']}**")

            # トーク割当チェックボックス（動的ボード対応）
            _current_assigns = _m.get("assignments", [])
            _new_assigns = []
            _talk_cols = _c_talks.columns(_n_boards)
            for _ti, _b in enumerate(_tool_boards_for_assign):
                _checked = _talk_cols[_ti].checkbox(
                    _b["label"], value=(_b["suffix"] in _current_assigns),
                    key=f"assign_{_mi}_{_b['suffix']}",
                )
                if _checked:
                    _new_assigns.append(_b["suffix"])
            if sorted(_new_assigns) != sorted(_current_assigns):
                _m["assignments"] = _new_assigns
                _member_changed = True

            # 削除ボタン
            if _c_del.button("✕", key=f"del_member_{_mi}", help=f"{_m['name']} を削除"):
                _m["active"] = False
                _m["assignments"] = []
                ok, msg = save_members(_tool_members)
                reload_talk_script_metrics()
                # board_orderからも除去
                for entry in st.session_state.get("board_order", []):
                    if entry.get("header") == "ツール":
                        items = entry.get("items", [])
                        if _m["name"] in items:
                            items.remove(_m["name"])
                            _save_board_order(st.session_state["board_order"])
                st.toast(f"「{_m['name']}」を削除しました", icon="✅")
                st.rerun()

        # 保存ボタン（トーク割当変更時）
        if st.button("💾 メンバー設定を保存", key="save_tool_members", type="primary"):
            ok, msg = save_members(_tool_members)
            if ok:
                reload_talk_script_metrics()
            st.toast(msg, icon="✅" if ok else "⚠️")

    st.divider()

    with st.expander("🏢 商流別名乗りマスタ", expanded=False):
        st.caption(
            "トーク本文の `{{名乗}}` プレースホルダーを、顧客の「取次商材情報」と「商流」から自動で置き換えます。"
            "新しい取次商材／商流が増えたらここに行を追加してください。"
        )
        from nanori_master_store import (
            get_rows as _nanori_get_rows,
            set_rows as _nanori_set_rows,
            save_master as _nanori_save,
            clear_cache as _nanori_clear_cache,
        )

        _nanori_state_key = "_nanori_rows"
        if _nanori_state_key not in st.session_state:
            st.session_state[_nanori_state_key] = [dict(r) for r in _nanori_get_rows()]

        _rows = st.session_state[_nanori_state_key]

        hc1, hc2, hc3, hc4, hc5 = st.columns([3, 2, 3, 2, 1])
        hc1.markdown("**取次商材情報**")
        hc2.markdown("**商流**")
        hc3.markdown("**名乗り（置換後の文言）**")
        hc4.markdown("**置換トリガー文字列**")
        hc5.markdown("**削除**")

        _nanori_to_delete = []
        for _ri, _row in enumerate(_rows):
            c1, c2, c3, c4, c5 = st.columns([3, 2, 3, 2, 1])
            with c1:
                _row["取次商材情報"] = st.text_input(
                    "商材", value=_row.get("取次商材情報", ""),
                    key=f"nanori_shozai_{_ri}",
                    label_visibility="collapsed",
                    placeholder="例: So-net光_004",
                )
            with c2:
                _row["商流"] = st.text_input(
                    "商流", value=_row.get("商流", ""),
                    key=f"nanori_shoryu_{_ri}",
                    label_visibility="collapsed",
                    placeholder="例: 株式会社WAF",
                )
            with c3:
                _row["名乗り"] = st.text_input(
                    "名乗り", value=_row.get("名乗り", ""),
                    key=f"nanori_nanori_{_ri}",
                    label_visibility="collapsed",
                    placeholder="例: 株式会社WAF",
                )
            with c4:
                _row["トリガー"] = st.text_input(
                    "トリガー", value=_row.get("トリガー", ""),
                    key=f"nanori_trigger_{_ri}",
                    label_visibility="collapsed",
                    placeholder="空欄なら {{名乗}}",
                )
            with c5:
                if st.button("🗑", key=f"nanori_del_{_ri}", help="この行を削除"):
                    _nanori_to_delete.append(_ri)

        if _nanori_to_delete:
            for _ri in sorted(_nanori_to_delete, reverse=True):
                _rows.pop(_ri)
            for _i in range(len(_rows) + len(_nanori_to_delete)):
                for _p in ("nanori_shozai_", "nanori_shoryu_", "nanori_nanori_", "nanori_trigger_"):
                    st.session_state.pop(f"{_p}{_i}", None)
            st.rerun()

        st.markdown("&nbsp;", unsafe_allow_html=True)
        bc1, bc2, bc3 = st.columns([1, 1, 1])
        if bc1.button("➕ 行を追加", key="nanori_add_row", use_container_width=True):
            _rows.append({"取次商材情報": "", "商流": "", "名乗り": "", "トリガー": ""})
            st.rerun()
        if bc2.button("💾 名乗りマスタを保存", key="nanori_save", type="primary", use_container_width=True):
            _nanori_set_rows(_rows)
            ok, msg = _nanori_save()
            st.toast(msg, icon="✅" if ok else "⚠️")
            if ok:
                st.session_state["selected"] = "_master"
                st.rerun()
        if bc3.button("⟳ 再読み込み", key="nanori_reload", use_container_width=True):
            _nanori_clear_cache()
            st.session_state.pop(_nanori_state_key, None)
            st.session_state["selected"] = "_master"
            st.rerun()

    st.divider()

    with st.expander("🔁 置換表", expanded=False):
        st.caption(
            "トーク本文中のトリガー文字列を置換後の文言に一律で差し替えます。"
            "条件なしで全トークに適用されます。"
        )
        from replace_master_store import (
            get_rows as _rep_get_rows,
            set_rows as _rep_set_rows,
            save_master as _rep_save,
            clear_cache as _rep_clear_cache,
        )

        _rep_state_key = "_replace_rows"
        if _rep_state_key not in st.session_state:
            st.session_state[_rep_state_key] = [dict(r) for r in _rep_get_rows()]
        _rep_rows = st.session_state[_rep_state_key]

        rhc1, rhc2, rhc3 = st.columns([3, 3, 1])
        rhc1.markdown("**置換トリガー文字列**")
        rhc2.markdown("**置換後の文言**")
        rhc3.markdown("**削除**")

        _rep_to_delete = []
        for _ri, _row in enumerate(_rep_rows):
            rc1, rc2, rc3 = st.columns([3, 3, 1])
            with rc1:
                _row["トリガー"] = st.text_input(
                    "トリガー", value=_row.get("トリガー", ""),
                    key=f"rep_trigger_{_ri}",
                    label_visibility="collapsed",
                    placeholder="置換前の文字列",
                )
            with rc2:
                _row["置換後"] = st.text_input(
                    "置換後", value=_row.get("置換後", ""),
                    key=f"rep_after_{_ri}",
                    label_visibility="collapsed",
                    placeholder="置換後の文言",
                )
            with rc3:
                if st.button("🗑", key=f"rep_del_{_ri}", help="この行を削除"):
                    _rep_to_delete.append(_ri)

        if _rep_to_delete:
            for _ri in sorted(_rep_to_delete, reverse=True):
                _rep_rows.pop(_ri)
            for _i in range(len(_rep_rows) + len(_rep_to_delete)):
                for _p in ("rep_trigger_", "rep_after_"):
                    st.session_state.pop(f"{_p}{_i}", None)
            st.rerun()

        st.markdown("&nbsp;", unsafe_allow_html=True)
        rbc1, rbc2, rbc3 = st.columns([1, 1, 1])
        if rbc1.button("➕ 行を追加", key="rep_add_row", use_container_width=True):
            _rep_rows.append({"トリガー": "", "置換後": ""})
            st.rerun()
        if rbc2.button("💾 置換表を保存", key="rep_save", type="primary", use_container_width=True):
            _rep_set_rows(_rep_rows)
            ok, msg = _rep_save()
            st.toast(msg, icon="✅" if ok else "⚠️")
            if ok:
                st.session_state["selected"] = "_master"
                st.rerun()
        if rbc3.button("⟳ 再読み込み", key="rep_reload", use_container_width=True):
            _rep_clear_cache()
            st.session_state.pop(_rep_state_key, None)
            st.session_state["selected"] = "_master"
            st.rerun()

    st.divider()

    with st.expander("📞 トークスクリプト編集", expanded=False):
        # 編集するトークスクリプトの種別を動的に生成
        _talk_script_options = ["（選択してください）"] + [b["label"] for b in get_boards()]
        _selected_script = st.selectbox(
            "編集するトークスクリプトを選択",
            _talk_script_options,
            key="master_talk_script_select",
        )

        if _selected_script == "（選択してください）":
            st.info("編集したいトークスクリプトを上のプルダウンから選択してください。")
            st.stop()

        st.caption(f"【{_selected_script}】セクションごとに本文を編集できます。「保存」を押すとGoogle Sheetsに即時保存され、全ユーザーに反映されます。")
        from talk_template_store import (
            get_templates,
            save_templates,
            reset_to_default,
            get_sections_by_kind,
            update_sections,
            get_section_rule,
            update_section_rule,
            SONET_FUBI_KEYS,
            SONET_CLOSING_KEYS,
            SONET_SOKUSHIN_KEYS,
            LINE_TEMPLATE_KEYS,
            clear_template_cache,
        )
        from talk_script_store import get_lookup_columns
        _lookup_cols = get_lookup_columns()

        templates = get_templates()
        _sections_by_kind = get_sections_by_kind()

        # 促進用トーク（代コン不備解消用）は専用編集UIに分岐
        if _selected_script == "促進用トーク":
            st.markdown(
                '<div style="background:#2E8B57;color:#fff;padding:10px 16px;'
                'border-radius:8px;font-weight:700;margin:8px 0 12px 0;">'
                '🎯 促進用トーク テンプレート（代コン不備解消用 5種）</div>',
                unsafe_allow_html=True,
            )
            st.caption(
                "ダイコンステータスに応じて自動で切り替わります。"
                "工事日調整希望／API工事取得→工事取得3者間 ／ 番ポ不備→番ポ不備FC ／ "
                "住所確認→住所確認FC ／ 現地調査必要→現地調査3者間 ／ 有派遣へ変更必要→有派遣変更3者間"
            )
            _sokushin_templates = templates.setdefault("Sonet_sokushin", {})
            for skey in SONET_SOKUSHIN_KEYS:
                with st.expander(f"🎯 {skey}", expanded=False):
                    current = _sokushin_templates.get(skey, "")
                    new_val = st.text_area(
                        skey,
                        value=current,
                        height=320,
                        key=f"master_sokushin_only_{skey}",
                        label_visibility="collapsed",
                    )
                    if new_val != current:
                        _sokushin_templates[skey] = new_val

            st.divider()
            col_save_sk, col_reload_sk = st.columns([1, 1])
            if col_save_sk.button(
                "💾 促進用トーク を保存",
                key="talk_save_sokushin_only",
                type="primary",
                use_container_width=True,
            ):
                ok, msg = save_templates()
                st.toast(msg, icon="✅" if ok else "⚠️")
                if ok:
                    st.session_state["selected"] = "_master"
                    st.rerun()
            if col_reload_sk.button(
                "⟳ 再読み込み",
                key="talk_reload_sokushin_only",
                use_container_width=True,
            ):
                clear_template_cache()
                st.session_state["selected"] = "_master"
                st.rerun()
            st.stop()

        talk_kind_tabs = st.tabs(["So-net光", "NURO光"])
        _kind_meta = [
            ("Sonet", "So-net光", "#1976D2"),
            ("NURO", "NURO光", "#7B1FA2"),
        ]
        for tab, (kind, label, color) in zip(talk_kind_tabs, _kind_meta):
            with tab:
                kind_templates = templates.setdefault(kind, {})
                current_sections = list(_sections_by_kind.get(kind, []))

                # サブタブで機能を整理
                sub_tabs = st.tabs([
                    "📝 セクション構成・表示条件",
                    "✏️ テンプレート本文編集",
                    "💬 LINEテンプレ",
                ])

                # ===== サブタブ1: セクション構成・表示条件 =====
                with sub_tabs[0]:
                    st.caption("セクション名の変更・追加・削除・並び替え、表示/非表示条件の設定ができます。変更後は下の「💾 保存」を押してください。")

                    # 並び替え用session_stateキー
                    _sec_order_key = f"_sec_order_{kind}"
                    if _sec_order_key not in st.session_state:
                        st.session_state[_sec_order_key] = list(current_sections)
                    _sec_list = st.session_state[_sec_order_key]

                    # 各セクションの編集行
                    _to_delete = []
                    _renamed = {}
                    _OP_OPTIONS = {
                        "not_empty": "入力済みの時だけ",
                        "empty": "空の時だけ",
                        "eq": "次の文字列と一致する",
                        "ne": "次の文字列と一致しない",
                        "contains": "次の文字列を含む",
                        "not_contains": "次の文字列を含まない",
                        "starts_with": "次の文字列から始まる",
                        "lt": "＜（より小さい）",
                        "gt": "＞（より大きい）",
                        "le": "＝＜（以下）",
                        "ge": "＝＞（以上）",
                    }
                    _OP_KEYS = list(_OP_OPTIONS.keys())
                    # value入力が必要な演算子
                    _OPS_NEED_VALUE = {"eq", "ne", "contains", "not_contains", "starts_with", "lt", "gt", "le", "ge"}
                    for si, sn in enumerate(_sec_list):
                        _rule_current = get_section_rule(kind, sn)
                        _cur_field = _rule_current.get("field", "")
                        _cur_op = _rule_current.get("op", "")
                        _has_rule = bool(_cur_field and _cur_op)

                        # 表示状態バッジ
                        if _has_rule:
                            _badge_bg = "#FFF3CD"
                            _badge_fg = "#856404"
                            _badge_text = f"⚙ 条件付き表示"
                        else:
                            _badge_bg = "#D4EDDA"
                            _badge_fg = "#155724"
                            _badge_text = "✓ 常に表示"

                        # カード枠の開始
                        st.markdown(
                            f'<div style="background:#fff;border:2px solid #8B5CF6;border-radius:10px;'
                            f'padding:14px 16px;margin:12px 0 6px 0;box-shadow:0 1px 3px rgba(0,0,0,0.06);">'
                            f'<div style="display:flex;align-items:center;gap:10px;margin-bottom:8px;">'
                            f'<span style="background:#8B5CF6;color:#fff;border-radius:50%;'
                            f'width:28px;height:28px;display:inline-flex;align-items:center;justify-content:center;'
                            f'font-weight:700;font-size:0.9rem;">{si+1}</span>'
                            f'<span style="font-weight:700;font-size:1.05rem;color:#333;">{sn}</span>'
                            f'<span style="background:{_badge_bg};color:{_badge_fg};padding:2px 10px;'
                            f'border-radius:12px;font-size:0.8rem;font-weight:600;margin-left:auto;">'
                            f'{_badge_text}</span>'
                            f'</div></div>',
                            unsafe_allow_html=True,
                        )

                        # 1行目: セクション名の編集 + 操作ボタン
                        st.markdown(
                            '<div style="font-size:0.85rem;color:#666;margin:4px 0 2px 0;">セクション名</div>',
                            unsafe_allow_html=True,
                        )
                        cn1, cn2, cn3, cn4 = st.columns([8, 1, 1, 1])
                        with cn1:
                            new_name = st.text_input(
                                f"sec_{si}", value=sn, key=f"sec_name_{kind}_{si}",
                                label_visibility="collapsed",
                            )
                            if new_name != sn:
                                _renamed[si] = new_name
                        with cn2:
                            if si > 0 and st.button("⬆", key=f"sec_up_{kind}_{si}", help="上に移動"):
                                _sec_list[si], _sec_list[si - 1] = _sec_list[si - 1], _sec_list[si]
                                # 並び替え後はtext_input session_stateをクリア
                                # （インデックスキーに古い名前が残ってrename誤検知を防ぐ）
                                for _i in range(len(_sec_list)):
                                    st.session_state.pop(f"sec_name_{kind}_{_i}", None)
                                st.rerun()
                        with cn3:
                            if si < len(_sec_list) - 1 and st.button("⬇", key=f"sec_down_{kind}_{si}", help="下に移動"):
                                _sec_list[si], _sec_list[si + 1] = _sec_list[si + 1], _sec_list[si]
                                for _i in range(len(_sec_list)):
                                    st.session_state.pop(f"sec_name_{kind}_{_i}", None)
                                st.rerun()
                        with cn4:
                            if st.button("🗑", key=f"sec_del_{kind}_{si}", help="削除"):
                                _to_delete.append(si)

                        # 2行目: 表示タイミング（ラジオで選択）
                        st.markdown(
                            '<div style="font-size:0.85rem;color:#666;margin:10px 0 2px 0;">表示タイミング</div>',
                            unsafe_allow_html=True,
                        )
                        _mode_options = ["常に表示", "条件付き（顧客情報に応じて自動判定）"]
                        _current_mode = _mode_options[1] if _has_rule else _mode_options[0]
                        _new_mode = st.radio(
                            "表示モード",
                            options=_mode_options,
                            index=_mode_options.index(_current_mode),
                            key=f"sec_rule_mode_{kind}_{si}",
                            label_visibility="collapsed",
                            horizontal=True,
                        )

                        # 条件付きを選んだ場合のみ、フィールド+値+条件の詳細が出現
                        if _new_mode == _mode_options[1]:
                            st.markdown(
                                '<div style="background:#F3E8FF;border-radius:6px;padding:10px 12px;margin-top:6px;">'
                                '<div style="font-size:0.88rem;color:#5B2C6F;margin-bottom:6px;">'
                                '顧客情報の下記項目が条件を満たす時のみ、このセクションを表示します。</div>'
                                '</div>',
                                unsafe_allow_html=True,
                            )
                            _field_options = [""] + _lookup_cols
                            _field_idx = _field_options.index(_cur_field) if _cur_field in _field_options else 0
                            _cur_value = _rule_current.get("value", "")

                            # 1段目: 顧客情報の項目
                            st.markdown(
                                '<div style="font-size:0.8rem;color:#666;margin:8px 0 2px 0;">① 顧客情報の項目</div>',
                                unsafe_allow_html=True,
                            )
                            _new_field = st.selectbox(
                                "判定項目",
                                options=_field_options,
                                format_func=lambda x: "（選択してください）" if x == "" else x,
                                index=_field_idx,
                                key=f"sec_rule_field_{kind}_{si}",
                                label_visibility="collapsed",
                            )

                            # 2段目: 文字列入力（比較する値）
                            st.markdown(
                                '<div style="font-size:0.8rem;color:#666;margin:8px 0 2px 0;">② 比較する値（文字列入力）</div>',
                                unsafe_allow_html=True,
                            )
                            _new_value = st.text_input(
                                "比較値",
                                value=_cur_value,
                                key=f"sec_rule_value_{kind}_{si}",
                                label_visibility="collapsed",
                                placeholder="例: あり / 2026-01-01 / ソネット など（空/入力済みチェックの時は未使用）",
                                disabled=(not _new_field),
                            )

                            # 3段目: 条件
                            st.markdown(
                                '<div style="font-size:0.8rem;color:#666;margin:8px 0 2px 0;">③ 条件</div>',
                                unsafe_allow_html=True,
                            )
                            _op_idx = _OP_KEYS.index(_cur_op) if _cur_op in _OP_KEYS else 0
                            _new_op = st.selectbox(
                                "条件",
                                options=_OP_KEYS,
                                format_func=lambda x: _OP_OPTIONS[x],
                                index=_op_idx,
                                key=f"sec_rule_op_{kind}_{si}",
                                label_visibility="collapsed",
                                disabled=(not _new_field),
                            )

                            # プレビュー文 & ルール確定
                            if not _new_field:
                                st.markdown(
                                    '<div style="background:#FEE2E2;border-left:3px solid #DC2626;'
                                    'padding:6px 10px;margin-top:8px;border-radius:4px;font-size:0.85rem;color:#7F1D1D;">'
                                    '⚠ 「顧客情報の項目」を選択してください</div>',
                                    unsafe_allow_html=True,
                                )
                                _new_rule = {}
                            elif _new_op in _OPS_NEED_VALUE and not _new_value:
                                st.markdown(
                                    '<div style="background:#FEE2E2;border-left:3px solid #DC2626;'
                                    'padding:6px 10px;margin-top:8px;border-radius:4px;font-size:0.85rem;color:#7F1D1D;">'
                                    '⚠ 「比較する値」を入力してください</div>',
                                    unsafe_allow_html=True,
                                )
                                _new_rule = {}
                            else:
                                if _new_op in _OPS_NEED_VALUE:
                                    _preview = f"💡 {_new_field} が「{_new_value}」{_OP_OPTIONS[_new_op]} 時のみ表示"
                                    _new_rule = {"field": _new_field, "op": _new_op, "value": _new_value}
                                else:
                                    _preview = f"💡 {_new_field} が {_OP_OPTIONS[_new_op]} 表示されます"
                                    _new_rule = {"field": _new_field, "op": _new_op}
                                st.markdown(
                                    f'<div style="background:#FFFBEA;border-left:3px solid #F59E0B;'
                                    f'padding:6px 10px;margin-top:8px;border-radius:4px;font-size:0.85rem;color:#78350F;">'
                                    f'{_preview}</div>',
                                    unsafe_allow_html=True,
                                )
                        else:
                            _new_rule = {}

                        st.markdown('<div style="margin-bottom:16px;"></div>', unsafe_allow_html=True)

                        # ルール変更を反映
                        if _new_rule != _rule_current:
                            update_section_rule(kind, sn, _new_rule)

                    # 名前変更を反映
                    for si, new_name in _renamed.items():
                        old_name = _sec_list[si]
                        _sec_list[si] = new_name
                        # テンプレート本文も引き継ぎ
                        if old_name in kind_templates and old_name != new_name:
                            kind_templates[new_name] = kind_templates.pop(old_name)
                        # 表示ルールも引き継ぎ
                        _old_rule = get_section_rule(kind, old_name)
                        if _old_rule and old_name != new_name:
                            update_section_rule(kind, old_name, {})
                            update_section_rule(kind, new_name, _old_rule)

                    # 削除を反映
                    if _to_delete:
                        for di in sorted(_to_delete, reverse=True):
                            _sec_list.pop(di)
                        # 削除後はインデックスがずれるためtext_input session_stateをクリア
                        for _i in range(len(_sec_list) + len(_to_delete)):
                            st.session_state.pop(f"sec_name_{kind}_{_i}", None)
                        st.rerun()

                    # 新規追加
                    ac1, ac2 = st.columns([4, 1])
                    with ac1:
                        _new_sec = st.text_input("新しいセクション名", key=f"sec_new_{kind}", placeholder="例: ヒアリング")
                    with ac2:
                        st.markdown("<div style='height:28px'></div>", unsafe_allow_html=True)
                        if st.button("＋ 追加", key=f"sec_add_{kind}", use_container_width=True):
                            if _new_sec and _new_sec not in _sec_list:
                                _sec_list.append(_new_sec)
                                # 新規追加後もtext_input session_stateをクリア（念のため）
                                for _i in range(len(_sec_list)):
                                    st.session_state.pop(f"sec_name_{kind}_{_i}", None)
                                st.rerun()

                # ===== サブタブ2: テンプレート本文編集 =====
                with sub_tabs[1]:
                    st.caption("各セクションの本文を編集できます。「💾 保存」で全ユーザーに反映されます。")
                    for sec_name in _sections_by_kind.get(kind, []):
                        # 不備解消(Sonet)は9種テンプレに展開
                        if sec_name == "不備解消" and kind == "Sonet":
                            fubi_templates = templates.setdefault("Sonet_fubi", {})
                            st.markdown(
                                f'<div style="background:{color};color:#fff;padding:8px 14px;'
                                f'border-radius:6px;font-weight:700;margin:18px 0 8px 0;">'
                                f'【不備解消】テンプレート（ダイコンステータス別 9種）</div>',
                                unsafe_allow_html=True,
                            )
                            st.caption("ダイコンステータスから自動選択されます。「工事日調整希望」は「工事取得」に変換されます。")
                            for fkey in SONET_FUBI_KEYS:
                                with st.expander(f"📋 {fkey}", expanded=False):
                                    current = fubi_templates.get(fkey, "")
                                    new_val = st.text_area(
                                        fkey,
                                        value=current,
                                        height=300,
                                        key=f"talk_edit_fubi_{fkey}",
                                        label_visibility="collapsed",
                                    )
                                    if new_val != current:
                                        fubi_templates[fkey] = new_val
                            continue

                        # 締め(Sonet)は2種テンプレに展開
                        if sec_name == "締め" and kind == "Sonet":
                            closing_templates = templates.setdefault("Sonet_closing", {})
                            st.markdown(
                                f'<div style="background:{color};color:#fff;padding:8px 14px;'
                                f'border-radius:6px;font-weight:700;margin:18px 0 8px 0;">'
                                f'【締め】テンプレート（利用回線あり/不明 2種）</div>',
                                unsafe_allow_html=True,
                            )
                            st.caption("お客様の利用回線が「あり」「不明 or 空欄」のどちらかで自動選択されます。")
                            for ckey in SONET_CLOSING_KEYS:
                                with st.expander(f"📋 {ckey}", expanded=False):
                                    current = closing_templates.get(ckey, "")
                                    new_val = st.text_area(
                                        ckey,
                                        value=current,
                                        height=240,
                                        key=f"talk_edit_closing_{ckey}",
                                        label_visibility="collapsed",
                                    )
                                    if new_val != current:
                                        closing_templates[ckey] = new_val
                            continue

                        with st.expander(f"【{sec_name}】", expanded=False):
                            current = kind_templates.get(sec_name, "")
                            new_val = st.text_area(
                                sec_name,
                                value=current,
                                height=240,
                                key=f"talk_edit_{kind}_{sec_name}",
                                label_visibility="collapsed",
                            )
                            if new_val != current:
                                kind_templates[sec_name] = new_val

                # ===== サブタブ3: LINEテンプレ =====
                with sub_tabs[2]:
                    st.caption("完了LINE・留守LINE・留守完了LINEの3種を編集できます。")
                    line_store_key = "Sonet_line" if kind == "Sonet" else "NURO_line"
                    line_store = templates.setdefault(line_store_key, {})
                    st.markdown(
                        f'<div style="background:#06C755;color:#fff;padding:8px 14px;'
                        f'border-radius:6px;font-weight:700;margin:18px 0 8px 0;">'
                        f'💬 LINEテンプレ（3種）</div>',
                        unsafe_allow_html=True,
                    )
                    for lkey in LINE_TEMPLATE_KEYS:
                        with st.expander(f"💬 {lkey}", expanded=False):
                            current = line_store.get(lkey, "")
                            new_val = st.text_area(
                                lkey,
                                value=current,
                                height=240,
                                key=f"talk_edit_line_{kind}_{lkey}",
                                label_visibility="collapsed",
                            )
                            if new_val != current:
                                line_store[lkey] = new_val

                # ===== 共通の保存/再読み込み（タブ外） =====
                st.divider()
                col_save, col_reload = st.columns([1, 1])
                if col_save.button(f"💾 {label} を保存", key=f"talk_save_{kind}", use_container_width=True, type="primary"):
                    # セクション構成の変更（並び替え・追加・削除）もここで確定
                    update_sections(kind, list(_sec_list))
                    st.session_state[_sec_order_key] = list(_sec_list)
                    ok, msg = save_templates()
                    st.toast(msg, icon="✅" if ok else "⚠️")
                    if ok:
                        st.session_state["selected"] = "_master"
                        st.rerun()
                if col_reload.button(f"⟳ 再読み込み", key=f"talk_reload_{kind}", use_container_width=True):
                    clear_template_cache()
                    st.session_state["selected"] = "_master"
                    st.rerun()
    st.stop()

metric = get_metric(selected_key)
# talk_script_NN_xxx の場合はメンバー名 / ボード名 を見出しに反映
_parsed_title = parse_talk_script_key(selected_key)
if _parsed_title:
    _title = f"{_parsed_title[0]} ／ {_parsed_title[1]}"
else:
    _title = metric.label
st.markdown(f'<h1 translate="no">{_title}</h1>', unsafe_allow_html=True)

# 資料ボード（ツール内）: talk_script_NN_shiryou 形式
if selected_key.startswith("talk_script_") and selected_key.endswith("_shiryou"):
    from metrics import fetch_fc_shiryou

    @st.cache_data(ttl=86400, show_spinner="資料を取得中...")
    def _load_shiryou(_cache_day: str):
        return fetch_fc_shiryou(_sf())

    from datetime import datetime, timezone, timedelta as _td
    _jst = timezone(_td(hours=9))
    _now = datetime.now(_jst)
    _shiryou_cache_key = (_now - _td(days=1)).strftime("%Y-%m-%d") if _now.hour < 11 else _now.strftime("%Y-%m-%d")

    fetched = _load_shiryou(_shiryou_cache_key)
    shiryou_data = fetched.get("__shiryou__") if isinstance(fetched, dict) else None
    if not shiryou_data:
        st.warning("資料データの取得に失敗しました。")
        if isinstance(fetched, dict):
            for k, v in fetched.items():
                st.subheader(k)
                st.dataframe(v)
        st.stop()

    sheet2, sheet3 = shiryou_data[0], shiryou_data[1]

    def _esc(t):
        return (t or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    st.markdown("""
    <style>
    .sr-font, .sr-font * { font-family: 'メイリオ', Meiryo, 'Hiragino Sans', sans-serif !important; }
    .sr-title { background: linear-gradient(90deg, #D4850A, #e8a83e); color: #fff; padding: 12px 20px;
        font-weight: bold; font-size: 1.15rem; border-radius: 8px; margin: 24px 0 16px 0; }
    .sr-info { background: #fff8ed; border-left: 4px solid #D4850A; border-radius: 0 8px 8px 0;
        padding: 12px 16px; margin: 8px 0; color: #3a2a0a; white-space: pre-wrap; line-height: 1.7; font-size: 0.88rem; }
    .sr-flow { display: flex; flex-direction: column; align-items: center; gap: 0; margin: 16px 0; }
    .sr-step { background: #fff; border: 2px solid #D4850A; border-radius: 10px; padding: 10px 20px;
        min-width: 280px; max-width: 90%; text-align: center; font-weight: bold; font-size: 0.92rem;
        color: #2a1a00; position: relative; white-space: pre-wrap; line-height: 1.6; }
    .sr-step-num { display: inline-block; background: #D4850A; color: #fff; width: 26px; height: 26px;
        border-radius: 50%; text-align: center; line-height: 26px; font-size: 0.82rem; margin-right: 8px; font-weight: bold; }
    .sr-arrow { color: #D4850A; font-size: 1.4rem; line-height: 1; margin: 2px 0; }
    .sr-branch { display: flex; gap: 12px; margin: 16px 0; flex-wrap: wrap; justify-content: center; }
    .sr-branch-card { flex: 1; min-width: 220px; max-width: 360px; border-radius: 10px; overflow: hidden;
        box-shadow: 0 2px 8px rgba(0,0,0,0.10); }
    .sr-branch-hdr { padding: 8px 14px; font-weight: bold; font-size: 0.9rem; text-align: center; }
    .sr-branch-body { padding: 10px 14px; background: #fff; font-size: 0.82rem; line-height: 1.65;
        white-space: pre-wrap; color: #2a1a0a; }
    .sr-compare { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; margin: 12px 0; }
    @media (max-width: 768px) { .sr-compare { grid-template-columns: 1fr; } }
    .sr-cmp-card { border-radius: 10px; overflow: hidden; box-shadow: 0 2px 6px rgba(0,0,0,0.08); }
    .sr-cmp-hdr { padding: 8px 14px; font-weight: bold; font-size: 0.9rem; text-align: center; color: #fff; }
    .sr-cmp-hdr-ok { background: linear-gradient(90deg, #27ae60, #2ecc71); }
    .sr-cmp-hdr-ng { background: linear-gradient(90deg, #e67e22, #f39c12); }
    .sr-cmp-body { padding: 10px 14px; background: #fff; font-size: 0.82rem; line-height: 1.65;
        white-space: pre-wrap; color: #2a1a0a; }
    .sr-warn { background: #fef3e2; border: 1px solid #f0c36d; border-radius: 8px; padding: 10px 14px;
        margin: 8px 0; font-size: 0.85rem; color: #7a5a00; line-height: 1.6; white-space: pre-wrap; }
    .sr-warn::before { content: "\\26A0\\FE0F "; }
    .sr-knowledge { background: #eef6ff; border: 1px solid #a3c4e8; border-radius: 8px; padding: 12px 16px;
        margin: 8px 0; font-size: 0.85rem; color: #1a3a5a; line-height: 1.7; white-space: pre-wrap; }
    .sr-cat-hdr { padding: 10px 16px; border-radius: 8px; color: #fff; font-weight: bold;
        font-size: 1rem; margin: 12px 0 8px 0; }
    </style>
    """, unsafe_allow_html=True)

    # シート2: 基本手順
    st.markdown(f'<div class="sr-title sr-font">{_esc(sheet2["title"])}</div>', unsafe_allow_html=True)
    st.markdown(f'<div class="sr-info sr-font"><b>対応範囲:</b> {_esc(sheet2["scope"])}</div>', unsafe_allow_html=True)
    st.markdown(f'<div class="sr-info sr-font"><b>やること:</b> {_esc(sheet2["task"])}</div>', unsafe_allow_html=True)

    st.markdown('<div class="sr-title sr-font" style="font-size:1rem;">確認 → 架電 フロー</div>', unsafe_allow_html=True)
    flow_html = '<div class="sr-flow sr-font">'
    for i, step in enumerate(sheet2["confirm"]):
        flow_html += f'<div class="sr-step"><span class="sr-step-num">{i+1}</span>{_esc(step)}</div>'
        if i < len(sheet2["confirm"]) - 1:
            flow_html += '<div class="sr-arrow">▼</div>'
    flow_html += '</div>'
    st.markdown(flow_html, unsafe_allow_html=True)

    st.markdown('<div class="sr-title sr-font" style="font-size:1rem;">架電後の後処理</div>', unsafe_allow_html=True)
    st.markdown('<div style="text-align:center;font-size:1.3rem;color:#D4850A;margin:4px 0;">▼ 架電結果により分岐 ▼</div>', unsafe_allow_html=True)
    colors = [("#e67e22", "#fef5ed"), ("#d35400", "#fef0e5"), ("#27ae60", "#eafaf1")]
    branch_html = '<div class="sr-branch sr-font">'
    for idx, ac in enumerate(sheet2["after_call"]):
        c_main, c_bg = colors[idx]
        items_html = "\n".join(_esc(it) for it in ac["items"])
        branch_html += (
            f'<div class="sr-branch-card" style="border: 2px solid {c_main};">'
            f'<div class="sr-branch-hdr" style="background:{c_main};color:#fff;">{_esc(ac["label"])}</div>'
            f'<div class="sr-branch-body" style="background:{c_bg};">{items_html}</div>'
            f'</div>'
        )
    branch_html += '</div>'
    st.markdown(branch_html, unsafe_allow_html=True)

    st.markdown('<div class="sr-title sr-font" style="font-size:1rem;">折り返し対応</div>', unsafe_allow_html=True)
    compare_html = '<div class="sr-compare sr-font">'
    for i, cb in enumerate(sheet2["callback"]):
        hdr_cls = "sr-cmp-hdr-ng" if i == 0 else "sr-cmp-hdr-ok"
        items_html = "\n".join(_esc(it) for it in cb["items"])
        compare_html += (
            f'<div class="sr-cmp-card">'
            f'<div class="sr-cmp-hdr {hdr_cls}">{_esc(cb["label"])}</div>'
            f'<div class="sr-cmp-body">{items_html}</div>'
            f'</div>'
        )
    compare_html += '</div>'
    st.markdown(compare_html, unsafe_allow_html=True)

    # シート3: 不備対応手順
    st.markdown(f'<div class="sr-title sr-font">{_esc(sheet3["title"])}</div>', unsafe_allow_html=True)
    tabs = st.tabs([cat["name"] for cat in sheet3["categories"]] + ["豆知識"])
    for tab, cat in zip(tabs[:-1], sheet3["categories"]):
        with tab:
            c = cat["color"]
            st.markdown(
                f'<div class="sr-cat-hdr sr-font" style="background:{c};">{_esc(cat["name"])}</div>'
                f'<div class="sr-info sr-font">{_esc(cat["desc"])}</div>',
                unsafe_allow_html=True,
            )
            if cat.get("steps"):
                st.markdown(f'<div class="sr-cat-hdr sr-font" style="background:{c};font-size:0.92rem;">対応手順</div>', unsafe_allow_html=True)
                fl = '<div class="sr-flow sr-font">'
                for si, s in enumerate(cat["steps"]):
                    fl += f'<div class="sr-step" style="border-color:{c};text-align:left;max-width:100%;"><span class="sr-step-num" style="background:{c};">{si+1}</span>{_esc(s)}</div>'
                    if si < len(cat["steps"]) - 1:
                        fl += f'<div class="sr-arrow" style="color:{c};">▼</div>'
                fl += '</div>'
                st.markdown(fl, unsafe_allow_html=True)
            if cat.get("notes"):
                st.markdown(f'<div class="sr-warn sr-font">{chr(10).join(_esc(n) for n in cat["notes"])}</div>', unsafe_allow_html=True)
            if cat.get("complete") or cat.get("absent"):
                st.markdown(f'<div class="sr-cat-hdr sr-font" style="background:{c};font-size:0.92rem;">架電結果</div>', unsafe_allow_html=True)
                cmp = '<div class="sr-compare sr-font">'
                cmp += (
                    f'<div class="sr-cmp-card"><div class="sr-cmp-hdr sr-cmp-hdr-ok">完了</div>'
                    f'<div class="sr-cmp-body">{chr(10).join(_esc(x) for x in cat.get("complete", []))}</div></div>'
                    f'<div class="sr-cmp-card"><div class="sr-cmp-hdr sr-cmp-hdr-ng">留守</div>'
                    f'<div class="sr-cmp-body">{chr(10).join(_esc(x) for x in cat.get("absent", []))}</div></div>'
                )
                cmp += '</div>'
                st.markdown(cmp, unsafe_allow_html=True)
            if cat.get("flow"):
                st.markdown(f'<div class="sr-cat-hdr sr-font" style="background:{c};font-size:0.92rem;">全体の流れ</div>', unsafe_allow_html=True)
                fl2 = '<div class="sr-flow sr-font">'
                for fi, fs in enumerate(cat["flow"]):
                    fl2 += f'<div class="sr-step" style="border-color:{c};"><span class="sr-step-num" style="background:{c};">{fi+1}</span>{_esc(fs)}</div>'
                    if fi < len(cat["flow"]) - 1:
                        fl2 += f'<div class="sr-arrow" style="color:{c};">▼</div>'
                fl2 += '</div>'
                st.markdown(fl2, unsafe_allow_html=True)
    with tabs[-1]:
        knowledge = sheet3.get("knowledge", [])
        if knowledge:
            st.markdown(f'<div class="sr-knowledge sr-font">{chr(10).join(_esc(k) for k in knowledge)}</div>', unsafe_allow_html=True)
    st.stop()

# トークスクリプト（テスト）: 電話番号で顧客情報引き当て
# selected_key が "talk_script_NN" 形式（メンバー別の独立ボード）
if selected_key.startswith("talk_script_"):
    from talk_script_store import (
        lookup_customer,
        load_talk_script,
        detect_kind,
        normalize_phone,
        clear_caches,
        resolve_lookup_sheet,
    )

    # メンバー別ユニーク接尾辞 → session_state を独立化
    _board_id = selected_key  # 例: talk_script_00_fc1week
    _parsed = parse_talk_script_key(selected_key)
    _member_name = _parsed[0] if _parsed else ""
    _board_label = _parsed[1] if _parsed else metric.label

    # ボードsuffixからlookup先ワークシートを解決（1週間後FC / 代コン不備 など）
    _key_parts = selected_key.split("_", 3)
    _board_suffix = _key_parts[3] if len(_key_parts) >= 4 else ""
    _lookup_sheet = resolve_lookup_sheet(_board_suffix)

    st.caption(f"電話番号を貼り付けると顧客情報を引き当て、商材に応じたトークスクリプトを表示します。（{_member_name} 専用ボード）")

    col_in, col_btn = st.columns([4, 1])
    with col_in:
        phone_input = st.text_input(
            "電話番号",
            placeholder="例: 080-4200-2238 / 08042002238",
            key=f"talk_phone_{_board_id}",
            label_visibility="collapsed",
        )
    with col_btn:
        if st.button("🔄 データ更新", key=f"talk_refresh_{_board_id}", use_container_width=True):
            clear_caches()
            st.rerun()

    phone_clean = normalize_phone(phone_input)

    if not phone_clean:
        st.info("電話番号を入力してください。")
        st.stop()

    info = lookup_customer(phone_clean, _lookup_sheet)
    if info is None:
        st.warning(f"電話番号 `{phone_clean}` に該当する顧客情報が見つかりません。")
        st.stop()

    # --- 商流変更アラート（直前に表示した商流と違えば警告） ---
    _current_shoryu = (info.get("商流（引用）") or "").strip()
    _prev_shoryu = st.session_state.get("_last_shoryu", "")
    if _current_shoryu and _prev_shoryu and _current_shoryu != _prev_shoryu:
        st.markdown(
            f'<div style="background:linear-gradient(90deg,#FF6B6B,#FF8E53);'
            f'color:#fff;padding:14px 20px;border-radius:10px;margin:10px 0 14px 0;'
            f'box-shadow:0 3px 10px rgba(255,107,107,0.4);font-weight:700;font-size:1.05rem;'
            f'border:2px solid #fff;">'
            f'📞 商流が変わったのでZOOM Phoneの発信番号の変更をお願いします。'
            f'<div style="font-size:0.85rem;font-weight:500;margin-top:4px;opacity:0.95;">'
            f'前回: {_prev_shoryu} → 今回: {_current_shoryu}</div>'
            f'</div>',
            unsafe_allow_html=True,
        )
        st.toast(
            f"⚠ 商流変更：{_prev_shoryu} → {_current_shoryu} ／ ZOOM Phone発信番号の変更をお願いします",
            icon="📞",
        )
    if _current_shoryu:
        st.session_state["_last_shoryu"] = _current_shoryu

    # --- 顧客情報カード ---
    def _v(key: str) -> str:
        v = info.get(key, "")
        return str(v) if v not in (None, "") else "—"

    shozai = _v("取次商材情報")
    kind = detect_kind(shozai)
    kind_label = "NURO光" if kind == "NURO" else "So-net光"
    kind_color = "#7B1FA2" if kind == "NURO" else "#1976D2"

    kessai_raw = info.get("決済登録日（引用）", "")
    kessai_status = "✅ 登録済み" if kessai_raw not in (None, "") else "❌ 未登録"

    # 年齢: 小数点以下切捨て
    _age_raw = info.get("年齢", "")
    try:
        _age_display = str(int(float(_age_raw))) if _age_raw not in (None, "") else "—"
    except (ValueError, TypeError):
        _age_display = str(_age_raw) if _age_raw not in (None, "") else "—"

    # 前確OKコメントから案内料金 / CB案内を抽出（Account ID単位で5分キャッシュ）
    _account_id = (info.get("取引先 ID") or "").strip()
    _zk = {"description": "", "activity_date": "", "found": False}
    _ryokin_display = "—"
    _cb_display = "—"
    if _account_id:
        try:
            from zenkaku_store import get_zenkaku_ok_comment
            _zk = get_zenkaku_ok_comment(_sf(), _account_id)
        except Exception:
            pass
        if _zk["found"]:
            import re as _re
            _desc_raw = _zk["description"]
            _m_r = _re.search(r"案内料金[：:]\s*([0-9,]+\s*円)", _desc_raw)
            if _m_r:
                _ryokin_display = _m_r.group(1).replace(" ", "")
            _m_cb = _re.search(r"CB案内[：:]\s*([^\r\n]+)", _desc_raw)
            if _m_cb:
                _cb_display = _m_cb.group(1).strip()

    st.markdown(
        f"""
        <div style="
            background: rgba(255,255,255,0.85);
            border-left: 6px solid {kind_color};
            border-radius: 8px;
            padding: 16px 20px;
            margin: 12px 0 20px 0;
            box-shadow: 0 2px 8px rgba(0,0,0,0.08);
        ">
        <div style="font-size:1.1rem;font-weight:700;color:{kind_color};margin-bottom:8px;">
            {kind_label}　|　{_v("申込者氏名")}（{_v("申込者氏名（フリガナ）")}）
        </div>
        <table style="width:100%;border-collapse:collapse;font-size:0.95rem;color:#222;">
            <tr>
                <td style="padding:4px 8px;width:25%;color:#666;">エントリ日</td>
                <td style="padding:4px 8px;font-weight:600;">{_v("案件進捗管理: エントリ日")}</td>
                <td style="padding:4px 8px;width:25%;color:#666;">工事予定日</td>
                <td style="padding:4px 8px;font-weight:600;">{_v("工事予定日（引用）")}</td>
            </tr>
            <tr>
                <td style="padding:4px 8px;color:#666;">開通日</td>
                <td style="padding:4px 8px;font-weight:600;">{_v("開通日（引用）")}</td>
                <td style="padding:4px 8px;color:#666;">ST大区分</td>
                <td style="padding:4px 8px;font-weight:600;">{_v("status大区分（引用）")}</td>
            </tr>
            <tr>
                <td style="padding:4px 8px;color:#666;">利用回線</td>
                <td style="padding:4px 8px;font-weight:600;">{_v("利用回線")}</td>
                <td style="padding:4px 8px;color:#666;">LINE登録(突合)</td>
                <td style="padding:4px 8px;font-weight:600;">{_v("【Lｽﾃｯﾌﾟ】突合完了日（引用）")}</td>
            </tr>
            <tr>
                <td style="padding:4px 8px;color:#666;">決済登録</td>
                <td style="padding:4px 8px;font-weight:600;">{kessai_status}</td>
                <td style="padding:4px 8px;color:#666;">取次商材</td>
                <td style="padding:4px 8px;font-weight:600;">{shozai}</td>
            </tr>
            <tr>
                <td style="padding:4px 8px;color:#666;">年齢</td>
                <td style="padding:4px 8px;font-weight:600;">{_age_display}</td>
                <td style="padding:4px 8px;color:#666;">利用携帯＆台数</td>
                <td style="padding:4px 8px;font-weight:600;">{_v("利用携帯＆利用台数")}</td>
            </tr>
            <tr>
                <td style="padding:4px 8px;color:#666;">商流</td>
                <td style="padding:4px 8px;font-weight:600;">{_v("商流（引用）")}</td>
                <td style="padding:4px 8px;color:#666;">エリア</td>
                <td style="padding:4px 8px;font-weight:600;">{_v("エリア（東西）")}</td>
            </tr>
            <tr>
                <td style="padding:4px 8px;color:#666;">郵便番号</td>
                <td style="padding:4px 8px;font-weight:600;">{_v("郵便番号(設置先)")}</td>
                <td style="padding:4px 8px;color:#666;">ダイコンST</td>
                <td style="padding:4px 8px;font-weight:600;">{_v("ダイコンステータス")}</td>
            </tr>
            <tr>
                <td style="padding:4px 8px;color:#666;">住所</td>
                <td colspan="3" style="padding:4px 8px;font-weight:600;">{_v("住所結合")}</td>
            </tr>
            <tr>
                <td style="padding:4px 8px;color:#666;">案内料金</td>
                <td style="padding:4px 8px;font-weight:600;">{_ryokin_display}</td>
                <td style="padding:4px 8px;color:#666;">CB案内</td>
                <td style="padding:4px 8px;font-weight:600;">{_cb_display}</td>
            </tr>
        </table>
        </div>
        """,
        unsafe_allow_html=True,
    )

    # 促進用トーク（代コン不備）：ダイコンステータス別の補足カードを表示
    if _board_suffix == "sokushin":
        from talk_template_store import select_sokushin_key as _select_sokushin_key_card
        _daikon_for_card = (info.get("ダイコンステータス") or "").strip()
        _sokushin_key_for_card = _select_sokushin_key_card(_daikon_for_card)
        _supplement_fields: list[tuple[str, str]] = []
        if _sokushin_key_for_card == "工事取得3者間":
            _supplement_fields = [
                ("工事予定日", "工事予定日（引用）"),
                ("工事Ⅰ状況", "工事Ⅰ状況（引用）"),
                ("申込時工事取得状況", "申込時工事取得状況"),
                ("初回取次(API取得工事日)", "初回取次(API取得工事日)"),
                ("工事取得FC回数", "工事取得FC回数"),
                ("API取次対象", "API取次対象"),
                ("代理店コンサル希望", "代理店コンサル希望"),
            ]
        elif _sokushin_key_for_card == "番ポ不備FC":
            _supplement_fields = [
                ("固定申込", "固定申込"),
                ("固定電話1", "固定電話1（引用）"),
                ("おでん案内フラグ", "おでん案内フラグ"),
                ("開通後ホーム電話案内", "開通後ホーム電話案内"),
            ]
        if _supplement_fields:
            _supp_rows = "".join(
                f'<tr><td style="padding:4px 8px;width:32%;color:#666;">{lbl}</td>'
                f'<td style="padding:4px 8px;font-weight:600;">{_v(col)}</td></tr>'
                for lbl, col in _supplement_fields
            )
            st.markdown(
                f'<div style="background:rgba(255,255,255,0.85);border-left:6px solid #2E8B57;'
                f'border-radius:8px;padding:14px 20px;margin:8px 0 16px 0;'
                f'box-shadow:0 2px 8px rgba(0,0,0,0.08);">'
                f'<div style="font-size:1.0rem;font-weight:700;color:#2E8B57;margin-bottom:6px;">'
                f'🎯 促進用 補足情報（{_sokushin_key_for_card}）</div>'
                f'<table style="width:100%;border-collapse:collapse;font-size:0.92rem;color:#222;">'
                f'{_supp_rows}</table></div>',
                unsafe_allow_html=True,
            )

    # --- 前確OKコメント全文（折りたたみ） ---
    if _zk["found"]:
        with st.expander(f"📋 前確OKコメント全文（{_zk['activity_date']}）", expanded=False):
            st.code(_zk["description"], language=None)

    # 促進用トーク（代コン不備）：ダイコンステータスに応じて5種テンプレから選択表示
    if _board_suffix == "sokushin":
        import html as _html_sk
        from talk_template_store import (
            get_templates as _get_tpl_sk,
            select_sokushin_key,
        )
        from nanori_master_store import apply_nanori_substitution as _apply_nanori_sk
        from replace_master_store import apply_replace_substitution as _apply_replace_sk

        _daikon_val = (info.get("ダイコンステータス") or "").strip()
        _sokushin_key = select_sokushin_key(_daikon_val)

        if not _sokushin_key:
            st.warning(
                f"ダイコンステータス「{_daikon_val or '(空)'}」は促進用トークの対応外です。"
                "対応値: 工事日調整希望 / API工事取得 / 番ポ不備 / 住所確認 / 現地調査必要 / 有派遣へ変更必要"
            )
            st.stop()

        _sokushin_tpl = _get_tpl_sk().get("Sonet_sokushin", {})
        _body_sk = _sokushin_tpl.get(_sokushin_key, "")

        st.subheader(f"🎯 促進用トーク　|　{_sokushin_key}")
        st.caption(f"ダイコンステータス: **{_daikon_val}** → テンプレ: **{_sokushin_key}**")

        if not _body_sk:
            st.info(f"「{_sokushin_key}」のテンプレートが未入力です。マスタ画面の「🎯 促進用」タブから編集してください。")
            st.stop()

        # 商流別名乗り・汎用置換を適用
        _body_sk = _apply_nanori_sk(_body_sk, info)
        _body_sk = _apply_replace_sk(_body_sk)

        _safe_sk = _html_sk.escape(_body_sk).replace("\n", "<br>").replace(" ", "&nbsp;")
        st.markdown(
            f'<div style="background:rgba(255,255,255,0.85);border-left:6px solid #2E8B57;'
            f'border-radius:6px;padding:14px 20px;font-size:0.95rem;line-height:1.7;color:#1a1a1a;'
            f'box-shadow:0 1px 4px rgba(0,0,0,0.06);white-space:pre-wrap;">{_safe_sk}</div>',
            unsafe_allow_html=True,
        )
        st.stop()

    # --- LINEテンプレ（折りたたみ） ---
    import html as _html
    from talk_template_store import get_templates as _get_tpl_for_line, LINE_TEMPLATE_KEYS
    _all_templates_for_line = _get_tpl_for_line()
    _line_store_key = "Sonet_line" if kind == "Sonet" else "NURO_line"
    line_templates = _all_templates_for_line.get(_line_store_key, {})
    if any(line_templates.values()):
        with st.expander("💬 LINEテンプレ", expanded=False):
            line_tabs = st.tabs(LINE_TEMPLATE_KEYS)
            from replace_master_store import apply_replace_substitution as _apply_replace_line
            from nanori_master_store import apply_nanori_substitution as _apply_nanori_line
            for tab, lkey in zip(line_tabs, LINE_TEMPLATE_KEYS):
                with tab:
                    body = line_templates.get(lkey, "")
                    if not body:
                        st.info("（テンプレなし）")
                        continue
                    # 名乗り＋置換表を適用（トーク本文と同じ扱い）
                    body = _apply_nanori_line(body, info)
                    body = _apply_replace_line(body)
                    safe = _html.escape(body).replace("\n", "<br>").replace(" ", "&nbsp;")
                    st.markdown(
                        f'<div style="background:rgba(255,255,255,0.9);border-left:4px solid #06C755;'
                        f'border-radius:6px;padding:14px 20px;font-size:0.92rem;line-height:1.7;'
                        f'color:#1a1a1a;box-shadow:0 1px 4px rgba(0,0,0,0.06);white-space:pre-wrap;">'
                        f'{safe}</div>',
                        unsafe_allow_html=True,
                    )

    # --- トークスクリプト本文（セクション別テンプレ + 動的処理） ---
    from talk_template_store import (
        get_templates,
        get_sections,
        get_section_rule,
        evaluate_section_rule,
        select_fubi_key,
        apply_dynamic_processing,
    )

    st.subheader(f"📞 トークスクリプト（{kind_label}）")
    templates = get_templates()
    sections = get_sections(kind)
    kind_templates = templates.get(kind, {})

    # Sonetの場合、不備解消セクションは9種テンプレから動的選択
    fubi_key_selected = None
    closing_key_selected = None
    if kind == "Sonet":
        fubi_key_selected = select_fubi_key(
            info.get("ダイコンステータス", ""),
            info.get("工事予定日（引用）", ""),
        )
        # 締め: 利用回線の有無で2種から選択
        _kaisen_val = (info.get("利用回線") or "").strip()
        closing_key_selected = "利用回線あり" if (_kaisen_val and _kaisen_val != "不明") else "利用回線不明"

    def _render_section_body(body: str) -> str:
        """セクション本文を行単位でHTML化（見出し/注釈をスタイリング）。"""
        if not body:
            return '<div style="color:#999;font-style:italic;">（空のセクション）</div>'
        out = []
        for raw in body.split("\n"):
            text = raw.rstrip()
            if not text.strip():
                out.append('<div style="height:8px;"></div>')
                continue
            safe = _html.escape(text).replace(" ", "&nbsp;")
            stripped = text.strip()
            if stripped.startswith("■") or stripped.startswith("★") or stripped.startswith("・"):
                out.append(
                    f'<div style="font-weight:700;color:{kind_color};margin:6px 0 2px 0;">{safe}</div>'
                )
            elif stripped.startswith("※") or stripped.startswith("→"):
                out.append(
                    f'<div style="color:#888;font-size:0.85rem;margin-left:8px;">{safe}</div>'
                )
            else:
                out.append(f'<div>{safe}</div>')
        return "".join(out)

    for sec_name in sections:
        # マスタで設定した引用情報ベースの表示ルールを評価
        _rule = get_section_rule(kind, sec_name)
        if not evaluate_section_rule(_rule, info):
            continue

        # 不備解消セクションは動的に9種から選択（Sonetのみ）
        if sec_name == "不備解消" and kind == "Sonet":
            fubi_templates = templates.get("Sonet_fubi", {})
            body = fubi_templates.get(fubi_key_selected, "")
            section_label = f"【不備解消】　🎯 {fubi_key_selected}"
        # 締めセクションは利用回線の有無で2種から選択（Sonetのみ）
        elif sec_name == "締め" and kind == "Sonet":
            closing_templates = templates.get("Sonet_closing", {})
            body = closing_templates.get(closing_key_selected, "")
            section_label = f"【締め】　🎯 {closing_key_selected}"
        else:
            body = kind_templates.get(sec_name, "")
            section_label = f"【{sec_name}】"

        # Sonet の動的処理を適用
        if kind == "Sonet":
            body = apply_dynamic_processing(body, info)

        # 商流別名乗りの差し込み（{{名乗}} → 取次商材情報＋商流で解決）
        from nanori_master_store import apply_nanori_substitution as _apply_nanori
        body = _apply_nanori(body, info)

        # 汎用置換表の適用（条件なし一律置換）
        from replace_master_store import apply_replace_substitution as _apply_replace
        body = _apply_replace(body)

        st.markdown(
            f'<div style="background:{kind_color};color:#fff;padding:8px 14px;'
            f'border-radius:6px;font-weight:700;margin:18px 0 6px 0;font-size:1.05rem;">'
            f'{section_label}</div>',
            unsafe_allow_html=True,
        )
        st.markdown(
            f'<div style="background:rgba(255,255,255,0.85);border-radius:6px;'
            f'padding:14px 20px;font-size:0.95rem;line-height:1.7;color:#1a1a1a;'
            f'box-shadow:0 1px 4px rgba(0,0,0,0.06);">{_render_section_body(body)}</div>',
            unsafe_allow_html=True,
        )

    st.stop()

