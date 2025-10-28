import streamlit as st
import io, zipfile, re
from typing import List, Optional, Tuple
import pandas as pd

from pypdf import PdfReader, PdfWriter
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib.pagesizes import A4

# (opsiyonel) docx'ten alt yazı çekmek için
try:
    import docx
    HAS_DOCX = True
except Exception:
    HAS_DOCX = False

# ---------- GENEL ----------
st.set_page_config(page_title="Atlas Vadi • Fatura & Apsiyon", page_icon="🧾", layout="centered")
pdfmetrics.registerFont(TTFont("NotoSans-Regular", "fonts/NotoSans-Regular.ttf"))
pdfmetrics.registerFont(TTFont("NotoSans-Bold",    "fonts/NotoSans-Bold.ttf"))
PAGE_W, PAGE_H = A4  # (595 x 842 pt)

# ---------- YARDIMCI ----------
def wrap_by_width(text: str, font_name: str, font_size: float, max_width: float) -> List[str]:
    lines = []
    for raw in text.replace("\r\n","\n").replace("\r","\n").split("\n"):
        if not raw.strip():
            lines.append(""); continue
        words = raw.split()
        current = ""
        for w in words:
            trial = (current + " " + w).strip()
            width = pdfmetrics.stringWidth(trial, font_name, font_size)
            if width <= max_width:
                current = trial
            else:
                if current: lines.append(current)
                # kelimenin kendisi bile sığmıyorsa harf harf böl
                if pdfmetrics.stringWidth(w, font_name, font_size) > max_width:
                    piece = ""
                    for ch in w:
                        if pdfmetrics.stringWidth(piece + ch, font_name, font_size) <= max_width:
                            piece += ch
                        else:
                            lines.append(piece); piece = ch
                    current = piece
                else:
                    current = w
        lines.append(current)
    return lines

def build_footer_overlay(page_w: float, page_h: float, footer_text: str,
                         font_size: int=11, leading: int=14, align: str="left",
                         bottom_margin: int=48, box_height: int=180, bold_rules: bool=True) -> io.BytesIO:
    packet = io.BytesIO()
    can = canvas.Canvas(packet, pagesize=(page_w, page_h))
    left_margin, right_margin = 36, 36
    max_text_width = page_w - left_margin - right_margin

    wrapped = wrap_by_width(footer_text, "NotoSans-Regular", font_size, max_text_width)
    max_lines = max(1, int(box_height // leading))
    if len(wrapped) > max_lines: wrapped = wrapped[:max_lines]

    y_start = bottom_margin + (len(wrapped) - 1)*leading + 4
    for i, line in enumerate(wrapped):
        use_bold = False
        if bold_rules:
            u = line.strip().upper()
            if i == 0 and u.startswith("SON ÖDEME"): use_bold = True
            if u == "AÇIKLAMA": use_bold = True
            if "TARİHLİ TEMSİLCİLER" in u: use_bold = True
        can.setFont("NotoSans-Bold" if use_bold else "NotoSans-Regular", font_size)
        y = y_start - i*leading
        if align == "center":
            can.drawCentredString(page_w/2.0, y, line)
        else:
            can.drawString(left_margin, y, line)
    can.save(); packet.seek(0)
    return packet

def add_footer_to_pdf(src_bytes: bytes, **kw) -> bytes:
    reader = PdfReader(io.BytesIO(src_bytes)); writer = PdfWriter()
    for page in reader.pages:
        w = float(page.mediabox.width); h = float(page.mediabox.height)
        overlay_io = build_footer_overlay(w, h, **kw)
        overlay = PdfReader(overlay_io)
        page.merge_page(overlay.pages[0])
        writer.add_page(page)
    out = io.BytesIO(); writer.write(out)
    return out.getvalue()

def split_pdf(src_bytes: bytes):
    reader = PdfReader(io.BytesIO(src_bytes))
    pages = []
    for i, p in enumerate(reader.pages, start=1):
        w = PdfWriter(); w.add_page(p)
        b = io.BytesIO(); w.write(b)
        pages.append((f"page_{i:03d}.pdf", b.getvalue()))
    return pages

# ---------- PDF → Apsiyon toplamları ----------
def _to_float_tr(s: str) -> float:
    if s is None: return 0.0
    if not isinstance(s, str): s = str(s)
    s = s.strip().replace(".", "").replace(",", ".")
    try: return float(s)
    except: return 0.0

def _pad3(n: str) -> str:
    try: return f"{int(float(n)) :03d}"
    except: return str(n)

def parse_manas_pdf_totals(pdf_bytes: bytes) -> dict:
    """
    Manas PDF içinden daire bazlı: {'A1-001': {'isitma': x, 'sicak': y, 'su': z}, ...}
    """
    reader = PdfReader(io.BytesIO(pdf_bytes))
    result = {}

    re_daire = re.compile(r"Daire\s*No\s*([A-Z]\d)\s*-\s*blk\s*daire\s*[:：]\s*(\d+)", re.IGNORECASE)
    re_odenecek = re.compile(r"Ödenecek\s*Tutar\s*([\d\.\,]+)")

    for page in reader.pages:
        txt = page.extract_text() or ""
        m = re_daire.search(txt)
        if not m:
            m = re.search(r"Daire\s*No\s*([A-Z]\d)\s*blk\s*daire\s*[:：]\s*(\d+)", txt, re.IGNORECASE)
        if not m:
            continue
        blok = m.group(1).upper()
        dno  = _pad3(m.group(2))
        did  = f"{blok}-{dno}"

        up = txt.upper(); end = len(up)
        idx_isitma = up.find("ISITMA")
        idx_sicak  = up.find("SICAK SU")
        idx_su     = up.find("\nSU") if "\nSU" in up else up.find("SU\n")
        if idx_su == -1: idx_su = up.find("\nSU\n")

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
            if not sec: continue
            mo = re_odenecek.search(sec)
            if not mo: continue
            val = _to_float_tr(mo.group(1))
            if key == "ISITMA": isitma = val
            elif key == "SICAK SU": sicak = val
            elif key == "SU": su = val

        result[did] = {"isitma": isitma, "sicak": sicak, "su": su}

    return result

# ======================================================
#                    U I
# ======================================================

st.title("🧾 Atlas Vadi — Tek PDF ile Çoklu İşlem")

# -- Tek PDF yükleyici (her iki mod bunu kullanır)
pdf_file = st.file_uploader("1) Fatura PDF dosyasını yükle (tek dosya)", type=["pdf"])
pdf_bytes: Optional[bytes] = pdf_file.read() if pdf_file else None

tabs = st.tabs(["📄 PDF İşlemleri", "📊 Apsiyon Gider Doldurucu"])

# -------------------- TAB 1: PDF İŞLEMLERİ --------------------
with tabs[0]:
    st.subheader("Alt Yazı Kaynağı")
    t1, t2 = st.columns(2)
    with t1:
        source = st.radio("Alt yazı kaynağı", ["Metin yaz", ".docx yükle"], index=0)
    footer_text = ""
    if source == "Metin yaz":
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
        footer_text = st.text_area("Alt yazı", value=default_text, height=220)
    else:
        if not HAS_DOCX:
            st.info("`.docx` için `python-docx` gerekir (requirements.txt: python-docx==1.1.2)")
        docx_file = st.file_uploader(".docx yükle", type=["docx"], key="docx_up_pdf")
        if docx_file and HAS_DOCX:
            try:
                d = docx.Document(docx_file)
                paragraphs = [p.text for p in d.paragraphs]
                footer_text = "\n".join(paragraphs).strip()
                st.success(".docx içeriği yüklendi.")
            except Exception as e:
                st.error(f".docx okunamadı: {e}")

    st.subheader("Görünüm Ayarları")
    c1, c2 = st.columns(2)
    with c1:
        font_size = st.slider("🅰️ Yazı Boyutu", 9, 16, 11)
        leading   = st.slider("↕️ Satır Aralığı (pt)", 12, 22, 14)
    with c2:
        align     = st.radio("Hizalama", ["left", "center"], index=0, format_func=lambda x: "Sol" if x=="left" else "Orta")
        bottom_m  = st.slider("Alt Marj (pt)", 24, 100, 48)
    box_h = st.slider("Alt Yazı Alanı Yüksekliği (pt)", 100, 260, 180)
    bold_rules = st.checkbox("Başlıkları otomatik kalın yap (SON ÖDEME, AÇIKLAMA, ...)", value=True)

    st.subheader("İşlem Seç")
    mode = st.radio(
        "Ne yapmak istersiniz?",
        ["Sadece sayfalara böl", "Sadece alt yazı uygula (tek PDF)", "Alt yazı uygula + sayfalara böl (ZIP)"],
        index=2
    )
    if st.button("🚀 PDF işlemini yap"):
        if not pdf_bytes:
            st.warning("Önce üstte PDF yükleyin."); st.stop()

        if mode == "Sadece sayfalara böl":
            pages = split_pdf(pdf_bytes)
            with io.BytesIO() as zbuf:
                with zipfile.ZipFile(zbuf, "w", zipfile.ZIP_DEFLATED) as z:
                    for name, data in pages: z.writestr(name, data)
                st.download_button("📥 Bölünmüş sayfalar (ZIP)", zbuf.getvalue(), file_name="bolunmus_sayfalar.zip")

        elif mode == "Sadece alt yazı uygula (tek PDF)":
            stamped = add_footer_to_pdf(
                pdf_bytes,
                footer_text=footer_text or "",
                font_size=font_size, leading=leading,
                align=align, bottom_margin=bottom_m,
                box_height=box_h, bold_rules=bold_rules,
            )
            st.download_button("📥 Alt yazılı PDF", stamped, file_name="alt_yazili.pdf")

        else:
            stamped = add_footer_to_pdf(
                pdf_bytes,
                footer_text=footer_text or "",
                font_size=font_size, leading=leading,
                align=align, bottom_margin=bottom_m,
                box_height=box_h, bold_rules=bold_rules,
            )
            pages = split_pdf(stamped)
            with io.BytesIO() as zbuf:
                with zipfile.ZipFile(zbuf, "w", zipfile.ZIP_DEFLATED) as z:
                    for name, data in pages: z.writestr(name, data)
                st.download_button("📥 Alt yazılı & bölünmüş (ZIP)", zbuf.getvalue(), file_name="alt_yazili_bolunmus.zip")

# -------------------- TAB 2: APSİYON --------------------
with tabs[1]:
    st.subheader("Apsiyon Boş Şablon")
    apsiyon_xlsx = st.file_uploader("2) Apsiyon boş şablon (Excel)", type=["xlsx","xls"], key="aps_xlsx")

    st.subheader("Daire Eşleşme Ayarı")
    id_mode = st.radio(
        "Şablonda daire alanı nasıl?",
        ["Blok + Daire No sütunları var", "Tek sütunda DaireID (örn. A1-001) var"],
        index=0
    )
    if id_mode == "Blok + Daire No sütunları var":
        col_b = st.text_input("Blok sütun adı", value="Blok")
        col_d = st.text_input("Daire No sütun adı", value="Daire No")
        single_id_col = None
    else:
        single_id_col = st.text_input("DaireID / Daire sütun adı", value="DaireID")
        col_b, col_d = None, None

    st.subheader("Yazım Modu (Gider kolonları)")
    mode2 = st.radio(
        "Giderleri nasıl yazalım?",
        [
            "Seçenek 1: Gider1 = Isıtma + Sıcak Su, Gider2 = Su, Gider3 = Isıtma",
            "Seçenek 2: Toplam (Isıtma + Sıcak Su + Su) sadece Gider1'e"
        ],
        index=0
    )
    if mode2.startswith("Seçenek 1"):
        g1_acik = st.text_input("Gider1 Açıklaması (Isıtma + Sıcak Su)", value="Isıtma + Sıcak Su")
        g2_acik = st.text_input("Gider2 Açıklaması (Su)",               value="Soğuk Su")
        g3_acik = st.text_input("Gider3 Açıklaması (Isıtma)",           value="Isıtma")
        single_desc = None
    else:
        single_desc = st.text_input("Gider1 Açıklaması (Toplam)", value="Isıtma + Sıcak Su + Su (Toplam)")
        g1_acik = g2_acik = g3_acik = None

    col_g1_tutar = st.text_input("Gider1 Tutarı sütun adı", value="Gider1 Tutarı")
    col_g1_acik  = st.text_input("Gider1 Açıklaması sütun adı", value="Gider1 Açıklaması")
    col_g2_tutar = st.text_input("Gider2 Tutarı sütun adı", value="Gider2 Tutarı")
    col_g2_acik  = st.text_input("Gider2 Açıklaması sütun adı", value="Gider2 Açıklaması")
    col_g3_tutar = st.text_input("Gider3 Tutarı sütun adı", value="Gider3 Tutarı")
    col_g3_acik  = st.text_input("Gider3 Açıklaması sütun adı", value="Gider3 Açıklaması")

    def write_expenses_to_sheet(df: pd.DataFrame, totals: dict, id_mode: str,
                                col_b: Optional[str], col_d: Optional[str], single_id_col: Optional[str],
                                mode: str, g1_acik: Optional[str], g2_acik: Optional[str], g3_acik: Optional[str],
                                single_desc: Optional[str],
                                cols: Tuple[str, str, str, str, str, str]) -> pd.DataFrame:
        col_g1_tutar, col_g1_acik, col_g2_tutar, col_g2_acik, col_g3_tutar, col_g3_acik = cols
        for c in [col_g1_tutar, col_g1_acik, col_g2_tutar, col_g2_acik, col_g3_tutar, col_g3_acik]:
            if c not in df.columns: df[c] = ""

        def row_id(r) -> Optional[str]:
            if id_mode == "Blok + Daire No sütunları var":
                if col_b in r and col_d in r:
                    b = str(r[col_b]).strip().upper()
                    d_raw = str(r[col_d]).strip()
                    d = _pad3(d_raw.split(".")[0])
                    if b and d: return f"{b}-{d}"
            else:
                if single_id_col in r:
                    v = str(r[single_id_col]).strip().upper()
                    m = re.match(r"([A-Z]\d)\-(\d+)$", v)
                    if m: return f"{m.group(1)}-{_pad3(m.group(2))}"
                    return v
            return None

        out = df.copy()
        for idx, r in out.iterrows():
            did = row_id(r)
            if not did or did not in totals: continue
            t = totals[did]
            isitma = t.get("isitma", 0.0); sicak = t.get("sicak", 0.0); su = t.get("su", 0.0)

            if mode.startswith("Seçenek 1"):
                g1 = (isitma + sicak); g2 = su; g3 = isitma
                out.at[idx, col_g1_tutar] = f"{g1:.2f}".replace(".", ",")
                out.at[idx, col_g2_tutar] = f"{g2:.2f}".replace(".", ",")
                out.at[idx, col_g3_tutar] = f"{g3:.2f}".replace(".", ",")
                out.at[idx, col_g1_acik]  = g1_acik or ""
                out.at[idx, col_g2_acik]  = g2_acik or ""
                out.at[idx, col_g3_acik]  = g3_acik or ""
            else:
                tot = (isitma + sicak + su)
                out.at[idx, col_g1_tutar] = f"{tot:.2f}".replace(".", ",")
                out.at[idx, col_g1_acik]  = single_desc or ""
                out.at[idx, col_g2_tutar] = ""; out.at[idx, col_g2_acik] = ""
                out.at[idx, col_g3_tutar] = ""; out.at[idx, col_g3_acik] = ""
        return out

    if st.button("🚀 Apsiyon dosyasını doldur ve indir"):
        if not pdf_bytes or not apsiyon_xlsx:
            st.error("PDF ve Apsiyon şablonunu birlikte yükleyin."); st.stop()

        try:
            df_in = pd.read_excel(apsiyon_xlsx)
        except Exception as e:
            st.error(f"Apsiyon dosyası okunamadı: {e}"); st.stop()

        totals = parse_manas_pdf_totals(pdf_bytes)
        if not totals:
            st.warning("PDF’den daire verisi çıkmadı. PDF yapısını kontrol edin."); st.stop()

        df_out = write_expenses_to_sheet(
            df_in, totals, id_mode, col_b, col_d, single_id_col,
            mode2, g1_acik, g2_acik, g3_acik, single_desc,
            (col_g1_tutar, col_g1_acik, col_g2_tutar, col_g2_acik, col_g3_tutar, col_g3_acik)
        )

        st.success("Tamam! Önizleme (ilk 15 satır):")
        st.dataframe(df_out.head(15))

        xbuf = io.BytesIO(); wrote_xlsx = False
        try:
            with pd.ExcelWriter(xbuf, engine="openpyxl") as writer:
                df_out.to_excel(writer, index=False)
            wrote_xlsx = True
        except Exception as e:
            st.info(f"openpyxl yok/hata: {e}. CSV olarak da indirebilirsiniz.")

        if wrote_xlsx:
            st.download_button("📥 Apsiyon (doldurulmuş).xlsx", xbuf.getvalue(), file_name="Apsiyon_doldurulmus.xlsx")
        csv_bytes = df_out.to_csv(index=False).encode("utf-8-sig")
        st.download_button("📥 Apsiyon (doldurulmuş).csv", csv_bytes, file_name="Apsiyon_doldurulmus.csv")
