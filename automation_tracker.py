import streamlit as st
import requests
import urllib3
from urllib3.exceptions import InsecureRequestWarning
from collections import defaultdict
import concurrent.futures
import pandas as pd
import io
import datetime

# SSL Uyarılarını gizle
urllib3.disable_warnings(InsecureRequestWarning)

# Sayfa Ayarları
st.set_page_config(page_title="Otomasyon Dashboard", page_icon="🚀", layout="wide")

# =============================================================================
# SABİT AYARLAR
# =============================================================================
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

# =============================================================================
# FONKSİYONLAR
# =============================================================================
def get_auth_headers(token, cookie_string):
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Cookie": cookie_string,
        # Bu satır, Python'u gerçek bir tarayıcı gibi gösterir:
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
                st.error("🚨 Yetkilendirme Hatası: Streamlit Secrets içindeki JIRA_TOKEN geçersiz.")
                st.stop()
            r.raise_for_status()
            data = r.json()
        except Exception:
            st.error("🚨 Güvenlik Engeli: Girdiğiniz Cookie hatalı veya süresi dolmuş olabilir.")
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
            histories = r.json().get("changelog", {}).get("histories", [])
            for h in histories:
                created_date = h.get("created", "")[:10]
                if start_date <= created_date <= end_date:
                    for item in h.get("items", []):
                        if str(item.get("field", "")).lower() == CUSTOM_FIELD.lower():
                            if str(item.get("toString", "")).strip() in TARGET_VALUES:
                                return {
                                    "key": key, 
                                    "author": h.get("author", {}).get("displayName", "Bilinmiyor"), 
                                    "status": item.get("toString"), 
                                    "date": created_date
                                }
    except: pass
    return None

def generate_excel(df_s, df_d):
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df_s.to_excel(writer, sheet_name="Özet", index=False)
        df_d.to_excel(writer, sheet_name="Detay", index=False)
    return output.getvalue()

# =============================================================================
# ARAYÜZ (UI)
# =============================================================================
st.title("🚀 QA Otomasyon Performans Dashboard")

with st.sidebar:
    st.header("⚙️ Ayarlar")
    
    # Arayüzde sadece Cookie alanı kaldı
    user_cookie = st.text_input("Jira Cookie (Bileti Yapıştır) 🎫", type="password", help="Chrome -> F12 -> Network -> Request Headers -> Cookie kısmındaki metin")
    
    selected_product = st.selectbox("📦 Ürün Seçin", options=list(PROJECTS.keys()), index=1)
    
    st.markdown("📅 **Tarih Aralığı**")
    col1, col2 = st.columns(2)
    with col1: start_d = st.date_input("Başlangıç", datetime.date(2026, 1, 1))
    with col2: end_d = st.date_input("Bitiş", datetime.date.today())
        
    run_btn = st.button("📊 Dashboard'u Oluştur", type="primary", use_container_width=True)

# =============================================================================
# ÇALIŞTIRMA
# =============================================================================
if run_btn:
    if not user_cookie:
        st.warning("⚠️ Devam etmek için lütfen Jira Cookie değerini yapıştırın.")
        st.stop()

    # PAT bilgisini Secrets'tan çekiyoruz
    try:
        jira_pat = st.secrets["JIRA_TOKEN"]
    except KeyError:
        st.error("⚠️ HATA: Streamlit Secrets içinde 'JIRA_TOKEN' bulunamadı.")
        st.stop()

    headers = get_auth_headers(jira_pat, user_cookie)
    start_date_str, end_date_str = start_d.strftime("%Y-%m-%d"), end_d.strftime("%Y-%m-%d")
    project_jql = f"project in ({PROJECTS[selected_product]})"

    with st.spinner("Jira taranıyor..."):
        issue_keys = get_issue_keys(headers, project_jql, start_date_str, end_date_str)
    
    if not issue_keys:
        st.warning("Senaryo bulunamadı.")
        st.stop()

    author_stats, detailed_records = defaultdict(int), []
    progress_bar = st.progress(0)
    
    with concurrent.futures.ThreadPoolExecutor(max_workers=15) as executor:
        futures = {executor.submit(check_who_automated, k, headers, start_date_str, end_date_str): k for k in issue_keys}
        for i, f in enumerate(concurrent.futures.as_completed(futures)):
            progress_bar.progress((i + 1) / len(issue_keys))
            res = f.result()
            if res:
                author_stats[res["author"]] += 1
                detailed_records.append(res)
    
    if not detailed_records:
        st.error("Seçilen tarihlerde otomasyona çekilen iş bulunamadı.")
        st.stop()

    df_summary = pd.DataFrame(sorted(author_stats.items(), key=lambda x: x[1], reverse=True), columns=["Kişi", "Sayı"])
    df_details = pd.DataFrame(detailed_records)

    st.success("Analiz Tamamlandı!")
    
    c1, c2 = st.columns(2)
    c1.metric("Toplam Otomatize", sum(author_stats.values()))
    c2.metric("Yazan Kişi Sayısı", len(author_stats))

    st.bar_chart(df_summary.set_index("Kişi"))
    st.subheader("📝 Detaylı Kayıtlar")
    st.dataframe(df_details, use_container_width=True, hide_index=True)
    
    st.download_button("📥 Excel Olarak İndir", generate_excel(df_summary, df_details), f"{selected_product}_Rapor.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
