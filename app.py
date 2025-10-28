# app.py
import io, os, zipfile, re, unicodedata
from typing import List, Dict

import streamlit as st
import pandas as pd

# PDF
from pypdf import PdfReader, PdfWriter

# ALT YAZI İÇİN
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

# (Opsiyonel) .docx'ten alt yazı çekmek için
try:
    import docx  # python-docx
    HAS_DOCX = True
except Exception:
    HAS_DOCX = False


# =========================================================
#  F O N T L A R  (Türkçe karakter için NotoSans ailesi)
#  /fonts klasöründe şu dosyalar olmalı:
#  - fonts/NotoSans-Regular.ttf
#  - fonts/NotoSans-Bold.ttf
# =========================================================
pdfmetrics.registerFont(TTFont("NotoSans-Regular", "fonts/NotoSans-Regular.ttf"))
pdfmetrics.registerFont(TTFont("NotoSans-Bold",    "fonts/NotoSans-Bold.ttf"))


# =========================================================
#  K U M A N D A  -  Y A R D I M C I L A R
# =========================================================
def _pad3(s: str) -> str:
    s = "".join(ch for ch in s if ch.isdigit())
    return s.zfill(3) if s else "000"

def _to_float_tr(s: str) -> float:
    if not s: return 0.0
    s = s.strip().replace(".", "").replace(",", ".")
    try: return float(s)
    except: return 0.0

def _normalize_tr(t: str) -> str:
    """Türkçe aksanları sadeleştir, büyük harfe çevir, boşlukları toparla."""
    if not t: return ""
    t = unicodedata.normalize("NFKD", t)
    t = "".join(ch for ch in t if not unicodedata.combining(ch))
    t = (t.replace("ı","i").replace("İ","I")
           .replace("ş","s").replace("Ş","S")
           .replace("ö","o").replace("Ö","O")
           .replace("ü","u").replace("Ü","U")
           .replace("ğ","g").replace("Ğ","G")
           .replace("ç","c").replace("Ç","C"))
    t = t.upper()
    t = re.sub(r"[ \t]+", " ", t)
    return t


# =========================================================
#  A L T  Y A Z I  –  METİN SARMA ve OVERLAY
# =========================================================
def wrap_by_width(text: str, font_name: str, font_size: float, max_width: float) -> List[str]:
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
) -> io.BytesIO:
    packet = io.BytesIO()
    can = canvas.Canvas(packet, pagesize=(page_w, page_h))

    left_margin = 36
    right_margin = 36
    max_text_width = page_w - left_margin - right_margin

    wrapped = wrap_by_width(footer_text, "NotoSans-Regular", font_size, max_text_width)

    max_lines = max(1, int(box_height // leading))
    if len(wrapped) > max_lines:
        wrapped = wrapped[:max_lines]

    y_start = bottom_margin + (len(wrapped) - 1) * leading + 4

    for i, line in enumerate(wrapped):
        use_bold = False
        if bold_rules:
            u = line.strip().upper()
            if i == 0 and u.startswith("SON ÖDEME"):
                use_bold = True
            if u == "AÇIKLAMA":
                use_bold = True
            if "TARİHLİ TEMSİLCİLER" in u:
                use_bold = True

        can.setFont("NotoSans-Bold" if use_bold else "NotoSans-Regular", font_size)

        y = y_start - i * leading
        if align == "center":
            can.drawCentredString(page_w / 2.0, y, line)
        else:
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


# =========================================================
#  M A N A S  P D F  P A R S E R  (Isıtma / Sıcak Su / Su / Toplam)
# =========================================================
def parse_manas_pdf_totals(pdf_bytes: bytes) -> Dict[str, Dict[str, float]]:
    """
    Dönüş:
      {'A1-001': {'isitma': x, 'sicak': y, 'su': z, 'toplam': t}, ...}
    """
    reader = PdfReader(io.BytesIO(pdf_bytes))
    result: Dict[str, Dict[str, float]] = {}

    # --- esnek Daire No yakalama desenleri ---
    # Not: Birini normalize (DAIRE) üzerinde, birini ham metin üzerinde deneyeceğiz.
    re_daire_norms = [
        # "DAIRE NO : A1 ... 01"
        re.compile(r"DAIRE\s*NO[^A-Z0-9]{0,15}([A-Z]\d)[^0-9]{0,20}(\d{1,4})"),
        # "A1 BLK DAIRE 01" veya "A1-BLK DAIRE:01"
        re.compile(r"([A-Z]\d)[^\d\n\r]{0,30}DAIRE[^0-9]{0,10}(\d{1,4})"),
    ]
    re_daire_raws = [
        # ham metinde Türkçe "DAİRE NO"
        re.compile(r"DA[İI]RE\s*NO[^A-Z0-9]{0,15}([A-Z]\d)[^0-9]{0,20}(\d{1,4})"),
        re.compile(r"([A-Z]\d)[^\d\n\r]{0,30}DA[İI]RE[^0-9]{0,10}(\d{1,4})"),
    ]

    # Ödenecek tutar yakalama (TL ve iki nokta/boşluk varyantları)
    re_odenecek = re.compile(
        r"(?:ÖDENECEK|ODENECEK)\s*TUTAR[^0-9]{0,10}([0-9\.\,]+)", re.IGNORECASE
    )
    re_toplam = re.compile(r"TOPLAM\s+TUTAR[^0-9]{0,10}([0-9\.\,]+)", re.IGNORECASE)

    def find_daire_id(raw_text: str) -> str | None:
        norm = _normalize_tr(raw_text)
        # önce normalize üzerinde dene
        for rx in re_daire_norms:
            m = rx.search(norm)
            if m:
                blok = m.group(1).upper()
                dno = _pad3(m.group(2))
                return f"{blok}-{dno}"
        # sonra ham metinde dene (Türkçe İ/ı olasılığı)
        for rx in re_daire_raws:
            m = rx.search(raw_text)
            if m:
                blok = m.group(1).upper()
                dno = _pad3(m.group(2))
                return f"{blok}-{dno}"
        return None

    def grab_section_amount(norm_text: str, header_word: str) -> float:
        """
        header_word: 'ISITMA' | 'SICAK SU' | 'SU'
        Başlıktan sonra gelen ilk ÖDENECEK TUTAR'ı alır.
        """
        # başlık yerini bul
        idx = norm_text.find(header_word)
        if idx == -1:
            return 0.0
        tail = norm_text[idx : idx + 2500]  # bölümden sonraki makul pencere
        m = re_odenecek.search(tail)
        return _to_float_tr(m.group(1)) if m else 0.0

    # sayfa sayfa tara
    for pi, page in enumerate(reader.pages):
        raw = page.extract_text() or ""
        norm = _normalize_tr(raw)

        did = find_daire_id(raw)
        if not did:
            # ilk sayfada bulunamadıysa debug kolaylığı
            if pi == 0:
                st.info("⚠️ Daire No satırı bulunamadı. İlk sayfanın normalize edilmiş içeriğinin bir kısmını gösteriyorum.")
                st.code(norm[:800])
            continue

        isitma = grab_section_amount(norm, "ISITMA")
        sicak  = grab_section_amount(norm, "SICAK SU")

        # "SU" başlığı "SICAK SU" ile karışmasın diye, önce ' SICAK SU ' yakalandığından emin olduk.
        # Saf 'SU' için ayrı yaklaşım: ' SICAK SU ' geçtiyse, geriye kalan kısımdan ara.
        su = 0.0
        # 'SICAK SU' bölümünün sonrasından dene:
        idx_sicak = norm.find("SICAK SU")
        search_base = norm[idx_sicak + 8 :] if idx_sicak != -1 else norm
        idx_su = search_base.find("\nSU")
        if idx_su == -1:
            idx_su = search_base.find(" SU ")
        if idx_su != -1:
            tail_su = search_base[idx_su : idx_su + 2000]
            m_su = re_odenecek.search(tail_su)
            if m_su:
                su = _to_float_tr(m_su.group(1))
        if su == 0.0:
            # olmadı, genel fallback:
            su = grab_section_amount(norm, "\nSU")

        mt = re_toplam.search(norm)
        toplam = _to_float_tr(mt.group(1)) if mt else (isitma + sicak + su)

        result[did] = {"isitma": isitma, "sicak": sicak, "su": su, "toplam": toplam}

    return result

# =========================================================
#  S T R E A M L I T   U I
# =========================================================
st.set_page_config(page_title="Fatura • Atlas Vadi", page_icon="🧾", layout="centered")
st.title("🧾 Vadi Fatura — Böl & Alt Yazı & Apsiyon")

tab_a, tab_b = st.tabs(["📄 Böl & Alt Yazı", "📊 Apsiyon Gider Doldurucu"])


# ---------------- TAB A: Böl & Alt Yazı ----------------
with tab_a:
    pdf_file = st.file_uploader("Fatura PDF dosyasını yükle", type=["pdf"], key="pdf_a")

    st.subheader("Alt Yazı Kaynağı")
    t1, t2 = st.tabs(["✍️ Metin alanı", "📄 .docx yükle (opsiyonel)"])

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

    with t1:
        footer_text = st.text_area("Alt yazı", value=default_text, height=220, key="footer_text")

    with t2:
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
        font_size = st.slider("🅰️ Yazı Boyutu", 9, 16, 11, key="fs")
        leading   = st.slider("↕️ Satır Aralığı (pt)", 12, 22, 14, key="lead")
    with c2:
        align     = st.radio("Hizalama", ["left", "center"], index=0, key="align", format_func=lambda x: "Sol" if x=="left" else "Orta")
        bottom_m  = st.slider("Alt Marj (pt)", 24, 100, 48, key="bm")
    box_h = st.slider("Alt Yazı Alanı Yüksekliği (pt)", 100, 260, 180, key="bh")
    bold_rules = st.checkbox("Başlıkları otomatik kalın yap (SON ÖDEME, AÇIKLAMA, ...)", value=True, key="boldrules")

    st.subheader("İşlem")
    mode = st.radio(
        "Ne yapmak istersiniz?",
        ["Sadece sayfalara böl", "Sadece alt yazı uygula (tek PDF)", "Alt yazı uygula + sayfalara böl (ZIP)"],
        index=2,
        key="mode"
    )
    go = st.button("🚀 Başlat", key="go_a")

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


# --------------- TAB B: Apsiyon Gider Doldurucu ---------------
with tab_b:
    st.header("📊 Apsiyon Gider Doldurucu (PDF → Apsiyon boş şablon)")

    pdf_b = st.file_uploader("Manas PDF (aynı PDF)", type=["pdf"], key="pdf_b")
    xlsx  = st.file_uploader("Apsiyon boş Excel (xlsx)", type=["xlsx"], key="xlsx")

    st.markdown("**Yerleşim (Seçenek 1):** Gider1 = **Sıcak Su**, Gider2 = **Su**, Gider3 = **Isıtma**  \n"
                "**Yerleşim (Seçenek 2):** Gider1 = **Toplam** (tek kalem), Gider2/3 boş")

    choice = st.radio("Doldurma şekli", ["Seçenek 1 (3 kalem)", "Seçenek 2 (toplam tek kalem)"], index=0, key="fillopt")

    colx = st.columns(3)
    with colx[0]:
        acik1 = st.text_input("Gider1 Açıklaması", "Sıcak Su", key="g1a")
    with colx[1]:
        acik2 = st.text_input("Gider2 Açıklaması", "Su", key="g2a")
    with colx[2]:
        acik3 = st.text_input("Gider3 Açıklaması", "Isıtma", key="g3a")

    go_b = st.button("🧩 Excel’i Doldur ve İndir", key="go_b")

    if go_b:
        if not pdf_b or not xlsx:
            st.warning("PDF ve Excel yükleyin.")
            st.stop()

        # 1) PDF'ten tutarları çıkar
        totals = parse_manas_pdf_totals(pdf_b.read())
        if not totals:
            st.error("PDF’ten tutar okunamadı. (Daire başlıkları bulunamadı)")
            st.stop()

        # st.write("Bulunan daireler:", list(totals.keys())[:10])
        # st.dataframe(pd.DataFrame.from_dict(totals, orient="index"))

        # 2) Excel’i oku
        try:
            df = pd.read_excel(xlsx)
        except Exception as e:
            st.error(f"Excel okunamadı: {e}")
            st.stop()

        # 3) DaireID üret (Blok + Daire No)
        # Kolon adlarını normalleştirerek bul
        cols = { _normalize_tr(c): c for c in df.columns }
        col_blok = cols.get("BLOK") or cols.get("BLOK ADI")
        col_dno  = cols.get("DAIRE NO") or cols.get("DAIRE NO:")
        if not (col_blok and col_dno):
            st.error("Excel’de 'Blok' ve 'Daire No' sütunları bulunamadı.")
            st.stop()

        # Gider sütunları (adlar aynen korunur; yoksa oluşturulur)
        def find_col(name_try: List[str]) -> str|None:
            norm = { _normalize_tr(c): c for c in df.columns }
            for n in name_try:
                got = norm.get(_normalize_tr(n))
                if got: return got
            return None

        col_g1_t = find_col(["Gider1 Tutarı","Gider 1 Tutarı"])
        col_g1_a = find_col(["Gider1 Açıklaması","Gider 1 Açıklaması"])
        col_g2_t = find_col(["Gider2 Tutarı","Gider 2 Tutarı"])
        col_g2_a = find_col(["Gider2 Açıklaması","Gider 2 Açıklaması"])
        col_g3_t = find_col(["Gider3 Tutarı","Gider 3 Tutarı"])
        col_g3_a = find_col(["Gider3 Açıklaması","Gider 3 Açıklaması"])

        # Yoksa ekle
        for want, default_name in [
            (col_g1_t, "Gider1 Tutarı"), (col_g1_a, "Gider1 Açıklaması"),
            (col_g2_t, "Gider2 Tutarı"), (col_g2_a, "Gider2 Açıklaması"),
            (col_g3_t, "Gider3 Tutarı"), (col_g3_a, "Gider3 Açıklaması"),
        ]:
            if want is None:
                df[default_name] = None

        # Gerçek isimleri tekrar al
        cols = { _normalize_tr(c): c for c in df.columns }
        col_g1_t = cols.get(_normalize_tr(col_g1_t or "Gider1 Tutarı")) or "Gider1 Tutarı"
        col_g1_a = cols.get(_normalize_tr(col_g1_a or "Gider1 Açıklaması")) or "Gider1 Açıklaması"
        col_g2_t = cols.get(_normalize_tr(col_g2_t or "Gider2 Tutarı")) or "Gider2 Tutarı"
        col_g2_a = cols.get(_normalize_tr(col_g2_a or "Gider2 Açıklaması")) or "Gider2 Açıklaması"
        col_g3_t = cols.get(_normalize_tr(col_g3_t or "Gider3 Tutarı")) or "Gider3 Tutarı"
        col_g3_a = cols.get(_normalize_tr(col_g3_a or "Gider3 Açıklaması")) or "Gider3 Açıklaması"

        # DaireID sütunu (geçici)
        def make_id(row) -> str:
            blok = str(row.get(col_blok,"")).strip().upper()
            dno  = _pad3(str(row.get(col_dno,"")))
            return f"{blok}-{dno}"
        df["_DaireID_"] = df.apply(make_id, axis=1)

        # 4) Doldurma
        filled = 0
        for idx, row in df.iterrows():
            did = row["_DaireID_"]
            t = totals.get(did)
            if not t:
                continue

            if choice.startswith("Seçenek 1"):
                # Gider1 = Sıcak Su, Gider2 = Su, Gider3 = Isıtma
                df.at[idx, col_g1_t] = round(t["sicak"], 2)
                df.at[idx, col_g1_a] = acik1
                df.at[idx, col_g2_t] = round(t["su"], 2)
                df.at[idx, col_g2_a] = acik2
                df.at[idx, col_g3_t] = round(t["isitma"], 2)
                df.at[idx, col_g3_a] = acik3
            else:
                # Seçenek 2: Toplam tek kalem Gider1
                df.at[idx, col_g1_t] = round(t["toplam"], 2)
                df.at[idx, col_g1_a] = acik1
                # diğerlerini boş bırak
            filled += 1

        df.drop(columns=["_DaireID_"], inplace=True)

        st.success(f"{filled} satır dolduruldu.")
        st.dataframe(df.head(10))

        # 5) Excel olarak indir
        out = io.BytesIO()
        try:
            # openpyxl (önerilir)
            with pd.ExcelWriter(out, engine="openpyxl") as writer:
                df.to_excel(writer, index=False, sheet_name="Sayfa1")
        except Exception:
            # xlsxwriter ile deneyelim
            with pd.ExcelWriter(out, engine="xlsxwriter") as writer:
                df.to_excel(writer, index=False, sheet_name="Sayfa1")
        st.download_button("📥 Doldurulmuş Excel (xlsx)", out.getvalue(), file_name="Apsiyon-doldurulmus.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
