import streamlit as st
import requests
import urllib3
from urllib3.exceptions import InsecureRequestWarning
from collections import defaultdict
import concurrent.futures
import pandas as pd
import io
import datetime

urllib3.disable_warnings(InsecureRequestWarning)
st.set_page_config(page_title="Otomasyon Dashboard", page_icon="🚀", layout="wide")

JIRA_URL = "https://jira.turkcell.com.tr"
CUSTOM_FIELD = "Automated"
TARGET_VALUES = ["Android-Automated", "IOS-Automated", "Half", "Yes"]

PROJECTS = {
    "BiP": "QA471990, QABIPBEL, QABR, QA252282, QA471299",
    "fizy": "QF284050, QB284050, QM284050",
    "Game+": "QA-GAME-WEB, QA-GAME-BACKEND",
    "lifebox": "QW259876, QB259876, QM259876",
    "Sardis": "QASARDIS",
    "TV+": "QATR, QATRT, QATMA, QAMRB, QBTBB",
    "Upcall": "QACALL"
}
PROJECTS["Tümü (Bütün Ürünler)"] = ", ".join(PROJECTS.values())

def get_auth_headers(token, cookie_string):
    """
    Sadece Cookie kullanarak bağlanıyoruz. 
    PAT (token) bazen F5 engeline takıldığı için devre dışı bıraktık.
    """
    return {
        "Cookie": cookie_string,
        "Accept": "application/json",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    }

def get_issue_keys(headers, jql, start_date, end_date):
    full_jql = f"({jql}) AND issuetype = Test AND updated >= '{start_date}' AND updated <= '{end_date} 23:59'"
    url = f"{JIRA_URL.rstrip('/')}/rest/api/2/search"
    start_at, max_results, issue_keys = 0, 100, []
    
    while True:
        params = {"jql": full_jql, "startAt": start_at, "maxResults": max_results, "fields": "key"}
        try:
            r = requests.get(url, headers=headers, params=params, verify=False, timeout=30)
            if r.status_code == 401:
                st.error("🚨 Token Hatası: PAT anahtarınız geçersiz.")
                st.stop()
            r.raise_for_status()
            data = r.json()
        except Exception:
            st.error("🚨 Bağlantı/Güvenlik Hatası: Cookie süresi dolmuş veya VPN kapalı olabilir.")
            with st.expander("Detaylı Yanıt"):
                st.code(r.text[:1000], language="html")
            st.stop()
            
        issues = data.get("issues", [])
        if not issues: break
        issue_keys.extend([i["key"] for i in issues])
        if len(issues) < max_results: break
        start_at += max_results
    return issue_keys

def check_who_automated(key, headers, start_date, end_date):
    url = f"{JIRA_URL.rstrip('/')}/rest/api/2/issue/{key}?expand=changelog"
    try:
        r = requests.get(url, headers=headers, verify=False, timeout=20)
        if r.status_code == 200:
            hists = r.json().get("changelog", {}).get("histories", [])
            for h in hists:
                dt_str = h.get("created", "")[:10]
                if start_date <= dt_str <= end_date:
                    for item in h.get("items", []):
                        if str(item.get("field", "")).lower() == CUSTOM_FIELD.lower():
                            if str(item.get("toString", "")).strip() in TARGET_VALUES:
                                return {"Senaryo": key, "Yazar": h.get("author", {}).get("displayName", "Bilinmiyor"), "Statü": item.get("toString"), "Tarih": dt_str}
    except: pass
    return None

def generate_excel(df_s, df_d):
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df_s.to_excel(writer, sheet_name="Ozet", index=False)
        df_d.to_excel(writer, sheet_name="Detay", index=False)
    return output.getvalue()

st.title("🚀 QA Otomasyon Dashboard")
with st.sidebar:
    st.header("⚙️ Kriterler")
    user_cookie = st.text_input("Jira Cookie (Metni Yapıştır)", type="password")
    selected_product = st.selectbox("📦 Ürün", options=list(PROJECTS.keys()), index=1)
    col1, col2 = st.columns(2)
    with col1: start_d = st.date_input("Başlangıç", datetime.date(2026, 1, 1))
    with col2: end_d = st.date_input("Bitiş", datetime.date.today())
    run_btn = st.button("📊 Raporu Oluştur", type="primary", use_container_width=True)

if run_btn:
    if not user_cookie:
        st.warning("Lütfen Cookie yapıştırın.")
        st.stop()
    
    pat = st.secrets["JIRA_TOKEN"]
    headers = get_auth_headers(pat, user_cookie)
    start_date_str, end_date_str = start_d.strftime("%Y-%m-%d"), end_d.strftime("%Y-%m-%d")
    project_jql = f"project in ({PROJECTS[selected_product]})"

    with st.spinner("Veriler çekiliyor..."):
        issue_keys = get_issue_keys(headers, project_jql, start_date_str, end_date_str)
    
    if not issue_keys:
        st.warning("Kayıt bulunamadı.")
        st.stop()

    author_stats, records = defaultdict(int), []
    progress = st.progress(0)
    with concurrent.futures.ThreadPoolExecutor(max_workers=15) as ex:
        futures = {ex.submit(check_who_automated, k, headers, start_date_str, end_date_str): k for k in issue_keys}
        for i, f in enumerate(concurrent.futures.as_completed(futures)):
            progress.progress((i + 1) / len(issue_keys))
            res = f.result()
            if res:
                author_stats[res["Yazar"]] += 1
                records.append(res)
    
    if not records:
        st.info("Bu tarih aralığında otomatize edilen senaryo yok.")
    else:
        df_summary = pd.DataFrame(sorted(author_stats.items(), key=lambda x: x[1], reverse=True), columns=["Kişi", "Sayı"])
        st.success("Hazır!")
        st.metric("Toplam Otomasyon", len(records))
        st.bar_chart(df_summary.set_index("Kişi"))
        st.dataframe(pd.DataFrame(records), use_container_width=True, hide_index=True)
        st.download_button("📥 Excel İndir", generate_excel(df_summary, pd.DataFrame(records)), "Rapor.xlsx")
