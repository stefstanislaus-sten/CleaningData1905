import re
import json
import os
import urllib.parse

import requests
import streamlit.components.v1 as components
import streamlit as st

st.set_page_config(page_title="Generator DC & Anomali", page_icon="📝", layout="wide", initial_sidebar_state="collapsed")

# ----------------------------------------------------------------------------
# DATA MASTER
# ----------------------------------------------------------------------------
KODE_DC = {
    "#0": "tetap",
    "#1": "berubah karena miskonsep PPL",
    "#2": "berubah karena konfirmasi/revisit responden",
    "#3": "berubah karena info survei/data administrasi",
    "#4": "berubah karena selain #1 - #3",
}
KODE_UBAH = [k for k in KODE_DC if k != "#0"]  # untuk menu anomali (ada perubahan)

KOLOM = ["26a", "26b", "26c", "26d", "27a", "28a", "28b", "28c"]
# Kolom tambahan: tidak masuk perhitungan utama (U2-U5), hanya untuk cek selisih 27c vs 26f
KOLOM_EXTRA = ["27b", "26e"]
# Nomor kolom pada catatan jika usaha dimulai tahun 2026
KOLOM_2026 = {
    "26a": "30a", "26b": "30b", "26c": "30c", "26d": "30d",
    "27a": "31a", "28a": "32a", "28b": "32b", "28c": "32c",
    "27b": "31b", "26e": "30e", "27c": "31c", "26f": "30f",
}

NAMA_KOLOM = {
    "26a": "Upah",
    "26b": "Biaya produksi",
    "26c": "Biaya pembelian barang yang terjual",
    "26d": "Biaya operasional (air, listrik, gas, internet, pulsa, pemeliharaan, biaya angkutan, dll.)",
    "27a": "Nilai pendapatan barang dan jasa",
    "28a": "Nilai aset tanah dan bangunan",
    "28b": "Nilai aset selain tanah dan bangunan",
    "28c": "Nilai total aset (28a + 28b)",
    "27b": "Pendapatan lainnya",
    "26e": "Pengeluaran lainnya",
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


def buat_kalimat(kode, kolom, awal, ubah, tampil_awal=True):
    awal = format_nilai(awal)
    ubah = format_nilai(ubah)
    if not awal and not ubah:
        return None
    ubah_txt = ubah if ubah else "kosong"
    if kode == "#0":
        return f"{kode} isian {kolom} tetap {awal if awal else 'kosong'}"
    if tampil_awal and awal:
        return f"{kode} memperbaiki isian {kolom} dari {awal} menjadi {ubah_txt}"
    return f"{kode} memperbaiki isian {kolom} menjadi {ubah_txt}"


# ----------------------------------------------------------------------------
# CALLBACK (tab generator)
# ----------------------------------------------------------------------------
def cb_format(key: str):
    st.session_state[key] = format_nilai(st.session_state.get(key, ""))


def cb_reset_gen():
    for kol in KOLOM:
        st.session_state[f"awal_{kol}"] = ""
        st.session_state[f"ubah_{kol}"] = ""


# ----------------------------------------------------------------------------
# PENELUSURAN GOOGLE (Gemini + Grounding with Google Search) -> jawaban singkat
# ----------------------------------------------------------------------------
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
MODEL_DEFAULT = ["gemini-3-flash-preview", "gemini-2.5-flash"]  # dicoba berurutan
PETUNJUK = (
    "Kamu asisten pencari data harga/statistik untuk petugas survei di Indonesia. "
    "Gunakan hasil Google Search. Jawab LANGSUNG dalam 1-2 kalimat bahasa Indonesia "
    "tanpa pembukaan, tanpa daftar. Jika menyangkut harga/nilai, sebutkan angka rata-rata "
    "(dan kisarannya bila ada) dalam Rupiah, contoh: 'Harga mesin fotocopy rata-rata "
    "sekitar Rp100 juta (kisaran Rp40-250 juta).' Jika data tidak ditemukan, katakan singkat."
)


def _secret(nama):
    try:
        v = st.secrets.get(nama)
    except Exception:
        v = None
    return v or os.environ.get(nama)


@st.cache_data(ttl=3600, show_spinner=False)
def cari_google(q: str):
    """Jawaban singkat berbasis Google Search. Error tidak di-cache."""
    kunci = _secret("GEMINI_API_KEY")
    if not kunci:
        raise RuntimeError("TIDAK_ADA_KUNCI")
    modelkustom = _secret("GEMINI_MODEL")
    daftar = ([modelkustom] if modelkustom else []) + MODEL_DEFAULT
    body = {
        "systemInstruction": {"parts": [{"text": PETUNJUK}]},
        "contents": [{"parts": [{"text": q}]}],
        "tools": [{"google_search": {}}],
        "generationConfig": {"temperature": 0.2},
    }
    r = None
    for model in daftar:
        r = requests.post(
            GEMINI_URL.format(model=model),
            headers={"x-goog-api-key": kunci, "Content-Type": "application/json"},
            json=body,
            timeout=45,
        )
        # model tidak ditemukan/tidak tersedia -> coba model berikutnya
        if r.status_code in (400, 404) and "model" in r.text.lower() and "not found" in r.text.lower():
            continue
        break
    if r.status_code != 200:
        try:
            pesan = r.json().get("error", {}).get("message", r.text)
        except Exception:
            pesan = r.text
        raise RuntimeError(f"Google/Gemini {r.status_code}: {pesan[:200]}")

    data = r.json()
    kandidat = (data.get("candidates") or [None])[0]
    if not kandidat:
        raise RuntimeError("Google tidak mengembalikan jawaban untuk pertanyaan ini.")
    teks = " ".join(
        p.get("text", "") for p in kandidat.get("content", {}).get("parts", [])
    ).strip()
    meta = kandidat.get("groundingMetadata", {})
    sumber, dilihat = [], set()
    for c in meta.get("groundingChunks", []):
        w = c.get("web") or {}
        uri = w.get("uri", "")
        if uri.startswith(("http://", "https://")) and uri not in dilihat:
            dilihat.add(uri)
            sumber.append((w.get("title") or "sumber", uri))
    return {
        "jawaban": teks or "Tidak ditemukan jawaban yang pasti.",
        "sumber": sumber[:4],
        "saran_html": (meta.get("searchEntryPoint") or {}).get("renderedContent", ""),
    }


def tampilkan_pencarian():
    """Panel telusur di sidebar: tersembunyi, dibuka lewat panah di pojok kiri atas."""
    with st.sidebar:
        st.markdown("### 🔎 Telusuri Google")
        with st.form("form_cari"):
            q = st.text_input(
                "Pertanyaan",
                placeholder="mis. berapa harga mesin fotocopy pada umumnya?",
                key="cari_q",
            )
            kirim = st.form_submit_button("Cari")

        if kirim:
            q = (q or "").strip()
            if not q:
                st.session_state["cari_hasil"] = None
                st.warning("Ketik pertanyaan dulu.")
            else:
                try:
                    with st.spinner("Menelusuri Google..."):
                        st.session_state["cari_hasil"] = {"q": q, **cari_google(q)}
                except RuntimeError as e:
                    st.session_state["cari_hasil"] = {"q": q, "error": str(e)}
                except Exception as e:
                    st.session_state["cari_hasil"] = {"q": q, "error": f"Gagal terhubung: {e}"}

        data = st.session_state.get("cari_hasil")
        if not data:
            return
        url_google = "https://www.google.com/search?hl=id&q=" + urllib.parse.quote(data["q"])

        if "error" in data:
            if data["error"] == "TIDAK_ADA_KUNCI":
                st.info(
                    "Penelusuran belum aktif. Tambahkan `GEMINI_API_KEY` di Settings → Secrets "
                    "(kunci gratis dari aistudio.google.com/apikey)."
                )
            else:
                st.error(data["error"])
        else:
            st.markdown(f"**{data['jawaban']}**")
            if data["sumber"]:
                tautan = " · ".join(
                    f"[{t.replace('[', '(').replace(']', ')')}]({u.replace(')', '%29')})"
                    for t, u in data["sumber"]
                )
                st.caption("Sumber: " + tautan)
            if data["saran_html"]:
                # Saran Penelusuran Google (kompatibel versi Streamlit baru & lama)
                if hasattr(st, "iframe"):
                    st.iframe(data["saran_html"], height="content")
                else:
                    components.html(data["saran_html"], height=120, scrolling=True)
        st.link_button("Buka hasil lengkap di Google ↗", url_google)
        if st.button("Tutup hasil"):
            st.session_state["cari_hasil"] = None
            st.rerun()


# ----------------------------------------------------------------------------
# TEMPLATE HTML/JS UNTUK TAB ANOMALI (perhitungan live saat mengetik)
# ----------------------------------------------------------------------------
HTML_ANOMALI = r"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
:root{--fg:#1f2430;--muted:#6b7280;--line:#e5e7eb;--card:#f3f4f6;--input:#fff;
--ok:#15803d;--okbg:#dcfce7;--bad:#b91c1c;--badbg:#fee2e2;--warn:#b45309;--warnbg:#fef3c7;
--up:#1d4ed8;--down:#c2410c;--btn:#e5e7eb}
:root.dark{--fg:#e5e7eb;--muted:#9ca3af;--line:#374151;--card:#1f2937;--input:#111827;
--ok:#4ade80;--okbg:#14532d;--bad:#fca5a5;--badbg:#7f1d1d;--warn:#fcd34d;--warnbg:#78350f;
--up:#93c5fd;--down:#fdba74;--btn:#374151}
*{box-sizing:border-box}
body{margin:0;font-family:"Source Sans Pro",system-ui,-apple-system,Segoe UI,sans-serif;color:var(--fg);font-size:15px;background:transparent}
#app{padding:4px 2px 16px}
h3{margin:22px 0 8px;font-size:18px}
table{width:100%;border-collapse:collapse}
th,td{padding:6px 8px;text-align:left;border-bottom:1px solid var(--line);vertical-align:middle}
th{font-size:13px;color:var(--muted);font-weight:600}
.num{text-align:right;font-variant-numeric:tabular-nums}
input[type=text],select{width:100%;padding:7px 9px;border:1px solid var(--line);border-radius:6px;background:var(--input);color:var(--fg);font:inherit}
input[type=text]{text-align:right}
select{width:auto;min-width:260px}
select{max-width:100%}
.scroll{width:100%;overflow-x:auto}
@media(max-width:520px){select{min-width:0;width:100%}}
.small{font-size:12px;color:var(--muted)}
input.ro{background:var(--card);font-weight:600;cursor:default}
tr.extra td{background:var(--card)}
.hide-sg .sg-col{display:none}
.sg{min-width:150px;padding:5px 9px;border:1px dashed var(--line);border-radius:6px;background:var(--card);text-align:right;opacity:.95}
.nom{cursor:pointer;font-weight:600;user-select:all;-webkit-user-select:all;border-bottom:1px dotted var(--muted)}
.nom::after{content:" 📋";font-size:11px;opacity:.55}
.nom:hover{background:var(--okbg)}
.nom.copied{background:var(--okbg);color:var(--ok)}
.nom.copied::after{content:" ✓ tersalin";font-size:11px;opacity:1}
.sg small .nom{font-size:12px;color:var(--fg)}
.sg small .nom.copied{color:var(--ok)}
.sg small{display:block;font-size:11px;color:var(--muted);font-weight:400}
details summary{cursor:pointer;margin:6px 0}
.tahan{display:block;text-align:right;font-size:11px;color:var(--muted);margin-top:3px;cursor:pointer}
.tahan input{vertical-align:middle;margin:0 3px 0 0}
.row{display:flex;gap:12px;align-items:center;flex-wrap:wrap;margin:6px 0}
button{padding:7px 12px;border:1px solid var(--line);border-radius:6px;background:var(--btn);color:var(--fg);font:inherit;cursor:pointer}
button:hover{filter:brightness(1.08)}
.tag{display:inline-block;padding:2px 8px;border-radius:999px;font-size:13px;white-space:nowrap}
.ok{background:var(--okbg);color:var(--ok)}.bad{background:var(--badbg);color:var(--bad)}
.warn{background:var(--warnbg);color:var(--warn)}.na{background:var(--card);color:var(--muted)}
.up{color:var(--up);font-weight:600}.down{color:var(--down);font-weight:600}.zero{color:var(--muted)}
.box{padding:10px 12px;border-radius:8px;margin:6px 0}
pre{margin:4px 0;padding:10px 12px;background:var(--card);border-radius:8px;white-space:pre-wrap;word-break:break-word;font-size:14px;min-height:20px}
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:14px}
@media(max-width:800px){.grid2{grid-template-columns:1fr}}
.kk{display:flex;gap:8px;flex-wrap:wrap}.kk div{display:flex;flex-direction:column;font-size:12px;color:var(--muted)}
.kk select{min-width:90px}
</style></head>
<body><div id="app">
<div class="row"><b>Kategori kewajaran</b><select id="kat"></select>
<label><input type="checkbox" id="saran"> Tampilkan saran perbaikan?</label></div>
<details id="saranOpt" style="display:none" open>
<summary><b>⚙️ Pengaturan saran</b></summary>
<div class="small">Urutan saran: <b>U4</b> (aset 28a+28b disesuaikan ke batas 0,2/0,4 dari Output) → <b>U3</b> (biaya 26b+26d disesuaikan ke batas kategori) → <b>U5</b> (upah 26a ke batas 0,4/0,6 dari NTB). Penambahan/pengurangan dibagi proporsional ke kolom terkait dan dibulatkan sesuai skala nilainya (nilai puluhan juta → kelipatan jutaan, nilai jutaan → kelipatan ratusan ribu).</div>
<label><input type="checkbox" id="kunci"> Otomatis pertahankan kolom yang sudah saya ubah</label>
<div class="small">Kategori <b>G</b> (perdagangan besar): 26c ditampilkan dan saran hanya untuk U3 (26c disesuaikan, lalu ada alternatif lewat 26b/26d); U4 dan U5 tidak dicek.</div>
</details>
<div class="scroll"><table id="tin" class="hide-sg">
<thead><tr><th>Kolom</th><th>Perhitungan Data Awal</th><th>Perhitungan Data Adjustment</th><th class="num">Perubahan (Adj &minus; Awal)</th><th class="num sg-col">Saran perbaikan perhitungan</th></tr></thead>
<tbody id="inbody"></tbody></table></div>
<div id="saranInfo"></div>
<div id="altG"></div>
<div class="row"><button id="salin">↩️ Samakan adjustment dengan data awal</button><button id="reset">🗑️ Reset semua isian</button></div>
<div class="small">Adjustment otomatis mengikuti Data Awal selama belum Anda ubah sendiri. Semua hasil dihitung langsung saat mengetik. Kolom 27b &amp; 26e (abu-abu) tidak masuk perhitungan U2–U5, hanya untuk cek selisih total pendapatan (27c) dan total pengeluaran (26f).</div>

<h3>Hasil perhitungan &amp; cek kewajaran (live)</h3>
<div class="scroll"><table><thead><tr><th>Indikator</th><th>Syarat wajar</th><th class="num">Data Awal</th><th>Status Awal</th><th class="num">Data Adjustment</th><th>Status Adjustment</th><th class="num">Perubahan</th></tr></thead>
<tbody id="res"></tbody></table></div>
<div id="ringkas"></div>

<h3>Generate kalimat perbaikan</h3>
<div class="small">Kalimat dibuat hanya untuk kolom yang nilainya berbeda antara Data Awal dan Adjustment.</div>
<div class="row"><b>Kode DC</b><select id="kode"></select>
<label><input type="checkbox" id="beda"> Pakai kode DC berbeda untuk tiap kolom</label>
<label><input type="checkbox" id="p28c"> Sertakan 28c (total aset) dalam kalimat</label>
<label><input type="checkbox" id="th26"> Usaha dimulai tahun 2026</label></div>
<div class="small" id="hint26" style="display:none">Nomor kolom pada catatan: 26a→30a, 26b→30b, 26c→30c, 26d→30d, 26e→30e, 26f→30f, 27a→31a, 27b→31b, 27c→31c, 28a→32a, 28b→32b, 28c→32c.</div>
<div id="kk" class="kk"></div>
<div id="gen"></div>
<div id="sel"></div>
</div>
<script>
(function(){
const KOLOM=__KOLOM__, EXTRA=__EXTRA__, NAMA=__NAMA__, KAT=__KAT__, KODE=__KODE__, KODE_DC=__KODE_DC__, MAP26=__MAP26__;
const ALL=KOLOM.concat(EXTRA);
const NO26C=['A','B','C','F','H','K','L','N','O','R','Q','S','T'];   // kategori tanpa 26c (otomatis 0)
function hide26c(){return NO26C.indexOf(S.kat)>=0;}
const U4=[0.2,0.4], U5=[0.4,0.6], KEY='dc_anomali_state_v5';
const $=id=>document.getElementById(id);
let S={kat:'A',awal:{},adj:{},dirty:{},kode:KODE[0],beda:false,kodeKol:{},pakai28c:false,saran:false,th26:false,kunci:true,tahan:{}};
try{const r=sessionStorage.getItem(KEY); if(r) S=Object.assign(S,JSON.parse(r));}catch(e){}
ALL.forEach(k=>{S.awal[k]=S.awal[k]||'';S.adj[k]=S.adj[k]||'';});
S.tahan=S.tahan||{};
let lastSaran=null;

// ---------- tema ----------
function detectDark(){
  try{
    const el=window.parent.document.querySelector('.stApp')||window.parent.document.body;
    const m=getComputedStyle(el).backgroundColor.match(/[\d.]+/g);
    if(m&&!(m.length>=4&&parseFloat(m[3])===0)) return (0.299*m[0]+0.587*m[1]+0.114*m[2])<128;
  }catch(e){}
  try{return window.matchMedia('(prefers-color-scheme: dark)').matches;}catch(e){}
  return false;
}
if(detectDark()) document.documentElement.classList.add('dark');

// ---------- format & parse ----------
function ribuan(s){return s.replace(/\B(?=(\d{3})+(?!\d))/g,'.');}
function fmtText(raw){
  raw=(raw||'').trim(); if(!raw) return '';
  const neg=raw[0]==='-';
  const s=raw.replace(/[^\d,]/g,'');
  const i=s.indexOf(',');
  let int=i<0?s:s.slice(0,i);
  const dec=i<0?null:s.slice(i+1).replace(/,/g,'');
  int=int.replace(/^0+(?=\d)/,'');
  if(int===''&&dec!==null) int='0';
  return (neg?'-':'')+ribuan(int)+(dec!==null?','+dec:'');
}
function liveFormat(el){
  const raw=el.value, pos=el.selectionStart==null?raw.length:el.selectionStart;
  const n=raw.slice(0,pos).replace(/[^\d,\-]/g,'').length;
  const f=fmtText(raw);
  el.value=f;
  let c=0,p=0;
  while(p<f.length&&c<n){ if(/[\d,\-]/.test(f[p])) c++; p++; }
  try{el.setSelectionRange(p,p);}catch(e){}
  return f;
}
function parse(s){
  if(!s) return null;
  const t=s.replace(/\./g,'').replace(',','.');
  if(t===''||t==='-') return null;
  const n=Number(t); return isNaN(n)?null:n;
}
function fmtAngka(x){
  if(Math.abs(x)<1e-9) return '0';
  const neg=x<0,a=Math.abs(x);
  if(Math.abs(a-Math.round(a))<1e-9) return (neg?'-':'')+ribuan(String(Math.round(a)));
  const p=a.toFixed(2).split('.'); return (neg?'-':'')+ribuan(p[0])+','+p[1];
}
function plainNum(x){  // angka polos tanpa pemisah ribuan (aman ditempel ke isian mana pun)
  if(Math.abs(x-Math.round(x))<1e-9) return String(Math.round(x));
  return x.toFixed(2).replace('.',',');
}
function fmtR(x){return x.toFixed(3).replace('.',',');}
function nz(x){return x===null?0:x;}

// ---------- perhitungan ----------
function totalAset(t){
  const a=parse(t['28a']),b=parse(t['28b']);
  return (a===null&&b===null)?'':fmtAngka(nz(a)+nz(b));
}
function syncTotal(){  // 28c = 28a + 28b (otomatis)
  S.awal['28c']=totalAset(S.awal); S.adj['28c']=totalAset(S.adj);
  $('a_28c').value=S.awal['28c']; $('d_28c').value=S.adj['28c'];
}
function hitung(t){
  const v={};let all=true;
  KOLOM.forEach(k=>{v[k]=parse(t[k]); if(k!=='28c'&&v[k]!==null) all=false;});
  return all?null:hitungV(v);
}
function hitungV(v){
  const g=k=>nz(v[k]);
  const aset=g('28a')+g('28b');
  const u2=g('27a')-(g('26b')+g('26c')+g('26d'));
  const out=g('27a')-g('26c');
  return {U2:u2,OUT:out,U3:out?u2/out:null,U4:aset?out/aset:null,U5:u2?g('26a')/u2:null};
}
function stRasio(n,iv){
  if(iv===null) return ['na','➖ Tidak ada interval'];
  if(n===null) return ['warn','⚠️ Tidak dapat dihitung'];
  const r=Math.round(n*1000)/1000;
  return (r>=iv[0]-1e-9&&r<=iv[1]+1e-9)?['ok','✅ Wajar']:['bad','❌ Tidak wajar'];
}
function evaluasi(h){
  if(h===null) return [0,1,2,3,4].map(()=>({txt:'-',cls:'na',stat:'Belum diisi'}));
  const iv=[null,null,KAT[S.kat],U4,U5], val=[h.U2,h.OUT,h.U3,h.U4,h.U5];
  return val.map((v,i)=>{
    if(i===0){const ok=v>=0; return {txt:fmtAngka(v),cls:ok?'ok':'bad',stat:ok?'✅ Wajar':'❌ Tidak wajar'};}
    if(i===1) return {txt:fmtAngka(v),cls:'na',stat:'➖ Tanpa syarat'};
    if(S.kat==='G'&&i>=3) return {txt:v===null?'-':fmtR(v),cls:'na',stat:'➖ Tidak dicek (kategori G)'};
    const s=stRasio(v,iv[i]); return {txt:v===null?'-':fmtR(v),cls:s[0],stat:s[1]};
  });
}
function delta(a,b,money){
  if(a===null||b===null) return {t:'-',c:'zero'};
  let d=b-a; if(!money) d=Math.round(d*1000)/1000;
  if(Math.abs(d)<1e-9) return {t:'0',c:'zero'};
  return {t:(d>0?'+':'−')+(money?fmtAngka(Math.abs(d)):fmtR(Math.abs(d))),c:d>0?'up':'down'};
}

// ---------- saran perbaikan ----------
// Urutan: U4 (aset) -> U3 (biaya 26b+26d) -> U5 (upah). Output = 27a - 26c.
function r3(x){return Math.round(x*1000)/1000;}
function inR(r,lo,hi){const x=r3(r);return x>=lo-1e-9&&x<=hi+1e-9;}
function cekOk(v){
  return evaluasi(hitungV(v)).every(e=>e.cls!=='bad'&&e.cls!=='warn');
}
function kunciMap(termasukOtomatis){  // terkunci = dipertahankan manual (centang) atau sudah diubah user
  const L={};
  KOLOM.forEach(k=>{
    if(k==='28c'){L[k]=false;return;}
    const manual=!!S.tahan[k];
    const diubah=termasukOtomatis&&!!S.kunci&&S.adj[k]!==S.awal[k];
    L[k]=manual||diubah;
  });
  return L;
}
// Bagi perubahan total d ke kolom ks secara proporsional terhadap nilainya.
// Tiap kolom dibulatkan ke skala nilainya (50 juta -> kelipatan 1 juta; 5 juta -> kelipatan 100 ribu).
// d>0: total perubahan minimal d; d<0: total perubahan maksimal d (dibulatkan ke arah aman).
// f = faktor kehalusan pembulatan (1, 0.1, 0.01, 0 = satuan rupiah).
const BOBOT_26B=2;   // bobot 26b dibanding 26d saat membagi saran (26b biasanya porsinya lebih besar)
function bagiProp(n,ks,d,L,f){
  const fr=ks.filter(k=>!L[k]);
  if(!fr.length||Math.abs(d)<1e-9) return;
  const stp=k=>f===0?1:Math.max(1,Math.pow(10,Math.floor(Math.log10(Math.max(n[k],1)))-1)*f);
  const w=k=>n[k]*(k==='26b'?BOBOT_26B:1);   // 26b mendapat porsi lebih besar daripada 26d
  const tot=fr.reduce((a,k)=>a+w(k),0);
  fr.sort((a,b)=>w(b)-w(a));
  const big=fr[0]; let sisa=d; const ch={};
  fr.slice(1).forEach(k=>{
    const sh=tot>0?d*w(k)/tot:0, s=stp(k);
    let c=Math.round(sh/s)*s;
    if(n[k]+c<0) c=-n[k];
    ch[k]=c; sisa-=c;
  });
  const sb=stp(big);
  let cb=(d>0?Math.ceil(sisa/sb-1e-9):Math.floor(sisa/sb+1e-9))*sb;
  if(n[big]+cb<0) cb=-n[big];
  ch[big]=cb;
  fr.forEach(k=>{n[k]=n[k]+ch[k];});
}
// Kategori G (perdagangan besar): hanya U3, dengan 26c sebagai penyeimbang.
// s = porsi perbaikan yang diambil dari 26b/26d (0 = hanya 26c, 1 = hanya 26b/26d).
function rencanaG(v,L,f,s){
  const lo=KAT.G[0], hi=KAT.G[1], notes=[];
  const n=Object.assign({},v);
  const O=v['27a']-v['26c'], C=v['26b']+v['26d'];
  const u=O>0?(O-C)/O:null;
  const low=(u===null)||r3(u)<lo-1e-9, high=u!==null&&r3(u)>hi+1e-9;
  const y26=!L['26c'];
  if(low){
    const tgt=v['27a']-C/(1-lo);   // 26c harus sekecil ini (maksimal)
    notes.push('U3 < '+fmtR(lo)+' → 26c maksimal = 27a − (26b+26d) ÷ (1 − '+fmtR(lo)+') = '+fmtAngka(tgt)+(tgt<0?' (negatif, jadi 26c diturunkan sampai 0 dan sisanya lewat 26b/26d)':'')+'.');
    const D=Math.max(0,v['26c']-tgt), want=y26?D*(1-s):0;
    if(want>1e-9) bagiProp(n,['26c'],-want,L,f);
    const yact=v['26c']-n['26c'], x=C-(1-lo)*(O+yact);
    if(x>1e-9) bagiProp(n,['26b','26d'],-x,L,f);
  }else if(high){
    const tgt=C>0?v['27a']-C/(1-hi):null;   // 26c harus sebesar ini (minimal)
    notes.push(tgt===null?'U3 > '+fmtR(hi)+' dan 26b+26d = 0 → 26b/26d perlu ditambah.':'U3 > '+fmtR(hi)+' → 26c minimal = 27a − (26b+26d) ÷ (1 − '+fmtR(hi)+') = '+fmtAngka(tgt)+'.');
    const Dm=tgt===null?0:Math.max(0,tgt-v['26c']), want=(y26&&tgt!==null)?Math.min(Dm*(1-s),O*0.99):0;
    if(want>1e-9) bagiProp(n,['26c'],want,L,f);
    const yact=n['26c']-v['26c'], x=(1-hi)*(O-yact)-C;
    if(x>1e-9) bagiProp(n,['26b','26d'],x,L,f);
  }
  n['28c']=n['28a']+n['28b'];
  return {n:n,notes:notes};
}
function rencana(v,L,f,s){
  if(S.kat==='G') return rencanaG(v,L,f,s||0);
  const iv3=KAT[S.kat]||[0,1], notes=[];
  const n=Object.assign({},v);
  const O=v['27a']-v['26c'];
  if(O<=0) return null;

  // --- U4: aset (28a+28b) ---
  const A=v['28a']+v['28b'];
  let dA=0;
  if(A<=0){dA=O/U4[1]; notes.push('U4: aset kosong → 28a+28b minimal = Output ÷ 0,4 = '+fmtAngka(O/U4[1])+'.');}
  else{
    const r=O/A;
    if(r3(r)<U4[0]-1e-9){dA=O/U4[0]-A; notes.push('U4 < 0,2 → 28a+28b maksimal = Output ÷ 0,2 = '+fmtAngka(O/U4[0])+' (dikurangi '+fmtAngka(-dA)+').');}
    else if(r3(r)>U4[1]+1e-9){dA=O/U4[1]-A; notes.push('U4 > 0,4 → 28a+28b minimal = Output ÷ 0,4 = '+fmtAngka(O/U4[1])+' (ditambah '+fmtAngka(dA)+').');}
  }
  if(dA) bagiProp(n,['28a','28b'],dA,L,f);

  // --- U3: biaya (26b+26d); NTB = Output - (26b+26d) ---
  const C=v['26b']+v['26d'], W=v['26a'], N=O-C;
  const wKunci=!!L['26a']&&W>0;
  let nlo=iv3[0]*O, nhi=iv3[1]*O;
  if(wKunci){const a=Math.max(nlo,W/U5[1]), b=Math.min(nhi,W/U5[0]); if(a<=b){nlo=a;nhi=b;}}
  const okU3=!KAT[S.kat]||inR(N/O,iv3[0],iv3[1]);
  const okU5=!wKunci||(N>0&&inR(W/N,U5[0],U5[1]));
  let dC=0;
  if(!okU3||!okU5){ if(N<nlo) dC=N-nlo; else if(N>nhi) dC=N-nhi; }
  if(dC){
    notes.push('U3'+(wKunci?' (dan U5 karena 26a dipertahankan)':'')+': '+(dC<0?'NTB/Output di bawah batas → 26b+26d maksimal = ':'NTB/Output di atas batas → 26b+26d minimal = ')+fmtAngka(C+dC)+' ('+(dC<0?'dikurangi ':'ditambah ')+fmtAngka(Math.abs(dC))+').');
    bagiProp(n,['26b','26d'],dC,L,f);
  }

  // --- U5: upah (26a) ---
  const N2=O-(n['26b']+n['26d']);
  if(!L['26a']&&N2>0&&!inR(W/N2,U5[0],U5[1])){
    const tgt=(W/N2<U5[0]?U5[0]:U5[1])*N2, dW=tgt-W;
    notes.push('U5: upah 26a '+(dW>0?'minimal = ':'maksimal = ')+fmtAngka(tgt)+' ('+(dW>0?'ditambah ':'dikurangi ')+fmtAngka(Math.abs(dW))+').');
    bagiProp(n,['26a'],dW,L,f);
  }
  n['28c']=n['28a']+n['28b'];
  return {n:n,notes:notes};
}
function cariSaranDasar(v,s){
  const Lall=kunciMap(true), Lman=kunciMap(false);
  const kunciA=KOLOM.filter(k=>Lall[k]), kunciM=KOLOM.filter(k=>Lman[k]);
  const lepasA=kunciA.filter(k=>!Lman[k]);
  const tries=[{L:Lall,kunci:kunciA,dilepas:[]}];
  if(lepasA.length) tries.push({L:Lman,kunci:kunciM,dilepas:lepasA});   // kolom "sudah diubah" dilepas bila tidak cukup
  let best=null;
  for(const p of tries){
    for(const f of [1,0.1,0.01,0]){
      const R=rencana(v,p.L,f,s);
      if(!R) return {tipe:'tidak',v:v};
      const ev=evaluasi(hitungV(R.n)), bad=ev.filter(e=>e.cls==='bad'||e.cls==='warn').length;
      const c={v:v,n:R.n,notes:R.notes,kunci:p.kunci,dilepas:p.dilepas,w5:!!p.L['26a'],bad:bad};
      if(bad===0) return Object.assign(c,{tipe:'ok'});
      if(f===0&&(!best||bad<best.bad)) best=c;
    }
  }
  return Object.assign(best,{tipe:'sebagian'});
}
function cariSaran(v){
  const r=cariSaranDasar(v,0);
  if(S.kat==='G'&&(r.tipe==='ok'||r.tipe==='sebagian')) r.alts=[0.5,1].map(s=>({s:s,r:cariSaranDasar(v,s)}));
  return r;
}
function sarankan(){
  const v={};let any=false;
  KOLOM.forEach(k=>{const x=parse(S.adj[k]); if(k!=='28c'&&x!==null) any=true; v[k]=nz(x);});
  if(!any) return {tipe:'kosong'};
  if(cekOk(v)) return {tipe:'wajar',v};
  return cariSaran(v);
}

// ---------- total pendapatan (27c) & pengeluaran (26f) ----------
function totalSel(g){
  const t27=g('27a')+g('27b'), t26=g('26a')+g('26b')+g('26c')+g('26d')+g('26e');
  return {t27:t27,t26:t26,sel:t26-t27};   // sel>0 -> pengeluaran melebihi pendapatan
}
function komentarSel(sel){
  return 'Masukkan selisih '+fmtAngka(sel)+' ini ke dalam '+lab('27b')+' Pendapatan lainnya';
}

// ---------- render ----------
const NAMA_IND=['NTB [U2]','Output','R_NTB/Output [U3]','R_Output/Aset [U4]','R_Upah/NTB [U5]'];
function renderDeltaCols(){
  ALL.forEach(k=>{
    const e=$('x_'+k);
    if(!S.awal[k]&&!S.adj[k]){e.textContent='';e.className='';return;}
    const d=delta(nz(parse(S.awal[k])),nz(parse(S.adj[k])),true);
    e.textContent=d.t; e.className=d.c;
  });
}
function renderResult(){
  const hA=hitung(S.awal),hD=hitung(S.adj),eA=evaluasi(hA),eD=evaluasi(hD);
  const iv=KAT[S.kat];
  const syarat=['≥ 0 (tidak negatif)','-',iv?fmtR(iv[0])+' – '+fmtR(iv[1]):'-',fmtR(U4[0])+' – '+fmtR(U4[1]),fmtR(U5[0])+' – '+fmtR(U5[1])];
  const vA=hA?[hA.U2,hA.OUT,hA.U3,hA.U4,hA.U5]:[null,null,null,null,null];
  const vD=hD?[hD.U2,hD.OUT,hD.U3,hD.U4,hD.U5]:[null,null,null,null,null];
  let h='';
  for(let i=0;i<5;i++){
    const d=delta(vA[i],vD[i],i<=1);
    h+=`<tr><td><b>${NAMA_IND[i]}</b></td><td>${syarat[i]}</td>
    <td class="num">${eA[i].txt}</td><td><span class="tag ${eA[i].cls}">${eA[i].stat}</span></td>
    <td class="num">${eD[i].txt}</td><td><span class="tag ${eD[i].cls}">${eD[i].stat}</span></td>
    <td class="num ${d.c}">${d.t}</td></tr>`;
  }
  $('res').innerHTML=h;
  let r='';
  [['Data Awal',eA],['Data Adjustment',eD]].forEach(([j,ev])=>{
    if(ev.every(e=>e.stat==='Belum diisi')) return;
    const bad=ev.map((e,i)=>(e.cls==='bad'||e.cls==='warn')?NAMA_IND[i]:null).filter(Boolean);
    r+=bad.length?`<div class="box warn">${j}: perlu dicek → ${bad.join(', ')}</div>`
                 :`<div class="box ok">${j}: semua indikator wajar ✅</div>`;
  });
  $('ringkas').innerHTML=r;
}
function renderSaran(){
  $('tin').classList.toggle('hide-sg',!S.saran);
  $('saranOpt').style.display=S.saran?'block':'none';
  const info=$('saranInfo'); info.innerHTML='';
  $('altG').innerHTML='';
  lastSaran=null;
  if(!S.saran) return;
  const r=sarankan(); lastSaran=r;
  const L=kunciMap(true), LM=kunciMap(false), ubah=[];
  KOLOM.forEach(k=>{
    const e=$('s_'+k); let d=0;
    if(r.n) d=r.n[k]-r.v[k];
    if(Math.abs(d)<0.5){
      e.innerHTML=(L[k]&&r.tipe!=='wajar'&&r.tipe!=='kosong')?'<span class="zero">🔒 '+(LM[k]?'dipertahankan':'sudah Anda ubah')+'</span>':'—';
      return;
    }
    if(k!=='28c') ubah.push(k);
    e.innerHTML=`<span class="${d>0?'up':'down'}">${d>0?'Tambah ':'Kurangi '}<span class="nom" data-val="${plainNum(Math.abs(d))}" title="Klik untuk menyalin ${plainNum(Math.abs(d))}">${fmtAngka(Math.abs(d))}</span></span><small>menjadi <span class="nom" data-val="${plainNum(r.n[k])}" title="Klik untuk menyalin ${plainNum(r.n[k])}">${fmtAngka(r.n[k])}</span>${LM[k]?' · dipertahankan':(L[k]?' · sudah Anda ubah':'')}</small>`;
  });
  let msg='',cls='na';
  if(r.tipe==='kosong'){msg='Isi data dulu untuk melihat saran.';}
  else if(r.tipe==='wajar'){msg='✅ Data Adjustment sudah wajar, tidak ada saran perbaikan.';cls='ok';}
  else if(r.tipe==='tidak'){msg='⚠️ Saran belum bisa dihitung. Pastikan 27a lebih besar dari 26c (Output harus positif).';cls='warn';}
  else{
    const sblm=(r.kunci&&r.kunci.length)?' 🔒 Kolom yang dipertahankan/sudah Anda ubah ('+r.kunci.join(', ')+') tidak diubah; penyesuaian dibagi proporsional ke kolom lain.':'';
    const dl=(r.dilepas||[]).filter(k=>ubah.indexOf(k)>=0);
    const lps=dl.length?' ⚠️ Kolom yang sudah Anda ubah ('+dl.join(', ')+') saja tidak cukup, jadi ikut disesuaikan.':'';
    const nt=(r.notes&&r.notes.length)?' '+r.notes.join(' '):'';
    if(r.tipe==='ok'){msg='✅ Jika saran diterapkan, U2–U5 semua wajar. Kolom yang berubah: '+ubah.join(', ')+'.'+nt+sblm+lps;cls='ok';}
    else{
      const ev=evaluasi(hitungV(r.n));
      const sisa=ev.map((e,i)=>(e.cls==='bad'||e.cls==='warn')?NAMA_IND[i]:null).filter(Boolean);
      const w5=sisa.some(x=>x.indexOf('U5')>=0)&&!!r.w5;
      msg='⚠️ Saran terbaik masih menyisakan: '+sisa.join(', ')+(w5?'':' (mis. karena kategori yang dipilih)')+'. Kolom yang berubah: '+ubah.join(', ')+'.'+nt+sblm+lps
        +(w5?' U5 = 26a ÷ NTB harus 0,4–0,6, jadi tidak bisa wajar selama 26a dipertahankan di '+fmtAngka(r.v['26a'])+'. Hilangkan centang "pertahankan" pada 26a jika ingin U5 ikut wajar.':'');cls='warn';
    }
  }
  info.innerHTML=`<div class="box ${cls}"></div>`; info.firstChild.textContent=msg;
  if(S.kat==='G'&&r.alts&&r.n){   // alternatif: sebagian perbaikan diambil dari 26b/26d
    let h='<div class="box na"><b>💡 Alternatif bila nilai 26c hasil hitung terasa kurang masuk akal</b><div class="small">Sebagian perbaikan dipindahkan ke 26b dan 26d (dibagi proporsional, dibulatkan sesuai skala nilainya). Klik nominal untuk menyalin.</div>';
    r.alts.forEach(a=>{
      const q=a.r; if(!q||!q.n) return;
      const bad=evaluasi(hitungV(q.n)).some(e=>e.cls==='bad'||e.cls==='warn');
      const items=['26c','26b','26d'].map(k=>{
        const d=q.n[k]-q.v[k]; if(Math.abs(d)<0.5) return null;
        return `${k}: <span class="${d>0?'up':'down'}">${d>0?'tambah ':'kurangi '}<span class="nom" data-val="${plainNum(Math.abs(d))}">${fmtAngka(Math.abs(d))}</span></span> (menjadi <span class="nom" data-val="${plainNum(q.n[k])}">${fmtAngka(q.n[k])}</span>)`;
      }).filter(Boolean);
      if(!items.length) return;
      h+=`<div style="margin-top:6px"><b>${a.s>=1?'Seluruhnya lewat 26b &amp; 26d (26c tetap)':'Dibagi: sebagian 26c, sebagian 26b &amp; 26d'}</b>${bad?' ⚠️ belum wajar':' ✅ U3 wajar'}<br>${items.join('<br>')}</div>`;
    });
    $('altG').innerHTML=h+'</div>';
  }
}
function lab(k){return S.th26?(MAP26[k]||k):k;}   // nomor kolom pada catatan (usaha mulai 2026 -> 30a..32c)
function kodeFor(k){return S.beda?(S.kodeKol[k]||S.kode):S.kode;}
function kalimat(kode,kol,awal,ubah,tampil){
  if(!awal&&!ubah) return null;
  const u=ubah||'kosong';
  if(tampil&&awal) return `${kode} memperbaiki isian ${kol} dari ${awal} menjadi ${u}`;
  return `${kode} memperbaiki isian ${kol} menjadi ${u}`;
}
function salinKe(t,sukses){  // salin teks ke clipboard (dengan cadangan untuk iframe)
  const fb=()=>{const ta=document.createElement('textarea');ta.value=t;ta.style.position='fixed';ta.style.opacity='0';
    document.body.appendChild(ta);ta.select();
    let ok=false;try{ok=document.execCommand('copy');}catch(e){}
    document.body.removeChild(ta); if(ok) sukses();};
  try{navigator.clipboard.writeText(t).then(sukses,fb);}catch(e){fb();}
}
function copyText(t,btn){
  salinKe(t,()=>{const o=btn.textContent;btn.textContent='Tersalin ✓';setTimeout(()=>btn.textContent=o,1200);});
}
function salinNominal(el){  // nilai disalin dari data-val (angka polos), tampilan tidak diubah
  salinKe(el.getAttribute('data-val'),()=>{el.classList.add('copied');setTimeout(()=>el.classList.remove('copied'),1300);});
}
function renderGen(){
  const ch=ALL.filter(k=>S.awal[k]!==S.adj[k]&&(k!=='28c'||S.pakai28c));
  $('hint26').style.display=S.th26?'block':'none';
  const box=$('gen'); box.innerHTML='';
  // kalimat tambahan: selisih 26f - 27c dimasukkan ke 27b (hanya bila pengeluaran melebihi pendapatan)
  const selD=totalSel(k=>nz(parse(S.adj[k]))).sel;
  const selLine=tampil=>{
    if(selD<=0.5) return null;
    const baru=fmtAngka(nz(parse(S.adj['27b']))+selD), awal=fmtAngka(nz(parse(S.awal['27b'])));
    const kd=kodeFor('27b');
    return tampil?`${kd} memperbaiki rincian ${lab('27b')} dari ${awal} menjadi ${baru}`:`${kd} memperbaiki rincian ${lab('27b')} menjadi ${baru}`;
  };
  if(!ch.length&&!selLine(true)){box.innerHTML='<div class="box na">Belum ada kolom yang berbeda antara Data Awal dan Adjustment.</div>';}
  else{
    const wrap=document.createElement('div');wrap.className='grid2';
    [['CATATAN PRIBADI [dengan nilai awal]',true],['FASIH/KONFIRMASI [tanpa nilai awal]',false]].forEach(([jd,tampil])=>{
      const t=ch.map(k=>kalimat(kodeFor(k),lab(k),S.awal[k],S.adj[k],tampil)).filter(Boolean).concat(selLine(tampil)||[]).join('\n');
      const d=document.createElement('div');
      const head=document.createElement('div');head.className='row';
      head.innerHTML='<b></b>';head.firstChild.textContent=jd;
      const b=document.createElement('button');b.textContent='Salin';b.onclick=()=>copyText(t,b);head.appendChild(b);
      const pre=document.createElement('pre');pre.textContent=t;
      d.appendChild(head);d.appendChild(pre);wrap.appendChild(d);
    });
    box.appendChild(wrap);
  }
  renderSel();
}
function renderSel(){   // warning 27c (total pendapatan) vs 26f (total pengeluaran)
  const box=$('sel'); box.innerHTML='';
  if(!ALL.some(k=>S.awal[k]||S.adj[k])) return;
  const gA=k=>nz(parse(S.awal[k])), gD=k=>nz(parse(S.adj[k]));
  const A=totalSel(gA), D=totalSel(gD);
  const c=s=>s>0.5?'bad':'ok';
  box.innerHTML=`<h3>Cek total pendapatan (${lab('27c')}) &amp; total pengeluaran (${lab('26f')})</h3>
  <div class="scroll"><table><thead><tr><th>Total</th><th class="num">Data Awal</th><th class="num">Data Adjustment</th></tr></thead><tbody>
  <tr><td><b>${lab('27c')}</b> Total pendapatan (27a + 27b)</td><td class="num">${fmtAngka(A.t27)}</td><td class="num">${fmtAngka(D.t27)}</td></tr>
  <tr><td><b>${lab('26f')}</b> Total pengeluaran (26a + 26b + 26c + 26d + 26e)</td><td class="num">${fmtAngka(A.t26)}</td><td class="num">${fmtAngka(D.t26)}</td></tr>
  <tr><td>Selisih (${lab('26f')} − ${lab('27c')})</td><td class="num"><span class="tag ${c(A.sel)}">${fmtAngka(A.sel)}</span></td><td class="num"><span class="tag ${c(D.sel)}">${fmtAngka(D.sel)}</span></td></tr>
  </tbody></table></div>`;
  const tambah=(jd,sel)=>{
    const d=document.createElement('div');
    if(sel>0.5){
      const t=komentarSel(sel);
      d.className='box warn';
      const s=document.createElement('div');s.textContent='⚠️ '+jd+': total pengeluaran melebihi total pendapatan.';
      const pre=document.createElement('pre');pre.textContent=t;
      const b=document.createElement('button');b.textContent='Salin komentar';b.onclick=()=>copyText(t,b);
      d.appendChild(s);d.appendChild(pre);d.appendChild(b);
    }else{
      d.className='box ok';d.textContent='✅ '+jd+': total pendapatan sudah mencukupi total pengeluaran.';
    }
    box.appendChild(d);
  };
  tambah('Data Adjustment',D.sel);
  if(S.saran&&lastSaran&&lastSaran.n){
    const SG=totalSel(k=>lastSaran.n[k]!==undefined?lastSaran.n[k]:gD(k));
    tambah('Jika saran perbaikan diterapkan',SG.sel);
  }
}
function buildKodeKol(){
  const c=$('kk');c.innerHTML='';
  if(!S.beda) return;
  ALL.filter(k=>(k!=='28c'||S.pakai28c)&&!(k==='26c'&&hide26c())).forEach(k=>{
    const d=document.createElement('div');d.textContent=lab(k);
    const s=document.createElement('select');
    KODE.forEach(x=>{const o=document.createElement('option');o.value=x;o.textContent=x;s.appendChild(o);});
    s.value=kodeFor(k);
    s.onchange=()=>{S.kodeKol[k]=s.value;update();};
    d.appendChild(s);c.appendChild(d);
  });
}
function save(){try{sessionStorage.setItem(KEY,JSON.stringify(S));}catch(e){}}
function apply26c(){   // kategori tanpa 26c: kolom disembunyikan dan nilainya 0 (dikosongkan)
  const h=hide26c();
  $('r_26c').style.display=h?'none':'';
  if(h){
    S.awal['26c']='';S.adj['26c']='';S.dirty['26c']=false;S.tahan['26c']=false;
    $('a_26c').value='';$('d_26c').value='';$('h_26c').checked=false;
  }
}
function update(){apply26c();renderDeltaCols();renderResult();renderSaran();renderGen();save();}

// ---------- init ----------
Object.keys(KAT).forEach(k=>{
  const o=document.createElement('option');o.value=k;
  const iv=KAT[k];
  o.textContent='Kategori '+k+' ('+(iv?fmtR(iv[0]).slice(0,4)+' – '+fmtR(iv[1]).slice(0,4):'-')+')';
  $('kat').appendChild(o);
});
$('kat').value=S.kat; $('kat').onchange=()=>{S.kat=$('kat').value;buildKodeKol();update();};
KODE.forEach(x=>{const o=document.createElement('option');o.value=x;o.textContent=x+' — '+KODE_DC[x];$('kode').appendChild(o);});
$('kode').value=S.kode; $('kode').onchange=()=>{S.kode=$('kode').value;update();};
$('saran').checked=S.saran; $('saran').onchange=()=>{S.saran=$('saran').checked;update();};
$('kunci').checked=S.kunci; $('kunci').onchange=()=>{S.kunci=$('kunci').checked;update();};
$('th26').checked=S.th26; $('th26').onchange=()=>{S.th26=$('th26').checked;buildKodeKol();update();};
$('p28c').checked=S.pakai28c; $('p28c').onchange=()=>{S.pakai28c=$('p28c').checked;buildKodeKol();update();};
$('beda').checked=S.beda; $('beda').onchange=()=>{S.beda=$('beda').checked;buildKodeKol();update();};

let rows='';
KOLOM.forEach(k=>{
  rows+=`<tr id="r_${k}"><td><b>${k}</b> ${NAMA[k]}</td>
  <td><input type="text" id="a_${k}" inputmode="decimal" autocomplete="off" placeholder="0"${k==='28c'?' readonly tabindex="-1" class="ro"':''}></td>
  <td><input type="text" id="d_${k}" inputmode="decimal" autocomplete="off" placeholder="0"${k==='28c'?' readonly tabindex="-1" class="ro"':''}></td>
  <td class="num"><span id="x_${k}"></span></td>
  <td class="sg-col"><div class="sg" id="s_${k}">—</div>${k==='28c'?'':`<label class="tahan"><input type="checkbox" id="h_${k}"> pertahankan nilai ini</label>`}</td></tr>`;
});
EXTRA.forEach(k=>{   // kolom tambahan: tidak masuk perhitungan utama
  rows+=`<tr class="extra"><td><b>${k}</b> ${NAMA[k]} <span class="small">(tidak masuk perhitungan utama)</span></td>
  <td><input type="text" id="a_${k}" inputmode="decimal" autocomplete="off" placeholder="0"></td>
  <td><input type="text" id="d_${k}" inputmode="decimal" autocomplete="off" placeholder="0"></td>
  <td class="num"><span id="x_${k}"></span></td>
  <td class="sg-col"></td></tr>`;
});
$('inbody').innerHTML=rows;
['tin','altG'].forEach(id=>$(id).addEventListener('click',e=>{   // klik nominal saran -> salin
  const el=e.target&&e.target.closest?e.target.closest('.nom'):null;
  if(el) salinNominal(el);
}));
KOLOM.forEach(k=>{
  const h=$('h_'+k); if(!h) return;
  h.checked=!!S.tahan[k];
  h.onchange=()=>{S.tahan[k]=h.checked;update();};
});
ALL.forEach(k=>{
  const a=$('a_'+k),d=$('d_'+k);
  a.value=S.awal[k]; d.value=S.adj[k];
  if(k==='28c') return;  // total otomatis, tidak diketik
  a.addEventListener('input',()=>{
    S.awal[k]=liveFormat(a);
    if(!S.dirty[k]){S.adj[k]=S.awal[k];d.value=S.adj[k];}
    syncTotal(); update();
  });
  d.addEventListener('input',()=>{
    S.adj[k]=liveFormat(d);
    S.dirty[k]=(S.adj[k]!==S.awal[k]);
    syncTotal(); update();
  });
});
$('salin').onclick=()=>{ALL.forEach(k=>{S.adj[k]=S.awal[k];S.dirty[k]=false;$('d_'+k).value=S.adj[k];});syncTotal();update();};
$('reset').onclick=()=>{ALL.forEach(k=>{S.awal[k]='';S.adj[k]='';S.dirty[k]=false;S.tahan[k]=false;$('a_'+k).value='';$('d_'+k).value='';if($('h_'+k))$('h_'+k).checked=false;});syncTotal();update();};
buildKodeKol(); syncTotal(); update();
window.__S=()=>S;
})();
</script></body></html>
"""


TINGGI_ANOMALI = 2300  # piksel; jika isi lebih panjang, bisa digulir di dalam kotak


def render_anomali():
    isi = (
        HTML_ANOMALI.replace("__KOLOM__", json.dumps(KOLOM))
        .replace("__EXTRA__", json.dumps(KOLOM_EXTRA))
        .replace("__NAMA__", json.dumps(NAMA_KOLOM, ensure_ascii=False))
        .replace("__KAT__", json.dumps(KATEGORI))
        .replace("__KODE__", json.dumps(KODE_UBAH))
        .replace("__KODE_DC__", json.dumps(KODE_DC, ensure_ascii=False))
        .replace("__MAP26__", json.dumps(KOLOM_2026))
    )
    if hasattr(st, "iframe"):  # Streamlit versi baru
        st.iframe(isi, height=TINGGI_ANOMALI)
    else:  # Streamlit versi lama
        components.html(isi, height=TINGGI_ANOMALI, scrolling=True)


# ----------------------------------------------------------------------------
# UI
# ----------------------------------------------------------------------------
tampilkan_pencarian()
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
    th2026 = st.checkbox(
        "Usaha dimulai tahun 2026 (nomor kolom di catatan: 26a→30a, 26b→30b, 26c→30c, 26d→30d, "
        "27a→31a, 28a→32a, 28b→32b, 28c→32c)",
        key="gen_th2026",
    )

    st.markdown("---")
    h1, h2, h3 = st.columns(3)
    h1.markdown("**Kolom**")
    h2.markdown("**Nilai awal (X / A)**")
    h3.markdown("**Nilai perubahan (Y)**")

    baris = []
    for kol in KOLOM:
        c1, c2, c3 = st.columns(3)
        c1.markdown(f"**{kol}**" + (f" → {KOLOM_2026[kol]}" if th2026 else ""))
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
        baris.append((kode, KOLOM_2026[kol] if th2026 else kol, awal, ubah))

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
    with st.expander("Rumus & aturan kewajaran"):
        st.markdown(
            "- **NTB [U2]** = 27a − (26b + 26c + 26d) → wajar jika **tidak negatif**\n"
            "- **Output** = 27a − 26c (ditampilkan tanpa syarat kewajaran)\n"
            "- **R_NTB/Output [U3]** = NTB / Output → wajar jika berada pada interval kategori terpilih\n"
            "- **R_Output/Aset [U4]** = Output / 28c, dengan **28c = 28a + 28b** → wajar jika **0,200 – 0,400**\n"
            "- **R_Upah/NTB [U5]** = 26a / NTB → wajar jika **0,400 – 0,600**\n\n"
            "Rasio dibulatkan ke 3 digit di belakang koma sebelum dicek terhadap interval. "
            "Titik = pemisah ribuan, koma = desimal.\n\n"
            "**Logika saran perbaikan** (urutan U4 → U3 → U5):\n"
            "- **U4**: jika < 0,2 maka 28a+28b dikurangi sampai maksimal Output ÷ 0,2; "
            "jika > 0,4 maka 28a+28b ditambah sampai minimal Output ÷ 0,4. "
            "Perubahan dibagi proporsional ke 28a dan 28b.\n"
            "- **U3**: jika di bawah batas bawah kategori, 26b+26d dikurangi sampai maksimal "
            "Output × (1 − batas bawah); jika di atas batas atas, 26b+26d ditambah sampai minimal "
            "Output × (1 − batas atas). Perubahan dibagi proporsional ke 26b dan 26d, dengan 26b diberi bobot 2× sehingga porsinya lebih besar.\n"
            "- **U5**: upah 26a disesuaikan ke batas 0,4 / 0,6 dari NTB.\n"
            "- **Kategori G**: 26c ditampilkan; hanya U3 yang dicek. Jika U3 < batas bawah, 26c maksimal = 27a − (26b+26d) ÷ (1 − batas bawah); "
            "jika U3 > batas atas, 26c minimal = 27a − (26b+26d) ÷ (1 − batas atas). Tersedia alternatif mengambil sebagian perbaikan lewat 26b/26d.\n"
            "- Pembulatan mengikuti skala nilai kolom (mis. 50 juta → kelipatan jutaan; 5 juta → kelipatan ratusan ribu).\n"
            "- Kolom yang sudah Anda ubah di Adjustment, atau yang dicentang *pertahankan nilai ini*, tidak diubah. "
            "Klik nominal pada kolom saran untuk menyalinnya (angka polos, tanpa titik).\n\n"
            "**Cek 27c & 26f**: 27c = 27a + 27b (total pendapatan), 26f = 26a + 26b + 26c + 26d + 26e (total pengeluaran). "
            "Kolom 27b dan 26e tidak masuk perhitungan U2–U5. Jika hasil adjustment membuat total pengeluaran melebihi total pendapatan, "
            "muncul komentar untuk memasukkan selisihnya ke 27b Pendapatan lainnya."
        )
    render_anomali()
