import streamlit as st
import os, io, shutil, tempfile, zipfile
from pypdf import PdfReader, PdfWriter
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4

st.set_page_config(page_title="Vadi Fatura Bölücü", page_icon="📄", layout="wide")
st.title("📄 Vadi Fatura Bölücü ve Kişiselleştirici")

uploaded_file = st.file_uploader("Fatura PDF dosyasını yükleyin", type=["pdf"])
footer_text = st.text_area(
    "Alt yazı (her sayfanın altına eklenecek)",
    value="Atlas Vadi Sitesi Yönetimi – İletişim: 0 (532) 000 0000",
    height=90,
)

def add_footer_to_page(page, text):
    # sayfanın altına yazı bind edecek tek-sayfa PDF üret
    packet = io.BytesIO()
    c = canvas.Canvas(packet, pagesize=A4)
    c.setFont("Helvetica", 9)
    c.drawString(72, 25, text)  # (x,y) 72pt = 1 inch soldan
    c.save()
    packet.seek(0)
    overlay = PdfReader(packet).pages[0]
    page.merge_page(overlay)
    return page

if uploaded_file:
    st.success("PDF yüklendi ✅")

    reader = PdfReader(uploaded_file)
    n = len(reader.pages)
    st.write(f"Toplam **{n}** sayfa bulundu.")

    # Her oturum için geçici klasör
    workdir = tempfile.mkdtemp(prefix="vadi_split_")
    outdir = os.path.join(workdir, "split_pdfs")
    os.makedirs(outdir, exist_ok=True)

    # sayfaları böl + footer ekle
    with st.spinner("Sayfalar ayrılıyor ve alt yazı ekleniyor…"):
        for i in range(n):
            writer = PdfWriter()
            page = reader.pages[i]
            page = add_footer_to_page(page, footer_text)
            writer.add_page(page)
            out_path = os.path.join(outdir, f"page_{i+1:03}.pdf")
            with open(out_path, "wb") as f:
                writer.write(f)

    st.success(f"Tüm sayfalar ayrıldı. Klasör: `{os.path.basename(outdir)}`")

    # Dosyaları listele
    files = sorted(os.listdir(outdir))
    st.write("İlk 10 dosya:", files[:10])

    # ZIP oluştur ve indir butonu
    zip_bytes = io.BytesIO()
    with zipfile.ZipFile(zip_bytes, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for fname in files:
            zf.write(os.path.join(outdir, fname), arcname=fname)
    zip_bytes.seek(0)

    st.download_button(
        label="⬇️ Tüm çıktı PDF’leri ZIP indir",
        data=zip_bytes,
        file_name="split_pdfs.zip",
        mime="application/zip",
    )

    st.info("Not: Şu an dosyalar oturumda tutuluyor. Bir sonraki adımda Google Drive’a otomatik yüklemeyi ekleyeceğiz.")
else:
    st.warning("Lütfen önce bir PDF yükleyin.")
