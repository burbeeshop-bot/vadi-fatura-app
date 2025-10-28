import streamlit as st
import os, io, tempfile, zipfile, re, textwrap
from typing import Tuple, Optional, List

from pypdf import PdfReader, PdfWriter, PageObject
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

# ---- Sabitler / Font Kurulumu ----
FONTS_DIR = os.path.join(os.path.dirname(__file__), "fonts")
FONT_REG = "NotoSans-Regular"
FONT_BOLD = "NotoSans-Bold"
FONT_ITALIC = "NotoSans-Italic"
FONT_BOLDITALIC = "NotoSans-BoldItalic"

def _register_fonts_once():
    """ReportLab font kayıtlarını tek sefer yap."""
    for name, file in [
        (FONT_REG, "NotoSans-Regular.ttf"),
        (FONT_BOLD, "NotoSans-Bold.ttf"),
        (FONT_ITALIC, "NotoSans-Italic.ttf"),
        (FONT_BOLDITALIC, "NotoSans-BoldItalic.ttf"),
    ]:
        path = os.path.join(FONTS_DIR, file)
        if name not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(TTFont(name, path))

_register_fonts_once()

PAGE_W, PAGE_H = A4  # (595.27, 841.89)

# ---- Yardımcılar ----
def wrap_text(text: str, max_chars: int) -> List[str]:
    """Metni Türkçe karakterleri koruyarak satırlara böl."""
    lines = []
    for raw_line in text.replace("\r", "").split("\n"):
        chunks = textwrap.wrap(raw_line, width=max_chars, break_long_words=False, replace_whitespace=False)
        if not chunks:
            lines.append("")  # boş satır
        else:
            lines.extend(chunks)
    return lines

def draw_footer_overlay(text: str, fontsize: int, bold: bool, margin: Tuple[int, int]) -> bytes:
    """Alt yazı için tek sayfalık saydam overlay PDF (A4)."""
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    fontname = FONT_BOLD if bold else FONT_REG
    c.setFont(fontname, fontsize)

    left_margin, bottom_margin = margin
    # Çok satırlı metin
    lines = wrap_text(text, max_chars=120 if fontsize <= 10 else 95 if fontsize <= 12 else 80)
    y = bottom_margin
    line_height = fontsize * 1.35

    for line in lines:
        c.drawString(left_margin, y, line)
        y += line_height

    c.save()
    buf.seek(0)
    return buf.read()

def draw_corner_label(text: str, fontsize: int, bold: bool, position: str = "TR", padding: Tuple[int, int]=(20, 20)) -> bytes:
    """
    Köşe etiketi (ör: daire numarası) için saydam overlay PDF (A4).
    position: "TL", "TR", "BL", "BR"
    padding: kenarlardan (x,y) px boşluk
    """
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    fontname = FONT_BOLD if bold else FONT_REG
    c.setFont(fontname, fontsize)

    padding_x, padding_y = padding
    text_width = pdfmetrics.stringWidth(text, fontname, fontsize)
    text_height = fontsize * 1.2

    x, y = padding_x, PAGE_H - padding_y - text_height
    if position == "TR":
        x = PAGE_W - padding_x - text_width
        y = PAGE_H - padding_y - text_height
    elif position == "BL":
        x = padding_x
        y = padding_y
    elif position == "BR":
        x = PAGE_W - padding_x - text_width
        y = padding_y
    # TL default

    c.drawString(x, y, text)
    c.save()
    buf.seek(0)
    return buf.read()

def merge_page_with_overlay(page: PageObject, overlay_pdf_bytes: bytes) -> PageObject:
    """Tek sayfalık overlay PDF'i mevcut sayfaya birleştir."""
    ov_reader = PdfReader(io.BytesIO(overlay_pdf_bytes))
    ov_page = ov_reader.pages[0]
    page.merge_page(ov_page)  # pypdf 4.x
    return page

# ---- Daire ID Algılama ----
DAIRE_PATTERNS = [
    # ÖR: "Daire No  A1-blk daire:01"
    re.compile(r"Daire\s*No\s*([A-Z]\d)-blk\s*daire[:\s]*(\d{1,3})", re.IGNORECASE),
    # Ek varyasyonlar istersek buraya eklenir
]

def extract_daire_id_from_text(text: str) -> Optional[str]:
    """Sayfa text'inden A1-001 gibi daireID üret."""
    t = " ".join(text.split())  # whitespace sadeleştir
    for pat in DAIRE_PATTERNS:
        m = pat.search(t)
        if m:
            blk = m.group(1).upper()
            num = int(m.group(2))
            return f"{blk}-{num:03d}"
    return None

# ---- Uygulama UI ----
st.set_page_config(page_title="Vadi Fatura Bölücü ve Kişiselleştirici", page_icon="📄", layout="centered")

st.title("Vadi Fatura Bölücü ve Kişiselleştirici")

pdf_file = st.file_uploader("Fatura PDF dosyasını yükleyin", type=["pdf"])
footer_text = st.text_area(
    "Alt yazı (her sayfanın altına eklenecek)",
    value="Atlas Vadi Sitesi Yönetimi – İletişim: 0 (532) 000 0000",
    height=120
)

with st.expander("Alt yazı / Görsel ayarlar", expanded=False):
    col1, col2, col3 = st.columns(3)
    footer_on = col1.checkbox("Alt yazıyı uygula", value=True)
    footer_font_size = int(col2.slider("Alt yazı punto", min_value=8, max_value=16, value=11, step=1))
    footer_bold = col3.checkbox("Alt yazı kalın", value=False)
    footer_left = int(st.number_input("Alt yazı sol boşluk (px)", min_value=0, max_value=100, value=36, step=2))
    footer_bottom = int(st.number_input("Alt yazı alt boşluk (px)", min_value=0, max_value=100, value=28, step=2))

with st.expander("Daire numarası etiketi", expanded=True):
    stamp_on = st.checkbox("Daire numarasını köşeye yaz", value=True)
    colp1, colp2, colp3 = st.columns(3)
    stamp_font = int(colp1.slider("Etiket punto", min_value=10, max_value=20, value=13, step=1))
    stamp_bold = colp2.checkbox("Etiket kalın", value=True)
    pos = colp3.selectbox("Konum", options=["TR", "TL", "BR", "BL"], index=0)
    prefix = st.text_input("Etiket metin şablonu", value="Daire: {daire_id}")
    pad_x = int(st.number_input("Köşe yatay boşluk (px)", min_value=0, max_value=80, value=20, step=2))
    pad_y = int(st.number_input("Köşe dikey boşluk (px)", min_value=0, max_value=80, value=20, step=2))

colA, colB = st.columns(2)
only_split = colA.radio("Çıktı tipi", ["Alt yazı uygula + böl", "Sadece böl", "Alt yazı uygula + tek PDF"], index=0)
go = colB.button("İşle ve indir")

# ---- İş Akışı ----
def process_pdf(input_bytes: bytes) -> Tuple[bytes, List[Tuple[str, bytes]]]:
    """
    Girdi PDF'yi sayfalara böl; (opsiyonel) her sayfaya footer ve daire etiketi uygula.
    Dönüş:
      - tek PDF bytes (tüm sayfalar birleştirilmiş)
      - [(dosya adı, bytes)] sayfalara bölünmüş liste
    """
    reader = PdfReader(io.BytesIO(input_bytes))
    single_writer = PdfWriter()
    split_pages: List[Tuple[str, bytes]] = []

    # Hazır overlay'ler (performans için bir kez üretilecek)
    footer_overlay = None
    if footer_on and footer_text.strip():
        footer_overlay = draw_footer_overlay(
            footer_text.strip(),
            fontsize=footer_font_size,
            bold=footer_bold,
            margin=(footer_left, footer_bottom)
        )

    for idx, page in enumerate(reader.pages, start=1):
        # Daire ID çıkar
        daire_id = None
        try:
            txt = page.extract_text() or ""
            daire_id = extract_daire_id_from_text(txt)
        except Exception:
            daire_id = None

        # Çalışma kopyası
        page_mod = PageObject.create_blank_page(width=page.mediabox.right, height=page.mediabox.top)
        page_mod.merge_page(page)

        # Daire etiketi
        if stamp_on and daire_id:
            label_txt = prefix.format(daire_id=daire_id)
            overlay = draw_corner_label(label_txt, fontsize=stamp_font, bold=stamp_bold, position=pos, padding=(pad_x, pad_y))
            page_mod = merge_page_with_overlay(page_mod, overlay)

        # Footer
        if footer_overlay:
            page_mod = merge_page_with_overlay(page_mod, footer_overlay)

        # Tek PDF için ekle
        single_writer.add_page(page_mod)

        # Tek tek dosyalar için yaz
        w = PdfWriter()
        w.add_page(page_mod)
        out_bytes = io.BytesIO()
        w.write(out_bytes)
        out_bytes.seek(0)

        # Dosya adı
        base_name = f"page_{idx:03d}.pdf"
        if daire_id:
            base_name = f"{daire_id}.pdf"
        split_pages.append((base_name, out_bytes.read()))

    # Tek PDF
    single_buf = io.BytesIO()
    single_writer.write(single_buf)
    single_buf.seek(0)
    return single_buf.read(), split_pages

if go:
    if not pdf_file:
        st.warning("Lütfen önce bir PDF yükleyin.")
    else:
        with st.spinner("İşleniyor…"):
            all_pdf, split_files = process_pdf(pdf_file.read())

        # Çıktılar
        if only_split == "Alt yazı uygula + tek PDF":
            st.download_button("Tek PDF indir", data=all_pdf, file_name="fatura_islenmis.pdf", mime="application/pdf")
        elif only_split == "Sadece böl":
            # yalnızca böl (footer ve etiket ayarları kapalı kabul edilir)
            # Ancak kullanıcı footer/etiket açık bırakmış olabilir.
            # Bu durumda yeniden sadece böl yapalım:
            # Basit yol: split_files zaten işlenmiş, fakat kullanıcı “sadece böl” dediği için
            # footer/etiketsiz bir tur daha yapıyoruz.
            reader = PdfReader(io.BytesIO(pdf_file.getvalue()))
            zbuf = io.BytesIO()
            with zipfile.ZipFile(zbuf, "w", zipfile.ZIP_DEFLATED) as z:
                for idx, page in enumerate(reader.pages, start=1):
                    w = PdfWriter()
                    w.add_page(page)
                    outb = io.BytesIO()
                    w.write(outb)
                    outb.seek(0)
                    z.writestr(f"page_{idx:03d}.pdf", outb.read())
            zbuf.seek(0)
            st.download_button("Bölünmüş PDF’ler (ZIP)", data=zbuf.getvalue(), file_name="bolunmus_pdfler.zip", mime="application/zip")
        else:
            # Alt yazı uygula + böl
            zbuf = io.BytesIO()
            with zipfile.ZipFile(zbuf, "w", zipfile.ZIP_DEFLATED) as z:
                for fname, data in split_files:
                    z.writestr(fname, data)
            zbuf.seek(0)
            st.download_button("Bölünmüş PDF’ler (ZIP)", data=zbuf.getvalue(), file_name="bolunmus_pdfler.zip", mime="application/zip")

# Alt bilgi
st.caption("© Atlas Vadi – PDF yardımcı aracı")
