# app.py
# ============== Vadi Fatura — Böl & Alt Yazı & Apsiyon & WhatsApp (Drive upload with UUID names) ==============
import io, os, re, zipfile, unicodedata, uuid, json
from typing import List, Dict, Tuple, Optional

import streamlit as st
import pandas as pd

# Google Drive client
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload

# PDF
from pypdf import PdfReader, PdfWriter

# ALT YAZI (ReportLab)
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

# (Opsiyonel) .docx
try:
    import docx  # python-docx
    HAS_DOCX = True
except Exception:
    HAS_DOCX = False

# =========================
# Page config
# =========================
st.set_page_config(page_title="Fatura • Atlas Vadi", page_icon="🧾", layout="wide")

# =========================
# Google Drive: servis hesabı helper
# =========================
_DRIVE_SCOPES = ["https://www.googleapis.com/auth/drive"]

@st.cache_resource(show_spinner=False)
def _drive_service():
    # Bekler: Streamlit Secrets içinde gcp_service_account olarak JSON dict var
    if "gcp_service_account" not in st.secrets:
        raise RuntimeError("Streamlit secrets içinde 'gcp_service_account' bulunamadı. Servis hesabı JSON'unu ekleyin.")
    sa_dict = dict(st.secrets["gcp_service_account"])
    creds = service_account.Credentials.from_service_account_info(sa_dict, scopes=_DRIVE_SCOPES)
    return build("drive", "v3", credentials=creds, cache_discovery=False)

def drive_ensure_folder(folder_name: str) -> str:
    """
    Servis hesabının Drive'ında folder_name klasörünü bul veya oluştur.
    Döner: folder_id
    """
    srv = _drive_service()
    q = f"name = '{folder_name}' and mimeType = 'application/vnd.google-apps.folder' and trashed = false"
    res = srv.files().list(q=q, spaces="drive", fields="files(id,name)", pageSize=10).execute()
    files = res.get("files", [])
    if files:
        return files[0]["id"]
    file_meta = {"name": folder_name, "mimeType": "application/vnd.google-apps.folder"}
    folder = srv.files().create(body=file_meta, fields="id").execute()
    return folder["id"]

def drive_upload_pdf(bytes_io: io.BytesIO, original_name: str, parent_folder_id: str) -> dict:
    """
    UUID ile tahmin edilemez ad vererek yükle.
    Döndürür: dict with id, name, webViewLink, webContentLink
    """
    srv = _drive_service()
    ext = os.path.splitext(original_name)[1] or ".pdf"
    safe_name = f"{uuid.uuid4().hex}{ext}"
    media = MediaIoBaseUpload(bytes_io, mimetype="application/pdf", resumable=False)
    file_meta = {"name": safe_name, "parents": [parent_folder_id]}
    f = srv.files().create(body=file_meta, media_body=media, fields="id,name,webViewLink,webContentLink").execute()
    return f

def drive_share_anyone_reader(file_id: str) -> None:
    """
    Dosyayı 'anyoneWithLink' okuyucu yap.
    """
    srv = _drive_service()
    perm = {"type": "anyone", "role": "reader"}
    try:
        srv.permissions().create(fileId=file_id, body=perm, fields="id").execute()
    except Exception:
        pass

# =========================
# (Buraya kadar Drive kısımları. Aşağıda uygulamanın geri kalanı - footer, parser, rehber, UI)
# =========================

# --- FONT register (NotoSans dosyalarını /fonts içinde bulundur) ---
try:
    pdfmetrics.registerFont(TTFont("NotoSans-Regular", "fonts/NotoSans-Regular.ttf"))
    pdfmetrics.registerFont(TTFont("NotoSans-Bold",    "fonts/NotoSans-Bold.ttf"))
except Exception:
    # eğer yoksa uygulama yine çalışsın (yalnızca alt yazı stilleri en iyi olmayabilir)
    pass

# ---------- Yardımcılar (kısaltılmış) ----------
def _pad3_digits(s: str) -> str:
    s = "".join(ch for ch in str(s) if ch.isdigit())
    return s.zfill(3) if s else "000"

def _to_float_tr(s: str) -> float:
    if not s: return 0.0
    s = str(s).strip().replace(".", "").replace(",", ".")
    try: return float(s)
    except: return 0.0

def _normalize_tr(t: str) -> str:
    if not t: return ""
    t = unicodedata.normalize("NFKD", t)
    t = "".join(ch for ch in t if not unicodedata.combining(ch))
    t = (t.replace("ı","i").replace("İ","I")
           .replace("ş","s").replace("Ş","S")
           .replace("ö","o").replace("Ö","O")
           .replace("ü","u").replace("Ü","U")
           .replace("ğ","g").replace("Ğ","G")
           .replace("ç","c").replace("Ç","C"))
    t = t.upper()
    t = re.sub(r"[ \t]+", " ", t)
    return t

def _norm_colname(s: str) -> str:
    return (str(s).strip().lower()
            .replace("\n"," ").replace("\r"," ")
            .replace(".","").replace("_"," ").replace("-"," "))

# (Aşağıda önceki uygulamadaki parser / rehber fonksiyonları aynen kullanılıyor — ihtiyaç halinde daha da sadeleştirilebilir)
# ... (kısa tutmak için uygulamanın tam PDF/reher/parsing fonksiyonlarını buraya olduğu gibi ekleyin)
# For brevity in this message I will reuse the earlier defined functions from your app:
# - parse_manas_pdf_totals
# - load_apsiyon_template
# - fill_expenses_to_apsiyon
# - export_excel_bytes
# - load_contacts_any
# - plus PDF footer/split helpers (wrap_by_width, build_footer_overlay, add_footer_to_pdf, split_pdf, add_footer_and_stamp_per_page)
#
# (In your copy paste, include the same helper definitions you already had above — I assume you will paste them here unchanged.)
#
# ---------------------------------------------------------------------
# UI — minimal WhatsApp Drive upload flow (sadece ilgili kısım):
st.title("☁️ Drive UUID Upload — Güvenli paylaşım")

st.markdown("""
Bu modül zip içindeki PDF'leri Drive'a **rastgele (UUID) isimle** yükler ve her dosya için tekil paylaşım linki üretir.
**ÖNEMLİ:** Eğer yüklemeyi kendi Google Drive'ınıza (varolan bir klasöre) yapmak istiyorsanız, o klasörü servis hesabı e-postasıyla **Editor** olarak paylaşmalısınız.
""")

col1, col2 = st.columns(2)
with col1:
    zip_up = st.file_uploader("Bölünmüş PDF ZIP yükle", type=["zip"])
with col2:
    # Kullanıcı isterse varolan folder_id verebilir (ör: Drive klasör linkinin sonunda görünen id)
    folder_id_input = st.text_input("Opsiyonel: Varolan Drive Klasör ID'si (boşsa yeni klasör oluşturulur)", value="")

st.text_input("Servis hesabı e-posta (bilgi amaçlı, ör: atlasvadi-drive-uploader@... )", value=st.secrets.get("gcp_service_account", {}).get("client_email",""), disabled=True)

drive_folder_name = st.text_input("Yeni klasör adı (servis hesabının Drive'ında oluşturulacaksa)", value="AtlasVadi_Faturalar")
if st.button("☁️ Yükle ve linkleri üret"):
    if not zip_up:
        st.warning("Önce ZIP dosyasını yükleyin.")
        st.stop()

    # 1) hangi klasöre yüklenecek?
    try:
        if folder_id_input.strip():
            target_folder_id = folder_id_input.strip()
            st.info("Belirtilen klasör ID'sine (kullanıcının Drive'ında) yükleme yapılacak. Bu klasörü servis hesabı ile paylaşmış olmanız gerekir.")
        else:
            with st.spinner("Servis hesabı Drive'ında klasör oluşturuluyor / aranıyor..."):
                target_folder_id = drive_ensure_folder(drive_folder_name)
    except Exception as e:
        st.error(f"Drive servisi hatası: {e}")
        st.stop()

    # 2) ZIP içindekileri yükle
    try:
        zf = zipfile.ZipFile(zip_up)
    except Exception as e:
        st.error(f"ZIP açılamadı: {e}")
        st.stop()

    pdf_infos = [i for i in zf.infolist() if (not i.is_dir()) and i.filename.lower().endswith(".pdf")]
    if not pdf_infos:
        st.error("ZIP içinde PDF bulunamadı.")
        st.stop()

    uploaded_map = {}
    progress = st.progress(0)
    total = len(pdf_infos)
    done = 0

    for info in pdf_infos:
        base = info.filename.rsplit("/",1)[-1].rsplit("\\",1)[-1]
        data = zf.read(info)
        bio = io.BytesIO(data)
        try:
            meta = drive_upload_pdf(bio, base, target_folder_id)
            drive_share_anyone_reader(meta["id"])
            link = meta.get("webViewLink") or meta.get("webContentLink")
            uploaded_map[base] = link
            done += 1
            progress.progress(done/total)
        except Exception as e:
            st.warning(f"Yükleme hatası ({base}): {e}")

    st.success(f"Yükleme tamam: {done}/{total} dosya yüklendi.")

    st.download_button("📥 uploaded_map.json", json.dumps(uploaded_map, ensure_ascii=False, indent=2).encode("utf-8"), file_name="uploaded_map.json")

    st.info("Not: Eğer kullanıcıların yalnızca kendi faturalarını görmesini istiyorsanız, WhatsApp CSV üretirken CSV'deki 'file_url' alanını bu uploaded_map ile eşleştiriyoruz (file_name üzerinden).")

    st.write("Örnek: uploaded_map içinden A1-013.pdf -> webViewLink eşleşmesini CSV'ye koyun.")

# (Buraya uygulamanın geri kalan UI işlemlerini ve WhatsApp CSV eşleştirme adımlarını ekleyin — yukarıdaki kodu tam app'nize entegre edin.)
