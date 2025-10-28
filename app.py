import streamlit as st
import io, zipfile, tempfile, os, textwrap
from pypdf import PdfReader, PdfWriter
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

# ----------------------------
# Font hazırlığı (Türkçe için)
# ----------------------------
# Repo içindeki fonts klasörünü deneyelim:
FONTS_DIR = os.path.join(os.path.dirname(__file__), "fonts")
REGULAR_FONT_PATH = os.path.join(FONTS_DIR, "NotoSans-Regular.ttf")
BOLD_FONT_PATH    = os.path.join(FONTS_DIR, "NotoSans-Bold.ttf")

REGULAR_FONT_NAME = "NotoSans-Regular"
BOLD_FONT_NAME    = "NotoSans-Bold"

def try_register_fonts():
    # Fontlar varsa kaydet; yoksa Helvetica'ya düşer (Türkçe kare çıkarabilir)
    try:
        if os.path.exists(REGULAR_FONT_PATH):
            pdfmetrics.registerFont(TTFont(REGULAR_FONT_NAME, REGULAR_FONT_PATH))
        if os.path.exists(BOLD_FONT_PATH):
            pdfmetrics.registerFont(TTFont(BOLD_FONT_NAME, BOLD_FONT_PATH))
    except Exception as e:
        # Font kaydı başarısızsa Helvetica ile devam
        pass

try_register_fonts()

def pick_font(bold: bool) -> str:
    # Türkçe glifli font varsa onu, yoksa Helvetica
    if bold and BOLD_FONT_NAME in pdfmetrics.getRegisteredFontNames():
        return BOLD_FONT_NAME
    if REGULAR_FONT_NAME in pdfmetrics.getRegisteredFontNames():
        return REGULAR_FONT_NAME
    return "Helvetica"

# ------------------------------------------------
# Alt yazıyı sayfa altına çok satırlı olarak basma
# ------------------------------------------------
def make_footer_overlay(page_width, page_height, footer_text, font_name="Helvetica",
                        font_size=9, bottom_margin=24, left_margin=24, line_spacing=1.25, max_width=None):
    """
    Verilen sayfa boyutuna uygun tek sayfalık bir PDF overlay üretir ve BytesIO döner.
    footer_text: çok satır (\\n) içerebilir; ayrıca uzun satırlar genişliğe göre kırpılır.
    """
    if max_width is None:
        # sağ ve soldan aynı boşluk
        max_width = page_width - 2 * left_margin

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(page_width, page_height))
    c.setFont(font_name, font_size)

    # footer_text'i satır sonlarına göre parçala
    paragraphs = footer_text.splitlines()
    wrapped_lines = []

    # Genişliğe göre kelime kaydır
    for para in paragraphs:
        if not para.strip():
            wrapped_lines.append("")  # boş satır korunsun
            continue

        # reportlab'ta otomatik wrap yok, stringWidth ile el ile sarıyoruz
        words = para.split(" ")
        line = ""
        for w in words:
            test = (line + " " + w).strip()
            width = pdfmetrics.stringWidth(test, font_name, font_size)
            if width <= max_width:
                line = test
            else:
                if line:
                    wrapped_lines.append(line)
                line = w
        if line:
            wrapped_lines.append(line)

    # En alttan yukarı doğru yaz
    # İlk satır en altta gözüksün istiyoruz.
    y = bottom_margin
    line_height = font_size * line_spacing

    # wrapped_lines'ı tersten yazarsak en altta ilk satır olur; ancak genelde
    # paragrafın ilk satırı altta değil üstte olsun isteriz. O yüzden normal sırayla
    # ama y'yi alttan yukarı artırarak yazacağız:
    for i, line in enumerate(wrapped_lines):
        c.drawString(left_margin, y, line)
        y += line_height

        # Çok aşırı uzun metin altta taşarsa, kırpmıyoruz; üstte biter.

    c.save()
    buf.seek(0)
    return buf

# -------------------------------------------
# Mevcut PDF'e footer'ı her sayfaya uygula
# -------------------------------------------
def apply_footer_to_pdf(src_pdf_bytes: bytes, footer_text: str,
                        bold: bool = False, font_size: int = 9, bottom_margin: int = 24,
                        left_margin: int = 24, line_spacing: float = 1.25) -> bytes:
    reader = PdfReader(io.BytesIO(src_pdf_bytes))
    writer = PdfWriter()

    font_name = pick_font(bold)

    for page in reader.pages:
        # Sayfa ölçüsünü al
        pw = float(page.mediabox.width)
        ph = float(page.mediabox.height)

        # Overlay üret
        overlay_buf = make_footer_overlay(
            page_width=pw,
            page_height=ph,
            footer_text=footer_text,
            font_name=font_name,
            font_size=font_size,
            bottom_margin=bottom_margin,
            left_margin=left_margin,
            line_spacing=line_spacing,
            max_width=pw - 2*left_margin
        )

        overlay_reader = PdfReader(overlay_buf)
        overlay_page = overlay_reader.pages[0]

        # pypdf 4.x: merge_page -> page.merge_page(...)
        page.merge_page(overlay_page)
        writer.add_page(page)

    out_buf = io.BytesIO()
    writer.write(out_buf)
    out_buf.seek(0)
    return out_buf.read()

# -------------------------------------------
# PDF'i sayfa sayfa böl ve ZIP olarak ver
# -------------------------------------------
def split_pdf_to_zip(src_pdf_bytes: bytes, base_name: str = "page") -> bytes:
    reader = PdfReader(io.BytesIO(src_pdf_bytes))
    zip_buf = io.BytesIO()
    with zipfile.ZipFile(zip_buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for i in range(len(reader.pages)):
            writer = PdfWriter()
            writer.add_page(reader.pages[i])
            single_buf = io.BytesIO()
            writer.write(single_buf)
            single_buf.seek(0)
            filename = f"{base_name}-{i+1:03d}.pdf"
            zf.writestr(filename, single_buf.read())
    zip_buf.seek(0)
    return zip_buf.read()

# =======================
#         UI
# =======================
st.set_page_config(page_title="Vadi Fatura Bölücü", layout="wide")
st.title("Vadi Fatura Bölücü ve Kişiselleştirici")

uploaded = st.file_uploader("Fatura PDF dosyasını yükleyin", type=["pdf"])

default_footer = (
    "SON ÖDEME TARİHİ     24.10.2025\n\n"
    "Manas paylaşımlarında oturumda olup ( 0 ) gelen dairelerin önceki ödediği paylaşım  tutarları baz alınarak "
    "bedel yansıtılması; ayrıca İski  su sayacının okuduğu harcama tutarı ile site içerisindeki harcama tutarı "
    "arasındaki farkın İSKİ faturasının ödenebilmesi için 152 daireye eşit olarak yansıtılması oya sunuldu. "
    "Oybirliği ile kabul edildi.\n\n"
    "28.02.2017 TARİHLİ TEMSİLCİLER OLAĞAN TOPLANTISINDA ALINAN KARARA İSTİNADEN\n"
    "AÇIKLAMA\n"
    "İski saatinden okunan m3 = 1.319  M3\n"
    "Manas okuması m3= 1.202,5 M3\n"
    "Ortak alan tüketimler m3= 32  M3 \n"
    "Açıkta kalan:  84,5 m3     \n"
    "Su m3 fiyatı 82,09   TL            84,5*82,9 = 7.005,05 TL/ 152= 46,08  TL."
)

footer_text = st.text_area(
    "Alt yazı (her sayfanın altına eklenecek – çok satır destekli)",
    value=default_footer,
    height=260
)

col1, col2, col3, col4 = st.columns(4)
with col1:
    bold = st.checkbox("Kalın yazı", value=False)
with col2:
    font_size = st.number_input("Yazı boyutu", 6, 18, 9, 1)
with col3:
    bottom_margin = st.number_input("Alt boşluk (px)", 10, 120, 26, 2)
with col4:
    left_margin = st.number_input("Sol boşluk (px)", 10, 120, 24, 2)

st.divider()

if uploaded is None:
    st.info("Lütfen önce bir PDF yükleyin.")
else:
    st.success("PDF yüklendi ✅")
    do_apply = st.button("Alt yazıyı uygulayıp tek PDF indir", type="primary")
    do_split = st.button("Sadece sayfalara böl (ZIP indir)")

    if do_apply:
        with st.spinner("Alt yazı ekleniyor..."):
            src_bytes = uploaded.read()
            out_bytes = apply_footer_to_pdf(
                src_pdf_bytes=src_bytes,
                footer_text=footer_text,
                bold=bold,
                font_size=int(font_size),
                bottom_margin=int(bottom_margin),
                left_margin=int(left_margin),
                line_spacing=1.25
            )
        st.download_button(
            "İndir: Alt yazılı PDF",
            data=out_bytes,
            file_name="fatura_alt_yazili.pdf",
            mime="application/pdf"
        )

    if do_split:
        with st.spinner("Sayfalara bölünüyor..."):
            src_bytes = uploaded.read()
            zip_bytes = split_pdf_to_zip(src_bytes, base_name="fatura_sayfa")
        st.download_button(
            "İndir: Bölünmüş sayfalar (ZIP)",
            data=zip_bytes,
            file_name="fatura_sayfalar.zip",
            mime="application/zip"
        )
