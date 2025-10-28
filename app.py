import streamlit as st
import io, zipfile, re
from typing import List, Tuple, Optional

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

PAGE_W, PAGE_H = A4  # (595.27, 841.89)

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

# ---------- (YENİ) DAİRE NO ALGILAMA & KÖŞE ETİKETİ ----------
# Manas formatı ör.: "Daire No  A1-blk daire:01"
DAIRE_PATTERNS = [
    re.compile(r"Daire\s*No\s*([A-Z]\d)-blk\s*daire[:\s]*(\d{1,3})", re.IGNORECASE),
]

def extract_daire_id_from_text(text: str) -> Optional[str]:
    t = " ".join((text or "").split())
    for pat in DAIRE_PATTERNS:
        m = pat.search(t)
        if m:
            blk = m.group(1).upper()
            num = int(m.group(2))
            return f"{blk}-{num:03d}"
    return None

def build_corner_label_overlay(
    page_w: float, page_h: float, label_text: str,
    font_size: int = 13, bold: bool = True,
    position: str = "TR", pad_x: int = 20, pad_y: int = 20
) -> io.BytesIO:
    """Köşeye (TL/TR/BL/BR) daire etiketi basmak için tek sayfalık overlay üretir."""
    packet = io.BytesIO()
    can = canvas.Canvas(packet, pagesize=(page_w, page_h))
    font_name = "NotoSans-Bold" if bold else "NotoSans-Regular"
    can.setFont(font_name, font_size)
    text_w = pdfmetrics.stringWidth(label_text, font_name, font_size)
    text_h = font_size * 1.2

    # Koordinatlar
    if position == "TR":
        x = page_w - pad_x - text_w
        y = page_h - pad_y - text_h
    elif position == "TL":
        x = pad_x
        y = page_h - pad_y - text_h
    elif position == "BR":
        x = page_w - pad_x - text_w
        y = pad_y
    else:  # BL
        x = pad_x
        y = pad_y

    can.drawString(x, y, label_text)
    can.save()
    packet.seek(0)
    return packet

def add_footer_and_stamp_per_page(
    src_bytes: bytes,
    footer_kwargs: dict,
    stamp_on: bool,
    label_tpl: str,
    stamp_opts: dict,
    rename_files: bool
) -> List[Tuple[str, bytes]]:
    """
    Her sayfayı tek tek işler (footer + opsiyonel etiket) ve sayfa sayfa döner.
    rename_files=True ise 'A1-001.pdf' gibi adlandırır (yakalanırsa).
    """
    reader = PdfReader(io.BytesIO(src_bytes))
    out_pages: List[Tuple[str, bytes]] = []

    for i, page in enumerate(reader.pages, start=1):
        # Başlangıç sayfasını kopyala
        w = float(page.mediabox.width)
        h = float(page.mediabox.height)

        # Önce footer
        footer_overlay_io = build_footer_overlay(w, h, **footer_kwargs)
        footer_overlay = PdfReader(footer_overlay_io)
        page.merge_page(footer_overlay.pages[0])

        # Daire ID çıkar
        daire_id = None
        try:
            txt = page.extract_text() or ""
            daire_id = extract_daire_id_from_text(txt)
        except Exception:
            daire_id = None

        # Etiket uygula
        if stamp_on and daire_id:
            label_text = label_tpl.format(daire_id=daire_id)
            label_overlay_io = build_corner_label_overlay(
                w, h, label_text,
                font_size=stamp_opts.get("font_size", 13),
                bold=stamp_opts.get("bold", True),
                position=stamp_opts.get("position", "TR"),
                pad_x=stamp_opts.get("pad_x", 20),
                pad_y=stamp_opts.get("pad_y", 20),
            )
            label_overlay = PdfReader(label_overlay_io)
            page.merge_page(label_overlay.pages[0])

        # Tek sayfalık PDF yaz
        wri = PdfWriter()
        wri.add_page(page)
        buf = io.BytesIO()
        wri.write(buf)
        buf.seek(0)

        fname = f"page_{i:03d}.pdf"
        if rename_files and daire_id:
            fname = f"{daire_id}.pdf"

        out_pages.append((fname, buf.getvalue()))

    return out_pages

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

# --- (YENİ) DAİRE ETİKETİ SEÇENEKLERİ (opsiyonel) ---
with st.expander("🏷️ Daire numarası etiketi (opsiyonel)", expanded=False):
    stamp_on = st.checkbox("Daire numarasını köşeye yaz", value=False)
    label_tpl = st.text_input("Etiket şablonu", value="Daire: {daire_id}")
    c3, c4, c5 = st.columns(3)
    with c3:
        stamp_font_size = st.slider("Etiket punto", 10, 20, 13)
    with c4:
        stamp_pos = st.selectbox("Konum", ["TR", "TL", "BR", "BL"], index=0)
    with c5:
        stamp_bold = st.checkbox("Kalın", value=True)
    c6, c7 = st.columns(2)
    with c6:
        pad_x = st.slider("Köşe yatay boşluk (px)", 0, 80, 20, step=2)
    with c7:
        pad_y = st.slider("Köşe dikey boşluk (px)", 0, 80, 20, step=2)
    rename_files = st.checkbox("Bölünmüş dosya adını daireID.pdf yap", value=True)

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
        # Orijinal davranış: hiçbir ek işlem yok
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
        # Alt yazı + (opsiyonel) daire etiketi + böl
        footer_kwargs = dict(
            footer_text=footer_text,
            font_size=font_size,
            leading=leading,
            align=align,
            bottom_margin=bottom_m,
            box_height=box_h,
            bold_rules=bold_rules,
        )
        stamp_opts = dict(
            font_size=stamp_font_size,
            bold=stamp_bold,
            position=stamp_pos,
            pad_x=pad_x,
            pad_y=pad_y,
        )

        pages = add_footer_and_stamp_per_page(
            src_bytes=src,
            footer_kwargs=footer_kwargs,
            stamp_on=stamp_on,
            label_tpl=label_tpl,
            stamp_opts=stamp_opts,
            rename_files=rename_files,
        )

        with io.BytesIO() as zbuf:
            with zipfile.ZipFile(zbuf, "w", zipfile.ZIP_DEFLATED) as z:
                for name, data in pages:
                    z.writestr(name, data)
            st.download_button("📥 Alt yazılı & bölünmüş (ZIP)", zbuf.getvalue(), file_name="alt_yazili_bolunmus.zip")
