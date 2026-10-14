import re
import pandas as pd
import streamlit as st

st.set_page_config(page_title="Generator DC & Anomali", page_icon="📝", layout="wide")

# ----------------------------------------------------------------------------
# DATA MASTER
# ----------------------------------------------------------------------------
KODE_DC = {
    "#DC_00": "tetap",
    "#DC_01": "berubah karena miskonsep PPL",
    "#DC_02": "berubah karena konfirmasi/revisit responden",
    "#DC_03": "berubah karena info survei/data administrasi",
    "#DC_04": "berubah karena selain #DC_01 - #DC_03",
}
KODE_UBAH = [k for k in KODE_DC if k != "#DC_00"]  # untuk menu anomali (ada perubahan)

KOLOM = ["26a", "26b", "26c", "26d", "27a", "28c"]
NAMA_KOLOM = {
    "26a": "Upah",
    "26b": "Biaya produksi",
    "26c": "Biaya pembelian barang yang terjual",
    "26d": "Biaya operasional (air, listrik, gas, internet, pulsa, pemeliharaan, biaya angkutan, dll.)",
    "27a": "Nilai pendapatan barang dan jasa",
    "28c": "Nilai total aset",
}

# Interval kewajaran R_NTB/Output [U3] per kategori (None = tidak ada interval)
KATEGORI = {
    "A": (0.51, 0.94), "B": (0.28, 0.81), "C": (0.15, 0.61), "D": (0.13, 0.68),
    "E": (0.29, 0.47), "F": (0.34, 0.44), "G": (0.64, 0.78), "H": (0.29, 0.64),
    "I": (0.36, 0.69), "J": (0.36, 0.73), "K": (0.50, 0.72), "L": (0.46, 0.86),
    "M": (0.50, 0.99), "N": (0.44, 0.57), "O": (0.54, 0.66), "P": None,
    "Q": (0.27, 0.82), "R": (0.20, 0.60), "S": (0.21, 0.57), "T": (0.30, 0.69),
    "U": (0.0, 0.0),
}
INTERVAL_U4 = (0.2, 0.4)  # R_Output/Aset
INTERVAL_U5 = (0.4, 0.6)  # R_Upah/NTB


# ----------------------------------------------------------------------------
# HELPER FORMAT & PARSE
# ----------------------------------------------------------------------------
def format_nilai(teks: str) -> str:
    """Rapikan angka dengan pemisah ribuan titik (500000 -> 500.000).
    Koma dipakai sebagai desimal. Teks non-angka dibiarkan apa adanya."""
    teks = (teks or "").strip()
    if not teks:
        return ""
    bersih = teks.replace(".", "").replace(" ", "")
    m = re.fullmatch(r"(-?)(\d+)(?:,(\d+))?", bersih)
    if m:
        tanda, bulat, desimal = m.groups()
        bulat = f"{int(bulat):,}".replace(",", ".")
        return f"{tanda}{bulat}" + (f",{desimal}" if desimal else "")
    return teks


def parse_angka(teks: str):
    """Teks -> float, atau None bila kosong / bukan angka."""
    bersih = (teks or "").strip().replace(".", "").replace(" ", "").replace(",", ".")
    if not bersih:
        return None
    try:
        return float(bersih)
    except ValueError:
        return None


def fmt_angka(x: float) -> str:
    if abs(x - round(x)) < 1e-9:
        return f"{int(round(x)):,}".replace(",", ".")
    s = f"{x:,.2f}"
    return s.replace(",", "#").replace(".", ",").replace("#", ".")


def fmt_rasio(x, desimal=3) -> str:
    return f"{x:.{desimal}f}".replace(".", ",")


def buat_kalimat(kode, kolom, awal, ubah, tampil_awal=True):
    awal = format_nilai(awal)
    ubah = format_nilai(ubah)
    if not awal and not ubah:
        return None
    ubah_txt = ubah if ubah else "kosong"
    if kode == "#DC_00":
        return f"{kode} isian {kolom} tetap {awal if awal else 'kosong'}"
    if tampil_awal and awal:
        return f"{kode} memperbaiki isian {kolom} dari {awal} menjadi {ubah_txt}"
    return f"{kode} memperbaiki isian {kolom} menjadi {ubah_txt}"


# ----------------------------------------------------------------------------
# CALLBACK
# ----------------------------------------------------------------------------
def cb_format(key: str):
    st.session_state[key] = format_nilai(st.session_state.get(key, ""))


def cb_awal(kol: str):
    """Format nilai awal, lalu autofill ke kolom adjustment."""
    k = f"an_awal_{kol}"
    st.session_state[k] = format_nilai(st.session_state.get(k, ""))
    st.session_state[f"an_adj_{kol}"] = st.session_state[k]


def cb_salin():
    for kol in KOLOM:
        st.session_state[f"an_adj_{kol}"] = st.session_state.get(f"an_awal_{kol}", "")


def cb_reset_gen():
    for kol in KOLOM:
        st.session_state[f"awal_{kol}"] = ""
        st.session_state[f"ubah_{kol}"] = ""


def cb_reset_an():
    for kol in KOLOM:
        st.session_state[f"an_awal_{kol}"] = ""
        st.session_state[f"an_adj_{kol}"] = ""


# ----------------------------------------------------------------------------
# PERHITUNGAN ANOMALI
# ----------------------------------------------------------------------------
def hitung(teks_per_kolom: dict):
    """Kembalikan dict U2..U5 (None bila semua kolom kosong)."""
    v = {k: parse_angka(t) for k, t in teks_per_kolom.items()}
    if all(x is None for x in v.values()):
        return None
    g = lambda k: v[k] or 0.0
    u2 = g("27a") - (g("26b") + g("26c") + g("26d"))
    output = g("27a") - g("26c")
    return {
        "U2": u2,
        "U3": u2 / output if output else None,
        "U4": output / g("28c") if g("28c") else None,
        "U5": g("26a") / u2 if u2 else None,
    }


def status_rasio(nilai, interval):
    if interval is None:
        return "➖ Tidak ada interval"
    if nilai is None:
        return "⚠️ Tidak dapat dihitung"
    lo, hi = interval
    r = round(nilai, 3)  # 3 digit di belakang koma
    return "✅ Wajar" if lo - 1e-9 <= r <= hi + 1e-9 else "❌ Tidak wajar"


def status_u2(nilai):
    return "✅ Wajar" if nilai >= 0 else "❌ Tidak wajar"


def evaluasi(hasil, interval_u3):
    """Kembalikan list (nilai_teks, status) untuk U2..U5."""
    if hasil is None:
        return [("-", "Belum diisi")] * 4
    out = [(fmt_angka(hasil["U2"]), status_u2(hasil["U2"]))]
    for kunci, interval in (("U3", interval_u3), ("U4", INTERVAL_U4), ("U5", INTERVAL_U5)):
        n = hasil[kunci]
        out.append((fmt_rasio(n) if n is not None else "-", status_rasio(n, interval)))
    return out


# ----------------------------------------------------------------------------
# UI
# ----------------------------------------------------------------------------
st.title("📝 Generator DC & Perhitungan Anomali")
tab_gen, tab_anomali = st.tabs(["📝 Generator Kalimat", "📊 Perhitungan Anomali"])

# ============================ TAB 1: GENERATOR ==============================
with tab_gen:
    st.caption("Isi nilai awal dan nilai perubahan. Hanya kolom yang terisi yang akan digenerate.")

    with st.expander("Keterangan kode DC"):
        for k, v in KODE_DC.items():
            st.markdown(f"- **{k}** — {v}")

    kode_global = st.selectbox(
        "Kode DC (default untuk semua kolom)",
        list(KODE_DC.keys()),
        format_func=lambda k: f"{k} — {KODE_DC[k]}",
        key="gen_kode_global",
    )
    beda_kode = st.checkbox("Pakai kode DC berbeda untuk tiap kolom", key="gen_beda_kode")

    st.markdown("---")
    h1, h2, h3 = st.columns(3)
    h1.markdown("**Kolom**")
    h2.markdown("**Nilai awal (X / A)**")
    h3.markdown("**Nilai perubahan (Y)**")

    baris = []
    for kol in KOLOM:
        c1, c2, c3 = st.columns(3)
        c1.markdown(f"**{kol}**")
        awal = c2.text_input(
            f"Awal {kol}", key=f"awal_{kol}", label_visibility="collapsed",
            placeholder="mis. 500000", on_change=cb_format, args=(f"awal_{kol}",),
        )
        ubah = c3.text_input(
            f"Perubahan {kol}", key=f"ubah_{kol}", label_visibility="collapsed",
            placeholder="mis. 50000", on_change=cb_format, args=(f"ubah_{kol}",),
        )
        kode = kode_global
        if beda_kode:
            kode = c1.selectbox(
                f"Kode {kol}", list(KODE_DC.keys()),
                index=list(KODE_DC.keys()).index(kode_global),
                key=f"kode_{kol}", label_visibility="collapsed",
            )
        baris.append((kode, kol, awal, ubah))

    st.markdown("---")
    hasil = [s for s in (buat_kalimat(*b) for b in baris) if s]

    st.subheader("Hasil generate")
    if hasil:
        teks_hasil = "\n".join(hasil)
        st.code(teks_hasil, language=None)
        st.text_area("Edit / salin manual", value=teks_hasil, height=30 + 28 * len(hasil), key="gen_manual")
    else:
        st.info("Belum ada kolom yang terisi.")

    st.button("🗑️ Reset semua isian", on_click=cb_reset_gen, key="gen_reset")

# ======================== TAB 2: PERHITUNGAN ANOMALI ========================
with tab_anomali:
    st.caption(
        "Isi Data Awal, kolom Adjustment otomatis terisi sama dan bisa diubah sampai hasilnya wajar."
    )

    with st.expander("Rumus & aturan kewajaran"):
        st.markdown(
            "- **NTB [U2]** = 27a − (26b + 26c + 26d) → wajar jika **tidak negatif**\n"
            "- **R_NTB/Output [U3]** = NTB / (27a − 26c) → wajar jika berada pada interval kategori terpilih\n"
            "- **R_Output/Aset [U4]** = (27a − 26c) / 28c → wajar jika **0,200 – 0,400**\n"
            "- **R_Upah/NTB [U5]** = 26a / NTB → wajar jika **0,400 – 0,600**\n\n"
            "Rasio dibulatkan ke 3 digit di belakang koma sebelum dicek terhadap interval."
        )

    def label_kategori(k):
        iv = KATEGORI[k]
        rentang = "-" if iv is None else f"{fmt_rasio(iv[0], 2)} – {fmt_rasio(iv[1], 2)}"
        return f"Kategori {k} ({rentang})"

    kategori = st.selectbox(
        "Kategori kewajaran", list(KATEGORI.keys()), format_func=label_kategori, key="an_kategori"
    )
    interval_u3 = KATEGORI[kategori]

    st.markdown("---")
    h1, h2, h3 = st.columns([2, 1.5, 1.5])
    h1.markdown("**Kolom**")
    h2.markdown("**Perhitungan Data Awal**")
    h3.markdown("**Perhitungan Data Adjustment**")

    teks_awal, teks_adj = {}, {}
    for kol in KOLOM:
        c1, c2, c3 = st.columns([2, 1.5, 1.5])
        c1.markdown(f"**{kol}** {NAMA_KOLOM[kol]}")
        teks_awal[kol] = c2.text_input(
            f"Awal {kol}", key=f"an_awal_{kol}", label_visibility="collapsed",
            placeholder="0", on_change=cb_awal, args=(kol,),
        )
        teks_adj[kol] = c3.text_input(
            f"Adjustment {kol}", key=f"an_adj_{kol}", label_visibility="collapsed",
            placeholder="0", on_change=cb_format, args=(f"an_adj_{kol}",),
        )

    b1, b2, _ = st.columns([1.5, 1.5, 4])
    b1.button("↩️ Samakan adjustment dengan data awal", on_click=cb_salin, key="an_salin")
    b2.button("🗑️ Reset semua isian", on_click=cb_reset_an, key="an_reset")

    # teks yang tidak bisa dibaca sebagai angka
    salah = [
        f"{kol} ({jenis})"
        for jenis, d in (("awal", teks_awal), ("adjustment", teks_adj))
        for kol, t in d.items()
        if t.strip() and parse_angka(t) is None
    ]
    if salah:
        st.warning("Isian bukan angka, dianggap kosong: " + ", ".join(salah))

    # ---- Hitung & cek kewajaran ----
    st.markdown("---")
    st.subheader("Hasil perhitungan & cek kewajaran")
    ev_awal = evaluasi(hitung(teks_awal), interval_u3)
    ev_adj = evaluasi(hitung(teks_adj), interval_u3)

    nama_ind = ["NTB [U2]", "R_NTB/Output [U3]", "R_Output/Aset [U4]", "R_Upah/NTB [U5]"]
    syarat = [
        "≥ 0 (tidak negatif)",
        "-" if interval_u3 is None else f"{fmt_rasio(interval_u3[0])} – {fmt_rasio(interval_u3[1])}",
        f"{fmt_rasio(INTERVAL_U4[0])} – {fmt_rasio(INTERVAL_U4[1])}",
        f"{fmt_rasio(INTERVAL_U5[0])} – {fmt_rasio(INTERVAL_U5[1])}",
    ]
    df = pd.DataFrame(
        {
            "Indikator": nama_ind,
            "Syarat wajar": syarat,
            "Data Awal": [e[0] for e in ev_awal],
            "Status Awal": [e[1] for e in ev_awal],
            "Data Adjustment": [e[0] for e in ev_adj],
            "Status Adjustment": [e[1] for e in ev_adj],
        }
    ).set_index("Indikator")
    st.table(df)

    def ringkas(judul, ev):
        if all(e[1] == "Belum diisi" for e in ev):
            return
        bermasalah = [
            nama_ind[i] for i, e in enumerate(ev) if e[1].startswith(("❌", "⚠️"))
        ]
        if bermasalah:
            st.warning(f"{judul}: perlu dicek → " + ", ".join(bermasalah))
        else:
            st.success(f"{judul}: semua indikator wajar ✅")

    ringkas("Data Awal", ev_awal)
    ringkas("Data Adjustment", ev_adj)

    # ---- Generate kalimat ----
    st.markdown("---")
    st.subheader("Generate kalimat perbaikan")
    st.caption("Kalimat dibuat hanya untuk kolom yang nilainya berbeda antara Data Awal dan Adjustment.")

    kode_an = st.selectbox(
        "Kode DC", KODE_UBAH, format_func=lambda k: f"{k} — {KODE_DC[k]}", key="an_kode_global"
    )
    beda_an = st.checkbox("Pakai kode DC berbeda untuk tiap kolom", key="an_beda_kode")
    kode_kolom = {kol: kode_an for kol in KOLOM}
    if beda_an:
        cols = st.columns(len(KOLOM))
        for c, kol in zip(cols, KOLOM):
            kode_kolom[kol] = c.selectbox(
                kol, KODE_UBAH, index=KODE_UBAH.index(kode_an), key=f"an_kode_{kol}"
            )

    berubah = [
        kol for kol in KOLOM
        if format_nilai(teks_awal[kol]) != format_nilai(teks_adj[kol])
    ]

    if berubah:
        gaya1 = [buat_kalimat(kode_kolom[k], k, teks_awal[k], teks_adj[k], True) for k in berubah]
        gaya2 = [buat_kalimat(kode_kolom[k], k, teks_awal[k], teks_adj[k], False) for k in berubah]
        g1, g2 = st.columns(2)
        with g1:
            st.markdown("**Gaya 1 — dengan nilai awal**")
            st.code("\n".join(s for s in gaya1 if s), language=None)
        with g2:
            st.markdown("**Gaya 2 — tanpa nilai awal**")
            st.code("\n".join(s for s in gaya2 if s), language=None)
    else:
        st.info("Belum ada kolom yang berbeda antara Data Awal dan Adjustment.")
