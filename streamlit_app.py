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
NAMA_KOLOM = {
    "26a": "Upah",
    "26b": "Biaya produksi",
    "26c": "Biaya pembelian barang yang terjual",
    "26d": "Biaya operasional (air, listrik, gas, internet, pulsa, pemeliharaan, biaya angkutan, dll.)",
    "27a": "Nilai pendapatan barang dan jasa",
    "28a": "Nilai aset tanah dan bangunan",
    "28b": "Nilai aset selain tanah dan bangunan",
    "28c": "Nilai total aset (28a + 28b)",
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
    if kode == "0":
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
.opt-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(250px,1fr));gap:8px 14px;margin:8px 0}
.opt-grid label{display:flex;flex-direction:column;gap:3px;font-size:12px;color:var(--muted)}
.opt-grid select{min-width:0;width:100%;font-size:14px;color:var(--fg)}
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
<summary><b>⚙️ Strategi saran</b> <span class="small">(atur kolom mana yang disesuaikan)</span></summary>
<div class="small">🤖 <b>Otomatis</b> = aplikasi memilih sendiri kolom yang diubah dengan perubahan terkecil, dan nilai yang disarankan dibuat sedekat mungkin dengan batas interval wajar.</div>
<div class="opt-grid">
<label>U4 · Output / Aset<select id="strategi">
<option value="auto">🤖 Otomatis (paling dekat batas wajar)</option>
<option value="bagi">Bagi proporsional: output &amp; aset</option>
<option value="aset">Ubah aset saja (output tetap)</option>
<option value="pendapatan">Ubah output saja (aset tetap)</option></select></label>
<label>Output dibawa oleh<select id="sR">
<option value="auto">🤖 Otomatis (paling dekat batas wajar)</option>
<option value="27a">Pendapatan 27a</option>
<option value="26c">Biaya pembelian barang 26c</option>
<option value="bagi">Bagi proporsional 27a &amp; 26c</option></select></label>
<label>U2 / U3 · NTB / Output (biaya)<select id="sC">
<option value="auto">🤖 Otomatis (paling dekat batas wajar)</option>
<option value="bagi">Bagi proporsional 26b &amp; 26d</option>
<option value="26b">Ubah 26b saja</option>
<option value="26d">Ubah 26d saja</option>
<option value="tanpa">Biaya tetap (ubah output)</option></select></label>
<label>Pembagian aset<select id="sA">
<option value="auto">🤖 Otomatis (paling dekat batas wajar)</option>
<option value="bagi">Bagi proporsional 28a &amp; 28b</option>
<option value="28a">Ubah 28a saja</option>
<option value="28b">Ubah 28b saja</option></select></label>
<label>U5 · Upah / NTB<select id="sW">
<option value="auto">🤖 Otomatis (paling dekat batas wajar)</option>
<option value="upah">Ubah upah 26a</option>
<option value="ntb">Upah tetap, ubah NTB (biaya/output)</option>
<option value="bagi">Bagi proporsional upah &amp; NTB</option></select></label>
</div>
<label><input type="checkbox" id="kunci"> Otomatis pertahankan kolom yang sudah saya ubah</label>
</details>
<div class="scroll"><table id="tin" class="hide-sg">
<thead><tr><th>Kolom</th><th>Perhitungan Data Awal</th><th>Perhitungan Data Adjustment</th><th class="num">Perubahan (Adj &minus; Awal)</th><th class="num sg-col">Saran perbaikan perhitungan</th></tr></thead>
<tbody id="inbody"></tbody></table></div>
<div id="saranInfo"></div>
<div class="row"><button id="salin">↩️ Samakan adjustment dengan data awal</button><button id="reset">🗑️ Reset semua isian</button></div>
<div class="small">Adjustment otomatis mengikuti Data Awal selama belum Anda ubah sendiri. Semua hasil dihitung langsung saat mengetik.</div>

<h3>Hasil perhitungan &amp; cek kewajaran (live)</h3>
<div class="scroll"><table><thead><tr><th>Indikator</th><th>Syarat wajar</th><th class="num">Data Awal</th><th>Status Awal</th><th class="num">Data Adjustment</th><th>Status Adjustment</th><th class="num">Perubahan</th></tr></thead>
<tbody id="res"></tbody></table></div>
<div id="ringkas"></div>

<h3>Generate kalimat perbaikan</h3>
<div class="small">Kalimat dibuat hanya untuk kolom yang nilainya berbeda antara Data Awal dan Adjustment.</div>
<div class="row"><b>Kode DC</b><select id="kode"></select>
<label><input type="checkbox" id="beda"> Pakai kode DC berbeda untuk tiap kolom</label>
<label><input type="checkbox" id="p28c"> Sertakan 28c (total aset) dalam kalimat</label></div>
<div id="kk" class="kk"></div>
<div id="gen"></div>
</div>
<script>
(function(){
const KOLOM=__KOLOM__, NAMA=__NAMA__, KAT=__KAT__, KODE=__KODE__, KODE_DC=__KODE_DC__;
const U4=[0.2,0.4], U5=[0.4,0.6], KEY='dc_anomali_state_v3';
const $=id=>document.getElementById(id);
let S={kat:'A',awal:{},adj:{},dirty:{},kode:KODE[0],beda:false,kodeKol:{},pakai28c:false,saran:false,kunci:true,strategi:'auto',sR:'auto',sC:'auto',sA:'auto',sW:'auto',tahan:{}};
try{const r=sessionStorage.getItem(KEY); if(r) S=Object.assign(S,JSON.parse(r));}catch(e){}
KOLOM.forEach(k=>{S.awal[k]=S.awal[k]||'';S.adj[k]=S.adj[k]||'';});
S.tahan=S.tahan||{};

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
let FR=0.02;  // margin di dalam interval (pecahan lebar interval); kecil = makin dekat batas wajar
function inOk(r,lo,hi){const x=Math.round(r*1000)/1000;return x>=lo-1e-9&&x<=hi+1e-9;}
function ratioFix(r,lo,hi){  // rasio terdekat yang wajar (margin kecil di dalam interval)
  const m=(hi-lo)*FR,l=lo+m,h=hi-m;
  if(r===null||!isFinite(r)) return h;
  if(inOk(r,lo,hi)) return r;
  return r<lo?l:h;
}
function cekOk(v){
  const h=hitungV(v);
  return evaluasi(h).every(e=>e.cls!=='bad'&&e.cls!=='warn');
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
const OPSI={strategi:['bagi','aset','pendapatan'],sR:['27a','26c','bagi'],sC:['bagi','26b','26d','tanpa'],sA:['bagi','28a','28b'],sW:['upah','ntb','bagi']};
const IDS=Object.keys(OPSI);
function cfgDaftar(){  // semua kombinasi untuk pilihan "Otomatis" (pilihan manual tetap satu nilai)
  let list=[{}];
  IDS.forEach(id=>{
    const pil=S[id]==='auto'?OPSI[id]:[S[id]];
    const nxt=[]; list.forEach(c=>pil.forEach(p=>{const o=Object.assign({},c);o[id]=p;nxt.push(o);})); list=nxt;
  });
  return list;
}
function strategiLock(cfg){  // pilihan strategi -> kolom yang "tidak disentuh" ('auto' = tidak ada kunci)
  cfg=cfg||{};
  const Lv={};
  if(cfg.sR==='27a') Lv['26c']=true; else if(cfg.sR==='26c') Lv['27a']=true;
  if(cfg.sC==='26b') Lv['26d']=true; else if(cfg.sC==='26d') Lv['26b']=true;
  else if(cfg.sC==='tanpa'){Lv['26b']=true;Lv['26d']=true;}
  if(cfg.sA==='28a') Lv['28b']=true; else if(cfg.sA==='28b') Lv['28a']=true;
  if(cfg.sW==='ntb') Lv['26a']=true;
  return Lv;
}
function alloc(n,ks,delta,L,fb){  // bagi perubahan proporsional ke kolom yang belum terkunci
  if(Math.abs(delta)<1e-9) return;
  const fr=ks.filter(k=>!L[k]); if(!fr.length) return;
  const s=fr.reduce((a,k)=>a+n[k],0);
  if(s>0) fr.forEach(k=>{n[k]=n[k]+delta*(n[k]/s);});
  else n[fr.indexOf(fb)>=0?fb:fr[0]]+=delta;
  fr.forEach(k=>{if(n[k]<0) n[k]=0;});
}
function clampR(x,lo,hi){  // jepit ke [lo,hi] dengan margin kecil di dalam
  if(x>=lo&&x<=hi) return x;
  const d=isFinite(hi)?(hi-lo)*FR:lo*FR;
  return x<lo?Math.min(lo+d,hi):Math.max(hi-d,lo);
}
function rencana(mode,v,L,relax,cfg){
  const iv3=KAT[S.kat]||[0.001,1];
  const A0=v['28a']+v['28b'], R0=v['27a']-v['26c'], C0=v['26b']+v['26d'], W=v['26a'];
  const bebasA=!(L['28a']&&L['28b']), bebasR=!(L['27a']&&L['26c']), bebasC=!(L['26b']&&L['26d']), bebasW=!L['26a'];
  const kenaW=!bebasW&&!relax;   // relax: 26a dipertahankan & U5 diabaikan (tidak mungkin wajar)

  // 1) batas nilai Output (R*) yang masih mungkin, mengingat kolom yang terkunci
  let lo=1e-9, hi=Infinity;
  const potong=(a,b)=>{if(a>lo) lo=a; if(b<hi) hi=b;};
  if(!bebasA&&A0>0) potong(U4[0]*A0,U4[1]*A0);                 // aset terkunci -> U4
  if(!bebasC){                                                  // biaya terkunci -> U3 (& U5)
    if(iv3[0]<1) potong(C0/(1-iv3[0]),Infinity);
    if(iv3[1]<1) potong(0,C0/(1-iv3[1]));
    if(kenaW) potong(C0+W/U5[1],C0+W/U5[0]);
  }else if(kenaW){                                              // upah terkunci -> U3 & U5
    if(iv3[0]>0) potong(0,W/(U5[0]*iv3[0]));
    if(iv3[1]>0) potong(W/(U5[1]*iv3[1]),Infinity);
  }

  // 2) Output target (R*) menurut strategi U4
  const r0=(A0>0&&R0>0)?R0/A0:null;
  const r4=ratioFix(r0,U4[0],U4[1]);
  let Rp;
  if(!bebasR) Rp=R0;
  else if(bebasA){
    if(mode==='aset') Rp=R0;
    else if(mode==='pendapatan') Rp=A0>0?r4*A0:R0;
    else Rp=r0!==null?R0*Math.sqrt(r4/r0):(A0>0?r4*A0:R0);      // bagi: pendapatan & aset sama-sama bergeser
  }else Rp=A0>0?r4*A0:R0;
  if(bebasR&&Rp<=0){ if(A0>0) Rp=r4*A0; else return null; }
  let Rs;
  if(!bebasR){ if(R0<lo-1e-6||R0>hi+1e-6) return null; Rs=R0; }
  else{ if(lo>hi) return null; Rs=clampR(Rp,lo,hi); }

  // 3) aset target (A*)
  let As=A0;
  if(bebasA) As=Rs/ratioFix(A0>0?Rs/A0:null,U4[0],U4[1]);

  // 4) biaya target (C*) -> NTB target (N*) agar U2 & U3 wajar
  let Cs=C0;
  if(bebasC){
    let nT=ratioFix((Rs-C0)/Rs,iv3[0],iv3[1])*Rs;
    if(kenaW){
      const nlo=Math.max(iv3[0]*Rs,W/U5[1]), nhi=Math.min(iv3[1]*Rs,W/U5[0]);
      nT=clampR(nT,nlo,Math.max(nhi,nlo));
    }else if(cfg.sW==='bagi'&&bebasW&&W>0&&nT>1e-9){        // upah naik, NTB turun (atau sebaliknya) sama-sama bergeser
      const r5=W/nT, r5t=ratioFix(r5,U5[0],U5[1]);
      if(r5t!==r5) nT=clampR(nT/Math.sqrt(r5t/r5),iv3[0]*Rs,iv3[1]*Rs);
    }
    Cs=Rs-nT;
  }
  const Ns=Rs-Cs;

  // 5) upah target (W*) -> U5
  let Ws=W;
  if(bebasW&&Ns>1e-9) Ws=ratioFix(W/Ns,U5[0],U5[1])*Ns;

  // 6) terapkan ke kolom (hanya kolom yang belum terkunci)
  const n=Object.assign({},v);
  const dR=Rs-R0;                                  // perubahan Output dibawa oleh 27a (+) dan/atau 26c (-)
  if(Math.abs(dR)>1e-9){
    const f27=!L['27a'], f26c=!L['26c'];
    if(f27&&f26c){
      const tot=v['27a']+v['26c'];
      if(tot>0){let dP=-dR*v['26c']/tot; if(v['26c']+dP<0) dP=-v['26c']; n['26c']=v['26c']+dP; n['27a']=v['27a']+dR+dP;}
      else n['27a']=v['27a']+dR;
    }else if(f27) n['27a']=v['27a']+dR;
    else if(f26c) n['26c']=Math.max(0,v['26c']-dR);
  }
  alloc(n,['26b','26d'],Cs-C0,L,'26b');
  alloc(n,['28a','28b'],As-A0,L,'28b');
  if(bebasW) n['26a']=Ws;
  KOLOM.forEach(k=>{ if(Math.abs(n[k]-v[k])<=1e-6*Math.max(1,Math.abs(v[k]))) n[k]=v[k]; });
  n['28c']=n['28a']+n['28b'];
  return n;
}
function bulatkan(n,v,step){
  const r=Object.assign({},n);
  KOLOM.forEach(k=>{ if(k!=='28c'&&r[k]!==v[k]) r[k]=Math.round(r[k]/step)*step; });
  r['28c']=r['28a']+r['28b']; return r;
}
function cariSaran(v,fr){
  FR=fr;
  const Lall=kunciMap(true), Lman=kunciMap(false);
  const kunciA=KOLOM.filter(k=>Lall[k]), kunciM=KOLOM.filter(k=>Lman[k]);
  const lepasA=kunciA.filter(k=>!Lman[k]);
  const U=(a,b)=>Object.assign({},a,b);
  const cfgs=cfgDaftar();
  const adaKunciStrategi=cfgs.some(c=>Object.keys(strategiLock(c)).length>0);
  const buruk=f=>evaluasi(hitungV(f)).filter(e=>e.cls==='bad'||e.cls==='warn').length;
  const coba=(Lk,relax,ls)=>{   // ls=true: strategi pilihan dilepas (tidak ada kunci dari strategi)
    const hasil=[];
    cfgs.forEach(cfg=>{
      const Lv=ls?{}:strategiLock(cfg), Lg=U(Lk,Lv);
      const n=rencana(cfg.strategi,v,Lg,relax,cfg); if(!n) return;
      let f=n,ok=false;
      for(const stp of [1000,100,10,1]){ f=bulatkan(n,v,stp); ok=cekOk(f); if(ok) break; }
      let tot=0; KOLOM.forEach(k=>{if(k!=='28c') tot+=Math.abs(f[k]-v[k]);});
      hasil.push({cfg:cfg,n:f,ok:ok,tot:tot,w5:!!(relax&&Lg['26a'])});
    });
    hasil.sort((a,b)=>((b.ok?1:0)-(a.ok?1:0))||(a.tot-b.tot));   // urutan sama -> pilihan lebih awal menang
    return hasil;
  };
  // urutan percobaan: (1) semua kunci & strategi dihormati, (2) lepas kunci "sudah diubah",
  // (3) lepas strategi, (4) lepas keduanya. Kunci centang "pertahankan" tidak pernah dilepas.
  const percobaan=[{Lk:Lall,kunci:kunciA,dilepas:[],ls:false}];
  if(lepasA.length) percobaan.push({Lk:Lman,kunci:kunciM,dilepas:lepasA,ls:false});
  if(adaKunciStrategi){
    percobaan.push({Lk:Lall,kunci:kunciA,dilepas:[],ls:true});
    if(lepasA.length) percobaan.push({Lk:Lman,kunci:kunciM,dilepas:lepasA,ls:true});
  }
  const kand=[];
  const jalan=(p,relax)=>{
    const h=coba(p.Lk,relax,p.ls); if(!h.length) return false;
    const c=Object.assign({},h[0],{kunci:p.kunci,dilepas:p.dilepas,ls:p.ls,bad:h[0].ok?0:buruk(h[0].n)});
    kand.push(c); return c.ok;
  };
  let ketemu=false;
  for(const p of percobaan){ if(jalan(p,false)){ketemu=true;break;} }
  if(!ketemu) for(const p of percobaan){ if(jalan(p,true)) break; }   // upah ditahan -> abaikan U5
  if(!kand.length) return {tipe:'tidak',v:v};
  let best=kand.find(c=>c.ok);
  if(!best) best=kand.reduce((a,b)=>b.bad<a.bad?b:a);
  return {tipe:best.ok?'ok':'sebagian',v:v,n:best.n,mode:best.cfg.strategi,cfg:best.cfg,fr:fr,kunci:best.kunci,dilepas:best.dilepas,ls:best.ls,w5:best.w5};
}
function sarankan(){
  const v={};let any=false;
  KOLOM.forEach(k=>{const x=parse(S.adj[k]); if(k!=='28c'&&x!==null) any=true; v[k]=nz(x);});
  if(!any) return {tipe:'kosong'};
  if(cekOk(v)) return {tipe:'wajar',v};
  const adaAuto=IDS.some(id=>S[id]==='auto');
  let r=cariSaran(v,adaAuto?0.003:0.02);            // otomatis: sedekat mungkin dengan batas interval
  if(adaAuto&&r.tipe!=='ok'){                         // jaga-jaga: coba lagi dengan margin lebih aman
    const r2=cariSaran(v,0.02);
    if(r2.tipe==='ok'||r.tipe==='tidak') r=r2;
  }
  return r;
}

// ---------- render ----------
const NAMA_IND=['NTB [U2]','Output','R_NTB/Output [U3]','R_Output/Aset [U4]','R_Upah/NTB [U5]'];
function renderDeltaCols(){
  KOLOM.forEach(k=>{
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
const LBL={
  strategi:{bagi:'output & aset digeser bersama',aset:'aset diubah, output tetap',pendapatan:'output diubah, aset tetap'},
  sR:{'27a':'output lewat 27a','26c':'output lewat 26c',bagi:'output lewat 27a & 26c'},
  sC:{bagi:'biaya 26b & 26d dibagi','26b':'biaya lewat 26b','26d':'biaya lewat 26d',tanpa:'biaya tetap'},
  sA:{bagi:'aset 28a & 28b dibagi','28a':'aset lewat 28a','28b':'aset lewat 28b'},
  sW:{upah:'upah 26a diubah',ntb:'upah 26a tetap',bagi:'upah & NTB dibagi'}
};
function renderSaran(){
  $('tin').classList.toggle('hide-sg',!S.saran);
  $('saranOpt').style.display=S.saran?'block':'none';
  const info=$('saranInfo'); info.innerHTML='';
  if(!S.saran) return;
  const r=sarankan(), L=kunciMap(true), LM=kunciMap(false), ubah=[];
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
  else if(r.tipe==='tidak'){msg='⚠️ Saran belum bisa dihitung. Pastikan 27a lebih besar dari 26c, atau isi nilai aset (28a/28b).';cls='warn';}
  else{
    const strategi={
      bagi:'U4 diperbaiki dengan menggeser output (27a/26c) dan aset (28a/28b) secara proporsional; biaya (26b/26d) dan upah (26a) disesuaikan seperlunya.',
      aset:'Output dipertahankan; aset (28a/28b), biaya (26b/26d), dan upah (26a) disesuaikan seperlunya.',
      pendapatan:'Aset dipertahankan; output (27a/26c), biaya (26b/26d), dan upah (26a) disesuaikan seperlunya.'
    }[r.mode];
    const autoIds=IDS.filter(id=>S[id]==='auto');
    const oto=autoIds.length?' 🤖 Pilihan otomatis: '+autoIds.map(id=>LBL[id][r.cfg[id]]).join('; ')+(r.fr<0.01?'. Nilai disarankan sedekat mungkin dengan batas interval wajar.':'.'):'';
    const sblm=(r.kunci&&r.kunci.length)?' 🔒 Kolom yang dipertahankan/sudah Anda ubah ('+r.kunci.join(', ')+') tidak diubah; sisa penyesuaian dibagi proporsional ke kolom lain.':'';
    const dl=(r.dilepas||[]).filter(k=>ubah.indexOf(k)>=0);
    const lps=dl.length?' ⚠️ Kolom yang sudah Anda ubah ('+dl.join(', ')+') saja tidak cukup, jadi ikut disesuaikan.':'';
    const sl=r.ls?Object.keys(strategiLock(S)).filter(k=>ubah.indexOf(k)>=0):[];
    const sls=sl.length?' ⚠️ Strategi pilihan Anda saja tidak cukup, jadi saran juga mengubah '+sl.join(', ')+'.':'';
    const kk=oto+sblm+lps+sls;
    if(r.tipe==='ok'){msg='✅ Jika saran diterapkan, U2–U5 semua wajar. Kolom yang disarankan berubah: '+ubah.join(', ')+'. '+(autoIds.indexOf('strategi')>=0?'':strategi)+kk;cls='ok';}
    else{
      const ev=evaluasi(hitungV(r.n));
      const sisa=ev.map((e,i)=>(e.cls==='bad'||e.cls==='warn')?NAMA_IND[i]:null).filter(Boolean);
      const w5=sisa.some(x=>x.indexOf('U5')>=0)&&!!r.w5;
      msg='⚠️ Saran terbaik masih menyisakan: '+sisa.join(', ')+(w5?'':' (mis. karena kategori yang dipilih)')+'. Kolom yang berubah: '+ubah.join(', ')+'.'+kk
        +(w5?' U5 = 26a ÷ NTB harus 0,4–0,6, jadi tidak bisa wajar selama 26a dipertahankan di '+fmtAngka(r.v['26a'])+'. Hilangkan centang "pertahankan" pada 26a (atau ubah strategi U5 menjadi Ubah upah 26a) jika ingin U5 ikut wajar.':'');cls='warn';
    }
  }
  info.innerHTML=`<div class="box ${cls}"></div>`; info.firstChild.textContent=msg;
}
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
  const ch=KOLOM.filter(k=>S.awal[k]!==S.adj[k]&&(k!=='28c'||S.pakai28c));
  const box=$('gen'); box.innerHTML='';
  if(!ch.length){box.innerHTML='<div class="box na">Belum ada kolom yang berbeda antara Data Awal dan Adjustment.</div>';return;}
  const wrap=document.createElement('div');wrap.className='grid2';
  [['CATATAN PRIBADI [dengan nilai awal]',true],['FASIH/KONFIRMASI [tanpa nilai awal]',false]].forEach(([jd,tampil])=>{
    const t=ch.map(k=>kalimat(kodeFor(k),k,S.awal[k],S.adj[k],tampil)).filter(Boolean).join('\n');
    const d=document.createElement('div');
    const head=document.createElement('div');head.className='row';
    head.innerHTML='<b></b>';head.firstChild.textContent=jd;
    const b=document.createElement('button');b.textContent='Salin';b.onclick=()=>copyText(t,b);head.appendChild(b);
    const pre=document.createElement('pre');pre.textContent=t;
    d.appendChild(head);d.appendChild(pre);wrap.appendChild(d);
  });
  box.appendChild(wrap);
}
function buildKodeKol(){
  const c=$('kk');c.innerHTML='';
  if(!S.beda) return;
  KOLOM.filter(k=>k!=='28c'||S.pakai28c).forEach(k=>{
    const d=document.createElement('div');d.textContent=k;
    const s=document.createElement('select');
    KODE.forEach(x=>{const o=document.createElement('option');o.value=x;o.textContent=x;s.appendChild(o);});
    s.value=kodeFor(k);
    s.onchange=()=>{S.kodeKol[k]=s.value;update();};
    d.appendChild(s);c.appendChild(d);
  });
}
function save(){try{sessionStorage.setItem(KEY,JSON.stringify(S));}catch(e){}}
function fit(){}  // tinggi iframe diatur tetap dari sisi Python
function update(){renderDeltaCols();renderResult();renderSaran();renderGen();save();fit();}

// ---------- init ----------
Object.keys(KAT).forEach(k=>{
  const o=document.createElement('option');o.value=k;
  const iv=KAT[k];
  o.textContent='Kategori '+k+' ('+(iv?fmtR(iv[0]).slice(0,4)+' – '+fmtR(iv[1]).slice(0,4):'-')+')';
  $('kat').appendChild(o);
});
$('kat').value=S.kat; $('kat').onchange=()=>{S.kat=$('kat').value;update();};
KODE.forEach(x=>{const o=document.createElement('option');o.value=x;o.textContent=x+' — '+KODE_DC[x];$('kode').appendChild(o);});
$('kode').value=S.kode; $('kode').onchange=()=>{S.kode=$('kode').value;update();};
$('saran').checked=S.saran; $('saran').onchange=()=>{S.saran=$('saran').checked;update();};
$('kunci').checked=S.kunci; $('kunci').onchange=()=>{S.kunci=$('kunci').checked;update();};
IDS.forEach(id=>{$(id).value=S[id]; $(id).onchange=()=>{S[id]=$(id).value;update();};});
$('p28c').checked=S.pakai28c; $('p28c').onchange=()=>{S.pakai28c=$('p28c').checked;buildKodeKol();update();};
$('beda').checked=S.beda; $('beda').onchange=()=>{S.beda=$('beda').checked;buildKodeKol();update();};

let rows='';
KOLOM.forEach(k=>{
  rows+=`<tr><td><b>${k}</b> ${NAMA[k]}</td>
  <td><input type="text" id="a_${k}" inputmode="decimal" autocomplete="off" placeholder="0"${k==='28c'?' readonly tabindex="-1" class="ro"':''}></td>
  <td><input type="text" id="d_${k}" inputmode="decimal" autocomplete="off" placeholder="0"${k==='28c'?' readonly tabindex="-1" class="ro"':''}></td>
  <td class="num"><span id="x_${k}"></span></td>
  <td class="sg-col"><div class="sg" id="s_${k}">—</div>${k==='28c'?'':`<label class="tahan"><input type="checkbox" id="h_${k}"> pertahankan nilai ini</label>`}</td></tr>`;
});
$('inbody').innerHTML=rows;
$('tin').addEventListener('click',e=>{   // klik nominal saran -> salin
  const el=e.target&&e.target.closest?e.target.closest('.nom'):null;
  if(el) salinNominal(el);
});
KOLOM.forEach(k=>{
  const h=$('h_'+k); if(!h) return;
  h.checked=!!S.tahan[k];
  h.onchange=()=>{S.tahan[k]=h.checked;update();};
});
KOLOM.forEach(k=>{
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
$('salin').onclick=()=>{KOLOM.forEach(k=>{S.adj[k]=S.awal[k];S.dirty[k]=false;$('d_'+k).value=S.adj[k];});syncTotal();update();};
$('reset').onclick=()=>{KOLOM.forEach(k=>{S.awal[k]='';S.adj[k]='';S.dirty[k]=false;S.tahan[k]=false;$('a_'+k).value='';$('d_'+k).value='';if($('h_'+k))$('h_'+k).checked=false;});syncTotal();update();};
buildKodeKol(); syncTotal(); update();
window.__S=()=>S;
})();
</script></body></html>
"""


TINGGI_ANOMALI = 1900  # piksel; jika isi lebih panjang, bisa digulir di dalam kotak


def render_anomali():
    isi = (
        HTML_ANOMALI.replace("__KOLOM__", json.dumps(KOLOM))
        .replace("__NAMA__", json.dumps(NAMA_KOLOM, ensure_ascii=False))
        .replace("__KAT__", json.dumps(KATEGORI))
        .replace("__KODE__", json.dumps(KODE_UBAH))
        .replace("__KODE_DC__", json.dumps(KODE_DC, ensure_ascii=False))
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
    with st.expander("Rumus & aturan kewajaran"):
        st.markdown(
            "- **NTB [U2]** = 27a − (26b + 26c + 26d) → wajar jika **tidak negatif**\n"
            "- **Output** = 27a − 26c (ditampilkan tanpa syarat kewajaran)\n"
            "- **R_NTB/Output [U3]** = NTB / Output → wajar jika berada pada interval kategori terpilih\n"
            "- **R_Output/Aset [U4]** = Output / 28c, dengan **28c = 28a + 28b** → wajar jika **0,200 – 0,400**\n"
            "- **R_Upah/NTB [U5]** = 26a / NTB → wajar jika **0,400 – 0,600**\n\n"
            "Rasio dibulatkan ke 3 digit di belakang koma sebelum dicek terhadap interval. "
            "Titik = pemisah ribuan, koma = desimal.\n\n"
            "**Saran perbaikan**: centang *Tampilkan saran perbaikan?* untuk memunculkan kolom "
            "saran (nominal tambah/kurangi per kolom agar U2–U5 wajar). Kolom yang sudah Anda ubah "
            "di Adjustment, atau yang Anda centang *pertahankan nilai ini*, tidak diubah; sisa penyesuaian dibagi proporsional ke kolom lain.\n\n"
            "**Strategi saran**: pilihan *🤖 Otomatis* membuat aplikasi memilih sendiri kolom yang diubah "
            "dengan perubahan terkecil, dengan nilai sedekat mungkin ke batas interval wajar. "
            "Klik nominal pada kolom saran untuk menyalinnya (angka polos, tanpa titik)."
        )
    render_anomali()
