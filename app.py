# =========================
# A P S İ Y O N   M O D Ü L Ü
# =========================
import re

st.header("📊 Apsiyon Gider Doldurucu (PDF → Apsiyon boş şablon)")

with st.expander("1) Dosyaları yükle", expanded=True):
    apsiyon_xlsx = st.file_uploader("Apsiyon boş şablon (Excel)", type=["xlsx", "xls"])
    manas_pdf    = st.file_uploader("Manas PDF (çok sayfalı, daire bazlı)", type=["pdf"], key="manaspdf_for_aps")

with st.expander("2) Eşleşme Ayarları (Daire)", expanded=True):
    st.caption("Şablonda daire alanı hangi formatta?")
    id_mode = st.radio(
        "Daire kimliği seçimi",
        ["Blok + Daire No sütunları var", "Tek sütunda DaireID (örn. A1-001) var"],
        index=0
    )
    if id_mode == "Blok + Daire No sütunları var":
        col_b = st.text_input("Blok sütun adı", value="Blok")
        col_d = st.text_input("Daire No sütun adı", value="Daire No")
        single_id_col = None
    else:
        single_id_col = st.text_input("DaireID / Daire sütun adı", value="DaireID")
        col_b, col_d = None, None

with st.expander("3) Yazım Modu (Gider kolonları)", expanded=True):
    mode = st.radio(
        "Giderleri nasıl yazalım?",
        [
            "Seçenek 1: Gider1 = Isıtma + Sıcak Su, Gider2 = Su, Gider3 = Isıtma",
            "Seçenek 2: Toplam (Isıtma + Sıcak Su + Su) sadece Gider1'e"
        ],
        index=0
    )

    if mode.startswith("Seçenek 1"):
        g1_acik = st.text_input("Gider1 Açıklaması (Isıtma + Sıcak Su)", value="Isıtma + Sıcak Su")
        g2_acik = st.text_input("Gider2 Açıklaması (Su)",               value="Soğuk Su")
        g3_acik = st.text_input("Gider3 Açıklaması (Isıtma)",           value="Isıtma")
        single_desc = None
    else:
        single_desc = st.text_input("Gider1 Açıklaması (Toplam)", value="Isıtma + Sıcak Su + Su (Toplam)")
        g1_acik = g2_acik = g3_acik = None

    # Hedef kolon adları (şablonda bire bir bu isimlerle olmalı; yoksa otomatik ekleriz)
    col_g1_tutar = st.text_input("Gider1 Tutarı sütun adı", value="Gider1 Tutarı")
    col_g1_acik  = st.text_input("Gider1 Açıklaması sütun adı", value="Gider1 Açıklaması")
    col_g2_tutar = st.text_input("Gider2 Tutarı sütun adı", value="Gider2 Tutarı")
    col_g2_acik  = st.text_input("Gider2 Açıklaması sütun adı", value="Gider2 Açıklaması")
    col_g3_tutar = st.text_input("Gider3 Tutarı sütun adı", value="Gider3 Tutarı")
    col_g3_acik  = st.text_input("Gider3 Açıklaması sütun adı", value="Gider3 Açıklaması")

def _to_float_tr(s: str) -> float:
    """'1.234,56' → 1234.56  ;  '0,00' → 0.0  ;  '—' → 0.0"""
    if not s or not isinstance(s, str):
        return 0.0
    s = s.strip().replace(".", "").replace(",", ".")
    try:
        return float(s)
    except:
        return 0.0

def _pad3(n: str) -> str:
    try:
        return f"{int(n):03d}"
    except:
        return str(n)

def parse_manas_pdf_totals(pdf_bytes: bytes) -> dict:
    """
    Her daire için { 'A1-001': {'isitma': x, 'sicak': y, 'su': z} } döner.
    PDF sayfa metinlerine göre regex ile çeker.
    """
    reader = PdfReader(io.BytesIO(pdf_bytes))
    result = {}

    # Daire No yakala: "Daire No  A1-blk daire:01"
    re_daire = re.compile(r"Daire\s*No\s*([A-Z]\d)\s*-\s*blk\s*daire\s*:\s*(\d+)", re.IGNORECASE)

    # Bölüm başlıkları ve ilgili "Ödenecek Tutar" değerleri
    # Her bölüm için 'Ödenecek Tutar <rakam>' ararız.
    re_odenecek = re.compile(r"Ödenecek\s*Tutar\s*([\d\.\,]+)")

    for page in reader.pages:
        txt = page.extract_text() or ""
        # Daire bilgisi
        m = re_daire.search(txt.replace("-", "-").replace("–", "-"))
        if not m:
            # Bazı PDF'lerde 'A1-blk' ile 'A1 - blk' arasında farklı boşluk/çizgi olabilir, biraz gevşetelim:
            m = re.search(r"Daire\s*No\s*([A-Z]\d)\s*-\s*blk\s*daire\s*[:：]\s*(\d+)", txt, re.IGNORECASE)
        if not m:
            # Son çare: 'A1 blk daire:01' (tire düşmüş)
            m = re.search(r"Daire\s*No\s*([A-Z]\d)\s*blk\s*daire\s*[:：]\s*(\d+)", txt, re.IGNORECASE)

        if not m:
            # Daire bulunamadıysa bu sayfayı atla
            continue

        blok = m.group(1).upper()     # A1, A2, ...
        dno  = _pad3(m.group(2))      # 01 -> 001
        daire_id = f"{blok}-{dno}"

        # Varsayılanlar
        isitma = sicak = su = 0.0

        # Sayfa içini bölümlere yaklaşık ayıralım
        sections = {
            "ISITMA": None,
            "SICAK SU": None,
            "SU": None
        }

        # Bölüm başlıklarının indexlerini bulup metin parçaları çıkaralım
        up = txt.upper()
        idx_isitma = up.find("ISITMA")
        idx_sicak  = up.find("SICAK SU")
        # 'SU' bölümü 'SICAK SU' metninin altına tekrar 'SU' başlığı şeklinde geliyor
        idx_su     = up.find("\nSU") if "\nSU" in up else up.find("\rSU")
        if idx_su == -1:
            # bazı dokümanlarda 'SU' başlık satırında başta/sonda boşluk olabilir
            idx_su = up.find("SU\n")
        # Bölge aralıkları
        end = len(up)
        if idx_isitma != -1:
            end_isitma = min([x for x in [idx_sicak, idx_su, end] if x != -1 and x > idx_isitma] or [end])
            sections["ISITMA"] = txt[idx_isitma:end_isitma]
        if idx_sicak != -1:
            end_sicak = min([x for x in [idx_su, end] if x != -1 and x > idx_sicak] or [end])
            sections["SICAK SU"] = txt[idx_sicak:end_sicak]
        if idx_su != -1:
            sections["SU"] = txt[idx_su:end]

        # Her bölümde ilk "Ödenecek Tutar" değerini al
        for key in sections:
            if not sections[key]:
                continue
            mo = re_odenecek.search(sections[key])
            if mo:
                val = _to_float_tr(mo.group(1))
                if key == "ISITMA":
                    isitma = val
                elif key == "SICAK SU":
                    sicak = val
                elif key == "SU":
                    su = val

        result[daire_id] = {"isitma": isitma, "sicak": sicak, "su": su}

    return result

def write_expenses_to_sheet(df: pd.DataFrame,
                            totals: dict,
                            id_mode: str,
                            col_b: Optional[str],
                            col_d: Optional[str],
                            single_id_col: Optional[str],
                            mode: str,
                            g1_acik: Optional[str], g2_acik: Optional[str], g3_acik: Optional[str],
                            single_desc: Optional[str],
                            cols: Tuple[str, str, str, str, str, str]) -> pd.DataFrame:
    """
    df: Apsiyon şablonu (hiçbir kolonu/satırı silmeyeceğiz)
    totals: {'A1-001': {'isitma': x, 'sicak': y, 'su': z}, ...}
    """
    col_g1_tutar, col_g1_acik, col_g2_tutar, col_g2_acik, col_g3_tutar, col_g3_acik = cols

    # Gerekli kolonlar yoksa ekle (sıra sonuna)
    for c in [col_g1_tutar, col_g1_acik, col_g2_tutar, col_g2_acik, col_g3_tutar, col_g3_acik]:
        if c not in df.columns:
            df[c] = ""

    def row_id(r) -> Optional[str]:
        if id_mode == "Blok + Daire No sütunları var":
            if col_b in r and col_d in r:
                b = str(r[col_b]).strip().upper()
                d = _pad3(str(r[col_d]).strip().split(".")[0])  # '1.0' gibi durumlar için
                if b and d:
                    return f"{b}-{d}"
        else:
            if single_id_col in r:
                v = str(r[single_id_col]).strip().upper()
                # A1-1 → A1-001 düzelt
                m = re.match(r"([A-Z]\d)\-(\d+)$", v)
                if m:
                    return f"{m.group(1)}-{_pad3(m.group(2))}"
                return v
        return None

    # Satır satır doldur
    out = df.copy()
    for idx, r in out.iterrows():
        did = row_id(r)
        if not did or did not in totals:
            continue
        t = totals[did]
        isitma = t.get("isitma", 0.0)
        sicak  = t.get("sicak", 0.0)
        su     = t.get("su", 0.0)

        if mode.startswith("Seçenek 1"):
            g1 = (isitma + sicak)
            g2 = su
            g3 = isitma

            out.at[idx, col_g1_tutar] = f"{g1:.2f}".replace(".", ",")
            out.at[idx, col_g2_tutar] = f"{g2:.2f}".replace(".", ",")
            out.at[idx, col_g3_tutar] = f"{g3:.2f}".replace(".", ",")

            out.at[idx, col_g1_acik]  = g1_acik or ""
            out.at[idx, col_g2_acik]  = g2_acik or ""
            out.at[idx, col_g3_acik]  = g3_acik or ""
        else:
            tot = (isitma + sicak + su)
            out.at[idx, col_g1_tutar] = f"{tot:.2f}".replace(".", ",")
            out.at[idx, col_g1_acik]  = single_desc or ""
            # Diğerlerini boş bırak
            out.at[idx, col_g2_tutar] = ""
            out.at[idx, col_g2_acik]  = ""
            out.at[idx, col_g3_tutar] = ""
            out.at[idx, col_g3_acik]  = ""

    return out

st.divider()
go = st.button("🚀 Apsiyon dosyasını doldur ve indir")

if go:
    if not apsiyon_xlsx or not manas_pdf:
        st.error("Lütfen hem **Apsiyon şablonu** hem de **Manas PDF** dosyasını yükleyin.")
        st.stop()

    try:
        df_in = pd.read_excel(apsiyon_xlsx)
    except Exception as e:
        st.error(f"Apsiyon dosyası okunamadı: {e}")
        st.stop()

    # Manas PDF'ini parse et
    totals = parse_manas_pdf_totals(manas_pdf.read())

    if not totals:
        st.warning("PDF’den hiçbir daire verisi çıkmadı. PDF yapısını kontrol edin.")
        st.stop()

    # Doldur
    df_out = write_expenses_to_sheet(
        df_in, totals, id_mode,
        col_b, col_d, single_id_col,
        mode, g1_acik, g2_acik, g3_acik, single_desc,
        (col_g1_tutar, col_g1_acik, col_g2_tutar, col_g2_acik, col_g3_tutar, col_g3_acik)
    )

    # Ön izleme
    st.success("Tamam! Aşağıda ilk 15 satır önizleme:")
    st.dataframe(df_out.head(15))

    # XLSX indirme (openpyxl varsa)
    xbuf = io.BytesIO()
    wrote_xlsx = False
    try:
        with pd.ExcelWriter(xbuf, engine="openpyxl") as writer:
            df_out.to_excel(writer, index=False)
        wrote_xlsx = True
    except Exception as e:
        st.info(f"Excel yazıcı (openpyxl) bulunamadı veya hata aldı ({e}). CSV olarak da indirebilirsiniz.")

    if wrote_xlsx:
        st.download_button("📥 Apsiyon (doldurulmuş).xlsx", xbuf.getvalue(), file_name="Apsiyon_doldurulmus.xlsx")
    # CSV yedeği
    csv_bytes = df_out.to_csv(index=False).encode("utf-8-sig")
    st.download_button("📥 Apsiyon (doldurulmuş).csv", csv_bytes, file_name="Apsiyon_doldurulmus.csv")
