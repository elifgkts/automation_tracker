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

def get_auth_headers(token):
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }

def get_issue_keys(jira_url, headers, jql, start_date, end_date):
    full_jql = f"({jql}) AND issuetype = Test AND updated >= '{start_date}' AND updated <= '{end_date} 23:59'"
    url = f"{jira_url.rstrip('/')}/rest/api/2/search"
    
    start_at = 0
    max_results = 100
    issue_keys = []
    
    while True:
        params = {"jql": full_jql, "startAt": start_at, "maxResults": max_results, "fields": "key"}
        r = requests.get(url, headers=headers, params=params, verify=False, timeout=30)
        
        if r.status_code == 401:
            st.error("HATA (401): Yetkilendirme reddedildi. Lütfen Token'ınızı kontrol edin.")
            st.stop()
            
        r.raise_for_status()
        data = r.json()
        issues = data.get("issues", [])
        if not issues:
            break
            
        issue_keys.extend([issue["key"] for issue in issues])
        
        if len(issues) < max_results:
            break
        start_at += max_results
        
    return issue_keys

def check_who_automated(key, jira_url, headers, start_date, end_date, custom_field_name, target_values):
    url = f"{jira_url.rstrip('/')}/rest/api/2/issue/{key}?expand=changelog"
    try:
        r = requests.get(url, headers=headers, verify=False, timeout=30)
        if r.status_code == 200:
            changelog = r.json().get("changelog", {}).get("histories", [])
            for history in changelog:
                hist_date = history.get("created", "")[:10]
                if start_date <= hist_date <= end_date:
                    for item in history.get("items", []):
                        field_name = str(item.get("field", "")).lower()
                        if field_name == custom_field_name.lower():
                            to_val = str(item.get("toString", "")).strip()
                            if to_val in target_values:
                                author = history.get("author", {}).get("displayName", "Bilinmeyen Kullanıcı")
                                return {"key": key, "author": author, "status": to_val, "date": hist_date}
    except Exception:
        pass
    return None

def generate_excel(df_summary, df_details):
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df_summary.to_excel(writer, sheet_name="Özet Puan Durumu", index=False)
        df_details.to_excel(writer, sheet_name="Senaryo Detayları", index=False)
    return output.getvalue()

# =============================================================================
# STREAMLIT ARAYÜZ (UI)
# =============================================================================

st.title("🚀 Jira Otomasyon Performans Dashboard")
st.markdown("Takımınızın otomasyona geçirdiği senaryoları kişi bazlı olarak takip edin.")

with st.sidebar:
    st.header("⚙️ Ayarlar")
    
    jira_url = st.text_input("Jira URL", value="https://jira.turkcell.com.tr")
    jira_token = st.text_input("Jira Token (PAT)", type="password", help="Jira profilinizden aldığınız Bearer Token")
    
    st.markdown("---")
    project_jql = st.text_area("JQL Sorgusu", value="project in (QF284050, QM284050, QB284050)")
    
    col1, col2 = st.columns(2)
    with col1:
        start_d = st.date_input("Başlangıç", datetime.date(2025, 1, 1))
    with col2:
        end_d = st.date_input("Bitiş", datetime.date.today())
        
    custom_field = st.text_input("Otomasyon Alanı Adı", value="Automated")
    target_vals_str = st.text_area("Hedef Değerler (Virgülle ayırın)", value="Android-Automated, IOS-Automated, Half, Yes")
    
    run_btn = st.button("📊 Raporu Oluştur", type="primary", use_container_width=True)

# =============================================================================
# ANA ÇALIŞMA MANTIĞI
# =============================================================================

if run_btn:
    if not jira_token:
        st.warning("⚠️ Lütfen devam etmeden önce Jira Token'ınızı girin.")
        st.stop()

    start_date_str = start_d.strftime("%Y-%m-%d")
    end_date_str = end_d.strftime("%Y-%m-%d")
    target_values = [x.strip() for x in target_vals_str.split(",")]
    headers = get_auth_headers(jira_token)

    with st.spinner("Jira'da senaryolar aranıyor..."):
        issue_keys = get_issue_keys(jira_url, headers, project_jql, start_date_str, end_date_str)
    
    if not issue_keys:
        st.info("İlgili tarih aralığında güncellenmiş Test senaryosu bulunamadı.")
        st.stop()

    author_stats = defaultdict(int)
    detailed_records = []
    total_issues = len(issue_keys)
    
    progress_bar = st.progress(0)
    status_text = st.empty()
    completed = 0
    
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        future_to_key = {
            executor.submit(check_who_automated, key, jira_url, headers, start_date_str, end_date_str, custom_field, target_values): key 
            for key in issue_keys
        }
        
        for future in concurrent.futures.as_completed(future_to_key):
            completed += 1
            progress_bar.progress(completed / total_issues)
            status_text.text(f"Geçmiş taranıyor: %{int((completed/total_issues)*100)} ({completed}/{total_issues})")
            
            result = future.result()
            if result:
                author_stats[result["author"]] += 1
                detailed_records.append(result)
                
    progress_bar.empty()
    status_text.empty()

    if not author_stats:
        st.warning("Taranan senaryolarda ilgili alanı değiştiren kimse bulunamadı.")
        st.stop()

    sorted_authors = sorted(author_stats.items(), key=lambda x: x[1], reverse=True)
    df_summary = pd.DataFrame(sorted_authors, columns=["Kişi", "Otomatize Edilen Senaryo Sayısı"])
    df_details = pd.DataFrame(detailed_records)
    df_details.columns = ["Senaryo ID", "Kişi", "Otomasyon Statüsü", "Tarih"]
    total_automated = sum(count for _, count in sorted_authors)

    st.success("Analiz tamamlandı!")

    m1, m2 = st.columns(2)
    m1.metric("Toplam Otomatize Edilen Senaryo", total_automated)
    m2.metric("Otomasyon Yazan Kişi Sayısı", len(sorted_authors))

    st.markdown("---")

    col_chart, col_table = st.columns([2, 1])
    with col_chart:
        st.subheader("📊 Kişi Bazlı Dağılım")
        st.bar_chart(df_summary.set_index("Kişi"), color="#0d6efd")
    with col_table:
        st.subheader("🏆 Liderlik Tablosu")
        st.dataframe(df_summary, use_container_width=True, hide_index=True)

    st.markdown("---")
    st.subheader("📝 Senaryo Detayları")
    st.dataframe(df_details, use_container_width=True, hide_index=True)
    
    excel_data = generate_excel(df_summary, df_details)
    st.download_button(
        label="📥 Sonuçları Excel Olarak İndir",
        data=excel_data,
        file_name=f"Otomasyon_Raporu_{start_date_str}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        type="primary"
    )
