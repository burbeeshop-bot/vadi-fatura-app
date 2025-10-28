import streamlit as st
import os
from PyPDF2 import PdfReader, PdfWriter
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
from io import BytesIO

# --- AYARLAR ---
st.set_page_config(page_title="Vadi Fatura Bölücü", page_icon="📄", layout="wide")

st.title("📄 Vadi Fatura Bölücü ve Kişiselleştirici")

# --- PDF YÜKLEME ---
uploaded_file = st.file_uploader("Fatura PDF dosyasını yükleyin", type=["pdf"])

footer_text = st.text_area("Alt yazı (her sayfanın altına eklenecek)", 
                           value="Atlas Vadi Sitesi Yönetimi – İletişim: 0 (532) 000 0000",
                           height=100)

if uploaded_file:
    st.success("PDF yüklendi ✅")

    # PDF'i oku
    reader = PdfReader(uploaded_file)
    num_pages = len(reader.pages)
    st.write(f"Toplam {num_pages} sayfa bulundu.")

    output_dir = "split_pdfs"
    os.makedirs(output_dir, exist_ok=True)

    for i in range(num_pages):
        writer = PdfWriter()
        page = reader.pages[i]
        writer.add_page(page)

        # --- Alt yazı ekle ---
        packet = BytesIO()
        can = canvas.Canvas(packet, pagesize=A4)
        can.setFont("Helvetica", 9)
        can.drawString(100, 25, footer_text)
        can.save()

        packet.seek(0)
        overlay_pdf = PdfReader(packet)
        page.merge_page(overlay_pdf.pages[0])

        # Kaydet
        output_filename = os.path.join(output_dir, f"page_{i+1:03}.pdf")
        with open(output_filename, "wb") as f_out:
            writer.write(f_out)

    st.success(f"Tüm sayfalar ayrıldı ve '{output_dir}' klasörüne kaydedildi ✅")

    st.info("⚙️ Bu dosyaları otomatik olarak Google Drive’a yükleme eklentisi bir sonraki adımda eklenecek.")
else:
    st.warning("Lütfen önce bir PDF yükleyin.")
