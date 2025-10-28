# app.py
import io, os, zipfile, re, unicodedata
from typing import List, Dict

import streamlit as st
import pandas as pd

if "settings" not in st.session_state:
    st.session_state["settings"] = {
        "font_size": 11,
        "leading": 14,
        "bottom_m": 48,
        "box_h": 180,
        "align": "left",
        "exp1": "Sıcak Su",
        "exp2": "Soğuk Su",
        "exp3": "Isıtma",
    }
# PDF
from pypdf import PdfReader, PdfWriter

# ALT YAZI İÇİN
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

# (Opsiyonel) .docx'ten alt yazı çekmek için
try:
    import docx  # python-docx
    HAS_DOCX = True
except Exception:
    HAS_DOCX = False


# =========================================================
#  F O N T L A R  (Türkçe karakter için NotoSans ailesi)
#  /fonts klasöründe şu dosyalar olmalı:
#  - fonts/NotoSans-Regular.ttf
#  - fonts/NotoSans-Bold.ttf
# =========================================================
pdfmetrics.registerFont(TTFont("NotoSans-Regular", "fonts/NotoSans-Regular.ttf"))
pdfmetrics.registerFont(TTFont("NotoSans-Bold",    "fonts/NotoSans-Bold.ttf"))


# =========================================================
#  K U M A N D A  -  Y A R D I M C I L A R
# =========================================================
def _pad3(s: str) -> str:
    s = "".join(ch for ch in s if ch.isdigit())
    return s.zfill(3) if s else "000"

def _to_float_tr(s: str) -> float:
    if not s: return 0.0
    s = s.strip().replace(".", "").replace(",", ".")
    try: return float(s)
    except: return 0.0

def _normalize_tr(t: str) -> str:
    """Türkçe aksanları sadeleştir, büyük harfe çevir, boşlukları toparla."""
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


# =========================================================
#  A L T  Y A Z I  –  METİN SARMA ve OVERLAY
# =========================================================
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

def split_pdf(src_bytes: bytes):
    reader = PdfReader(io.BytesIO(src_bytes))
    pages = []
    for i, p in enumerate(reader.pages, start=1):
        w = PdfWriter()
        w.add_page(p)
        b = io.BytesIO()
        w.write(b)
        pages.append((f"page_{i:03d}.pdf", b.getvalue()))
    return pages


# =========================================================
#  M A N A S  P D F  P A R S E R  (Isıtma / Sıcak Su / Su / Toplam)
# =========================================================
def parse_manas_pdf_totals(pdf_bytes: bytes) -> Dict[str, Dict[str, float]]:
    """
    Dönüş:
      {'A1-001': {'isitma': x, 'sicak': y, 'su': z, 'toplam': t}, ...}
    """
    reader = PdfReader(io.BytesIO(pdf_bytes))
    result: Dict[str, Dict[str, float]] = {}

    # --- esnek Daire No yakalama desenleri ---
    # Not: Birini normalize (DAIRE) üzerinde, birini ham metin üzerinde deneyeceğiz.
    re_daire_norms = [
        # "DAIRE NO : A1 ... 01"
        re.compile(r"DAIRE\s*NO[^A-Z0-9]{0,15}([A-Z]\d)[^0-9]{0,20}(\d{1,4})"),
        # "A1 BLK DAIRE 01" veya "A1-BLK DAIRE:01"
        re.compile(r"([A-Z]\d)[^\d\n\r]{0,30}DAIRE[^0-9]{0,10}(\d{1,4})"),
    ]
    re_daire_raws = [
        # ham metinde Türkçe "DAİRE NO"
        re.compile(r"DA[İI]RE\s*NO[^A-Z0-9]{0,15}([A-Z]\d)[^0-9]{0,20}(\d{1,4})"),
        re.compile(r"([A-Z]\d)[^\d\n\r]{0,30}DA[İI]RE[^0-9]{0,10}(\d{1,4})"),
    ]

    # Ödenecek tutar yakalama (TL ve iki nokta/boşluk varyantları)
    re_odenecek = re.compile(
        r"(?:ÖDENECEK|ODENECEK)\s*TUTAR[^0-9]{0,10}([0-9\.\,]+)", re.IGNORECASE
    )
    re_toplam = re.compile(r"TOPLAM\s+TUTAR[^0-9]{0,10}([0-9\.\,]+)", re.IGNORECASE)

    def find_daire_id(raw_text: str) -> str | None:
        norm = _normalize_tr(raw_text)
        # önce normalize üzerinde dene
        for rx in re_daire_norms:
            m = rx.search(norm)
            if m:
                blok = m.group(1).upper()
                dno = _pad3(m.group(2))
                return f"{blok}-{dno}"
        # sonra ham metinde dene (Türkçe İ/ı olasılığı)
        for rx in re_daire_raws:
            m = rx.search(raw_text)
            if m:
                blok = m.group(1).upper()
                dno = _pad3(m.group(2))
                return f"{blok}-{dno}"
        return None

    def grab_section_amount(norm_text: str, header_word: str) -> float:
        """
        header_word: 'ISITMA' | 'SICAK SU' | 'SU'
        Başlıktan sonra gelen ilk ÖDENECEK TUTAR'ı alır.
        """
        # başlık yerini bul
        idx = norm_text.find(header_word)
        if idx == -1:
            return 0.0
        tail = norm_text[idx : idx + 2500]  # bölümden sonraki makul pencere
        m = re_odenecek.search(tail)
        return _to_float_tr(m.group(1)) if m else 0.0

    # sayfa sayfa tara
    for pi, page in enumerate(reader.pages):
        raw = page.extract_text() or ""
        norm = _normalize_tr(raw)

        did = find_daire_id(raw)
        if not did:
            # ilk sayfada bulunamadıysa debug kolaylığı
            if pi == 0:
                st.info("⚠️ Daire No satırı bulunamadı. İlk sayfanın normalize edilmiş içeriğinin bir kısmını gösteriyorum.")
                st.code(norm[:800])
            continue

        isitma = grab_section_amount(norm, "ISITMA")
        sicak  = grab_section_amount(norm, "SICAK SU")

        # "SU" başlığı "SICAK SU" ile karışmasın diye, önce ' SICAK SU ' yakalandığından emin olduk.
        # Saf 'SU' için ayrı yaklaşım: ' SICAK SU ' geçtiyse, geriye kalan kısımdan ara.
        su = 0.0
        # 'SICAK SU' bölümünün sonrasından dene:
        idx_sicak = norm.find("SICAK SU")
        search_base = norm[idx_sicak + 8 :] if idx_sicak != -1 else norm
        idx_su = search_base.find("\nSU")
        if idx_su == -1:
            idx_su = search_base.find(" SU ")
        if idx_su != -1:
            tail_su = search_base[idx_su : idx_su + 2000]
            m_su = re_odenecek.search(tail_su)
            if m_su:
                su = _to_float_tr(m_su.group(1))
        if su == 0.0:
            # olmadı, genel fallback:
            su = grab_section_amount(norm, "\nSU")

        mt = re_toplam.search(norm)
        toplam = _to_float_tr(mt.group(1)) if mt else (isitma + sicak + su)

        result[did] = {"isitma": isitma, "sicak": sicak, "su": su, "toplam": toplam}

    return result

# =========================================================
#  S T R E A M L I T   U I
# =========================================================
st.set_page_config(page_title="Fatura • Atlas Vadi", page_icon="🧾", layout="centered")
st.title("🧾 Vadi Fatura — Böl & Alt Yazı & Apsiyon")

tab_a, tab_b = st.tabs(["📄 Böl & Alt Yazı", "📊 Apsiyon Gider Doldurucu"])


# ---------------- TAB A: Böl & Alt Yazı ----------------
with tab_a:
    pdf_file = st.file_uploader("Fatura PDF dosyasını yükle", type=["pdf"], key="pdf_a")

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
            st.info("python-docx yüklü değilse .docx modu devre dışı olur. requirements.txt içinde `python-docx==1.1.2` olduğundan emin olun.")
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
    font_size = st.slider("🅰️ Yazı Boyutu", 9, 16, st.session_state["settings"]["font_size"])
    leading   = st.slider("↕️ Satır Aralığı (pt)", 12, 22, st.session_state["settings"]["leading"])

with c2:
    align     = st.radio("Hizalama", ["left", "center"], index=0 if st.session_state["settings"]["align"]=="left" else 1, format_func=lambda x: "Sol" if x=="left" else "Orta")
    bottom_m  = st.slider("Alt Marj (pt)", 24, 100, st.session_state["settings"]["bottom_m"])

box_h = st.slider("Alt Yazı Alanı Yüksekliği (pt)", 100, 260, st.session_state["settings"]["box_h"])
bold_rules = st.checkbox("Başlıkları otomatik kalın yap (SON ÖDEME, AÇIKLAMA, ...)", value=True, key="boldrules")

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
st.session_state["settings"].update({
    "font_size": font_size,
    "leading": leading,
    "bottom_m": bottom_m,
    "box_h": box_h,
    "align": align,
    "exp1": exp1,
    "exp2": exp2,
    "exp3": exp3,
})
        else:
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
            pages = split_pdf(stamped)
            with io.BytesIO() as zbuf:
                with zipfile.ZipFile(zbuf, "w", zipfile.ZIP_DEFLATED) as z:
                    for name, data in pages:
                        z.writestr(name, data)
                st.download_button("📥 Alt yazılı & bölünmüş (ZIP)", zbuf.getvalue(), file_name="alt_yazili_bolunmus.zip")


# --------------- TAB B: Apsiyon Gider Doldurucu ---------------
# ================== A P S İ Y O N  (Sağlam okuma + doldurma) ==================
import pandas as pd
from io import BytesIO

def _norm(s: str) -> str:
    return (
        str(s)
        .strip()
        .lower()
        .replace("\n", " ")
        .replace("\r", " ")
        .replace(".", "")
        .replace("_", " ")
        .replace("-", " ")
    )

def _pad3(x) -> str:
    try:
        n = int(str(x).strip())
        return f"{n:03d}"
    except:
        # "01" gibi gelmişse
        s = str(x).strip()
        # en sondaki sayıları bul
        nums = "".join([ch for ch in s if ch.isdigit()])
        if nums:
            return f"{int(nums):03d}"
        return s  # son çare

def _find_header_row(df_raw: pd.DataFrame) -> int | None:
    """
    İlk 15 satırda 'blok' ve ('daire no' | 'daire') geçen bir satırı başlık sayar.
    """
    limit = min(15, len(df_raw))
    for i in range(limit):
        cells = [_norm(c) for c in list(df_raw.iloc[i].values)]
        row_text = " | ".join(cells)
        if ("blok" in row_text) and (("daire no" in row_text) or ("daire" in row_text)):
            return i
    return None

def _rename_apsiyon_cols(df: pd.DataFrame) -> pd.DataFrame:
    """
    Sütunları normalize edip 'Blok', 'Daire No' yakalar; mevcutsa Gider sütunlarını korur.
    """
    mapping = {}
    for c in df.columns:
        nc = _norm(c)
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
        # diğer tüm sütunlar aynen kalsın

    df2 = df.rename(columns=mapping)

    # Eksikse gider sütunlarını oluştur
    for col in [
        "Gider1 Tutarı", "Gider1 Açıklaması",
        "Gider2 Tutarı", "Gider2 Açıklaması",
        "Gider3 Tutarı", "Gider3 Açıklaması",
    ]:
        if col not in df2.columns:
            df2[col] = None

    return df2

def load_apsiyon_template(excel_bytes: bytes) -> pd.DataFrame:
    # Önce ham okuma (başlıksız gibi)
    raw = pd.read_excel(BytesIO(excel_bytes), header=None, engine="openpyxl")
    hdr = _find_header_row(raw)
    if hdr is None:
        # Yine de deneriz: normal header=0 ile
        df = pd.read_excel(BytesIO(excel_bytes), engine="openpyxl")
    else:
        df = pd.read_excel(BytesIO(excel_bytes), header=hdr, engine="openpyxl")

    df = _rename_apsiyon_cols(df)

    if ("Blok" not in df.columns) or ("Daire No" not in df.columns):
        # Debug için kullanıcıya göstermek üzere ilk 5 satır/sütun
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
    """
    totals: {'A1-001': {'isitma':..., 'sicak':..., 'su':..., 'toplam':...}, ...}
    mode:
      - "Seçenek 1 (G1=Sıcak Su, G2=Su, G3=Isıtma)"
      - "Seçenek 2 (G1=Toplam, G2/G3 boş)"
    """
    df = df_in.copy()

    def make_did(blok, dno) -> str:
        b = str(blok).strip().upper()
        d = _pad3(dno)
        return f"{b}-{d}"

    g1t, g1a = "Gider1 Tutarı", "Gider1 Açıklaması"
    g2t, g2a = "Gider2 Tutarı", "Gider2 Açıklaması"
    g3t, g3a = "Gider3 Tutarı", "Gider3 Açıklaması"

    # Dolum
    for idx, row in df.iterrows():
        blok = row.get("Blok", "")
        dno  = row.get("Daire No", "")
        did  = make_did(blok, dno)

        if did in totals:
            t = totals[did]
            if mode.startswith("Seçenek 1"):
                # G1 = Sıcak Su, G2 = Su, G3 = Isıtma
                df.at[idx, g1t] = t.get("sicak", 0.0)
                df.at[idx, g1a] = exp1 or ""
                df.at[idx, g2t] = t.get("su", 0.0)
                df.at[idx, g2a] = exp2 or ""
                df.at[idx, g3t] = t.get("isitma", 0.0)
                df.at[idx, g3a] = exp3 or ""
            else:
                # Seçenek 2: G1 = Toplam, G2/G3 boş
                df.at[idx, g1t] = t.get("toplam", 0.0)
                df.at[idx, g1a] = exp1 or ""
                df.at[idx, g2t] = None
                df.at[idx, g2a] = None
                df.at[idx, g3t] = None
                df.at[idx, g3a] = None
        else:
            # eşleşmeyen daireleri boş bırak
            pass

    return df

def export_excel_bytes(df: pd.DataFrame, filename: str = "Apsiyon_Doldurulmus.xlsx") -> bytes:
    bio = BytesIO()
    with pd.ExcelWriter(bio, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="Sheet1")
    return bio.getvalue()

# ---------- Streamlit UI entegrasyonu ----------
st.subheader("📊 Apsiyon Gider Doldurucu")
apsiyon_file = st.file_uploader("Apsiyon 'boş şablon' Excel dosyasını yükle (.xlsx)", type=["xlsx"], key="apsiyon_up")

colM1, colM2 = st.columns(2)
with colM1:
    aps_mode = st.radio(
        "Doldurma Şekli",
        ["Seçenek 1 (G1=Sıcak Su, G2=Su, G3=Isıtma)", "Seçenek 2 (G1=Toplam, G2/G3 boş)"],
        index=0
    )
with colM2:
    exp1 = st.text_input("Gider1 Açıklaması", value=st.session_state["settings"]["exp1"])
    exp2 = st.text_input("Gider2 Açıklaması", value=st.session_state["settings"]["exp2"])
    exp3 = st.text_input("Gider3 Açıklaması", value=st.session_state["settings"]["exp3"])

go_fill = st.button("📥 PDF’ten tutarları çek ve Excel’e yaz")

if go_fill:
    if not pdf_file:
        st.warning("Önce üstte fatura PDF’sini yükleyin (aynı PDF).")
        st.stop()
    if not apsiyon_file:
        st.warning("Apsiyon Excel şablonunu yükleyin.")
        st.stop()

    # 1) PDF'ten tutarları parse et (önceden tanımlı parse_manas_pdf_totals fonksiyonunu kullanıyoruz)
    totals_map = parse_manas_pdf_totals(pdf_file.read())
    if not totals_map:
        st.error("PDF’ten tutar okunamadı. (Daire başlıkları veya tutarlar bulunamadı)")
        st.stop()

    # 2) Excel’i oku (başlığı otomatik bul, kolonları eşle)
    try:
        df_aps = load_apsiyon_template(apsiyon_file.read())
    except Exception as e:
        st.error(f"Excel okunamadı: {e}")
        st.stop()

    # 3) Doldur
    df_out = fill_expenses_to_apsiyon(df_aps, totals_map, aps_mode, exp1, exp2, exp3)

    # 4) İndir
    out_bytes = export_excel_bytes(df_out)
    st.success("Excel dolduruldu.")
    st.download_button("📥 Doldurulmuş Apsiyon Excel", out_bytes, file_name="Apsiyon_Doldurulmus.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
