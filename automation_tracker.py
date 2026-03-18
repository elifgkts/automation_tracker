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

# =============================================================================
# SABİT AYARLAR (Arka Planda Çalışan Kurallar)
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

# Tümü seçeneği için bütün projeleri birleştir
PROJECTS["Tümü (Bütün Ürünler)"] = ", ".join(PROJECTS.values())

# Sayfa Ayarları
st.set_page_config(page_title="Otomasyon Dashboard", page_icon="🚀", layout="wide")

# =============================================================================
# FONKSİYONLAR
# =============================================================================
def get_auth_headers(token):
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }

def get_issue_keys(headers, jql, start_date, end_date):
    full_jql = f"({jql}) AND issuetype = Test AND updated >= '{start_date}' AND updated <= '{end_date} 23:59'"
    url = f"{JIRA_URL.rstrip('/')}/rest/api/2/search"
    
    start_at = 0
    max_results = 100
    issue_keys = []
    
    while True:
        params = {"jql": full_jql, "startAt": start_at, "maxResults": max_results, "fields": "key"}
        
        try:
            r = requests.get(url, headers=headers, params=params, verify=False, timeout=30)
        except requests.exceptions.RequestException as e:
            st.error(f"🚨 **Bağlantı Hatası:** Jira sunucusuna ulaşılamıyor. Şirket ağında (VPN) olduğunuzdan emin olun.\n\n`{e}`")
            st.stop()
            
        # 200 (Başarılı) dışındaki kodları yakala
        if r.status_code != 200:
            st.error(f"🚨 **Jira API Hatası (Kod: {r.status_code})**")
            if r.status_code == 401:
                st.warning("Yetkilendirme reddedildi. JIRA_TOKEN değerinizi kontrol edin.")
            elif r.status_code in [403, 503]:
                st.warning("Erişim engellendi. Streamlit Cloud sunucuları şirketinizin güvenlik duvarına (Firewall/VPN) takılıyor olabilir.")
            
            with st.expander("Jira'dan Dönen Hata Mesajı Detayı"):
                st.code(r.text[:1000], language="html")
            st.stop()
            
        # JSON dönüştürme hatasını yakala
        try:
            data = r.json()
        except Exception:
            st.error("🚨 **Veri Okuma Hatası:** Jira'dan beklenen veri gelmedi. Sunucu JSON yerine bir HTML sayfası döndürdü (Muhtemelen bir Firewall engeli sayfası).")
            with st.expander("Gelen Yanıt (İlk 1000 Karakter)"):
                st.code(r.text[:1000], language="html")
            st.stop()

        issues = data.get("issues", [])
        if not issues:
            break
            
        issue_keys.extend([issue["key"] for issue in issues])
        
        if len(issues) < max_results:
            break
        start_at += max_results
        
    return issue_keys

def check_who_automated(key, headers, start_date, end_date):
    url = f"{JIRA_URL.rstrip('/')}/rest/api/2/issue/{key}?expand=changelog"
    try:
        r = requests.get(url, headers=headers, verify=False, timeout=30)
        if r.status_code == 200:
            changelog = r.json().get("changelog", {}).get("histories", [])
            for history in changelog:
                hist_date = history.get("created", "")[:10]
                if start_date <= hist_date <= end_date:
                    for item in history.get("items", []):
                        field_name = str(item.get("field", "")).lower()
                        if field_name == CUSTOM_FIELD.lower():
                            to_val = str(item.get("toString", "")).strip()
                            if to_val in TARGET_VALUES:
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

st.title("🚀 QA Otomasyon Performans Dashboard")
st.markdown("Ürün ve tarih aralığı seçerek takımınızın otomasyon katkılarını interaktif olarak analiz edin.")

# Sol Menü (Sidebar) Ayarları
with st.sidebar:
    st.header("⚙️ Rapor Kriterleri")
    
    # Proje Seçimi
    selected_product = st.selectbox(
        "📦 Ürün Seçin",
        options=list(PROJECTS.keys()),
        index=1  # Varsayılan olarak fizy
    )
    
    st.markdown("📅 **Tarih Aralığı**")
    col1, col2 = st.columns(2)
    with col1:
        start_d = st.date_input("Başlangıç", datetime.date(datetime.date.today().year, 1, 1))
    with col2:
        end_d = st.date_input("Bitiş", datetime.date.today())
        
    st.markdown("<br>", unsafe_allow_html=True)
    run_btn = st.button("📊 Dashboard'u Oluştur", type="primary", use_container_width=True)

# =============================================================================
# ANA ÇALIŞMA MANTIĞI
# =============================================================================

if run_btn:
    # Şifreyi Streamlit'in gizli kasasından çekiyoruz
    try:
        jira_token = st.secrets["JIRA_TOKEN"]
    except KeyError:
        st.error("⚠️ HATA: Jira Token bulunamadı! Lütfen Streamlit Cloud ayarlarından (Secrets) 'JIRA_TOKEN' değerini ekleyin.")
        st.stop()

    start_date_str = start_d.strftime("%Y-%m-%d")
    end_date_str = end_d.strftime("%Y-%m-%d")
    headers = get_auth_headers(jira_token)
    
    selected_project_keys = PROJECTS[selected_product]
    project_jql = f"project in ({selected_project_keys})"

    st.info(f"🔍 **{selected_product}** ürünü için `{start_date_str}` ile `{end_date_str}` tarihleri arası taranıyor...")

    # 1. Aşama: Jira'dan kayıtları bul
    with st.spinner("İlgili senaryolar Jira'dan çekiliyor..."):
        issue_keys = get_issue_keys(headers, project_jql, start_date_str, end_date_str)
    
    if not issue_keys:
        st.warning(f"Seçilen kriterlerde ({selected_product}) güncellenmiş Test senaryosu bulunamadı.")
        st.stop()

    # 2. Aşama: Geçmişleri (Changelog) tara
    author_stats = defaultdict(int)
    detailed_records = []
    total_issues = len(issue_keys)
    
    progress_bar = st.progress(0)
    status_text = st.empty()
    completed = 0
    
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        future_to_key = {
            executor.submit(check_who_automated, key, headers, start_date_str, end_date_str): key 
            for key in issue_keys
        }
        
        for future in concurrent.futures.as_completed(future_to_key):
            completed += 1
            progress_bar.progress(completed / total_issues)
            status_text.text(f"Geçmiş kayıtları taranıyor: %{int((completed/total_issues)*100)} ({completed}/{total_issues})")
            
            result = future.result()
            if result:
                author_stats[result["author"]] += 1
                detailed_records.append(result)
                
    progress_bar.empty()
    status_text.empty()

    # 3. Aşama: Sonuçları Göster
    if not author_stats:
        st.warning("Taranan senaryolarda 'Automated' alanını hedef değerlere çeken kimse bulunamadı.")
        st.stop()

    sorted_authors = sorted(author_stats.items(), key=lambda x: x[1], reverse=True)
    df_summary = pd.DataFrame(sorted_authors, columns=["Kişi", "Otomatize Edilen Senaryo Sayısı"])
    df_details = pd.DataFrame(detailed_records)
    df_details.columns = ["Senaryo ID", "Kişi", "Otomasyon Statüsü", "Tarih"]
    
    total_automated = sum(count for _, count in sorted_authors)

    st.success("Analiz başarıyla tamamlandı! 🎉")

    # Metrik Kartları
    m1, m2, m3 = st.columns(3)
    m1.metric("📦 Seçilen Ürün", selected_product)
    m2.metric("✅ Otomatize Edilen Senaryo", total_automated)
    m3.metric("👥 Otomasyon Yazan Kişi", len(sorted_authors))

    st.markdown("---")

    # Grafik ve Tablo Yan Yana
    col_chart, col_table = st.columns([2, 1])
    
    with col_chart:
        st.subheader("📊 Kişi Bazlı Dağılım Grafiği")
        st.bar_chart(df_summary.set_index("Kişi"), color="#0dcaf0")
        
    with col_table:
        st.subheader("🏆 Liderlik Tablosu")
        st.dataframe(df_summary, use_container_width=True, hide_index=True)

    st.markdown("---")
    
    # Detaylar ve Excel İndirme
    st.subheader("📝 İşlem Detayları (Kanıt Kayıtları)")
    st.dataframe(df_details, use_container_width=True, hide_index=True)
    
    st.markdown("<br>", unsafe_allow_html=True)
    
    excel_data = generate_excel(df_summary, df_details)
    st.download_button(
        label="📥 Tüm Sonuçları Excel Olarak İndir",
        data=excel_data,
        file_name=f"{selected_product}_Otomasyon_Raporu_{start_date_str}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        type="primary"
    )
