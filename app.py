import streamlit as st
import os, io, zipfile, re
from typing import List, Dict
import pandas as pd

# PDF & yazı işleri
from pypdf import PdfReader, PdfWriter
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

# .docx'ten alt yazı çekmek opsiyonel
try:
    import docx
    HAS_DOCX = True
except Exception:
    HAS_DOCX = False

# ===================== KALICI AYARLAR (session_state) =====================
if "settings" not in st.session_state:
    st.session_state["settings"] = {
        "font_size": 11,
        "leading": 14,
        "bottom_m": 48,
        "box_h": 180,
        "align": "left",
        "exp1": "Sıcak Su",   # Gider1 açıklaması (Seçenek 1)
        "exp2": "Soğuk Su",   # Gider2 açıklaması (Seçenek 1)
        "exp3": "Isıtma",     # Gider3 açıklaması (Seçenek 1)
        "exp_total": "Aylık Toplam Isınma+Sıcak Su+Su",  # Seçenek 2 açıklaması
    }

# ===================== FONT KAYITLARI (Türkçe) =====================
# Repo kökünde fonts klasöründe .ttf’ler olmalı.
pdfmetrics.registerFont(TTFont("NotoSans-Regular", "fonts/NotoSans-Regular.ttf"))
pdfmetrics.registerFont(TTFont("NotoSans-Bold",    "fonts/NotoSans-Bold.ttf"))

# ===================== YARDIMCI FONKSİYONLAR =====================
def wrap_by_width(text: str, font_name: str, font_size: float, max_width: float) -> List[str]:
    """Satırları gerçek yazı genişliğine göre sarar; boş satırı korur, çok uzun kelimeyi böler."""
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
                # tek kelime dahi sığmıyorsa harf harf böl
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
    """Sayfa altına çok satırlı alt yazı overlay'i üretir; satır sırası korunur."""
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

def split_pdf(src_bytes: bytes) -> List[tuple]:
    reader = PdfReader(io.BytesIO(src_bytes))
    pages = []
    for i, p in enumerate(reader.pages, start=1):
        w = PdfWriter()
        w.add_page(p)
        b = io.BytesIO()
        w.write(b)
        pages.append((f"page_{i:03d}.pdf", b.getvalue()))
    return pages

def _pad3(s: str) -> str:
    s = re.sub(r"\D", "", s or "")
    if not s:
        return "000"
    return f"{int(s):03d}"

def _to_float_tr(s: str) -> float:
    if s is None:
        return 0.0
    s = s.strip()
    s = s.replace(".", "").replace(",", ".")
    try:
        return float(s)
    except Exception:
        return 0.0

def parse_manas_pdf_totals(pdf_bytes: bytes) -> Dict[str, Dict[str, float]]:
    """
    PDF'ten A1-001 formatında daireID ve tutarları çıkarır.
    Dönen örnek:
    {
      'A1-001': {'isitma': 123.45, 'sicak': 67.89, 'su': 45.00, 'toplam': 236.34},
      ...
    }
    """
    reader = PdfReader(io.BytesIO(pdf_bytes))
    result: Dict[str, Dict[str, float]] = {}

    # Daire satırı: "DAİRE NO : A1-blk daire:01" gibi esnek yakala
    re_daire_flex = re.compile(
        r"DA[İI]RE\s*NO\s*[:：]?\s*([A-Z]\d)[^\d\n\r]{0,20}?(\d+)",
        re.IGNORECASE
    )
    re_odenecek = re.compile(r"ÖDENECEK\s*TUTAR\s*([\d\.\,]+)", re.IGNORECASE)
    re_toplam   = re.compile(r"TOPLAM\s+TUTAR\s*([\d\.\,]+)", re.IGNORECASE)

    for page in reader.pages:
        txt = page.extract_text() or ""
        up  = txt.upper()

        # DaireID bul
        did = None
        m = re_daire_flex.search(up)
        if m:
            blok = m.group(1).upper()
            dno  = _pad3(m.group(2))
            did  = f"{blok}-{dno}"
        if not did:
            # sayfa tanınmadıysa atla
            continue

        # Bölüm başlangıçları
        # Not: " SU " gibi varyantları da yakalamaya çalışıyoruz
        idx_isitma = up.find("ISITMA")
        idx_sicak  = up.find("SICAK SU")
        idx_su     = up.find("\nSU")
        if idx_su == -1: idx_su = up.find("SU\n")
        if idx_su == -1: idx_su = up.find("\rSU")
        if idx_su == -1: idx_su = up.find("SU\r")
        if idx_su == -1: 
            # fallback: " SU " veya satır başı/sonu
            pos = up.find(" SU ")
            idx_su = pos if pos != -1 else up.find("SU")

        end = len(up)
        sections = {"ISITMA": None, "SICAK SU": None, "SU": None}
        if idx_isitma != -1:
            end_isitma = min([x for x in [idx_sicak, idx_su, end] if x != -1 and x > idx_isitma] or [end])
            sections["ISITMA"] = txt[idx_isitma:end_isitma]
        if idx_sicak != -1:
            end_sicak = min([x for x in [idx_su, end] if x != -1 and x > idx_sicak] or [end])
            sections["SICAK SU"] = txt[idx_sicak:end_sicak]
        if idx_su != -1:
            sections["SU"] = txt[idx_su:end]

        isitma = sicak = su = 0.0
        for key, sec in sections.items():
            if not sec:
                continue
            mo = re_odenecek.search(sec)
            if not mo:
                continue
            val = _to_float_tr(mo.group(1))
            if key == "ISITMA":     isitma = val
            elif key == "SICAK SU":  sicak = val
            elif key == "SU":        su = val

        # Toplam
        toplam = 0.0
        mt = re_toplam.search(up)
        if mt:
            toplam = _to_float_tr(mt.group(1))
        else:
            toplam = isitma + sicak + su

        result[did] = {"isitma": isitma, "sicak": sicak, "su": su, "toplam": toplam}

    return result

def read_excel_find_headers(excel_bytes: bytes) -> pd.DataFrame:
    """Apsiyon boş şablonunda başlık satırını otomatik bul ve DF döndür."""
    xls = pd.ExcelFile(io.BytesIO(excel_bytes))
    # İlk sayfa
    df_raw = pd.read_excel(xls, sheet_name=0, header=None)
    header_row = None
    for i in range(min(len(df_raw), 10)):  # ilk 10 satırda ara
        row_vals = df_raw.iloc[i].astype(str).str.upper().tolist()
        if ("BLOK" in row_vals) and ("DAIRE NO" in [v.replace("İ","I") for v in row_vals]):
            header_row = i
            break
    if header_row is None:
        # değilse 0 kabul et
        header_row = 0
    df = pd.read_excel(xls, sheet_name=0, header=header_row)
    return df

def ensure_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Gerekli gider sütunları yoksa ekle."""
    cols_needed = [
        "Gider1 Tutarı", "Gider1 Açıklaması",
        "Gider2 Tutarı", "Gider2 Açıklaması",
        "Gider3 Tutarı", "Gider3 Açıklaması",
    ]
    for c in cols_needed:
        if c not in df.columns:
            df[c] = ""
    return df

def df_make_daire_id(df: pd.DataFrame) -> pd.DataFrame:
    """Blok + Daire No → A1-001 formatına çevir ve yeni DaireID sütununa yaz."""
    if "Blok" not in df.columns or "Daire No" not in df.columns:
        raise ValueError("Excel’de 'Blok' ve 'Daire No' sütunları bulunamadı.")
    out = df.copy()
    def fmt(row):
        blok = str(row.get("Blok", "")).strip().upper()
        dno  = _pad3(str(row.get("Daire No", "")))
        return f"{blok}-{dno}"
    out["DaireID"] = out.apply(fmt, axis=1)
    return out

def fill_apsiyon(df: pd.DataFrame,
                 totals: Dict[str, Dict[str, float]],
                 mode: str,
                 exp1: str, exp2: str, exp3: str, exp_total: str) -> pd.DataFrame:
    """
    mode:
      "sec1" → Gider1=Sıcak Su, Gider2=Su, Gider3=Isıtma
      "sec2" → Toplam → Gider1
    """
    df = ensure_columns(df)
    df = df_make_daire_id(df)
    filled = df.copy()

    for idx, row in filled.iterrows():
        did = row["DaireID"]
        vals = totals.get(did, None)
        if not vals:
            continue
        if mode == "sec1":
            filled.at[idx, "Gider1 Tutarı"] = f'{vals["sicak"]:.2f}'.replace(".", ",")
            filled.at[idx, "Gider1 Açıklaması"] = exp1

            filled.at[idx, "Gider2 Tutarı"] = f'{vals["su"]:.2f}'.replace(".", ",")
            filled.at[idx, "Gider2 Açıklaması"] = exp2

            filled.at[idx, "Gider3 Tutarı"] = f'{vals["isitma"]:.2f}'.replace(".", ",")
            filled.at[idx, "Gider3 Açıklaması"] = exp3
        else:
            # sec2
            filled.at[idx, "Gider1 Tutarı"] = f'{vals["toplam"]:.2f}'.replace(".", ",")
            filled.at[idx, "Gider1 Açıklaması"] = exp_total

    return filled

# ===================== STREAMLIT ARAYÜZ =====================
st.set_page_config(page_title="Atlas Vadi • Fatura Aracı", page_icon="🧾", layout="centered")
st.title("🧾 Atlas Vadi • Fatura / Apsiyon Yardımcısı")

tab_a, tab_b = st.tabs(["📄 PDF Böl & Alt yazı", "📊 Apsiyon Gider Doldurucu"])

# --------------- TAB A: PDF Böl & Alt Yazı ---------------
with tab_a:
    st.subheader("Fatura PDF’i Yükle")
    pdf_file = st.file_uploader("Fatura PDF dosyasını yükle", type=["pdf"], key="pdf_main")

    st.subheader("Alt Yazı Kaynağı")
    taa, tab = st.tabs(["✍️ Metin alanı", "📄 .docx (opsiyonel)"])
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

    with taa:
        footer_text = st.text_area("Alt yazı", value=default_text, height=220, key="footer_text_main")
    with tab:
        if not HAS_DOCX:
            st.info("`.docx` içe aktarmak için requirements.txt içinde `python-docx==1.1.2` olduğundan emin olun.")
        docx_file = st.file_uploader(".docx yükleyin (opsiyonel)", type=["docx"], key="docx_main")
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
    bold_rules = st.checkbox("Başlıkları otomatik kalın yap (SON ÖDEME, AÇIKLAMA, ...)", value=True, key="boldrules_main")

    st.subheader("İşlem")
    mode = st.radio(
        "Ne yapmak istersiniz?",
        ["Sadece sayfalara böl", "Sadece alt yazı uygula (tek PDF)", "Alt yazı uygula + sayfalara böl (ZIP)"],
        index=2,
        key="mode_main"
    )
    go = st.button("🚀 Başlat", key="go_main")

    if go:
        if not pdf_file:
            st.warning("Lütfen önce bir PDF yükleyin.")
        else:
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

        # Görünüm ayarlarını kalıcılaştır
        st.session_state["settings"].update({
            "font_size": font_size,
            "leading": leading,
            "bottom_m": bottom_m,
            "box_h": box_h,
            "align": align,
        })

# --------------- TAB B: Apsiyon Gider Doldurucu ---------------
with tab_b:
    st.header("📊 Apsiyon Gider Doldurucu (PDF → Apsiyon boş şablon)")

    pdf_all = st.file_uploader("Manas faturalarının olduğu **tek PDF** dosyayı yükle", type=["pdf"], key="pdf_all")
    xlsx_tpl = st.file_uploader("Apsiyon boş şablon (Excel) dosyasını yükle", type=["xls", "xlsx"], key="xlsx_tpl")

    st.markdown("**Gider Dağıtım Seçeneği**")
    mode_fill = st.radio(
        "Seçin:",
        [
            "Seçenek 1: Gider1 = Sıcak Su, Gider2 = Su, Gider3 = Isıtma",
            "Seçenek 2: Toplam tutarı Gider1'e yaz"
        ],
        index=0,
        key="mode_fill"
    )

    c1, c2 = st.columns(2)
    with c1:
        exp1 = st.text_input("Gider1 Açıklaması", value=st.session_state["settings"]["exp1"], key="exp1")
        exp2 = st.text_input("Gider2 Açıklaması", value=st.session_state["settings"]["exp2"], key="exp2")
        exp3 = st.text_input("Gider3 Açıklaması", value=st.session_state["settings"]["exp3"], key="exp3")
    with c2:
        exp_total = st.text_input("Seçenek 2: Gider1 Açıklaması", value=st.session_state["settings"]["exp_total"], key="exp_total")

    run_fill = st.button("🚀 PDF'ten oku ve Excel'i doldur", key="run_fill")

    if run_fill:
        if not pdf_all or not xlsx_tpl:
            st.warning("Lütfen PDF ve Excel dosyalarını yükleyin.")
        else:
            try:
                totals = parse_manas_pdf_totals(pdf_all.read())
                if not totals:
                    st.error("PDF’ten tutar okunamadı. (Daire başlıkları bulunamadı)")
                else:
                    df_in = read_excel_find_headers(xlsx_tpl.read())
                    mode_key = "sec1" if mode_fill.startswith("Seçenek 1") else "sec2"
                    df_out = fill_apsiyon(df_in, totals, mode_key, exp1, exp2, exp3, exp_total)

                    # İndirme
                    out_buf = io.BytesIO()
                    with pd.ExcelWriter(out_buf, engine="xlsxwriter") as writer:
                        df_out.to_excel(writer, index=False, sheet_name="Sheet1")
                    st.success("Excel dolduruldu.")
                    st.download_button("📥 Doldurulmuş Apsiyon Excel", out_buf.getvalue(), file_name="Apsiyon_Doldurulmus.xlsx")

                    # Açıklamaları kalıcılaştır
                    st.session_state["settings"].update({
                        "exp1": exp1,
                        "exp2": exp2,
                        "exp3": exp3,
                        "exp_total": exp_total,
                    })
            except Exception as e:
                st.error(f"Hata: {e}")
