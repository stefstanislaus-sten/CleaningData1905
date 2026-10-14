import re
import streamlit as st

st.set_page_config(page_title="Generator Kalimat DC", page_icon="📝", layout="centered")

KODE_DC = {
    "#DC_00": "tetap",
    "#DC_01": "berubah karena miskonsep PPL",
    "#DC_02": "berubah karena konfirmasi/revisit responden",
    "#DC_03": "berubah karena info survei/data administrasi",
    "#DC_04": "berubah karena selain #DC_01 - #DC_03",
}
KOLOM = ["26a", "26b", "26c", "26d", "27a", "28c"]


def format_nilai(teks: str) -> str:
    """Rapikan angka dengan pemisah ribuan titik (500000 -> 500.000).
    Jika bukan angka murni, teks dipakai apa adanya."""
    teks = (teks or "").strip()
    if not teks:
        return ""
    bersih = teks.replace(".", "").replace(",", "").replace(" ", "")
    if re.fullmatch(r"-?\d+", bersih):
        return f"{int(bersih):,}".replace(",", ".")
    return teks


def generate(baris):
    hasil = []
    for b in baris:
        awal = format_nilai(b["awal"])
        ubah = format_nilai(b["ubah"])
        if not awal and not ubah:
            continue  # kolom kosong tidak digenerate
        awal_txt = awal if awal else "kosong"
        ubah_txt = ubah if ubah else "kosong"
        if b["kode"] == "#DC_00":
            hasil.append(f'{b["kode"]} isian {b["kolom"]} tetap {awal_txt}')
        else:
            hasil.append(
                f'{b["kode"]} memperbaiki isian {b["kolom"]} dari {awal_txt} menjadi {ubah_txt}'
            )
    return hasil


st.title("📝 Generator Kalimat Perbaikan Isian")
st.caption("Isi nilai awal dan nilai perubahan. Hanya kolom yang terisi yang akan digenerate.")

with st.expander("Keterangan kode DC"):
    for k, v in KODE_DC.items():
        st.markdown(f"- **{k}** — {v}")

kode_global = st.selectbox(
    "Kode DC (default untuk semua kolom)",
    list(KODE_DC.keys()),
    format_func=lambda k: f"{k} — {KODE_DC[k]}",
)
beda_kode = st.checkbox("Pakai kode DC berbeda untuk tiap kolom")

st.markdown("---")
h1, h2, h3 = st.columns([1, 1, 1])
h1.markdown("**Kolom**")
h2.markdown("**Nilai awal (X / A)**")
h3.markdown("**Nilai perubahan (Y)**")

baris = []
for kol in KOLOM:
    c1, c2, c3 = st.columns([1, 1, 1])
    c1.markdown(f"**{kol}**")
    awal = c2.text_input(f"Awal {kol}", key=f"awal_{kol}", label_visibility="collapsed")
    ubah = c3.text_input(f"Perubahan {kol}", key=f"ubah_{kol}", label_visibility="collapsed")
    kode = kode_global
    if beda_kode:
        kode = c1.selectbox(
            f"Kode {kol}",
            list(KODE_DC.keys()),
            index=list(KODE_DC.keys()).index(kode_global),
            key=f"kode_{kol}",
            label_visibility="collapsed",
        )
    baris.append({"kolom": kol, "kode": kode, "awal": awal, "ubah": ubah})

st.markdown("---")
hasil = generate(baris)

st.subheader("Hasil generate")
if hasil:
    teks_hasil = "\n".join(hasil)
    st.code(teks_hasil, language=None)  # ada tombol copy di pojok kanan atas
    st.text_area("Edit / salin manual", value=teks_hasil, height=30 + 28 * len(hasil))
else:
    st.info("Belum ada kolom yang terisi.")

if st.button("🗑️ Reset semua isian"):
    for kol in KOLOM:
        for p in ("awal_", "ubah_", "kode_"):
            st.session_state.pop(f"{p}{kol}", None)
    st.rerun()
