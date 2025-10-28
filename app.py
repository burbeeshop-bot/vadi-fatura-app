import streamlit as st
import io, zipfile
from typing import List
from pypdf import PdfReader, PdfWriter
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib.pagesizes import A4

# (Opsiyonel) .docx'ten alt yazı çekmek için
try:
    import docx  # python-docx
    HAS_DOCX = True
except Exception:
    HAS_DOCX = False

# ---------- FONT KAYITLARI (Türkçe) ----------
# Repo'da fonts/NotoSans-Regular.ttf ve fonts/NotoSans-Bold.ttf olmalı
pdfmetrics.registerFont(TTFont("NotoSans-Regular", "fonts/NotoSans-Regular.ttf"))
pdfmetrics.registerFont(TTFont("NotoSans-Bold",    "fonts/NotoSans-Bold.ttf"))

# ---------- METİN SARMA (piksel/genişlik ile) ----------
def wrap_by_width(text: str, font_name: str, font_size: float, max_width: float) -> List[str]:
    """
    Satırları, gerçek yazı genişliğine göre kelime kelime sarar.
    Boş satırları korur; çok uzun tek kelimeyi de parçalar.
    """
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
                # tek kelimenin kendisi bile sığmıyorsa harf harf böl
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

# ---------- ALT YAZI OVERLAY OLUŞTUR ----------
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
):
    """
    Sayfa altına çok satırlı alt yazı overlay'i üretir (BytesIO döner).
    Satır sırası KORUNUR. Taşma olursa box yüksekliği kadar basılır.
    """
    packet = io.BytesIO()
    can = canvas.Canvas(packet, pagesize=(page_w, page_h))

    # Yazı alanı genişliği (soldan-sağa)
    left_margin = 36
    right_margin = 36
    max_text_width = page_w - left_margin - right_margin

    # Metni uygun genişliğe göre sar
    wrapped = wrap_by_width(footer_text, "NotoSans-Regular", font_size, max_text_width)

    # Sığacak maksimum satır
    max_lines = max(1, int(box_height // leading))
    if len(wrapped) > max_lines:
        wrapped = wrapped[:max_lines]

    # Üst satırın başlangıç Y pozisyonu (alta yakın kutu içinde yukarıdan aşağı yazacağız)
    y_start = bottom_margin + (len(wrapped) - 1) * leading + 4  # küçük nefes payı

    # Satır satır yaz
    for i, line in enumerate(wrapped):
        # Kalınlaştırma kuralları
        use_bold = False
        if bold_rules:
            u = line.strip().upper()
            if i == 0 and u.startswith("SON ÖDEME"):  # 1. satır "SON ÖDEME..." ise kalın
                use_bold = True
            if u == "AÇIKLAMA":
                use_bold = True
            if "TARİHLİ TEMSİLCİLER" in u:
                use_bold = True

        can.setFont("NotoSans-Bold" if use_bold else "NotoSans-Regular", font_size)

        y = y_start - i * leading
        if align == "center":
            # ortalı
            can.drawCentredString(page_w / 2.0, y, line)
        else:
            # sola hizalı
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

# ---------- STREAMLIT UI ----------
st.set_page_config(page_title="Fatura Bölücü • Atlas Vadi", page_icon="🧾", layout="centered")
st.title("📄 Fatura • Böl & Alt Yazı Ekle")

pdf_file = st.file_uploader("Fatura PDF dosyasını yükle", type=["pdf"])

st.subheader("Alt Yazı Kaynağı")
tab1, tab2 = st.tabs(["✍️ Metin alanı", "📄 .docx yükle (opsiyonel)"])

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

with tab1:
    footer_text = st.text_area("Alt yazı", value=default_text, height=220)

with tab2:
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
    font_size = st.slider("🅰️ Yazı Boyutu", 9, 16, 11)
    leading   = st.slider("↕️ Satır Aralığı (pt)", 12, 22, 14)
with c2:
    align     = st.radio("Hizalama", ["left", "center"], index=0, format_func=lambda x: "Sol" if x=="left" else "Orta")
    bottom_m  = st.slider("Alt Marj (pt)", 24, 100, 48)
box_h = st.slider("Alt Yazı Alanı Yüksekliği (pt)", 100, 260, 180)
bold_rules = st.checkbox("Başlıkları otomatik kalın yap (SON ÖDEME, AÇIKLAMA, ...)", value=True)

st.subheader("İşlem")
mode = st.radio(
    "Ne yapmak istersiniz?",
    ["Sadece sayfalara böl", "Sadece alt yazı uygula (tek PDF)", "Alt yazı uygula + sayfalara böl (ZIP)"],
    index=2
)
go = st.button("🚀 Başlat")

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
