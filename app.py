# app.py
# === Vadi Fatura — Böl & Alt Yazı & Apsiyon & WhatsApp (Google Drive ile Tekil Linkler) ===
# Gereken paketler:
#   pip install streamlit pandas openpyxl pypdf reportlab python-docx

import io, os, re, zipfile, unicodedata, json
from typing import List, Dict, Tuple, Optional

import streamlit as st
import pandas as pd

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
# Sabitler (Drive klasörün)
# -----------------------------------------------------------------------------
# Bu klasörü kullanacağız: https://drive.google.com/drive/folders/1P8CZXb0G0RcNIe89CIyDASCborzmgSYF
DRIVE_FOLDER_ID_DEFAULT = "1P8CZXb0G0RcNIe89CIyDASCborzmgSYF"

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
    except Exception:
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
    except Exception:
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
                df.at[idx, g1t] = t.get("sicak", 0.0); df.at[idx, g1a] = exp1 or ""
                df.at[idx, g2t] = t.get("su", 0.0);    df.at[idx, g2a] = exp2 or ""
                df.at[idx, g3t] = t.get("isitma", 0.0);df.at[idx, g3a] = exp3 or ""
            else:
                df.at[idx, g1t] = t.get("toplam", 0.0); df.at[idx, g1a] = exp1 or ""
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
# Rehber Okuyucu (WhatsApp için)
# -----------------------------------------------------------------------------
def _norm_rehber(s: str) -> str:
    return (str(s).strip().lower()
            .replace("\n"," ").replace("\r"," ")
            .replace(".","").replace("_"," ").replace("-"," "))

def _find_header_row_contacts(df_raw: pd.DataFrame, search_rows: int = 50) -> Optional[int]:
    limit = min(search_rows, len(df_raw))
    for i in range(limit):
        cells = [_norm_rehber(c) for c in list(df_raw.iloc[i].values)]
        row_text = " | ".join(cells)
        has_blok  = "blok" in row_text
        has_daire = ("daire no" in row_text) or ("daire  no" in row_text) or ("daire" in row_text) or ("daireno" in row_text)
        has_tel   = ("telefon" in row_text) or ("tel" in row_text) or ("gsm" in row_text) or ("cep" in row_text) or ("telefon no" in row_text)
        if has_blok and has_daire and has_tel:
            return i
    return None

def _map_contact_columns(df: pd.DataFrame) -> pd.DataFrame:
    mapping = {}
    for c in df.columns:
        nc = _norm_rehber(c)
        if nc in ("blok","blok adi","blok adı","blokadi","blok ad","blokad"):
            mapping[c] = "Blok"
        elif nc in ("daire no","daire  no","daireno","daire"):
            mapping[c] = "Daire No"
        elif "ad soyad / unvan" in nc or "ad soyad/unvan" in nc or "ad soyad" in nc or "unvan" in nc:
            mapping[c] = "Ad Soyad / Unvan"
        elif nc in ("tel tip","tel tipi","tel tip:","tel tipi:","tel tip ","tel tipi "):
            mapping[c] = "Tel.Tip"
        elif (nc in ("telefon","tel","cep","gsm","telefon no","tel no","telefon numarasi","telefon numarası")) or ("telefon no" in nc):
            mapping[c] = "Telefon"
    return df.rename(columns=mapping)

def load_contacts_any(file_bytes: bytes, filename: str) -> pd.DataFrame:
    from io import BytesIO

    if filename.lower().endswith(".csv"):
        raw = pd.read_csv(BytesIO(file_bytes), header=None, dtype=str)
    else:
        raw = pd.read_excel(BytesIO(file_bytes), header=None, dtype=str, engine="openpyxl")

    hdr = _find_header_row_contacts(raw, search_rows=50)
    if hdr is None:
        hdr = 0

    if filename.lower().endswith(".csv"):
        df = pd.read_csv(BytesIO(file_bytes), header=hdr, dtype=str)
    else:
        df = pd.read_excel(BytesIO(file_bytes), header=hdr, dtype=str, engine="openpyxl")

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

    df = df.dropna(axis=1, how="all")
    df = _map_contact_columns(df)

    missing = [c for c in ["Blok","Daire No","Telefon"] if c not in df.columns]
    if missing:
        cols_map_debug = {c: _norm_colname(c) for c in df.columns}
        st.error(f"Rehberde zorunlu kolon(lar) eksik: {', '.join(missing)}")
        st.write("Algılanan kolonlar (normalize):", cols_map_debug)
        st.dataframe(df.head(20), use_container_width=True)
        raise ValueError("Apsiyon rehber başlık eşlemesi yapılamadı.")

    def _pad3_for_merge(x) -> str:
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

    if "Ad Soyad / Unvan" not in df.columns:
        df["Ad Soyad / Unvan"] = None

    df["Blok"] = df["Blok"].astype(str).str.upper().str.strip()
    df["Daire No"] = df["Daire No"].apply(_pad3_for_merge)
    df["Telefon"] = df["Telefon"].apply(_quick_norm_phone)
    df["DaireID"] = df["Blok"] + "-" + df["Daire No"]

    out = df[["Blok","Daire No","Ad Soyad / Unvan","Telefon","DaireID"]].copy()
    return out

# -----------------------------------------------------------------------------
# Google Drive Link Helpers (ID → görünür link)
# -----------------------------------------------------------------------------
def drive_direct_link(file_id: str, mode: str = "view") -> str:
    """mode: 'view' (tarayıcı önizleme) veya 'download' (indir)."""
    if mode == "download":
        return f"https://drive.google.com/uc?export=download&id={file_id}"
    return f"https://drive.google.com/uc?export=view&id={file_id}"

def parse_daire_from_filename(fn: str) -> Optional[str]:
    """'A1-013.pdf' / 'B2_7.pdf' / 'C3 105.pdf' gibi adlardan DaireID çıkarır → 'A1-013'."""
    name = fn.rsplit("/",1)[-1].rsplit("\\",1)[-1]
    m = (re.search(r"([A-Za-z]\d)\s*[-_]\s*(\d{1,3})", name)
         or re.search(r"([A-Za-z]\d)\s+(\d{1,3})", name)
         or re.search(r"([A-Za-z]\d).*?(\d{3})", name))
    if not m:
        return None
    blok = m.group(1).upper()
    try:
        dno = f"{int(m.group(2)):03d}"
    except Exception:
        dno = str(m.group(2)).zfill(3)
    return f"{blok}-{dno}"

# -----------------------------------------------------------------------------
# UI — 3 Sekme
# -----------------------------------------------------------------------------
st.title("🧾 Vadi Fatura — Böl & Alt Yazı & Apsiyon & WhatsApp")

tab_a, tab_b, tab_c = st.tabs([
    "📄 Böl & Alt Yazı",
    "📊 Apsiyon Gider Doldurucu",
    "📤 WhatsApp (Drive Linkli)"
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
            ["Seçenek 1 (G1=Sıcak Su, G2=Su, G3=Isıtma)", "Seçenek 2 (G1=Toplam, G2/G3 boş)"],
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

# ---------------- TAB C: WhatsApp Gönderim Hazırlığı (Google Drive) ----------------
with tab_c:
    st.markdown("""
    <div style='background-color:#25D366;padding:10px 16px;border-radius:10px;display:flex;align-items:center;gap:10px;color:white;margin-bottom:15px;'>
      <img src='https://upload.wikimedia.org/wikipedia/commons/6/6b/WhatsApp.svg' width='28'>
      <h3 style='margin:0;'>WhatsApp Gönderim Hazırlığı — Google Drive</h3>
    </div>
    """, unsafe_allow_html=True)

    st.info(
        "PDF’leri **Google Drive klasörüne** koyuyorsun ("
        f"[klasör linki](https://drive.google.com/drive/folders/{DRIVE_FOLDER_ID_DEFAULT})"
        "). Klasördeki PDF adları `A1-013.pdf` formatında olmalı. "
        "Aşağıdaki **drive_file_list.csv** dosyasını yükleyerek her PDF için **tekil link** oluşturacağız."
    )

    with st.expander("🔧 drive_file_list.csv nasıl oluşturulur? (Apps Script 1 dak.)", expanded=False):
        st.code(
            f"""// Drive'da 'Yeni > Diğer > Apps Script' deyip çalıştırın.
function exportDriveFileListToCSV() {{
  var FOLDER_ID = '{DRIVE_FOLDER_ID_DEFAULT}';
  var folder = DriveApp.getFolderById(FOLDER_ID);
  var files = folder.getFiles();
  var rows = [['file_name','file_id']];
  while (files.hasNext()) {{
    var f = files.next();
    if ((f.getMimeType()+'').indexOf('pdf') !== -1) {{
      rows.push([f.getName(), f.getId()]);
    }}
  }}
  var csv = rows.map(r => r.map(v => `"${{(v+'').replace(/"/g,'""')}}"`).join(',')).join('\\n');
  var out = folder.createFile('drive_file_list.csv', csv, MimeType.PLAIN_TEXT);
  Logger.log('CSV oluşturuldu: ' + out.getUrl());
}}
""",
            language="javascript"
        )

    st.subheader("Girdi dosyaları")
    c_up1, c_up2 = st.columns([1,1], vertical_alignment="top")
    with c_up1:
        drive_csv = st.file_uploader("1) drive_file_list.csv (file_name,file_id)", type=["csv"], key="wa_drive_csv")
    with c_up2:
        rehber_up = st.file_uploader("2) Rehber (XLSX/CSV)", type=["xlsx","csv"], key="wa_rehber")

    link_mode_label = st.radio("Link tipi", ["Tarayıcıda görüntüle (önerilen)", "Direkt indir"], index=0, key="wa_linkmode")
    go_btn = st.button("📑 Eşleştir ve WhatsApp CSV oluştur", use_container_width=True, key="wa_go_drive")

    if go_btn:
        if not drive_csv:
            st.warning("Önce drive_file_list.csv yükleyin."); st.stop()
        if not rehber_up:
            st.warning("Önce Rehber dosyasını yükleyin."); st.stop()

        # Drive CSV → DaireID çıkar
        df_drive = pd.read_csv(drive_csv, dtype=str)
        need_cols = {"file_name","file_id"}
        if not need_cols.issubset(df_drive.columns):
            st.error("drive_file_list.csv içinde 'file_name' ve 'file_id' sütunları olmalı.")
            st.stop()

        df_drive = df_drive.dropna(subset=["file_name","file_id"]).copy()
        df_drive["DaireID"] = df_drive["file_name"].apply(parse_daire_from_filename)

        if df_drive["DaireID"].isna().any():
            st.warning(f"⚠️ DaireID çıkarılamayan dosya sayısı: {int(df_drive['DaireID'].isna().sum())} — "
                       "PDF adlarını `A1-013.pdf` gibi düzenleyin.")

        # Rehber
        try:
            rehber_df = load_contacts_any(rehber_up.read(), rehber_up.name)
        except Exception as e:
            st.error(f"Rehber okunamadı / eşlenemedi: {e}")
            st.stop()

        # Merge
        merged = df_drive.merge(rehber_df[["DaireID","Telefon","Ad Soyad / Unvan"]], on="DaireID", how="left")

        link_mode = "view" if link_mode_label.startswith("Tarayıcı") else "download"
        merged["file_url"] = merged["file_id"].apply(lambda fid: drive_direct_link(fid, link_mode) if pd.notna(fid) else "")

        a1, a2, a3 = st.columns(3)
        with a1: st.metric("Toplam kayıt", len(merged))
        with a2: st.metric("DaireID bulunamadı (Drive dosya adı)", int(merged["DaireID"].isna().sum()))
        with a3: st.metric("Telefon eksik (Rehber)", int((merged["Telefon"].isna() | (merged["Telefon"]=="")).sum()))

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
        st.download_button("📥 WhatsApp_Recipients.csv", b_csv,
                           file_name="WhatsApp_Recipients.csv", mime="text/csv", use_container_width=True)

        with st.expander("📨 Örnek mesaj gövdesi", expanded=False):
            st.code(
                "Merhaba {name},\n"
                "{daire_id} numaralı dairenizin aylık bildirimi hazırdır.\n"
                "Dosyayı aşağıdaki butondan görüntüleyebilirsiniz.\n",
                language="text"
            )
