# app.py
# === Vadi Fatura — Böl & Alt Yazı & Apsiyon & WhatsApp (Drive entegrasyonlu) ===
import io, os, re, zipfile, unicodedata, json, uuid
from typing import List, Dict, Tuple, Optional

import streamlit as st
import pandas as pd
# ---------------- GOOGLE DRIVE: Secrets ile bağlan & yardımcılar ----------------
try:
    from google.oauth2 import service_account
    from googleapiclient.discovery import build
    from googleapiclient.errors import HttpError
    _GDRIVE_OK = True
except Exception:
    _GDRIVE_OK = False

import streamlit as st
import json

@st.cache_resource(show_spinner=False)
def get_drive_service_from_secrets():
    """
    Streamlit Secrets'taki [gdrive_service_account] ile Drive service oluşturur.
    """
    info = st.secrets.get("gdrive_service_account")
    if not info:
        raise RuntimeError("Streamlit Secrets içinde [gdrive_service_account] yok.")
    scopes = ["https://www.googleapis.com/auth/drive"]
    creds = service_account.Credentials.from_service_account_info(info, scopes=scopes)
    service = build("drive", "v3", credentials=creds, cache_discovery=False)
    return service

def list_pdfs_in_folder(service, folder_id: str):
    """
    Verilen klasördeki PDF dosyalarını listeler.
    """
    files = []
    page_token = None
    query = f"'{folder_id}' in parents and mimeType='application/pdf' and trashed=false"
    while True:
        resp = service.files().list(
            q=query,
            fields="nextPageToken, files(id,name,webViewLink,webContentLink)",
            pageSize=1000,
            pageToken=page_token,
            supportsAllDrives=True,
            includeItemsFromAllDrives=True
        ).execute()
        files.extend(resp.get("files", []))
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    return files

def ensure_anyone_with_link_permission(service, file_id: str):
    """
    Dosyayı 'linke sahip olan görüntüleyebilir' yapar (sadece dosya bazında).
    """
    try:
        service.permissions().create(
            fileId=file_id,
            body={"role": "reader", "type": "anyone"},
            fields="id",
            supportsAllDrives=True
        ).execute()
    except HttpError:
        pass

def build_direct_file_link(file_id: str, mode: str = "download") -> str:
    """
    'download' -> doğrudan indirme linki
    'view'     -> Drive görüntüleme linki
    """
    if mode == "view":
        return f"https://drive.google.com/file/d/{file_id}/view?usp=drivesdk"
    else:
        return f"https://drive.google.com/uc?export=download&id={file_id}"
# PDF
from pypdf import PdfReader, PdfWriter

# ReportLab (alt yazı)
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

# (opsiyonel) .docx
try:
    import docx  # python-docx
    HAS_DOCX = True
except Exception:
    HAS_DOCX = False

# -----------------------------------------------------------------------------
# Streamlit Page
# -----------------------------------------------------------------------------
st.set_page_config(page_title="Fatura • Atlas Vadi", page_icon="🧾", layout="wide")

# -----------------------------------------------------------------------------
# Fontlar (varsa yükle; yoksa sessiz geçsin)
# -----------------------------------------------------------------------------
try:
    pdfmetrics.registerFont(TTFont("NotoSans-Regular", "fonts/NotoSans-Regular.ttf"))
    pdfmetrics.registerFont(TTFont("NotoSans-Bold",    "fonts/NotoSans-Bold.ttf"))
except Exception:
    pass

# -----------------------------------------------------------------------------
# Yardımcılar (genel)
# -----------------------------------------------------------------------------
def _pad3_digits(s: str) -> str:
    s = "".join(ch for ch in str(s) if ch.isdigit())
    return s.zfill(3) if s else "000"

def _to_float_tr(s: str) -> float:
    if not s:
        return 0.0
    s = str(s).strip().replace(".", "").replace(",", ".")
    try:
        return float(s)
    except:
        return 0.0

def _normalize_tr(t: str) -> str:
    """Türkçe aksanları sadeleştir, büyük harfe çevir, spacing’i toparlar."""
    if not t:
        return ""
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

# -----------------------------------------------------------------------------
# Alt Yazı (wrap & overlay)
# -----------------------------------------------------------------------------
def wrap_by_width(text: str, font_name: str, font_size: float, max_width: float) -> List[str]:
    lines = []
    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if not raw.strip():
            lines.append("")
            continue
        words = raw.split()
        current = ""
        for w in words:
            trial = (current + " " + w).strip()
            width = pdfmetrics.stringWidth(trial, font_name, font_size)
            if width <= max_width:
                current = trial
            else:
                if current:
                    lines.append(current)
                if pdfmetrics.stringWidth(w, font_name, font_size) > max_width:
                    piece = ""
                    for ch in w:
                        if pdfmetrics.stringWidth(piece + ch, font_name, font_size) <= max_width:
                            piece += ch
                        else:
                            lines.append(piece)
                            piece = ch
                    current = piece
                else:
                    current = w
        lines.append(current)
    return lines

def build_footer_overlay(
    page_w: float,
    page_h: float,
    footer_text: str,
    font_size: int = 11,
    leading: int = 14,
    align: str = "left",  # "left" | "center"
    bottom_margin: int = 48,
    box_height: int = 180,
    bold_rules: bool = True,
) -> io.BytesIO:
    packet = io.BytesIO()
    can = canvas.Canvas(packet, pagesize=(page_w, page_h))

    left_margin = 36
    right_margin = 36
    max_text_width = page_w - left_margin - right_margin

    wrapped = wrap_by_width(footer_text, "NotoSans-Regular", font_size, max_text_width)

    max_lines = max(1, int(box_height // leading))
    if len(wrapped) > max_lines:
        wrapped = wrapped[:max_lines]

    y_start = bottom_margin + (len(wrapped) - 1) * leading + 4

    for i, line in enumerate(wrapped):
        use_bold = False
        if bold_rules:
            u = line.strip().upper()
            if i == 0 and u.startswith("SON ÖDEME"):
                use_bold = True
            if u == "AÇIKLAMA":
                use_bold = True
            if "TARİHLİ TEMSİLCİLER" in u:
                use_bold = True

        can.setFont("NotoSans-Bold" if use_bold else "NotoSans-Regular", font_size)
        y = y_start - i * leading
        if align == "center":
            can.drawCentredString(page_w / 2.0, y, line)
        else:
            can.drawString(left_margin, y, line)

    can.save()
    packet.seek(0)
    return packet

def add_footer_to_pdf(src_bytes: bytes, **kw) -> bytes:
    reader = PdfReader(io.BytesIO(src_bytes))
    writer = PdfWriter()
    for page in reader.pages:
        w = float(page.mediabox.width)
        h = float(page.mediabox.height)
        overlay_io = build_footer_overlay(w, h, **kw)
        overlay = PdfReader(overlay_io)
        page.merge_page(overlay.pages[0])
        writer.add_page(page)
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()

def split_pdf(src_bytes: bytes) -> List[Tuple[str, bytes]]:
    reader = PdfReader(io.BytesIO(src_bytes))
    pages = []
    for i, p in enumerate(reader.pages, start=1):
        w = PdfWriter()
        w.add_page(p)
        b = io.BytesIO()
        w.write(b)
        pages.append((f"page_{i:03d}.pdf", b.getvalue()))
    return pages

# -----------------------------------------------------------------------------
# Daire No Algılama & Köşe Etiketi & Yeniden Adlandırma
# -----------------------------------------------------------------------------
_re_daire_norms = [
    re.compile(r"DAIRE\s*NO[^A-Z0-9]{0,15}([A-Z]\d)[^0-9]{0,20}(\d{1,4})"),
    re.compile(r"([A-Z]\d)[^\d\n\r]{0,30}DAIRE[^0-9]{0,10}(\d{1,4})"),
]
_re_daire_raws = [
    re.compile(r"DA[İI]RE\s*NO[^A-Z0-9]{0,15}([A-Z]\d)[^0-9]{0,20}(\d{1,4})"),
    re.compile(r"([A-Z]\d)[^\d\n\r]{0,30}DA[İI]RE[^0-9]{0,10}(\d{1,4})"),
]

def _find_daire_id(raw_text: str) -> Optional[str]:
    norm = _normalize_tr(raw_text)
    for rx in _re_daire_norms:
        m = rx.search(norm)
        if m:
            blok = m.group(1).upper()
            dno  = _pad3_digits(m.group(2))
            return f"{blok}-{dno}"
    for rx in _re_daire_raws:
        m = rx.search(raw_text)
        if m:
            blok = m.group(1).upper()
            dno  = _pad3_digits(m.group(2))
            return f"{blok}-{dno}"
    return None

def build_corner_label_overlay(
    page_w: float, page_h: float, label_text: str,
    font_size: int = 13, bold: bool = True,
    position: str = "TR", pad_x: int = 20, pad_y: int = 20
) -> io.BytesIO:
    packet = io.BytesIO()
    can = canvas.Canvas(packet, pagesize=(page_w, page_h))
    font_name = "NotoSans-Bold" if bold else "NotoSans-Regular"
    can.setFont(font_name, font_size)
    text_w = pdfmetrics.stringWidth(label_text, font_name, font_size)
    text_h = font_size * 1.2

    if position == "TR":
        x = page_w - pad_x - text_w
        y = page_h - pad_y - text_h
    elif position == "TL":
        x = pad_x
        y = page_h - pad_y - text_h
    elif position == "BR":
        x = page_w - pad_x - text_w
        y = pad_y
    else:  # BL
        x = pad_x
        y = pad_y

    can.drawString(x, y, label_text)
    can.save()
    packet.seek(0)
    return packet

def add_footer_and_stamp_per_page(
    src_bytes: bytes,
    footer_kwargs: dict,
    stamp_on: bool,
    label_tpl: str,
    stamp_opts: dict,
    rename_files: bool
) -> List[Tuple[str, bytes]]:
    reader = PdfReader(io.BytesIO(src_bytes))
    out_pages: List[Tuple[str, bytes]] = []

    for i, page in enumerate(reader.pages, start=1):
        w = float(page.mediabox.width)
        h = float(page.mediabox.height)

        # footer
        footer_overlay_io = build_footer_overlay(w, h, **footer_kwargs)
        footer_overlay = PdfReader(footer_overlay_io)
        page.merge_page(footer_overlay.pages[0])

        # DaireID
        daire_id = None
        try:
            txt = page.extract_text() or ""
            daire_id = _find_daire_id(txt)
        except Exception:
            daire_id = None

        # köşe etiketi
        if stamp_on and daire_id:
            label_text = label_tpl.format(daire_id=daire_id)
            label_overlay_io = build_corner_label_overlay(
                w, h, label_text,
                font_size=stamp_opts.get("font_size", 13),
                bold=stamp_opts.get("bold", True),
                position=stamp_opts.get("position", "TR"),
                pad_x=stamp_opts.get("pad_x", 20),
                pad_y=stamp_opts.get("pad_y", 20),
            )
            label_overlay = PdfReader(label_overlay_io)
            page.merge_page(label_overlay.pages[0])

        # tek sayfa pdf
        wri = PdfWriter()
        wri.add_page(page)
        buf = io.BytesIO()
        wri.write(buf)
        buf.seek(0)

        fname = f"page_{i:03d}.pdf"
        if rename_files and daire_id:
            fname = f"{daire_id}.pdf"

        out_pages.append((fname, buf.getvalue()))

    return out_pages

# -----------------------------------------------------------------------------
# MANAS PDF Parser (Isıtma / Sıcak Su / Su / Toplam)
# -----------------------------------------------------------------------------
def parse_manas_pdf_totals(pdf_bytes: bytes) -> Dict[str, Dict[str, float]]:
    reader = PdfReader(io.BytesIO(pdf_bytes))
    result: Dict[str, Dict[str, float]] = {}

    re_daire_norms = [
        re.compile(r"DAIRE\s*NO[^A-Z0-9]{0,15}([A-Z]\d)[^0-9]{0,20}(\d{1,4})"),
        re.compile(r"([A-Z]\d)[^\d\n\r]{0,30}DAIRE[^0-9]{0,10}(\d{1,4})"),
    ]
    re_daire_raws = [
        re.compile(r"DA[İI]RE\s*NO[^A-Z0-9]{0,15}([A-Z]\d)[^0-9]{0,20}(\d{1,4})"),
        re.compile(r"([A-Z]\d)[^\d\n\r]{0,30}DA[İI]RE[^0-9]{0,10}(\d{1,4})"),
    ]
    re_odenecek = re.compile(r"(?:ÖDENECEK|ODENECEK)\s*TUTAR[^0-9]{0,10}([0-9\.\,]+)", re.IGNORECASE)
    re_toplam   = re.compile(r"TOPLAM\s+TUTAR[^0-9]{0,10}([0-9\.\,]+)", re.IGNORECASE)

    def find_daire_id(raw_text: str) -> Optional[str]:
        norm = _normalize_tr(raw_text)
        for rx in re_daire_norms:
            m = rx.search(norm)
            if m:
                return f"{m.group(1).upper()}-{_pad3_digits(m.group(2))}"
        for rx in re_daire_raws:
            m = rx.search(raw_text)
            if m:
                return f"{m.group(1).upper()}-{_pad3_digits(m.group(2))}"
        return None

    def grab_section_amount(norm_text: str, header_word: str) -> float:
        idx = norm_text.find(header_word)
        if idx == -1:
            return 0.0
        tail = norm_text[idx: idx + 2500]
        m = re_odenecek.search(tail)
        return _to_float_tr(m.group(1)) if m else 0.0

    for pi, page in enumerate(reader.pages):
        raw = page.extract_text() or ""
        norm = _normalize_tr(raw)

        did = find_daire_id(raw)
        if not did:
            if pi == 0:
                st.info("⚠️ Daire No satırı bulunamadı. İlk sayfanın normalize içeriğinin bir kısmı:")
                st.code(norm[:800])
            continue

        isitma = grab_section_amount(norm, "ISITMA")
        sicak  = grab_section_amount(norm, "SICAK SU")

        # SU başlığı SICAK SU ile karışmasın:
        su = 0.0
        idx_sicak = norm.find("SICAK SU")
        search_base = norm[idx_sicak + 8:] if idx_sicak != -1 else norm
        idx_su = search_base.find("\nSU")
        if idx_su == -1:
            idx_su = search_base.find(" SU ")
        if idx_su != -1:
            tail_su = search_base[idx_su: idx_su + 2000]
            m_su = re_odenecek.search(tail_su)
            if m_su:
                su = _to_float_tr(m_su.group(1))
        if su == 0.0:
            su = grab_section_amount(norm, "\nSU")

        mt = re_toplam.search(norm)
        toplam = _to_float_tr(mt.group(1)) if mt else (isitma + sicak + su)

        result[did] = {"isitma": isitma, "sicak": sicak, "su": su, "toplam": toplam}

    return result

# -----------------------------------------------------------------------------
# Apsiyon Excel Yardımcıları
# -----------------------------------------------------------------------------
def _norm_cols(s: str) -> str:
    return (str(s).strip().lower()
            .replace("\n"," ").replace("\r"," ")
            .replace(".","").replace("_"," ").replace("-"," "))

def _pad3_aps(x) -> str:
    try:
        n = int(str(x).strip());  return f"{n:03d}"
    except:
        s = str(x).strip()
        nums = "".join([ch for ch in s if ch.isdigit()])
        return f"{int(nums):03d}" if nums else s

def _find_header_row(df_raw: pd.DataFrame) -> Optional[int]:
    limit = min(15, len(df_raw))
    for i in range(limit):
        cells = [_norm_cols(c) for c in list(df_raw.iloc[i].values)]
        row_text = " | ".join(cells)
        if ("blok" in row_text) and (("daire no" in row_text) or ("daire" in row_text)):
            return i
    return None

def _rename_apsiyon_cols(df: pd.DataFrame) -> pd.DataFrame:
    mapping = {}
    for c in df.columns:
        nc = _norm_cols(c)
        if "blok" == nc:
            mapping[c] = "Blok"
        elif ("daire no" == nc) or (nc == "daire") or ("daire  no" == nc) or ("daireno" == nc):
            mapping[c] = "Daire No"
        elif "gider1 tutarı" in nc or "gider 1 tutarı" in nc or "gider1 tutari" in nc:
            mapping[c] = "Gider1 Tutarı"
        elif "gider1 açıklaması" in nc or "gider 1 aciklamasi" in nc or "gider1 aciklamasi" in nc:
            mapping[c] = "Gider1 Açıklaması"
        elif "gider2 tutarı" in nc or "gider 2 tutarı" in nc or "gider2 tutari" in nc:
            mapping[c] = "Gider2 Tutarı"
        elif "gider2 açıklaması" in nc or "gider 2 aciklamasi" in nc or "gider2 aciklamasi" in nc:
            mapping[c] = "Gider2 Açıklaması"
        elif "gider3 tutarı" in nc or "gider 3 tutarı" in nc or "gider3 tutari" in nc:
            mapping[c] = "Gider3 Tutarı"
        elif "gider3 açıklaması" in nc or "gider 3 aciklamasi" in nc or "gider3 aciklamasi" in nc:
            mapping[c] = "Gider3 Açıklaması"
    df2 = df.rename(columns=mapping)
    for col in ["Gider1 Tutarı","Gider1 Açıklaması","Gider2 Tutarı","Gider2 Açıklaması","Gider3 Tutarı","Gider3 Açıklaması"]:
        if col not in df2.columns:
            df2[col] = None
    return df2

def load_apsiyon_template(excel_bytes: bytes) -> pd.DataFrame:
    from io import BytesIO
    raw = pd.read_excel(BytesIO(excel_bytes), header=None, engine="openpyxl")
    hdr = _find_header_row(raw)
    if hdr is None:
        df = pd.read_excel(BytesIO(excel_bytes), engine="openpyxl")
    else:
        df = pd.read_excel(BytesIO(excel_bytes), header=hdr, engine="openpyxl")
    df = _rename_apsiyon_cols(df)
    if ("Blok" not in df.columns) or ("Daire No" not in df.columns):
        st.error("Excel’de 'Blok' ve 'Daire No' sütunları bulunamadı.")
        st.dataframe(df.head(10))
        raise ValueError("Apsiyon şablonunda 'Blok' / 'Daire No' başlıkları tespit edilemedi.")
    return df

def fill_expenses_to_apsiyon(
    df_in: pd.DataFrame,
    totals: dict,
    mode: str,
    exp1: str,
    exp2: str,
    exp3: str,
) -> pd.DataFrame:
    df = df_in.copy()

    def make_did(blok, dno) -> str:
        b = str(blok).strip().upper()
        d = _pad3_aps(dno)
        return f"{b}-{d}"

    g1t, g1a = "Gider1 Tutarı", "Gider1 Açıklaması"
    g2t, g2a = "Gider2 Tutarı", "Gider2 Açıklaması"
    g3t, g3a = "Gider3 Tutarı", "Gider3 Açıklaması"

    for idx, row in df.iterrows():
        did = make_did(row.get("Blok", ""), row.get("Daire No", ""))
        if did in totals:
            t = totals[did]

            if mode.startswith("Seçenek 1"):
                # G1=Sıcak Su, G2=Su, G3=Isıtma
                df.at[idx, g1t] = t.get("sicak", 0.0);  df.at[idx, g1a] = exp1 or ""
                df.at[idx, g2t] = t.get("su", 0.0);     df.at[idx, g2a] = exp2 or ""
                df.at[idx, g3t] = t.get("isitma", 0.0); df.at[idx, g3a] = exp3 or ""

            elif mode.startswith("Seçenek 2"):
                # G1=Toplam, G2/G3 boş
                df.at[idx, g1t] = t.get("toplam", 0.0); df.at[idx, g1a] = exp1 or ""
                df.at[idx, g2t] = None; df.at[idx, g2a] = None
                df.at[idx, g3t] = None; df.at[idx, g3a] = None

            elif mode.startswith("Seçenek 3"):
                # G1=Sıcak Su
                df.at[idx, g1t] = t.get("sicak", 0.0);  df.at[idx, g1a] = exp1 or ""
                df.at[idx, g2t] = None; df.at[idx, g2a] = None
                df.at[idx, g3t] = None; df.at[idx, g3a] = None

            elif mode.startswith("Seçenek 4"):
                # G1=Su
                df.at[idx, g1t] = t.get("su", 0.0);     df.at[idx, g1a] = exp1 or ""
                df.at[idx, g2t] = None; df.at[idx, g2a] = None
                df.at[idx, g3t] = None; df.at[idx, g3a] = None

            elif mode.startswith("Seçenek 5"):
                # G1=Isıtma
                df.at[idx, g1t] = t.get("isitma", 0.0); df.at[idx, g1a] = exp1 or ""
                df.at[idx, g2t] = None; df.at[idx, g2a] = None
                df.at[idx, g3t] = None; df.at[idx, g3a] = None

    return df

def export_excel_bytes(df: pd.DataFrame, filename: str = "Apsiyon_Doldurulmus.xlsx") -> bytes:
    from io import BytesIO
    bio = BytesIO()
    with pd.ExcelWriter(bio, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="Sheet1")
    return bio.getvalue()

# -----------------------------------------------------------------------------
# Rehber Okuyucu (WhatsApp için) — Esnek: Apsiyon veya Basit CSV şeması
# -----------------------------------------------------------------------------
def _norm_rehber(s: str) -> str:
    return (str(s).strip().lower()
            .replace("\n"," ").replace("\r"," ")
            .replace(".","").replace("_"," ").replace("-"," "))

def _find_header_row_contacts(df_raw: pd.DataFrame, search_rows: int = 50) -> Optional[int]:
    """
    'Blok' + ('Daire'/'Daire No') + ('Telefon'/'Tel'/'GSM'/'Cep') birlikte görünen satırı başlık kabul eder.
    Ama basit CSV şemasını (phone/name/daire_id) da destekleyeceğiz; o durumda 0 döner.
    """
    limit = min(search_rows, len(df_raw))
    for i in range(limit):
        cells = [_norm_rehber(c) for c in list(df_raw.iloc[i].values)]
        row_text = " | ".join(cells)
        has_blok  = "blok" in row_text or "block" in row_text
        has_daire = ("daire no" in row_text) or ("daire  no" in row_text) or ("daire" in row_text) \
                    or ("daireno" in row_text) or ("apartment" in row_text) or ("flat" in row_text)
        has_tel   = ("telefon" in row_text) or ("tel" in row_text) or ("gsm" in row_text) or ("cep" in row_text) \
                    or ("telefon no" in row_text) or ("phone" in row_text) or ("mobile" in row_text)
        if has_blok and has_daire and has_tel:
            return i
    # Esnek davran: bulamazsa 0 kabul et (çoğu CSV zaten ilk satır başlık)
    return 0

def _map_contact_columns(df: pd.DataFrame) -> pd.DataFrame:
    """
    Hem Apsiyon başlıklarını hem de basit CSV başlıklarını destekler.
    Hedef final kolonlar: Blok, Daire No, Ad Soyad / Unvan (ops), Telefon
    Ayrıca daire_id varsa parçalar.
    """
    # Orijinal kolon adları
    original_cols = list(df.columns)

    # Önce normalize edilmiş bir isim haritası oluştur
    norm_map = {_norm_rehber(c): c for c in original_cols}

    # Basit CSV şeması mı? (phone + daire_id)
    has_phone    = any(k in norm_map for k in ["phone","mobile","telefon","tel","gsm","cep","telefon no"])
    has_daire_id = any(k in norm_map for k in ["daire id","daireid","daireid ","daire_id"])
    if has_phone and has_daire_id:
        c_phone    = norm_map.get("phone") or norm_map.get("mobile") or norm_map.get("telefon") \
                     or norm_map.get("tel") or norm_map.get("gsm") or norm_map.get("cep") or norm_map.get("telefon no")
        c_daire_id = norm_map.get("daire id") or norm_map.get("daireid") or norm_map.get("daireid ") or norm_map.get("daire_id")
        c_name     = norm_map.get("name") or norm_map.get("ad soyad") or norm_map.get("ad soyad  unvan") \
                     or norm_map.get("ad soyad/unvan") or norm_map.get("unvan")

        # Yeni DataFrame’i oluştur
        tmp = pd.DataFrame()
        tmp["Telefon"] = df[c_phone].astype(str)
        tmp["Ad Soyad / Unvan"] = df[c_name].astype(str) if c_name else None
        tmp["DaireID"] = df[c_daire_id].astype(str)

        # DaireID → Blok ve Daire No çıkar
        def _split_did(val: str) -> Tuple[str,str]:
            s = str(val).strip()
            m = (re.search(r"([A-Za-z]\d)\s*[-_ ]\s*(\d{1,3})", s)
                 or re.search(r"([A-Za-z]\d).*?(\d{3})", s)
                 or re.search(r"([A-Za-z]\d)\s+(\d{1,3})", s))
            if not m:
                return "", ""
            blok = m.group(1).upper()
            try:
                dno = f"{int(m.group(2)):03d}"
            except:
                dno = str(m.group(2)).zfill(3)
            return blok, dno

        tmp["Blok"], tmp["Daire No"] = zip(*tmp["DaireID"].map(_split_did))
        # Telefonu normalize et
        def _quick_norm_phone(x: str) -> str:
            s = re.sub(r"[^\d+]", "", str(x))
            if s.startswith("+"):                return s
            if re.fullmatch(r"05\d{9}", s):      return "+90" + s[1:]
            if re.fullmatch(r"5\d{9}", s):       return "+90" + s
            if re.fullmatch(r"0\d{10,11}", s):   return "+90" + s[1:]
            if re.fullmatch(r"90\d{10}", s):     return "+" + s
            return s
        tmp["Telefon"] = tmp["Telefon"].apply(_quick_norm_phone)

        # Eksik olanları kontrol edip final döndür
        if "Ad Soyad / Unvan" not in tmp.columns:
            tmp["Ad Soyad / Unvan"] = None
        tmp["Blok"] = tmp["Blok"].astype(str).str.upper().str.strip()
        tmp["Daire No"] = tmp["Daire No"].astype(str).str.replace(r"\D","", regex=True).str.zfill(3)
        tmp["DaireID"] = tmp["Blok"] + "-" + tmp["Daire No"]
        return tmp[["Blok","Daire No","Ad Soyad / Unvan","Telefon","DaireID"]]

    # Apsiyon şeması (TR/EN çeşitleri) — esnek eşleme
    mapping = {}
    for c in original_cols:
        nc = _norm_rehber(c)
        if nc in ("blok","blok adi","blok adı","blokadi","blok ad","blokad","block"):
            mapping[c] = "Blok"
        elif nc in ("daire no","daire  no","daireno","daire","apartment","flat","apt no","apartment no","unit","unit no"):
            mapping[c] = "Daire No"
        elif ("ad soyad / unvan" in nc) or ("ad soyad/unvan" in nc) or ("ad soyad" in nc) or ("unvan" in nc) or (nc == "name") or ("full name" in nc):
            mapping[c] = "Ad Soyad / Unvan"
        elif (nc in ("telefon","tel","cep","gsm","telefon no","tel no","telefon numarasi","telefon numarası","phone","mobile")) or ("telefon no" in nc):
            mapping[c] = "Telefon"
        elif nc in ("daire id","daireid","daire id ","daire_id"):
            mapping[c] = "DaireID"

    df2 = df.rename(columns=mapping)

    # Eğer DaireID var ve Blok/Daire No yoksa parçala
    if "DaireID" in df2.columns and (("Blok" not in df2.columns) or ("Daire No" not in df2.columns)):
        def _split_did2(val: str) -> Tuple[str,str]:
            s = str(val).strip()
            m = (re.search(r"([A-Za-z]\d)\s*[-_ ]\s*(\d{1,3})", s)
                 or re.search(r"([A-Za-z]\d).*?(\d{3})", s)
                 or re.search(r"([A-Za-z]\d)\s+(\d{1,3})", s))
            if not m:
                return "", ""
            blok = m.group(1).upper()
            try:
                dno = f"{int(m.group(2)):03d}"
            except:
                dno = str(m.group(2)).zfill(3)
            return blok, dno
        blk, dno = zip(*df2["DaireID"].map(_split_did2))
        df2["Blok"] = df2.get("Blok", pd.Series(blk)).fillna(blk)
        df2["Daire No"] = df2.get("Daire No", pd.Series(dno)).fillna(dno)

    # Zorunlu kolonlar
    for need in ["Blok","Daire No","Telefon"]:
        if need not in df2.columns:
            # Basit hata gösterimi için aynı uyarı metnini kullanalım
            cols_map_debug = {c: _norm_rehber(c) for c in df.columns}
            st.error(f"Rehberde zorunlu kolon(lar) eksik: Blok, Daire No, Telefon")
            st.write("Algılanan kolonlar (normalize):", cols_map_debug)
            raise ValueError("Apsiyon rehber başlık eşlemesi yapılamadı.")

    # Temizlik
    def _pad3_for_merge(x) -> str:
        digits = "".join(ch for ch in str(x or "") if str(x))
        digits = "".join(ch for ch in str(x or "") if ch.isdigit())
        return digits.zfill(3) if digits else ""

    def _quick_norm_phone(x: str) -> str:
        s = re.sub(r"[^\d+]", "", str(x))
        if s.startswith("+"):                return s
        if re.fullmatch(r"05\d{9}", s):      return "+90" + s[1:]
        if re.fullmatch(r"5\d{9}", s):       return "+90" + s
        if re.fullmatch(r"0\d{10,11}", s):   return "+90" + s[1:]
        if re.fullmatch(r"90\d{10}", s):     return "+" + s
        return s

    if "Ad Soyad / Unvan" not in df2.columns:
        df2["Ad Soyad / Unvan"] = None

    df2["Blok"] = df2["Blok"].astype(str).str.upper().str.strip()
    df2["Daire No"] = df2["Daire No"].apply(_pad3_for_merge)
    df2["Telefon"] = df2["Telefon"].apply(_quick_norm_phone)
    df2["DaireID"] = df2["Blok"] + "-" + df2["Daire No"]

    return df2[["Blok","Daire No","Ad Soyad / Unvan","Telefon","DaireID"]]


def load_contacts_any(file_bytes: bytes, filename: str) -> pd.DataFrame:
    """
    - Apsiyon ham Excel/CSV (Blok, Daire No, Telefon …)
    - Basit CSV (phone, name, daire_id, [file_name])
    Şemalarının her ikisini de kabul eder.
    """
    from io import BytesIO

    # 1) Ham oku (header=None) ve mantıklı başlık satırı tespit et
    if filename.lower().endswith(".csv"):
        raw = pd.read_csv(BytesIO(file_bytes), header=None, dtype=str)
    else:
        raw = pd.read_excel(BytesIO(file_bytes), header=None, dtype=str, engine="openpyxl")

    hdr = _find_header_row_contacts(raw, search_rows=50)

    # 2) Başlıkla tekrar oku
    if filename.lower().endswith(".csv"):
        df = pd.read_csv(BytesIO(file_bytes), header=hdr, dtype=str)
    else:
        df = pd.read_excel(BytesIO(file_bytes), header=hdr, dtype=str, engine="openpyxl")

    # 3) 'Unnamed' kolon isimlerini bir üst satırdan düzelt (Apsiyon ham dosyalarda sık görülür)
    if hdr > 0:
        upper = raw.iloc[hdr-1]
        new_cols = []
        for i, c in enumerate(df.columns):
            name = str(c)
            if name.lower().startswith("unnamed"):
                alt = upper[i] if i < len(upper) else None
                if pd.notna(alt) and str(alt).strip():
                    name = str(alt)
                else:
                    name = f"Kolon_{i+1}"
            new_cols.append(name)
        df.columns = new_cols

    # 4) Tamamen boş kolonları at
    df = df.dropna(axis=1, how="all")

    # 5) Esnek kolon eşlemesi ve temiz DataFrame
    out = _map_contact_columns(df)
    return out

# -----------------------------------------------------------------------------
# Google Drive — Servis Hesabı ile klasörden PDF listeleme + tekil link üretme
# -----------------------------------------------------------------------------
DEFAULT_DRIVE_FOLDER_ID = "1P8CZXb0G0RcNIe89CIyDASCborzmgSYF"  # senin verdiğin klasör

def _drive_available() -> bool:
    try:
        import googleapiclient.discovery  # noqa
        from google.oauth2 import service_account  # noqa
        return True
    except Exception:
        return False

def get_drive_service(json_key_path: str):
    from google.oauth2 import service_account
    from googleapiclient.discovery import build
    scopes = ["https://www.googleapis.com/auth/drive"]
    creds = service_account.Credentials.from_service_account_file(json_key_path, scopes=scopes)
    return build("drive", "v3", credentials=creds, cache_discovery=False)

def list_pdfs_in_folder(service, folder_id: str) -> list[dict]:
    q = f"'{folder_id}' in parents and mimeType='application/pdf' and trashed=false"
    fields = "files(id,name,webViewLink,webContentLink),nextPageToken"
    files = []
    page_token = None
    while True:
        resp = service.files().list(q=q, fields=fields, pageToken=page_token).execute()
        items = resp.get("files", [])
        files.extend(items)
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    return files

def ensure_anyone_viewer_and_get_link(service, file_id: str) -> str:
    """Dosyayı linke sahip herkese 'görüntüleyici' yapar ve webViewLink döner."""
    # link permissions — errors ignore if already exists
    try:
        service.permissions().create(
            fileId=file_id,
            body={"type": "anyone", "role": "reader"},
            fields="id"
        ).execute()
    except Exception:
        pass
    file_meta = service.files().get(fileId=file_id, fields="webViewLink").execute()
    return file_meta.get("webViewLink", f"https://drive.google.com/file/d/{file_id}/view")

def extract_daire_from_filename(name: str) -> Optional[str]:
    """
    A1-1.pdf, A1-001.pdf, A1_12.pdf, A1 12.pdf, A1.012.pdf gibi varyantlardan 'A1-012' üretir.
    """
    base = name.rsplit("/",1)[-1].rsplit("\\",1)[-1]
    base = re.sub(r"\.pdf$", "", base, flags=re.IGNORECASE)
    m = (re.search(r"([A-Za-z]\d)\s*[-_ .]\s*(\d{1,3})", base)
         or re.search(r"([A-Za-z]\d)\s+(\d{1,3})", base)
         or re.search(r"([A-Za-z]\d).*?(\d{3})", base))
    if not m:
        return None
    blok = m.group(1).upper()
    try:
        dno = f"{int(m.group(2)):03d}"
    except:
        dno = m.group(2).zfill(3)
    return f"{blok}-{dno}"

# -----------------------------------------------------------------------------
# UI — 3 Sekme
# -----------------------------------------------------------------------------
st.title("🧾 Vadi Fatura — Böl & Alt Yazı & Apsiyon")

tab_a, tab_b, tab_c, tab_w, tab_r = st.tabs([
    "📄 Böl & Alt Yazı",
    "📊 Apsiyon Gider Doldurucu",
    "📤 WhatsApp Gönderim Hazırlığı",
    "📲 WhatsApp Gönder (Cloud API)",
    "📑 Gelir-Gider Raporu (PDF)"
])
# ---------------- TAB A: Böl & Alt Yazı ----------------
with tab_a:
    pdf_file = st.file_uploader("Fatura PDF dosyasını yükle", type=["pdf"], key="pdf_a")

    if pdf_file:
        st.session_state["pdf_bytes"] = pdf_file.getvalue()

    st.subheader("Alt Yazı Kaynağı")
    t1, t2 = st.tabs(["✍️ Metin alanı", "📄 .docx yükle (opsiyonel)"])

    default_text = (
        "SON ÖDEME TARİHİ     24.10.2025\n\n"
        "Manas paylaşımlarında oturumda olup (0) gelen dairelerin önceki ödediği paylaşım tutarları baz alınarak "
        "bedel yansıtılması; ayrıca İSKİ su sayacının okuduğu harcama tutarı ile site içerisindeki harcama tutarı "
        "arasındaki farkın İSKİ faturasının ödenebilmesi için 152 daireye eşit olarak yansıtılması oya sunuldu. "
        "Oybirliği ile kabul edildi.\n\n"
        "28.02.2017 TARİHLİ TEMSİLCİLER OLAĞAN TOPLANTISINDA ALINAN KARARA İSTİNADEN\n"
        "AÇIKLAMA\n"
        "İski saatinden okunan m3 = 1.319  M3\n"
        "Manas okuması m3= 1.202,5 M3\n"
        "Ortak alan tüketimler m3= 32  M3 \n"
        "Açıkta kalan:  84,5 m3     \n"
        "Su m3 fiyatı 82,09   TL    84,5*82,9 = 7.005,05 TL / 152 = 46,08 TL."
    )

    with t1:
        footer_text = st.text_area("Alt yazı", value=default_text, height=220, key="footer_text")

    with t2:
        if not HAS_DOCX:
            st.info("python-docx yüklü değilse .docx modu devre dışı olur.")
        docx_file = st.file_uploader(".docx yükleyin (opsiyonel)", type=["docx"], key="docx_up")
        if docx_file and HAS_DOCX:
            try:
                d = docx.Document(docx_file)
                paragraphs = [p.text for p in d.paragraphs]
                docx_text = "\n".join(paragraphs).strip()
                if docx_text:
                    footer_text = docx_text
                    st.success("Alt yazı .docx içeriğinden alındı.")
            except Exception as e:
                st.error(f".docx okunamadı: {e}")

    st.subheader("Görünüm Ayarları")
    c1, c2 = st.columns(2)
    with c1:
        font_size = st.slider("🅰️ Yazı Boyutu", 9, 16, 11, key="fs")
        leading   = st.slider("↕️ Satır Aralığı (pt)", 12, 22, 14, key="lead")
    with c2:
        align     = st.radio("Hizalama", ["left", "center"], index=0, key="align", format_func=lambda x: "Sol" if x=="left" else "Orta")
        bottom_m  = st.slider("Alt Marj (pt)", 24, 100, 48, key="bm")
    box_h = st.slider("Alt Yazı Alanı Yüksekliği (pt)", 100, 260, 180, key="bh")
    bold_rules = st.checkbox("Başlıkları otomatik kalın yap (SON ÖDEME, AÇIKLAMA, ...)", value=True, key="boldrules")

    with st.expander("🏷️ Daire numarası etiketi & yeniden adlandırma (opsiyonel)", expanded=False):
        stamp_on = st.checkbox("Daire numarasını köşeye yaz", value=False, key="stamp_on")
        label_tpl = st.text_input("Etiket şablonu", value="Daire: {daire_id}", key="label_tpl")
        c3, c4, c5 = st.columns(3)
        with c3:
            stamp_font_size = st.slider("Etiket punto", 10, 20, 13, key="stamp_fs")
        with c4:
            stamp_pos = st.selectbox("Konum", ["TR", "TL", "BR", "BL"], index=0, key="stamp_pos")
        with c5:
            stamp_bold = st.checkbox("Kalın", value=True, key="stamp_bold")
        c6, c7 = st.columns(2)
        with c6:
            pad_x = st.slider("Köşe yatay boşluk (px)", 0, 80, 20, step=2, key="pad_x")
        with c7:
            pad_y = st.slider("Köşe dikey boşluk (px)", 0, 80, 20, step=2, key="pad_y")
        rename_files = st.checkbox("Bölünmüş dosya adını daireID.pdf yap", value=True, key="rename_files")

    st.subheader("İşlem")
    mode = st.radio(
        "Ne yapmak istersiniz?",
        ["Sadece sayfalara böl", "Sadece alt yazı uygula (tek PDF)", "Alt yazı uygula + sayfalara böl (ZIP)"],
        index=2,
        key="mode"
    )
    go = st.button("🚀 Başlat", key="go_a")

    if go:
        if not pdf_file:
            st.warning("Lütfen önce bir PDF yükleyin.")
            st.stop()

        src = pdf_file.read()

        if mode == "Sadece sayfalara böl":
            pages = split_pdf(src)
            with io.BytesIO() as zbuf:
                with zipfile.ZipFile(zbuf, "w", zipfile.ZIP_DEFLATED) as z:
                    for name, data in pages:
                        z.writestr(name, data)
                st.download_button("📥 Bölünmüş sayfalar (ZIP)", zbuf.getvalue(), file_name="bolunmus_sayfalar.zip")

        elif mode == "Sadece alt yazı uygula (tek PDF)":
            stamped = add_footer_to_pdf(
                src,
                footer_text=footer_text,
                font_size=font_size,
                leading=leading,
                align=align,
                bottom_margin=bottom_m,
                box_height=box_h,
                bold_rules=bold_rules,
            )
            st.download_button("📥 Alt yazılı PDF", stamped, file_name="alt_yazili.pdf")

        else:
            footer_kwargs = dict(
                footer_text=footer_text,
                font_size=font_size,
                leading=leading,
                align=align,
                bottom_margin=bottom_m,
                box_height=box_h,
                bold_rules=bold_rules,
            )
            stamp_opts = dict(
                font_size=stamp_font_size,
                bold=stamp_bold,
                position=stamp_pos,
                pad_x=pad_x,
                pad_y=pad_y,
            )
            pages = add_footer_and_stamp_per_page(
                src_bytes=src,
                footer_kwargs=footer_kwargs,
                stamp_on=stamp_on,
                label_tpl=label_tpl,
                stamp_opts=stamp_opts,
                rename_files=rename_files,
            )
            with io.BytesIO() as zbuf:
                with zipfile.ZipFile(zbuf, "w", zipfile.ZIP_DEFLATED) as z:
                    for name, data in pages:
                        z.writestr(name, data)
                st.download_button("📥 Alt yazılı & bölünmüş (ZIP)", zbuf.getvalue(), file_name="alt_yazili_bolunmus.zip")

# ---------------- TAB B: Apsiyon Gider Doldurucu ----------------
with tab_b:
    st.subheader("📊 Apsiyon Gider Doldurucu")
    apsiyon_file = st.file_uploader("Apsiyon 'boş şablon' Excel dosyasını yükle (.xlsx)", type=["xlsx"], key="apsiyon_up")

    colM1, colM2 = st.columns(2)
    with colM1:
        aps_mode = st.radio(
    "Doldurma Şekli",
    [
        "Seçenek 1 (G1=Sıcak Su, G2=Su, G3=Isıtma)",
        "Seçenek 2 (G1=Toplam, G2/G3 boş)",
        "Seçenek 3 (G1=Sıcak Su)",
        "Seçenek 4 (G1=Su)",
        "Seçenek 5 (G1=Isıtma)"
    ],
    index=0,
    key="aps_mode"
)
    with colM2:
        exp1 = st.text_input("Gider1 Açıklaması", value="Sıcak Su", key="aps_exp1")
        exp2 = st.text_input("Gider2 Açıklaması", value="Soğuk Su", key="aps_exp2")
        exp3 = st.text_input("Gider3 Açıklaması", value="Isıtma", key="aps_exp3")

    go_fill = st.button("📥 PDF’ten tutarları çek ve Excel’e yaz", key="go_fill")

    if go_fill:
        pdf_bytes = st.session_state.get("pdf_bytes")
        if not pdf_bytes:
            st.warning("Önce A sekmesinde fatura PDF’sini yükleyin (aynı PDF).")
            st.stop()
        if not apsiyon_file:
            st.warning("Apsiyon Excel şablonunu yükleyin.")
            st.stop()

        totals_map = parse_manas_pdf_totals(pdf_bytes)
        if not totals_map:
            st.error("PDF’ten tutar okunamadı. (Daire başlıkları veya tutarlar bulunamadı)")
            st.stop()

        try:
            df_aps = load_apsiyon_template(apsiyon_file.read())
        except Exception as e:
            st.error(f"Excel okunamadı: {e}")
            st.stop()

        df_out = fill_expenses_to_apsiyon(df_aps, totals_map, aps_mode, exp1, exp2, exp3)
        out_bytes = export_excel_bytes(df_out)
        st.success("Excel dolduruldu.")
        st.download_button(
            "📥 Doldurulmuş Apsiyon Excel",
            out_bytes,
            file_name="Apsiyon_Doldurulmus.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            key="dl_aps"
        )
# -----------------------------------------------------------------------------
# Gelir-Gider PDF Okuyucu (genel tablo)  —  PDF metninden kalem/tutar çıkarır
# -----------------------------------------------------------------------------
AMT_RX = re.compile(r"(?<!\d)(\d{1,3}(?:\.\d{3})*,\d{2})(?!\d)")  # 1.234,56

# Türkçe kalem adlarını normalize et (eşleştirmeyi sağlamlaştırır)
def _norm_tr_token(s: str) -> str:
    s = _normalize_tr(s).strip()
    s = s.replace("  ", " ")
    return s

# İsim eşleştirme için beklenen kalem şablonları (anahtar → görülebilecek varyantlar)
_GG_PATTERNS = {
    "AİDAT GELİRLERİ":           [r"AIDAT GELIR", r"AIDAT GELIRLERI"],
    "SU/ISINMA GELİRİ":          [r"SU VE ISINMA .* GELIR", r"SU\S* SICAK SU GELIRI", r"SU VE SICAK SU GELIRI"],
    "GECIKME TAZMINATI":         [r"GECIKME TAZMINATI", r"GECIKME .* TAHSIL"],
    "OGS SATIŞ GELİRİ":          [r"OGS SATIS GELIR"],
    "BANKA FAİZ GELİRİ":         [r"BANKA FAIZ GELIR"],
    "REKLAM/LUNCH KIRALAMA":     [r"REKLAM GELIR", r"LUNCH .* KIRALAMA GELIR"],
    "DÖNEM GİDER FAZLASI":       [r"DONEM GIDER FAZLASI"],

    "SU/ISINMA GİDERİ":          [r"SU VE ISINMA .* GIDER", r"SU\+DOGALGAZ"],
    "ORTAK ALAN ELEKTRIK":       [r"ORTAK ALAN ELEKTRIK"],
    "DOGALGAZ ORTAK ALAN":       [r"DOGALGAZ ORTAK ALAN"],
    "TELEFON/ULASIM/BANKA/KIRT.": [r"TELEFON.*ULASIM.*KIRTASIYE.*BANKA", r"TELEFON,ULASIM,NAKLIYE,KIRTASIYE,BANKA"],
    "IS GUVENLIGI":              [r"IS GUVENLIG"],
    "APSIYON YAZILIM":           [r"APSIYON YAZILIM PROGRAM"],
    "HIDRAFOR/LOGAR/SU MOTORLARI": [r"HIDRAFOR LOGAR SU MOTORLARI"],
    "SITE ICI ILACLAMA":         [r"SITE ICI ILACLAMA"],
    "TEMIZLIK MALZ. / IS KIYAFET": [r"TEMIZLIK MALZEMELERI.*IS KIYAFET"],
    "BAHCE/PEYZAJ":              [r"BAHCE BAKIM .* PEYZAJ"],
    "AVUKAT/HUKUKI DANISM.":     [r"HUKUKI DANISMANLIK .* AVUKAT"],
    "MALI MUSAVIRLIK/MUHASEBE":  [r"MALI MUSAVIRLIK .* MUHASEBE"],
    "TEMSİL/AGIRLAMA":           [r"TEMSIL .* AGIRLAMA"],
    "ASANSOR PERIYODIK BAKIM":   [r"ASANSOR PERIYODIK BAKIM"],
    "HAVUZ BAKIM/KIMYASAL":      [r"HAVUZ BAKIM.* KIMYASAL"],
    "DEMIRBAS/ONGORULEMEYEN":    [r"DEMIRBAS .* ONGORU"],
    "PERSONEL NET UCRETLER":     [r"PERSONEL NET UCRET"],
    "PERSONEL SSK+MUHTASAR":     [r"PERSONEL SSK .* MUHTASAR"],
    "ISKI ORTAK ALAN":           [r"ISKI ORTAK ALAN"],
}

# Basit eşleştirme yardımcısı
def _match_key(line_norm: str) -> Optional[str]:
    for key, pats in _GG_PATTERNS.items():
        for p in pats:
            if re.search(p, line_norm):
                return key
    return None

def parse_income_expense_pdf(pdf_bytes: bytes) -> pd.DataFrame:
    """
    PDF içinden satır satır metni gezip 'Kalem' ve 'Tutar' yakalar, 'Tür' (Gelir/Gider) atar.
    Toplamlar ayrıca hesaplanır. Dönüş: DataFrame[Kalem, Tutar, Tür]
    """
    reader = PdfReader(io.BytesIO(pdf_bytes))
    rows = []
    current_section = None  # "GELIR" | "GIDER" | None

    for page in reader.pages:
        raw = page.extract_text() or ""
        # Bölümleri tahminlemek için büyük başlıkları ara
        norm_lines = [_norm_tr_token(ln) for ln in raw.splitlines() if ln.strip()]
        for ln_norm in norm_lines:
            # Bölüm geçiş ipuçları
            if re.search(r"\bGELIR(LER)?\b", ln_norm):
                current_section = "GELIR"
            elif re.search(r"\bGIDER(LER)?\b", ln_norm):
                current_section = "GIDER"

            # Tutar ara
            m_amt = AMT_RX.search(ln_norm)
            if not m_amt:
                continue
            amt = _to_float_tr(m_amt.group(1))

            # Kalem adı
            key = _match_key(ln_norm)
            if not key:
                # Toplam satırları ve notları atla
                if "TOPLAM" in ln_norm or "GENEL TOPLAM" in ln_norm:
                    continue
                # Bulamadıysak ham satırı da ekleyelim (takip/tuning için)
                key = ln_norm[:80]

            tur = "Gelir" if current_section == "GELIR" else ("Gider" if current_section == "GIDER" else "Bilinmiyor")
            rows.append({"Kalem": key, "Tutar": amt, "Tür": tur})

    if not rows:
        return pd.DataFrame(columns=["Kalem","Tutar","Tür"])

    df = pd.DataFrame(rows)
    # Aynı başlıklar toplanır
    df = df.groupby(["Kalem","Tür"], as_index=False)["Tutar"].sum()
    # Gelir/Gider sıralama
    df["Tür"] = pd.Categorical(df["Tür"], categories=["Gelir","Gider","Bilinmiyor"], ordered=True)
    df = df.sort_values(["Tür","Kalem"]).reset_index(drop=True)
    return df

def export_income_expense_excel(df: pd.DataFrame, filename: str = "GelirGider_Parsed.xlsx") -> bytes:
    from io import BytesIO
    bio = BytesIO()
    # Özet sayfa + detay sayfa
    with pd.ExcelWriter(bio, engine="openpyxl") as xw:
        df.to_excel(xw, index=False, sheet_name="Detay")
        # Pivot özet
        piv = df.pivot_table(index="Tür", values="Tutar", aggfunc="sum").reset_index()
        piv.to_excel(xw, index=False, sheet_name="Özet")
    return bio.getvalue()
# ---------------- TAB C: WhatsApp Gönderim Hazırlığı ----------------
with tab_c:
    st.markdown("""
    <div style='background-color:#25D366;padding:10px 16px;border-radius:10px;display:flex;align-items:center;gap:10px;color:white;margin-bottom:15px;'>
      <img src='https://upload.wikimedia.org/wikipedia/commons/6/6b/WhatsApp.svg' width='28'>
      <h3 style='margin:0;'>WhatsApp Gönderim Hazırlığı</h3>
    </div>
    """, unsafe_allow_html=True)

    wa_tab1, wa_tab2 = st.tabs([
        "ZIP + (opsiyonel) Base URL",
        "Google Drive klasöründen link üret"
    ])

    # --- Yol 1: ZIP + Base URL (mevcut akış) ---
    with wa_tab1:
        up1, up2 = st.columns([1,1], vertical_alignment="top")
        with up1:
            st.markdown("**Adım 1:** Bölünmüş PDF’lerin olduğu **ZIP**’i yükle (dosya adları `A1-001.pdf` gibi).")
            zip_up = st.file_uploader("Bölünmüş PDF ZIP", type=["zip"], key="wa_zip", label_visibility="collapsed")
        with up2:
            st.markdown("**Adım 2:** Güncel **Rehber** dosyasını yükle (Apsiyon ham Excel/CSV).")
            rehber_up = st.file_uploader("Rehber (XLSX/CSV)", type=["xlsx","csv"], key="wa_rehber", label_visibility="collapsed")

        with st.expander("🔗 Opsiyonel link üretimi (base URL)", expanded=False):
            base_url = st.text_input("Base URL (örn: https://cdn.site.com/faturalar/ )", value="", key="wa_base")

        ctop1, ctop2 = st.columns([1,3], vertical_alignment="center")
        with ctop1:
            go_btn = st.button("📑 Eşleştir ve CSV oluştur", use_container_width=True, key="wa_go")
        with ctop2:
            st.caption("Butona bastıktan sonra aşağıda önizleme ve indirme butonu görünür.")

        if go_btn:
            if not zip_up:
                st.warning("Önce ZIP yükleyin."); st.stop()
            if not rehber_up:
                st.warning("Önce Rehber dosyası yükleyin."); st.stop()

            # ZIP → PDF listesi + DaireID çıkar
            try:
                zf = zipfile.ZipFile(zip_up)
                pdf_rows = []
                for info in zf.infolist():
                    if info.is_dir() or (not info.filename.lower().endswith(".pdf")):
                        continue
                    base = info.filename.rsplit("/",1)[-1].rsplit("\\",1)[-1]
                    m = (re.search(r"([A-Za-z]\d)\s*[-_]\s*(\d{1,3})", base)
                         or re.search(r"([A-Za-z]\d)\s+(\d{1,3})", base)
                         or re.search(r"([A-Za-z]\d).*?(\d{3})", base))
                    daire_id = None
                    if m:
                        try:
                            daire_id = f"{m.group(1).upper()}-{int(m.group(2)):03d}"
                        except:
                            daire_id = f"{m.group(1).upper()}-{m.group(2)}"
                    pdf_rows.append({"file_name": base, "DaireID": daire_id})
                pdf_df = pd.DataFrame(pdf_rows)
            except Exception as e:
                st.error(f"ZIP okunamadı: {e}"); st.stop()

            if pdf_df.empty:
                st.error("ZIP’te PDF bulunamadı."); st.stop()

            # Rehber oku
            try:
                rehber_df = load_contacts_any(rehber_up.read(), rehber_up.name)
            except Exception as e:
                st.error(f"Rehber okunamadı / eşlenemedi: {e}"); st.stop()

            # Eşleştirme
            merged = pdf_df.merge(rehber_df[["DaireID","Telefon","Ad Soyad / Unvan"]], on="DaireID", how="left")
            merged["file_url"] = merged["file_name"].apply(
                lambda fn: (base_url.rstrip("/") + "/" + fn) if base_url and base_url.strip() else ""
            )

            a1, a2, a3 = st.columns(3)
            with a1: st.metric("Toplam kayıt", len(merged))
            with a2: st.metric("DaireID bulunamadı", int(merged["DaireID"].isna().sum()))
            with a3: st.metric("Telefon eksik", int((merged["Telefon"].isna() | (merged["Telefon"]=="")).sum()))

            st.markdown("**Eşleştirme Önizleme**")
            st.dataframe(merged.rename(columns={"Telefon":"phone", "Ad Soyad / Unvan":"name"}),
                         use_container_width=True, height=600)

            out_csv = merged.rename(columns={
                "Telefon": "phone",
                "Ad Soyad / Unvan": "name",
                "DaireID": "daire_id",
                "file_name": "file_name",
                "file_url": "file_url",
            })[["phone","name","daire_id","file_name","file_url"]]
            b_csv = out_csv.to_csv(index=False).encode("utf-8-sig")
            st.download_button("📥 WhatsApp_Recipients.csv (UTF-8, BOM)", b_csv,
                               file_name="WhatsApp_Recipients.csv", mime="text/csv", use_container_width=True, key="dl_csv")

    # --- Yol 2: Google Drive klasöründen PDF listele + tekil link üret ---
with wa_tab2:
    st.markdown("**Bu yöntemde ZIP gerekmez.** PDF’leri Google Drive’daki klasöre koyman yeterli.")
    if not _GDRIVE_OK:
        st.error("Google Drive kütüphaneleri yüklü değil. Terminalde şunu kur:\n\npip install google-api-python-client google-auth google-auth-oauthlib")
    else:
        # JSON dosya yolu GİTTİ. Secrets kullanıyoruz.
        folder_id = st.text_input(
            "Drive Folder ID",
            value=DEFAULT_DRIVE_FOLDER_ID,
            help="Klasör ID: 1P8CZXb0G0RcNIe89CIyDASCborzmgSYF"
        )

        # Rehber yükleme (Apsiyon ham dosyası)
        rehber_up2 = st.file_uploader(
            "Rehber (XLSX/CSV) — Apsiyon ham dosya",
            type=["xlsx","csv"], key="wa_rehber2"
        )

        link_mode = st.radio(
            "Link tipi",
            ["Doğrudan indirme (önerilir)", "Görüntüleme linki (Drive görünümü)"],
            horizontal=True
        )

        drive_go = st.button("🗂️ Drive’dan PDF’leri çek, eşleştir ve CSV üret", use_container_width=True)

        if drive_go:
            if not folder_id.strip():
                st.error("Folder ID boş olamaz."); st.stop()
            if not rehber_up2:
                st.error("Rehber dosyası yükleyin."); st.stop()

            # 1) Drive servisine bağlan (Secrets)
            try:
                service = get_drive_service_from_secrets()
            except Exception as e:
                st.error(f"Drive servisine bağlanılamadı: {e}")
                st.stop()

            # 2) Klasördeki PDF'leri çek
            try:
                gfiles = list_pdfs_in_folder(service, folder_id.strip())
            except Exception as e:
                st.error(f"Klasör listelenemedi: {e}")
                st.stop()

            if not gfiles:
                st.warning("Klasörde PDF bulunamadı."); st.stop()

            # 3) PDF adlarından DaireID tahmini (A1-001.pdf gibi)
            import re, pandas as pd
            pdf_rows = []
            for f in gfiles:
                base = f.get("name","")
                m = (re.search(r"([A-Za-z]\d)\s*[-_]\s*(\d{1,3})", base)
                     or re.search(r"([A-Za-z]\d)\s+(\d{1,3})", base)
                     or re.search(r"([A-Za-z]\d).*?(\d{3})", base))
                daire_id = None
                if m:
                    try:
                        daire_id = f"{m.group(1).upper()}-{int(m.group(2)):03d}"
                    except:
                        daire_id = f"{m.group(1).upper()}-{m.group(2)}"
                pdf_rows.append({"file_name": base, "DaireID": daire_id, "file_id": f["id"]})
            pdf_df = pd.DataFrame(pdf_rows)

            # 4) Rehberi oku
            try:
                rehber_df = load_contacts_any(rehber_up2.read(), rehber_up2.name)
            except Exception as e:
                st.error(f"Rehber okunamadı / eşlenemedi: {e}"); st.stop()

            # 5) Eşleştir
            merged = pdf_df.merge(
                rehber_df[["DaireID", "Telefon", "Ad Soyad / Unvan"]],
                on="DaireID",
                how="left"
            )

            # 6) Dosyaları "linke sahip olan görüntüleyebilir" yap + link üret
            # (sadece dosya bazında; klasör listing açılmaz)
            link_kind = "download" if link_mode.startswith("Doğrudan") else "view"

            st.write("🔓 Dosyalar paylaşıma açılıyor ve linkler oluşturuluyor (dosya bazında)...")
            for i, row in merged.iterrows():
                fid = row.get("file_id")
                if not fid:
                    continue
                try:
                    ensure_anyone_with_link_permission(service, fid)
                except Exception:
                    pass
                merged.at[i, "file_url"] = build_direct_file_link(fid, link_kind)

            # 7) Önizleme + CSV
            a1, a2, a3 = st.columns(3)
            with a1: st.metric("Toplam kayıt", len(merged))
            with a2: st.metric("DaireID bulunamadı", int(merged["DaireID"].isna().sum()))
            with a3: st.metric("Telefon eksik", int((merged["Telefon"].isna() | (merged["Telefon"]=="")).sum()))

            st.markdown("**Eşleştirme Önizleme**")
            st.dataframe(
                merged.rename(columns={"Telefon":"phone", "Ad Soyad / Unvan":"name"}),
                use_container_width=True, height=600
            )

            out_csv = merged.rename(columns={
                "Telefon": "phone",
                "Ad Soyad / Unvan": "name",
                "DaireID": "daire_id",
                "file_name": "file_name",
                "file_url": "file_url",
            })[["phone","name","daire_id","file_name","file_url"]]
            b_csv = out_csv.to_csv(index=False).encode("utf-8-sig")
            st.download_button(
                "📥 WhatsApp_Recipients.csv (Drive linkli)",
                b_csv,
                file_name="WhatsApp_Recipients.csv",
                mime="text/csv",
                use_container_width=True
            )

            with st.expander("📨 Örnek mesaj gövdesi", expanded=False):
                st.code(
                    "Merhaba {name},\n"
                    "{daire_id} numaralı dairenizin aylık bildirimi hazırdır.\n"
                    "Dosyayı butondan görüntüleyebilirsiniz.\n",
                    language="text"
                )
# ---------------- TAB W: WhatsApp Gönder (Cloud API) ----------------
with tab_w:
    st.markdown("### 📲 WhatsApp Gönder (Meta Cloud API)")

    st.info("İlk mesajı **şablon** ile başlatmalısın. Sonrasında 24 saat içinde serbest metin / belge gönderebilirsin.")

    colK1, colK2 = st.columns(2)
    with colK1:
        csv_up = st.file_uploader("WhatsApp_Recipients.csv yükle", type=["csv"], key="wa_send_csv")
    with colK2:
        preview_btn = st.button("Önizle", use_container_width=True, key="wa_preview")

    # API Kimlikleri (secrets varsa otomatik doldur)
    st.markdown("#### Cloud API Ayarları")
    default_token = st.secrets.get("whatsapp", {}).get("token", "")
    default_phone_id = st.secrets.get("whatsapp", {}).get("phone_number_id", "")
    colA1, colA2 = st.columns(2)
    with colA1:
        wa_token = st.text_input("Access Token", value=default_token, type="password", help="Meta for Developers → WhatsApp → System User token (mümkünse kalıcı).")
    with colA2:
        phone_number_id = st.text_input("Phone Number ID", value=default_phone_id, help="WABA içindeki WhatsApp numaranızın ID’si")

    st.markdown("#### Şablonla Başlat (zorunlu ilk mesaj)")
    colT1, colT2, colT3 = st.columns(3)
    with colT1:
        template_name = st.text_input("Template adı", value="fatura_bildirimi")  # Örn: fatura_bildirimi
    with colT2:
        template_lang = st.text_input("Dil (BCP-47)", value="tr")  # tr, tr_TR gibi
    with colT3:
        header_document = st.checkbox("Şablon header'ı belge (document) kullansın", value=False,
                                      help="Şablonunuz 'HEADER: DOCUMENT' içeriyorsa işaretleyin. PDF linkini header’a koyacağız.")

    st.caption("Örnek şablon gövdesi (Meta'da oluşturup onaylat):\n"
               "Merhaba {{1}},\n{{2}} dairenizin bildirimi hazırdır.\nDosya: {{3}}")

    st.markdown("#### 24 saat PENCERE AÇILDIKTAN SONRA opsiyonel mesaj")
    colF1, colF2 = st.columns(2)
    with colF1:
        send_followup_text = st.checkbox("Ardından serbest metin gönder", value=False)
        followup_text = st.text_area("Serbest metin", value="Merhaba {name},\n{daire_id} dairenizin PDF bildirimi ektedir:\n{file_url}")
    with colF2:
        send_document = st.checkbox("Ardından PDF'yi belge (document) olarak gönder", value=True,
                                    help="file_url doğrudan indirilebilir/görüntülenebilir olmalı.")

    go_send = st.button("🚀 Gönderimi Başlat", use_container_width=True, key="wa_send")

    import time, requests
    import pandas as pd

    def _ok_number(s: str) -> str:
        s = str(s or "").strip()
        # "+90..." formatı bekliyoruz; yoksa basit normalize
        s = s.replace(" ", "")
        if s.startswith("05") and len(s) == 11:
            return "+90" + s[1:]
        if s.startswith("5") and len(s) == 10:
            return "+90" + s
        if s.startswith("0") and len(s) in (10,11):
            return "+90" + s[1:]
        return s

    def send_template(access_token: str, phone_id: str, to: str, t_name: str, lang: str, name: str, daire_id: str, file_url: str, header_doc=False):
        url = f"https://graph.facebook.com/v20.0/{phone_id}/messages"
        headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}

        components = []
        # BODY vars
        components.append({
            "type": "body",
            "parameters": [
                {"type": "text", "text": name or ""},
                {"type": "text", "text": daire_id or ""},
                {"type": "text", "text": file_url or ""},
            ]
        })
        # HEADER document varsa (şablonunuzda HEADER: DOCUMENT tanımlı olmalı)
        if header_doc and file_url:
            components.insert(0, {
                "type": "header",
                "parameters": [
                    {"type": "document", "document": {"link": file_url, "filename": f"{daire_id or 'Dosya'}.pdf"}}
                ]
            })

        payload = {
            "messaging_product": "whatsapp",
            "to": to,
            "type": "template",
            "template": {
                "name": t_name,
                "language": {"code": lang},
                "components": components
            }
        }
        r = requests.post(url, headers=headers, json=payload, timeout=30)
        return r

    def send_text(access_token: str, phone_id: str, to: str, text: str):
        url = f"https://graph.facebook.com/v20.0/{phone_id}/messages"
        headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}
        payload = {
            "messaging_product": "whatsapp",
            "to": to,
            "type": "text",
            "text": {"preview_url": True, "body": text}
        }
        r = requests.post(url, headers=headers, json=payload, timeout=30)
        return r

    def send_document_msg(access_token: str, phone_id: str, to: str, file_url: str, caption: str):
        url = f"https://graph.facebook.com/v20.0/{phone_id}/messages"
        headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}
        payload = {
            "messaging_product": "whatsapp",
            "to": to,
            "type": "document",
            "document": {"link": file_url, "caption": caption}
        }
        r = requests.post(url, headers=headers, json=payload, timeout=30)
        return r

    if preview_btn and csv_up:
        df = pd.read_csv(csv_up, dtype=str).fillna("")
        st.dataframe(df.head(50), use_container_width=True)
        st.success(f"{len(df)} alıcı yüklendi.")

    if go_send:
        # doğrulamalar
        if not csv_up:
            st.error("Önce CSV yükleyin."); st.stop()
        if not wa_token or not phone_number_id:
            st.error("Access Token ve Phone Number ID gerekir."); st.stop()
        df = pd.read_csv(csv_up, dtype=str).fillna("")
        if not {"phone","name","daire_id","file_url"}.issubset(set(df.columns)):
            st.error("CSV kolonları eksik. Gerekli: phone, name, daire_id, file_url")
            st.stop()

        send_results = []
        progress = st.progress(0)
        total = len(df)
        success_cnt = 0
        fail_cnt = 0

        for i, row in df.iterrows():
            to = _ok_number(row.get("phone", ""))
            name = row.get("name","")
            did  = row.get("daire_id","")
            furl = row.get("file_url","")

            # 1) Şablonla başlat
            try:
                r1 = send_template(wa_token, phone_number_id, to, template_name, template_lang, name, did, furl, header_doc=header_document)
                if r1.ok:
                    success = True
                    info = "template OK"
                else:
                    success = False
                    info = f"template ERR {r1.status_code}: {r1.text}"
            except Exception as e:
                success = False
                info = f"template EXC: {e}"
            send_results.append({"to": to, "step": "template", "ok": success, "info": info})
            success_cnt += 1 if success else 0
            fail_cnt    += 0 if success else 1

            # 2) Pencere açıksa follow-up (opsiyonel)
            if success:
                # küçük bekleme (rate limit / ordering)
                time.sleep(0.4)
                if send_followup_text and followup_text:
                    try:
                        msg = followup_text.format(name=name, daire_id=did, file_url=furl)
                    except Exception:
                        msg = followup_text
                    r2 = send_text(wa_token, phone_number_id, to, msg)
                    send_results.append({"to": to, "step": "text", "ok": r2.ok, "info": ("" if r2.ok else f"{r2.status_code}: {r2.text}")})
                    time.sleep(0.3)
                if send_document and furl:
                    cap = f"{did} bildirimi"
                    r3 = send_document_msg(wa_token, phone_number_id, to, furl, cap)
                    send_results.append({"to": to, "step": "document", "ok": r3.ok, "info": ("" if r3.ok else f"{r3.status_code}: {r3.text}")})
                    time.sleep(0.3)

            progress.progress((i+1)/total)

        st.success(f"Gönderim bitti. Başarılı: {success_cnt}, Hatalı: {fail_cnt}")
        st.dataframe(pd.DataFrame(send_results), use_container_width=True)
        # ---------------- TAB GG: Gelir-Gider PDF Dönüştürücü ----------------
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm

def _grab_amount(block_text: str, label: str) -> float:
    """
    Verilen metin bloğunda label'dan sonraki ilk TL tutarı yakalar.
    """
    # nokta binlik, virgül ondalık destekli sayı
    pat = rf"{re.escape(label)}[^\d\-]*([0-9\.\,\-]+)"
    m = re.search(pat, block_text, flags=re.IGNORECASE)
    return _to_float_tr(m.group(1)) if m else 0.0

def _find_lines(block_text: str, labels: list[str]) -> list[tuple[str, float]]:
    out = []
    for lab in labels:
        val = _grab_amount(block_text, lab)
        if val != 0.0:
            out.append((lab, val))
    return out

def parse_gg_from_pdf(pdf_bytes: bytes) -> dict:
    """
    Apsiyon 'Özet Gelir-Gider' PDF'inden gider, gelir, toplamlara ilişkin sayıları çeker.
    Çıkış:
      {
        "giderler": [(ad, tutar), ...],
        "gelirler": [(ad, tutar), ...],
        "toplam_gider": float,
        "toplam_gelir": float,
        "donem_farki": float,
        "faaliyetler": [satırlar...]
      }
    """
    rdr = PdfReader(io.BytesIO(pdf_bytes))
    raw = "\n".join([(p.extract_text() or "") for p in rdr.pages])
    norm = _normalize_tr(raw)

    # Sıklıkla görülen kalem adlarını hedefleyelim (metne göre esnek)
    gider_lbls = [
        "SU VE ISINMA GIDERI",
        "PERSONEL SSK PRIM +MUHTASAR GIDERLERI",
        "SITE ICI ILACLAMA GIDERI",
        "PERSONEL MAAS GIDERI",
        "ORTAK ALAN ELEKTRIK",
        "TELEFON, ULASIM, NAKLIYE, KIRTASIYE, BANKA PROVIZYON GIDERLERI",
        "IS GUVENLIGI UZMANI",
        "SU+DOGALGAZ",
        "HIDRAFOR LOGAR SU MOTORLARI BAKIM TAMIRAT GID",
        "TEMIZLIK MALZEMELERI IS KIYAFETLERI",
        "HUKUKI DANISMANLIK VE AVUKATLIK",
        "MALI MUSAVIRLIK VE MUHASEBE ISLEMLERI",
        "DEMIRBAS VE ONGORULEMEYEN GIDERLER",
        "ASANSOR PERIYODIK BAKIM",
        "BAHCE BAKIM VE PEYZAJ GIDERI",
        "TEMSIL AGIRLAMA GIDERI",
        "HAVUZ BAKIMI VE KIMYASAL GIDERLER",
        "APSIYON YAZILIM PROGRAM GIDERI",
    ]
    gelir_lbls = [
        "AIDAT GELIRI",
        "SU, SICAK SU  GELIRI",
        "GECIKME TAZMINATI TAHAKKUKU",
        "OGS SATIS GELIRI",
        "BANKA FAIZ GELIRI",
        "LUNCH ALANI KIRALAMA GELIRI",
    ]

    # 'GIDERLER' ve 'GELIRLER' bloklarını ayırmaya çalış
    # (başlıklar arası kesit al)
    def _slice_between(text, start_kw, end_kw):
        s = text.find(start_kw)
        if s == -1: return text
        e = text.find(end_kw, s+len(start_kw)) if end_kw else -1
        return text[s:e] if e != -1 else text[s:]

    gider_block = _slice_between(norm, "GIDERLER", "GELIRLER")
    gelir_block = _slice_between(norm, "GELIRLER", "ALACAKLARIMIZ")

    giderler = _find_lines(gider_block, gider_lbls)
    gelirler = _find_lines(gelir_block, gelir_lbls)

    toplam_gider = _grab_amount(norm, "GIDER TOPLAMI")
    toplam_gelir = _grab_amount(norm, "GELIR TOPLAMI")

    # DÖNEM FARKI (fazlası/eksigi)
    donem_farki = 0.0
    for key in ["DONEM GIDER FAZLASI", "DONEM GELIR FAZLASI", "DONEM FARKI"]:
        v = _grab_amount(norm, key)
        if v != 0.0:
            # metinde eksi olabiliyor, işareti koruyalım
            sign_m = re.search(rf"{key}[^\d\-]*([\-])?[0-9\.\,]+", norm)
            if sign_m and sign_m.group(1) == "-":
                v = -abs(v)
            donem_farki = v
            break

    # "2025 EYLÜL AYI FAALİYETLERİMİZ" altındaki madde satırlarını yakala
    faaliyetler = []
    fx = re.search(r"FAALIYETLERIMIZ(.+)$", norm, flags=re.DOTALL)
    if fx:
        tail = fx.group(1)
        for line in tail.split("\n"):
            line = line.strip()
            if re.search(r"\d{2}\.\d{2}\.\d{4}", line):
                faaliyetler.append(line)

    return {
        "giderler": giderler,
        "gelirler": gelirler,
        "toplam_gider": toplam_gider,
        "toplam_gelir": toplam_gelir,
        "donem_farki": donem_farki,
        "faaliyetler": faaliyetler,
    }

def build_gg_pdf(data: dict, site_adi: str, baslik: str) -> bytes:
    """
    Çıkan veriyi rapor PDF'ine döker (A4 yatay iki kolon).
    """
    page_w, page_h = A4  # (595x842 pt)
    # Yatay yerleşim için sayfayı döndürmeye gerek yok; iki sütun tasarlayalım.
    margin = 18*mm
    col_gap = 12*mm
    col_w = (page_w - 2*margin - col_gap) / 2.0
    y = page_h - margin

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)

    # Üst başlık
    c.setFont("NotoSans-Bold", 14)
    c.drawString(margin, y, f"{site_adi.upper()} {baslik.upper()}")
    y -= 10*mm

    # Sol kolon: GİDERLER
    xL = margin
    xR = margin + col_w + col_gap

    def _draw_table(x, y_top, title, rows):
        c.setFont("NotoSans-Bold", 11)
        c.drawString(x, y_top, title)
        y = y_top - 6*mm
        c.setFont("NotoSans-Regular", 10)
        for name, val in rows:
            txt = name.title()
            c.drawString(x, y, txt)
            c.drawRightString(x + col_w, y, f"{val:,.2f} TL".replace(",", "X").replace(".", ",").replace("X", "."))
            y -= 5.2*mm
            if y < 30*mm:
                c.showPage(); y = page_h - margin; c.setFont("NotoSans-Bold", 11); c.drawString(x, y, title); y -= 6*mm; c.setFont("NotoSans-Regular", 10)
        return y

    yL = _draw_table(xL, y, "GİDERLER", data.get("giderler", []))
    # Gider toplamı
    c.setFont("NotoSans-Bold", 10)
    c.drawString(xL, yL-2*mm, "GİDER TOPLAMI")
    c.drawRightString(xL + col_w, yL-2*mm, f"{data.get('toplam_gider',0.0):,.2f} TL".replace(",", "X").replace(".", ",").replace("X", "."))
    yL -= 10*mm

    # Sağ kolon: GELİRLER
    yR = _draw_table(xR, y, "GELİRLER", data.get("gelirler", []))
    c.setFont("NotoSans-Bold", 10)
    c.drawString(xR, yR-2*mm, "GELİR TOPLAMI")
    c.drawRightString(xR + col_w, yR-2*mm, f"{data.get('toplam_gelir',0.0):,.2f} TL".replace(",", "X").replace(".", ",").replace("X", "."))
    yR -= 8*mm

    # Dönem farkı (vurgulu)
    dfark = data.get("donem_farki", 0.0)
    c.setFont("NotoSans-Bold", 10)
    c.drawString(xR, yR-2*mm, "DÖNEM FARKI")
    c.setFillColorRGB(1, 1, 0.7)  # hafif vurgulu kutu
    c.rect(xR + col_w - 45*mm, yR-5*mm, 45*mm, 7*mm, fill=1, stroke=0)
    c.setFillColorRGB(0,0,0)
    c.drawRightString(xR + col_w - 2*mm, yR-2*mm, f"{dfark:,.2f} TL".replace(",", "X").replace(".", ",").replace("X", "."))
    yR -= 12*mm

    # Alt: Faaliyetler
    y_end = min(yL, yR)
    if y_end < 50*mm:
        c.showPage()
        y_end = page_h - margin

    acts = data.get("faaliyetler", [])
    if acts:
        c.setFont("NotoSans-Bold", 11)
        c.drawString(margin, y_end, "AY İÇİ FAALİYETLER")
        c.setFont("NotoSans-Regular", 10)
        y = y_end - 6*mm
        for line in acts:
            for wrapped in wrap_by_width(line, "NotoSans-Regular", 10, page_w - 2*margin):
                c.drawString(margin, y, wrapped)
                y -= 5*mm
                if y < 25*mm:
                    c.showPage(); y = page_h - margin; c.setFont("NotoSans-Regular", 10)

    c.showPage()
    c.save()
    buf.seek(0)
    return buf.getvalue()

with st.expander("📑 Gelir-Gider PDF Dönüştürücü", expanded=True):
    st.write("Apsiyon’dan indirdiğin **Özet Gelir-Gider** PDF’ini yükle; aynı düzenle yeni bir PDF üretelim.")
    gg_pdf = st.file_uploader("Özet Gelir-Gider PDF", type=["pdf"], key="gg_pdf_up")
    c1, c2 = st.columns(2)
    with c1:
        site_adi = st.text_input("Site adı", value="Atlas Vadi Sitesi")
    with c2:
        baslik  = st.text_input("Başlık", value="2025 EYLÜL AYI GELİR GİDER RAPORU")

    if st.button("🧾 PDF’yi oluştur", use_container_width=True, key="gg_build"):
        if not gg_pdf:
            st.error("Önce PDF yükleyin."); st.stop()
        try:
            data = parse_gg_from_pdf(gg_pdf.read())
        except Exception as e:
            st.error(f"PDF çözümlenemedi: {e}")
            st.stop()

        out_pdf = build_gg_pdf(data, site_adi, baslik)
        st.success("PDF hazır.")
        st.download_button("📥 Raporu indir (PDF)", out_pdf, file_name="GelirGider_Raporu.pdf", mime="application/pdf")
        # ---------------- TAB R: Gelir-Gider Raporu (tek sayfa PDF, çift kolon) ----------------
with tab_r:
    st.markdown("### 📑 Atlas Vadi – Gelir/Gider Raporu PDF üret")

    st.info("Girdi dosyası basit bir tablo olmalı. İki yöntemden birini kullan:")
    st.caption("""
    **Yöntem A – Tek CSV/XLSX (önerilen)**  
    Kolonlar:  Tür, Kalem, Tutar  
    • Tür: 'GİDER' veya 'GELİR'  
    • Tutar: 137.580,27 gibi TR formatı veya 137580.27
    
    **Yöntem B – İki ayrı tablo**  
    Soldaki 'GİDERLER' ve sağdaki 'GELİRLER'i iki ayrı CSV/XLSX olarak yükle.
    """)

    mode_rep = st.radio(
        "Girdi biçimi",
        ["Tek dosyada 'Tür, Kalem, Tutar'", "Ayrı ayrı: Giderler dosyası + Gelirler dosyası"],
        horizontal=True
    )

    from io import BytesIO
    def _read_any_table(up):
        name = (up.name or "").lower()
        if name.endswith(".csv"):
            df = pd.read_csv(BytesIO(up.read()), dtype=str).fillna("")
        else:
            df = pd.read_excel(BytesIO(up.read()), dtype=str, engine="openpyxl").fillna("")
        return df

    def _to_num(x: str) -> float:
        s = str(x or "").strip()
        # TR -> float
        s = s.replace(".", "").replace(",", ".")
        try:
            return float(s)
        except:
            return 0.0

    def _split_tables_from_one(df):
        # beklenen kolonlar: Tür | Kalem | Tutar (esnek isimlendirme)
        ren = {c.lower().strip(): c for c in df.columns}
        def pick(*alts):
            for a in alts:
                if a in ren: return ren[a]
            return None
        c_tur   = pick("tür","tur","type","kategori")
        c_kalem = pick("kalem","açıklama","aciklama","item","hesap kalemi")
        c_tutar = pick("tutar","tutar (try)","tutar tl","amount","tutarı","tutar (tl)")
        if not (c_tur and c_kalem and c_tutar):
            raise ValueError("Başlıklar bulunamadı. Gerekli: Tür, Kalem, Tutar")

        df2 = df[[c_tur, c_kalem, c_tutar]].copy()
        df2.columns = ["Tür","Kalem","Tutar"]
        df2["Tür"]   = df2["Tür"].str.upper().str.strip().replace({"GIDER":"GİDER"})
        df2["TutarN"] = df2["Tutar"].apply(_to_num)

        giders = df2[df2["Tür"]=="GİDER"][["Kalem","TutarN"]].reset_index(drop=True)
        gelirs = df2[df2["Tür"]=="GELİR"][["Kalem","TutarN"]].reset_index(drop=True)
        return giders, gelirs

    if mode_rep.startswith("Tek dosyada"):
        up_all = st.file_uploader("Tek dosya yükle (CSV/XLSX)", type=["csv","xlsx"], key="rep_all")
        df_gider = df_gelir = None
        if up_all is not None:
            try:
                df = _read_any_table(up_all)
                df_gider, df_gelir = _split_tables_from_one(df)
                st.success(f"GİDER: {len(df_gider)} satır, GELİR: {len(df_gelir)} satır")
            except Exception as e:
                st.error(f"Okuma/ayırma hatası: {e}")
                df_gider = df_gelir = None
    else:
        up_g = st.file_uploader("Giderler (CSV/XLSX)", type=["csv","xlsx"], key="rep_g")
        up_l = st.file_uploader("Gelirler (CSV/XLSX)", type=["csv","xlsx"], key="rep_l")
        df_gider = df_gelir = None
        if up_g and up_l:
            try:
                dfg = _read_any_table(up_g); dfl = _read_any_table(up_l)
                # başlık sezgisel: ilk iki metin kolondan 'Kalem', parasal ilk kolondan 'Tutar'
                def canon(df0):
                    # ilk metin benzeri kolon
                    name_col = next((c for c in df0.columns if df0[c].astype(str).str.len().mean()>=2), df0.columns[0])
                    # ilk para benzeri kolon
                    val_col = next((c for c in df0.columns if df0[c].astype(str).str.contains(r"\d", regex=True).mean()>0.6), df0.columns[-1])
                    out = pd.DataFrame({"Kalem": df0[name_col].astype(str), "TutarN": df0[val_col].apply(_to_num)})
                    return out
                    #
                df_gider = canon(dfg); df_gelir = canon(dfl)
                st.success(f"GİDER: {len(df_gider)} satır, GELİR: {len(df_gelir)} satır")
            except Exception as e:
                st.error(f"Okuma hatası: {e}")
                df_gider = df_gelir = None

    # Özet alanları
    st.markdown("#### Alt Özet Alanları")
    c1, c2 = st.columns(2)
    with c1:
        alacak_tahakkuk   = st.text_input("2025 EYLÜL AYI AİDAT ALACAKLARIMIZ / TAHAKKUK EDİLEN", "821.532,21")
        tahsil_edilen     = st.text_input("2025 EYLÜL AYI TAHSİL EDİLEN AİDAT (GERÇEKLEŞEN)", "574.259,57")
        kalan_alacak      = st.text_input("2025 EYLÜL AYI KALAN AİDAT ALACAKLARIMIZ (TAHSİL EDİLECEK)", "247.272,64")
        son_tu_alacak     = st.text_input("2025 EYLÜL AYI SONU TU ALACAKLARIMIZ", "506.593,48")
    with c2:
        banka_kasa        = st.text_input("2025 EYLÜL AYI BANKA-KASA MEVCUDU", "409.965,72")
        mevcut_alacak     = st.text_input("2025 EYLÜL AYI SONU MEVCUT ALACAK", "916.559,20")
        mevcut_borclar    = st.text_input("2025 EYLÜL AYI SONU BORÇLARIMIZ", "469.059,87")

    faaliyet = st.text_area("Faaliyet Notları (madde madde)", "1) ...\n2) ...\n3) ...")

    btn = st.button("🧾 PDF Üret", use_container_width=True)

    # ---- PDF ÇİZİMİ ----
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas
    from reportlab.lib.units import mm
    from reportlab.lib import colors

    def _draw_table_pair(can, left_rows, right_rows, title="ATLAS VADİ SİTESİ 2025 EYLÜL AYI GELİR GİDER RAPORU"):
        W, H = A4
        can.setFont("NotoSans-Bold", 14)
        can.drawCentredString(W/2, H-40, title)

        # kolon kutuları
        left_x, right_x = 20*mm, 110*mm
        top_y = H-60
        row_h = 12
        colw_name = 70*mm; colw_val = 30*mm

        def box(x, y, w, h):
            can.rect(x, y-h, w, h, stroke=1, fill=0)

        # başlıklar
        can.setFont("NotoSans-Bold", 10)
        can.drawString(left_x,  top_y+6, "GİDERLER")
        can.drawString(right_x, top_y+6, "GELİRLER")

        # satırlar
        can.setFont("NotoSans-Regular", 9)

        def draw_side(x0, rows):
            y = top_y
            for name, val in rows:
                y -= row_h
                box(x0, y, colw_name+colw_val, row_h)
                can.drawString(x0+4, y+3, str(name)[:38])
                s = f"{val:,.2f} TRY".replace(",", "X").replace(".", ",").replace("X",".")
                tw = can.stringWidth(s, "NotoSans-Regular", 9)
                can.drawString(x0+colw_name+colw_val-tw-4, y+3, s)
            return y

        yL = draw_side(left_x,  [(r["Kalem"], float(r["TutarN"])) for _, r in df_gider.iterrows()] if isinstance(df_gider, pd.DataFrame) else [])
        yR = draw_side(right_x, [(r["Kalem"], float(r["TutarN"])) for _, r in df_gelir.iterrows()] if isinstance(df_gelir, pd.DataFrame) else [])

        # toplam satırları (renkli şerit)
        def sum_rows(df):
            return float(df["TutarN"].sum()) if isinstance(df, pd.DataFrame) else 0.0

        gider_toplam = sum_rows(df_gider)
        gelir_toplam = sum_rows(df_gelir)
        donem_fazla  = gelir_toplam - gider_toplam

        # sağ tabloda “GELİR TOPLAMI” ve “DÖNEM GİDER FAZLASI”
        y = min(yL, yR) - row_h
        can.setFillColorRGB(1,1,0.6)  # sarımsı
        can.rect(right_x, y-row_h,  colw_name+colw_val, row_h, stroke=0, fill=1)
        can.setFillColor(colors.black)
        can.setFont("NotoSans-Bold", 9)
        can.drawString(right_x+4, y-row_h+3, "GELİR TOPLAMI")
        s = f"{gelir_toplam:,.2f} TRY".replace(",", "X").replace(".", ",").replace("X",".")
        tw = can.stringWidth(s, "NotoSans-Bold", 9)
        can.drawString(right_x+colw_name+colw_val-tw-4, y-row_h+3, s)

        y2 = y-2*row_h
        can.setFillColorRGB(1,1,0.6)
        can.rect(right_x, y2-row_h, colw_name+colw_val, row_h, stroke=0, fill=1)
        can.setFillColor(colors.black)
        can.drawString(right_x+4, y2-row_h+3, "DÖNEM GİDER FAZLASI")
        s = f"{donem_fazla:,.2f} TRY".replace(",", "X").replace(".", ",").replace("X",".")
        tw = can.stringWidth(s, "NotoSans-Bold", 9)
        can.drawString(right_x+colw_name+colw_val-tw-4, y2-row_h+3, s)

        return min(y2-2*row_h, yL-2*row_h, yR-2*row_h)

    def _draw_bottom(can, y0):
        W, H = A4
        can.setFont("NotoSans-Bold", 9)
        can.drawString(20*mm, y0, "ALACAKLARIMIZ")
        can.setFont("NotoSans-Regular", 9)

        def par(s): return _to_num(s)

        lines = [
            ("2025 EYLÜL AYI AİDAT  ALACAKLARIMIZ / TAHAKKUK EDİLEN", par(alacak_tahakkuk)),
            ("2025 EYLÜL  AYI TAHSİL EDİLEN AİDAT  (GERÇEKLEŞEN)", par(tahsil_edilen)),
            ("2025 EYLÜL  AYI KALAN AİDAT ALACAKLARIMIZ (TAHSİL EDİLECEK )", par(kalan_alacak)),
            ("2025 EYLÜL  AYI SONU TU ALACAKLARIMIZ", par(son_tu_alacak)),
            ("2025 EYLÜL  AYI BANKA-KASA MEVCUDU", par(banka_kasa)),
            ("2025 EYLÜL  AYI SONU MEVCUT ALACAK", par(mevcut_alacak)),
            ("2025 EYLÜL  AYI SONU BORÇLARIMIZ", par(mevcut_borclar)),
        ]
        x_name, x_val = 22*mm, 180*mm
        y = y0-14
        for name, val in lines:
            can.drawString(x_name, y, name)
            s = f"{val:,.2f}".replace(",", "X").replace(".", ",").replace("X",".")
            tw = can.stringWidth(s, "NotoSans-Regular", 9)
            can.drawString(x_val-tw, y, s)
            y -= 14

        # faaliyet
        y -= 8
        can.setFont("NotoSans-Bold", 9)
        can.drawString(20*mm, y, "2025 EYLÜL AYI FAALİYETLERİMİZ")
        can.setFont("NotoSans-Regular", 9)
        y -= 12
        for ln in (faaliyet or "").splitlines():
            can.drawString(20*mm, y, ln.strip())
            y -= 12

    if btn:
        if df_gider is None or df_gelir is None:
            st.error("Önce tabloları yükleyin.")
            st.stop()

        pdf_io = BytesIO()
        can = canvas.Canvas(pdf_io, pagesize=A4)

        # baş ve iki kolon
        y_after = _draw_table_pair(can, df_gider, df_gelir)
        # alt özet alanları
        _draw_bottom(can, y_after)

        can.showPage()
        can.save()
        pdf_bytes = pdf_io.getvalue()

        st.success("PDF hazır.")
        st.download_button("📥 Gelir-Gider Raporu.pdf", pdf_bytes, file_name="GelirGiderRaporu.pdf", mime="application/pdf")
# ---------------- TAB GG: Gelir-Gider Dönüştürücü ----------------
with tab_r:
    st.subheader("📑 PDF’ten Gelir-Gider Tablosu Çıkar")
    gg_pdf = st.file_uploader("Gelir-Gider PDF (Apsiyon/özet PDF)", type=["pdf"], key="gg_pdf")
    st.caption("Not: Kalem isimleri PDF’teki başlıklara göre otomatik eşleştirilir. Uymayan satırlar 'ham' metin olarak da yazılır ki düzenleyebilesin.")

    c1, c2 = st.columns(2)
    with c1:
        parse_btn = st.button("🧾 Oku & Parse Et", use_container_width=True)
    with c2:
        st.write("")

    if parse_btn:
        if not gg_pdf:
            st.warning("Önce PDF yükle."); st.stop()

        try:
            df_gg = parse_income_expense_pdf(gg_pdf.read())
        except Exception as e:
            st.error(f"PDF parse edilemedi: {e}")
            st.stop()

        if df_gg.empty:
            st.warning("Herhangi bir kalem/tutar yakalanamadı. Kalem başlıkları için desenleri genişletmek gerekebilir.")
            st.stop()

        # Özet metrikler
        total_gelir = float(df_gg.loc[df_gg["Tür"]=="Gelir","Tutar"].sum())
        total_gider = float(df_gg.loc[df_gg["Tür"]=="Gider","Tutar"].sum())
        kpi1, kpi2, kpi3 = st.columns(3)
        with kpi1: st.metric("Toplam Gelir", f"{total_gelir:,.2f} TL".replace(",", "X").replace(".", ",").replace("X","."))
        with kpi2: st.metric("Toplam Gider", f"{total_gider:,.2f} TL".replace(",", "X").replace(".", ",").replace("X","."))
        with kpi3: st.metric("Dönem Net", f"{(total_gelir-total_gider):,.2f} TL".replace(",", "X").replace(".", ",").replace("X","."))

        st.markdown("**Detay Tablo**")
        st.dataframe(df_gg, use_container_width=True, height=520)

        # Excel indirme
        xls = export_income_expense_excel(df_gg)
        st.download_button(
            "📥 Excel indir (Detay + Özet)",
            xls,
            file_name="GelirGider_Parsed.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True
        )
