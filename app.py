import streamlit as st
import io, zipfile, textwrap
from pypdf import PdfReader, PdfWriter
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

# --- Font kayıtları ---
pdfmetrics.registerFont(TTFont("NotoSans-Regular", "fonts/NotoSans-Regular.ttf"))
pdfmetrics.registerFont(TTFont("NotoSans-Bold", "fonts/NotoSans-Bold.ttf"))

# --- PDF alt yazısı oluşturucu ---
def make_footer_overlay(page_width, page_height, footer_text, font_size=11):
    packet = io.BytesIO()
    c = canvas.Canvas(packet, pagesize=(page_width, page_height))

    left_margin = 36
    bottom_margin = 48
    max_width = page_width - 72
    line_height = font_size + 3

    # Metni satırlara böl (otomatik kaydırma)
    lines = textwrap.wrap(footer_text, width=int(max_width / (font_size * 0.55)))
    y = bottom_margin + len(lines) * line_height

    for i, line in enumerate(lines):
        if i == 0 and line.strip().startswith("SON ÖDEME"):
            c.setFont("NotoSans-Bold", font_size)
        else:
            c.setFont("NotoSans-Regular", font_size)
        c.drawString(left_margin, y - i * line_height, line)

    c.save()
    packet.seek(0)
    return packet

# --- Alt yazıyı PDF'e uygula ---
def add_footer_to_pdf(pdf_bytes, footer_text, font_size=11):
    reader = PdfReader(io.BytesIO(pdf_bytes))
    writer = PdfWriter()

    for page in reader.pages:
        width = float(page.mediabox.width)
        height = float(page.mediabox.height)
        overlay = PdfReader(make_footer_overlay(width, height, footer_text, font_size))
        page.merge_page(overlay.pages[0])
        writer.add_page(page)

    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()

# --- PDF'i sayfalara böl ---
def split_pdf(pdf_bytes):
    reader = PdfReader(io.BytesIO(pdf_bytes))
    result = []
    for i, page in enumerate(reader.pages, start=1):
        writer = PdfWriter()
        writer.add_page(page)
        page_buf = io.BytesIO()
        writer.write(page_buf)
        result.append((f"page_{i:03}.pdf", page_buf.getvalue()))
    return result

# --- Streamlit Arayüzü ---
st.set_page_config(page_title="Vadi Fatura Uygulaması", page_icon="🧾")

st.title("📄 Vadi Fatura Bölücü + Alt Yazı Uygulayıcı")
st.markdown("PDF’leri sayfalara bölebilir, altına açıklama ekleyebilir veya ikisini birden yapabilirsiniz.")

uploaded = st.file_uploader("PDF dosyasını yükle", type=["pdf"])
footer_text = st.text_area(
    "Alt Yazı (çok satırlı destekli, Türkçe uyumlu)",
    value="SON ÖDEME TARİHİ     24.10.2025\n\nAtlas Vadi Sitesi Yönetimi",
    height=200
)
font_size = st.slider("🅰️ Yazı Boyutu", 8, 20, 11)
mode = st.radio("İşlem Türü", [
    "Sadece sayfalara böl",
    "Sadece alt yazı uygula (tek PDF indir)",
    "Alt yazı uygula + sayfalara böl"
])
run = st.button("🚀 İşlemi Başlat")

if run:
    if not uploaded:
        st.warning("Lütfen bir PDF yükleyin.")
        st.stop()

    pdf_bytes = uploaded.read()

    if mode == "Sadece sayfalara böl":
        pages = split_pdf(pdf_bytes)
        with io.BytesIO() as z:
            with zipfile.ZipFile(z, "w") as zf:
                for name, data in pages:
                    zf.writestr(name, data)
            st.download_button("📥 ZIP olarak indir", z.getvalue(), file_name="bolunmus_sayfalar.zip")

    elif mode == "Sadece alt yazı uygula (tek PDF indir)":
        output = add_footer_to_pdf(pdf_bytes, footer_text, font_size)
        st.download_button("📥 Alt Yazılı PDF İndir", output, file_name="altyazili.pdf")

    else:
        stamped = add_footer_to_pdf(pdf_bytes, footer_text, font_size)
        pages = split_pdf(stamped)
        with io.BytesIO() as z:
            with zipfile.ZipFile(z, "w") as zf:
                for name, data in pages:
                    zf.writestr(name, data)
            st.download_button("📥 Alt Yazılı ve Bölünmüş (ZIP) İndir", z.getvalue(), file_name="altyazili_bolunmus.zip")
