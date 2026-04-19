"""
Salesforce クライアント（読み取り専用）

方針:
- Salesforce のデータは絶対に書き換えない
- query / describe など参照系のみ使用する
- create / update / upsert / delete は呼び出さない
"""

import os
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass
from simple_salesforce import Salesforce


def _load_creds() -> dict:
    """Streamlit secrets → 環境変数 の順に資格情報を読み込む。"""
    try:
        import streamlit as st  # type: ignore
        if hasattr(st, "secrets") and "SF_USERNAME" in st.secrets:
            return {
                "username": st.secrets["SF_USERNAME"],
                "password": st.secrets["SF_PASSWORD"],
                "security_token": st.secrets["SF_TOKEN"],
                "domain": st.secrets.get("SF_DOMAIN", "login"),
            }
    except Exception:
        pass
    return {
        "username": os.environ["SF_USERNAME"],
        "password": os.environ["SF_PASSWORD"],
        "security_token": os.environ["SF_TOKEN"],
        "domain": os.environ.get("SF_DOMAIN", "login"),
    }


def get_sf() -> Salesforce:
    """Salesforce 接続を作成して返す。"""
    return Salesforce(**_load_creds())


if __name__ == "__main__":
    sf = get_sf()
    result = sf.query("SELECT Id, Name FROM Account LIMIT 5")
    for r in result["records"]:
        print(r["Id"], r["Name"])
