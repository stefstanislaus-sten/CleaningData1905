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
                st.iframe(data["saran_html"], height="content")  # Saran Penelusuran Google
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
<div class="row"><b>Kategori kewajaran</b><select id="kat"></select></div>
<div class="scroll"><table>
<thead><tr><th>Kolom</th><th>Perhitungan Data Awal</th><th>Perhitungan Data Adjustment</th><th class="num">Perubahan (Adj &minus; Awal)</th></tr></thead>
<tbody id="inbody"></tbody></table></div>
<div class="row"><button id="salin">↩️ Samakan adjustment dengan data awal</button><button id="reset">🗑️ Reset semua isian</button></div>
<div class="small">Adjustment otomatis mengikuti Data Awal selama belum Anda ubah sendiri. Semua hasil dihitung langsung saat mengetik.</div>

<h3>Hasil perhitungan &amp; cek kewajaran (live)</h3>
<div class="scroll"><table><thead><tr><th>Indikator</th><th>Syarat wajar</th><th class="num">Data Awal</th><th>Status Awal</th><th class="num">Data Adjustment</th><th>Status Adjustment</th><th class="num">Perubahan</th></tr></thead>
<tbody id="res"></tbody></table></div>
<div id="ringkas"></div>

<h3>Generate kalimat perbaikan</h3>
<div class="small">Kalimat dibuat hanya untuk kolom yang nilainya berbeda antara Data Awal dan Adjustment.</div>
<div class="row"><b>Kode DC</b><select id="kode"></select>
<label><input type="checkbox" id="beda"> Pakai kode DC berbeda untuk tiap kolom</label></div>
<div id="kk" class="kk"></div>
<div id="gen"></div>
</div>
<script>
(function(){
const KOLOM=__KOLOM__, NAMA=__NAMA__, KAT=__KAT__, KODE=__KODE__, KODE_DC=__KODE_DC__;
const U4=[0.2,0.4], U5=[0.4,0.6], KEY='dc_anomali_state_v1';
const $=id=>document.getElementById(id);
let S={kat:'A',awal:{},adj:{},dirty:{},kode:KODE[0],beda:false,kodeKol:{}};
try{const r=sessionStorage.getItem(KEY); if(r) S=Object.assign(S,JSON.parse(r));}catch(e){}
KOLOM.forEach(k=>{S.awal[k]=S.awal[k]||'';S.adj[k]=S.adj[k]||'';});

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
function fmtR(x){return x.toFixed(3).replace('.',',');}
function nz(x){return x===null?0:x;}

// ---------- perhitungan ----------
function hitung(t){
  const v={};let all=true;
  KOLOM.forEach(k=>{v[k]=parse(t[k]); if(v[k]!==null) all=false;});
  if(all) return null;
  const g=k=>nz(v[k]);
  const u2=g('27a')-(g('26b')+g('26c')+g('26d'));
  const out=g('27a')-g('26c');
  return {U2:u2,U3:out?u2/out:null,U4:g('28c')?out/g('28c'):null,U5:u2?g('26a')/u2:null};
}
function stRasio(n,iv){
  if(iv===null) return ['na','➖ Tidak ada interval'];
  if(n===null) return ['warn','⚠️ Tidak dapat dihitung'];
  const r=Math.round(n*1000)/1000;
  return (r>=iv[0]-1e-9&&r<=iv[1]+1e-9)?['ok','✅ Wajar']:['bad','❌ Tidak wajar'];
}
function evaluasi(h){
  if(h===null) return [0,1,2,3].map(()=>({txt:'-',cls:'na',stat:'Belum diisi'}));
  const iv=[null,KAT[S.kat],U4,U5], val=[h.U2,h.U3,h.U4,h.U5];
  return val.map((v,i)=>{
    if(i===0){const ok=v>=0; return {txt:fmtAngka(v),cls:ok?'ok':'bad',stat:ok?'✅ Wajar':'❌ Tidak wajar'};}
    const s=stRasio(v,iv[i]); return {txt:v===null?'-':fmtR(v),cls:s[0],stat:s[1]};
  });
}
function delta(a,b,money){
  if(a===null||b===null) return {t:'-',c:'zero'};
  let d=b-a; if(!money) d=Math.round(d*1000)/1000;
  if(Math.abs(d)<1e-9) return {t:'0',c:'zero'};
  return {t:(d>0?'+':'−')+(money?fmtAngka(Math.abs(d)):fmtR(Math.abs(d))),c:d>0?'up':'down'};
}

// ---------- render ----------
const NAMA_IND=['NTB [U2]','R_NTB/Output [U3]','R_Output/Aset [U4]','R_Upah/NTB [U5]'];
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
  const syarat=['≥ 0 (tidak negatif)',iv?fmtR(iv[0])+' – '+fmtR(iv[1]):'-',fmtR(U4[0])+' – '+fmtR(U4[1]),fmtR(U5[0])+' – '+fmtR(U5[1])];
  const vA=hA?[hA.U2,hA.U3,hA.U4,hA.U5]:[null,null,null,null];
  const vD=hD?[hD.U2,hD.U3,hD.U4,hD.U5]:[null,null,null,null];
  let h='';
  for(let i=0;i<4;i++){
    const d=delta(vA[i],vD[i],i===0);
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
function kodeFor(k){return S.beda?(S.kodeKol[k]||S.kode):S.kode;}
function kalimat(kode,kol,awal,ubah,tampil){
  if(!awal&&!ubah) return null;
  const u=ubah||'kosong';
  if(tampil&&awal) return `${kode} memperbaiki isian ${kol} dari ${awal} menjadi ${u}`;
  return `${kode} memperbaiki isian ${kol} menjadi ${u}`;
}
function copyText(t,btn){
  const done=()=>{const o=btn.textContent;btn.textContent='Tersalin ✓';setTimeout(()=>btn.textContent=o,1200);};
  const fb=()=>{const ta=document.createElement('textarea');ta.value=t;document.body.appendChild(ta);ta.select();
    try{document.execCommand('copy');done();}catch(e){}document.body.removeChild(ta);};
  try{navigator.clipboard.writeText(t).then(done,fb);}catch(e){fb();}
}
function renderGen(){
  const ch=KOLOM.filter(k=>S.awal[k]!==S.adj[k]);
  const box=$('gen'); box.innerHTML='';
  if(!ch.length){box.innerHTML='<div class="box na">Belum ada kolom yang berbeda antara Data Awal dan Adjustment.</div>';return;}
  const wrap=document.createElement('div');wrap.className='grid2';
  [['Gaya 1 — dengan nilai awal',true],['Gaya 2 — tanpa nilai awal',false]].forEach(([jd,tampil])=>{
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
  KOLOM.forEach(k=>{
    const d=document.createElement('div');d.textContent=k;
    const s=document.createElement('select');
    KODE.forEach(x=>{const o=document.createElement('option');o.value=x;o.textContent=x;s.appendChild(o);});
    s.value=kodeFor(k);
    s.onchange=()=>{S.kodeKol[k]=s.value;update();};
    d.appendChild(s);c.appendChild(d);
  });
}
function save(){try{sessionStorage.setItem(KEY,JSON.stringify(S));}catch(e){}}
function fit(){
  try{const h=$('app').offsetHeight+24;window.frameElement.style.height=h+'px';window.frameElement.height=h;}catch(e){}
}
function update(){renderDeltaCols();renderResult();renderGen();save();fit();}

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
$('beda').checked=S.beda; $('beda').onchange=()=>{S.beda=$('beda').checked;buildKodeKol();update();};

let rows='';
KOLOM.forEach(k=>{
  rows+=`<tr><td><b>${k}</b> ${NAMA[k]}</td>
  <td><input type="text" id="a_${k}" inputmode="decimal" autocomplete="off" placeholder="0"></td>
  <td><input type="text" id="d_${k}" inputmode="decimal" autocomplete="off" placeholder="0"></td>
  <td class="num"><span id="x_${k}"></span></td></tr>`;
});
$('inbody').innerHTML=rows;
KOLOM.forEach(k=>{
  const a=$('a_'+k),d=$('d_'+k);
  a.value=S.awal[k]; d.value=S.adj[k];
  a.addEventListener('input',()=>{
    S.awal[k]=liveFormat(a);
    if(!S.dirty[k]){S.adj[k]=S.awal[k];d.value=S.adj[k];}
    update();
  });
  d.addEventListener('input',()=>{
    S.adj[k]=liveFormat(d);
    S.dirty[k]=(S.adj[k]!==S.awal[k]);
    update();
  });
});
$('salin').onclick=()=>{KOLOM.forEach(k=>{S.adj[k]=S.awal[k];S.dirty[k]=false;$('d_'+k).value=S.adj[k];});update();};
$('reset').onclick=()=>{KOLOM.forEach(k=>{S.awal[k]='';S.adj[k]='';S.dirty[k]=false;$('a_'+k).value='';$('d_'+k).value='';});update();};
buildKodeKol(); update();
window.__S=()=>S;
})();
</script></body></html>
"""


def render_anomali():
    isi = (
        HTML_ANOMALI.replace("__KOLOM__", json.dumps(KOLOM))
        .replace("__NAMA__", json.dumps(NAMA_KOLOM, ensure_ascii=False))
        .replace("__KAT__", json.dumps(KATEGORI))
        .replace("__KODE__", json.dumps(KODE_UBAH))
        .replace("__KODE_DC__", json.dumps(KODE_DC, ensure_ascii=False))
    )
    if hasattr(st, "iframe"):  # Streamlit versi baru
        st.iframe(isi, height="content")
    else:  # Streamlit versi lama
        components.html(isi, height=1300, scrolling=True)


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
            "- **R_NTB/Output [U3]** = NTB / (27a − 26c) → wajar jika berada pada interval kategori terpilih\n"
            "- **R_Output/Aset [U4]** = (27a − 26c) / 28c → wajar jika **0,200 – 0,400**\n"
            "- **R_Upah/NTB [U5]** = 26a / NTB → wajar jika **0,400 – 0,600**\n\n"
            "Rasio dibulatkan ke 3 digit di belakang koma sebelum dicek terhadap interval. "
            "Titik = pemisah ribuan, koma = desimal."
        )
    render_anomali()
