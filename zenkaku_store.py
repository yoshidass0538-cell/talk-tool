"""
前確OKコメント引用ストア。

指定 Account ID に紐づく Salesforce Task の中から
- Field4_del__c = '前確OK'
- Description が空でない
を満たすレコードを取得し、Description が最長のものを返す。

キャッシュ: account_id 単位で 5分。
"""

from __future__ import annotations

import streamlit as st


@st.cache_data(ttl=300, show_spinner=False)
def get_zenkaku_ok_comment(_sf, account_id: str) -> dict:
    """
    指定顧客の前確OK Description（最長）を返す。

    Returns:
      {"description": str, "activity_date": str, "found": bool}
    """
    if not account_id:
        return {"description": "", "activity_date": "", "found": False}
    try:
        soql = (
            "SELECT Description, ActivityDate "
            "FROM Task "
            f"WHERE WhatId = '{account_id}' "
            "AND Field4_del__c = '前確OK' "
            "ORDER BY ActivityDate DESC"
        )
        res = _sf.query_all(soql)
    except Exception:
        return {"description": "", "activity_date": "", "found": False}

    best_desc = ""
    best_date = ""
    for r in res.get("records", []):
        desc = (r.get("Description") or "").strip()
        if len(desc) > len(best_desc):
            best_desc = desc
            best_date = r.get("ActivityDate") or ""

    return {
        "description": best_desc,
        "activity_date": best_date,
        "found": bool(best_desc),
    }
