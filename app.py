import streamlit as st
import io, zipfile
from pypdf import PdfReader, PdfWriter
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

st.set_page_config(page_title="Vadi Fatura Bölücü", page_icon="📄", layout="centered")
st.title("📄 Vadi Fatura Bölücü ve Kişiselleştirici")

# --- Türkçe TTF fontları kaydet ---
# Repo: fonts/NotoSans-Regular.ttf ve fonts/NotoSans-Bold.ttf
pdfmetrics.registerFont(TTFont("NotoSans", "fonts/NotoSans-Regular.ttf"))
pdfmetrics.registerFont(TTFont("NotoSans-Bold", "fonts/NotoSans-Bold.ttf"))

uploaded_file = st.file_uploader("Fatura PDF dosyasını yükleyin", type=["pdf"])

default_footer = (
    "SON ÖDEME TARİHİ 24.10.2025\n\n"
    "Manas paylaşımlarında oturumda olup (0) gelen dairelerin önceki ödediği paylaşım tutarları baz alınarak "
    "bedel yansıtılması; ayrıca İSKİ su sayacının okuduğu harcama tutarı ile site içerisindeki harcama tutarı "
    "arasındaki farkın İSKİ faturasının ödenebilmesi için 152 daireye eşit olarak yansıtılması oya sunuldu. "
    "Oybirliği ile kabul edildi.\n\n"
    "28.02.2017 TARİHLİ TEMSİLCİLER OLAĞAN TOPLANTISINDA ALINAN KARARA İSTİNADEN\n"
    "AÇIKLAMA\n"
    "İski saatinden okunan m3 = 1.319 M3\n"
    "Manas okuması m3= 1.202,5 M3\n"
    "Ortak alan tüketimler m3= 32 M3\n"
    "Açıkta kalan: 84,5 m3\n"
    "Su m3 fiyatı 82,09 TL   84,5*82,9 = 7.005,05 TL / 152 = 46,08 TL."
)

footer_text = st.text_area(
    "Alt yazı (çok satır destekli, Türkçe karakterli)",
    value=default_footer,
    height=220
)

option = st.radio(
    "Ne yapmak istersiniz?",
    [
        "Alt yazı uygula ve tek PDF indir",
        "Sadece sayfalara böl",
        "Alt yazıyı uygula ve sayfalara böl (ZIP indir)"
    ],
    index=2
)

def draw_multiline_footer(can: canvas.Canvas, text: str, left=40, bottom_margin=40,
                          leading=14, normal_size=10, bold_size=10.5):
    """
    Çok satırlı footer’ı sayfanın alt bölümüne, yukarıdan aşağı DOĞRU sırayla basar.
    'SON ÖDEME TARİHİ' ve 'AÇIKLAMA' gibi satırları kalın yapar.
    """
    lines = [ln.rstrip() for ln in text.replace("\r\n", "\n").split("\n")]

    # Kaç satır varsa, üst satırın y’sini hesapla ki alttaki satırlar taşmasın
    # textLine aşağı doğru ilerlediği için ilk satırı daha yukarıdan başlatıyoruz.
    y_start = bottom_margin + leading * (len(lines)-1) + 6  # +6 küçük nefes payı

    can.setFont("NotoSans", normal_size)
    textobj = can.beginText()
    textobj.setTextOrigin(left, y_start)

    for ln in lines:
        # Basit bir otomatik bold kuralı (istersen kaldırabilirsin):
        upper = ln.strip().upper()
        if upper.startswith("SON ÖDEME TARİHİ") or upper == "AÇIKLAMA" or "TARİHLİ TEMSİLCİLER" in upper:
            textobj.setFont("NotoSans-Bold", bold_size)
        else:
            textobj.setFont("NotoSans", normal_size)
        textobj.textLine(ln)

    can.drawText(textobj)

def add_footer_to_page(page, footer):
    packet = io.BytesIO()
    can = canvas.Canvas(packet, pagesize=A4)
    # Çok satırlı, Türkçe karakterli alt yazı
    draw_multiline_footer(can, footer_text)
    can.save()
    packet.seek(0)
    overlay = PdfReader(packet)
    page.merge_page(overlay.pages[0])
    return page

if uploaded_file:
    reader = PdfReader(uploaded_file)

    if option == "Alt yazı uygula ve tek PDF indir":
        writer = PdfWriter()
        for page in reader.pages:
            writer.add_page(add_footer_to_page(page, footer_text))
        buff = io.BytesIO()
        writer.write(buff)
        st.download_button("📥 Alt yazılı tek PDF indir", buff.getvalue(), "fatura_alt_yazili.pdf")

    elif option == "Sadece sayfalara böl":
        zbuf = io.BytesIO()
        with zipfile.ZipFile(zbuf, "w") as z:
            for i, page in enumerate(reader.pages, start=1):
                w = PdfWriter()
                w.add_page(page)
                b = io.BytesIO(); w.write(b)
                z.writestr(f"page_{i:03d}.pdf", b.getvalue())
        st.download_button("📂 Sayfalara bölünmüş ZIP indir", zbuf.getvalue(), "fatura_sayfalara_bolunmus.zip")

    else:  # Alt yazıyı uygula ve sayfalara böl (ZIP)
        zbuf = io.BytesIO()
        with zipfile.ZipFile(zbuf, "w") as z:
            for i, page in enumerate(reader.pages, start=1):
                w = PdfWriter()
                w.add_page(add_footer_to_page(page, footer_text))
                b = io.BytesIO(); w.write(b)
                z.writestr(f"fatura_{i:03d}.pdf", b.getvalue())
        st.download_button("📦 Alt yazılı ve bölünmüş ZIP indir", zbuf.getvalue(), "fatura_alt_yazili_bolunmus.zip")
else:
    st.info("Lütfen önce bir PDF yükleyin.")
