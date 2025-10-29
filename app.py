# =============== TAB C: WhatsApp Gönderim Hazırlığı (TEK BLOK) ===============
with tab_c:
    # --- Yalnız Tab C için benzersiz key üretici (çakışmayı önler) ---
    KEY_PREFIX = "wa_c_v1_"
    def skey(name: str) -> str:
        return KEY_PREFIX + name

    # --- Sadece Tab C içinde kullanılacak yardımcılar (global ile çakışmaz) ---
    import zipfile
    from io import BytesIO
    import re as _re
    import pandas as _pd

    def _norm_colname(s: str) -> str:
        return (str(s).strip().lower()
                .replace("\n"," ").replace("\r"," ")
                .replace(".","").replace("_"," ").replace("-"," "))

    def _pick_col(cols_map: dict, *candidates) -> str | None:
        for orig, normed in cols_map.items():
            if normed in candidates:
                return orig
        return None

    def _pad3_for_merge(x) -> str:
        digits = "".join(ch for ch in str(x or "") if ch.isdigit())
        return digits.zfill(3) if digits else ""

    def _extract_daire_from_filename(name: str) -> str | None:
        base = name.rsplit("/",1)[-1]
        base = base.rsplit("\\",1)[-1]
        m = _re.search(r"([A-Za-z]\d)\s*[-_]\s*(\d{1,3})", base)
        if m:
            return f"{m.group(1).upper()}-{int(m.group(2)):03d}"
        m = _re.search(r"([A-Za-z]\d)\s+(\d{1,3})", base)
        if m:
            return f"{m.group(1).upper()}-{int(m.group(2)):03d}"
        m = _re.search(r"([A-Za-z]\d).*?(\d{3})", base)
        if m:
            return f"{m.group(1).upper()}-{m.group(2)}"
        return None

    def _quick_norm_phone(x: str) -> str:
        s = _re.sub(r"[^\d+]", "", str(x or ""))
        if s.startswith("+"):
            return s
        if _re.fullmatch(r"05\d{9}", s):  # 05XXXXXXXXX
            return "+90" + s[1:]
        if _re.fullmatch(r"5\d{9}", s):   # 5XXXXXXXXX
            return "+90" + s
        if _re.fullmatch(r"0\d{10,11}", s):  # 0XXXXXXXXXXX
            return "+90" + s[1:]
        return s

    st.markdown("""
    <div style='background-color:#25D366;padding:10px 16px;border-radius:10px;display:flex;align-items:center;gap:10px;color:white;margin-bottom:15px;'>
      <img src='https://upload.wikimedia.org/wikipedia/commons/6/6b/WhatsApp.svg' width='28'>
      <h3 style='margin:0;'>WhatsApp Gönderim Hazırlığı</h3>
    </div>
    """, unsafe_allow_html=True)

    # --- 2 sütunlu yükleme alanı ---
    up1, up2 = st.columns([1,1], vertical_alignment="top")
    with up1:
        st.markdown("**Adım 1:** Bölünmüş PDF’lerin olduğu **ZIP**’i yükle (dosya adları `A1-001.pdf` gibi).")
        zip_up = st.file_uploader("Bölünmüş PDF ZIP", type=["zip"], key=skey("zip"), label_visibility="collapsed")
    with up2:
        st.markdown("**Adım 2:** Güncel **Rehber** dosyasını yükle (XLSX/CSV). En az `Blok`, `Daire No`, `Telefon` olmalı.")
        rehber_up = st.file_uploader("Rehber (XLSX/CSV)", type=["xlsx","csv"], key=skey("rehber"), label_visibility="collapsed")

    with st.expander("🔗 Opsiyonel link üretimi (base URL)", expanded=False):
        base_url = st.text_input("Base URL (örn: https://cdn.site.com/faturalar/ )", value="", key=skey("base"))
        st.caption("Dosyaları aynı adlarla bir sunucuya koyacaksan, link = base_url + dosya_adı şeklinde otomatik oluşur.")

    ctop1, ctop2 = st.columns([1,3], vertical_alignment="center")
    with ctop1:
        go_btn = st.button("📑 Eşleştir ve CSV oluştur", use_container_width=True, key=skey("build"))
    with ctop2:
        st.caption("Butona bastıktan sonra aşağıda geniş bir önizleme tablosu ve indirme butonu görünür.")

    if go_btn:
        if not zip_up:
            st.warning("Önce ZIP yükleyin.")
            st.stop()
        if not rehber_up:
            st.warning("Önce Rehber dosyası yükleyin.")
            st.stop()

        # --- ZIP → PDF listesi ---
        try:
            zf = zipfile.ZipFile(zip_up)
            pdf_rows = []
            for info in zf.infolist():
                if info.is_dir():
                    continue
                if not info.filename.lower().endswith(".pdf"):
                    continue
                pdf_rows.append({
                    "file_name": info.filename.rsplit("/",1)[-1],
                    "daire_id": _extract_daire_from_filename(info.filename)
                })
            pdf_df = _pd.DataFrame(pdf_rows)
        except Exception as e:
            st.error(f"ZIP okunamadı: {e}")
            st.stop()

        if pdf_df.empty:
            st.error("ZIP’te PDF bulunamadı.")
            st.stop()

        # --- Rehber oku ---
        try:
            if rehber_up.name.lower().endswith(".csv"):
                raw = _pd.read_csv(rehber_up)
            else:
                raw = _pd.read_excel(rehber_up, engine="openpyxl")
        except Exception as e:
            st.error(f"Rehber okunamadı: {e}")
            st.stop()

        # --- Kolon haritalama ---
        cols_map = {c: _norm_colname(c) for c in raw.columns}
        c_blok = _pick_col(cols_map, "blok")
        c_dno  = _pick_col(cols_map, "daire no","daire","daireno","daire  no")
        c_tel  = _pick_col(cols_map, "telefon","tel","cep","tel no","telefon no","gsm")
        c_ad   = _pick_col(cols_map, "ad soyad","ad soyad / unvan","ad soyad/unvan","unvan")

        if not c_blok or not c_dno or not c_tel:
            st.error("Rehberde en az 'Blok', 'Daire No', 'Telefon' bulunmalıdır.")
            st.dataframe(raw.head(20), use_container_width=True, height=480)
            st.stop()

        # --- Rehber normalize ---
        reh = _pd.DataFrame({
            "Blok": raw[c_blok].astype(str).str.upper().str.strip(),
            "Daire No": raw[c_dno].apply(_pad3_for_merge),
            "Telefon": raw[c_tel].astype(str),
            "name": raw[c_ad].astype(str) if c_ad else ""
        })
        reh["daire_id"] = reh["Blok"].str.upper().str.strip() + "-" + reh["Daire No"]
        reh["phone"] = reh["Telefon"].apply(_quick_norm_phone)

        # --- Eşleştirme ---
        merged = pdf_df.merge(reh[["daire_id","phone","name"]], on="daire_id", how="left")
        merged["file_url"] = merged["file_name"].apply(
            lambda fn: (base_url.rstrip("/") + "/" + fn) if base_url.strip() else ""
        )

        # --- Durum metrikleri ---
        a1, a2, a3 = st.columns(3)
        with a1:
            st.metric("Toplam kayıt", len(merged))
        with a2:
            st.metric("DaireID bulunamadı (dosya adından)", int(merged["daire_id"].isna().sum()))
        with a3:
            st.metric("Telefon eksik", int((merged["phone"].isna() | (merged["phone"]=="")).sum()))

        st.markdown("**Eşleştirme Önizleme**")
        st.dataframe(merged[["daire_id","file_name","file_url","name","phone"]],
                     use_container_width=True, height=700)

        # --- Çıkış CSV (WhatsApp alıcı listesi) ---
        out_csv = merged[["phone","name","daire_id","file_name","file_url"]].copy()
        b_csv = out_csv.to_csv(index=False).encode("utf-8-sig")
        st.download_button("📥 WhatsApp_Recipients.csv (UTF-8, BOM)",
                           b_csv,
                           file_name="WhatsApp_Recipients.csv",
                           mime="text/csv",
                           use_container_width=True)

        with st.expander("📨 Örnek mesaj gövdesi", expanded=False):
            st.code(
                "Merhaba {name},\n"
                "{daire_id} numaralı dairenizin aylık bildirimi hazırdır.\n"
                "Butondan görüntüleyebilirsiniz.\n",
                language="text"
            )
            st.info("WhatsApp şablonunda **URL butonu** kullan: CSV’deki `file_url` alanını butona bağla. "
                    "Drive kullanıyorsan, paylaşımları 'linki olan herkes görüntüleyebilir' yapmayı unutma.")
