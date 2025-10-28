import streamlit as st
import os, io, zipfile
from pypdf import PdfReader, PdfWriter
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4

st.set_page_config(page_title="Vadi Fatura Bölücü", page_icon="📄", layout="centered")

st.title("📄 Vadi Fatura Bölücü ve Kişiselleştirici")

uploaded_file = st.file_uploader("Fatura PDF dosyasını yükleyin", type=["pdf"])
footer_text = st.text_area("Alt yazı (her sayfanın altına eklenecek)",
                           "Atlas Vadi Sitesi Yönetimi – İletişim: 0 (532) 000 0000")

option = st.radio("Ne yapmak istersiniz?",
                  ["Alt yazı uygula ve tek PDF indir",
                   "Sadece sayfalara böl",
                   "Alt yazıyı uygula ve sayfalara böl (ZIP indir)"])

def add_footer_to_page(page, footer_text):
    packet = io.BytesIO()
    can = canvas.Canvas(packet, pagesize=A4)
    can.setFont("Helvetica", 9)
    can.drawString(40, 40, footer_text)
    can.save()
    packet.seek(0)
    new_pdf = PdfReader(packet)
    page.merge_page(new_pdf.pages[0])
    return page

if uploaded_file:
    reader = PdfReader(uploaded_file)

    if option == "Alt yazı uygula ve tek PDF indir":
        writer = PdfWriter()
        for page in reader.pages:
            page = add_footer_to_page(page, footer_text)
            writer.add_page(page)

        output = io.BytesIO()
        writer.write(output)
        st.download_button("📥 Alt yazılı tek PDF indir",
                           data=output.getvalue(),
                           file_name="fatura_alt_yazili.pdf")

    elif option == "Sadece sayfalara böl":
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, "w") as zipf:
            for i, page in enumerate(reader.pages, start=1):
                writer = PdfWriter()
                writer.add_page(page)
                buf = io.BytesIO()
                writer.write(buf)
                zipf.writestr(f"page_{i:03d}.pdf", buf.getvalue())
        st.download_button("📂 Sayfalara bölünmüş ZIP indir",
                           data=zip_buffer.getvalue(),
                           file_name="fatura_sayfalara_bolunmus.zip")

    elif option == "Alt yazıyı uygula ve sayfalara böl (ZIP indir)":
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, "w") as zipf:
            for i, page in enumerate(reader.pages, start=1):
                writer = PdfWriter()
                page = add_footer_to_page(page, footer_text)
                writer.add_page(page)
                buf = io.BytesIO()
                writer.write(buf)
                zipf.writestr(f"fatura_{i:03d}.pdf", buf.getvalue())

        st.download_button("📦 Alt yazılı ve bölünmüş ZIP indir",
                           data=zip_buffer.getvalue(),
                           file_name="fatura_alt_yazili_bolunmus.zip")
else:
    st.warning("Lütfen önce bir PDF yükleyin.")
