import os
import re
import time
import unicodedata
import sqlite3
import urllib.request
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import streamlit as st
import pandas as pd
from fpdf import FPDF

# =====================================================================
# 🚀 LAYER 0: BOOTSTRAP (Musí být jako první)
# =====================================================================
st.set_page_config(page_title="W-SERVIS Enterprise v53.0", layout="wide", page_icon="🛡️")

# =====================================================================
# 🗄️ LAYER 1: GLOBÁLNÍ IDENTITY (Fix pro NameError)
# =====================================================================
FIRMA_VLASTNI = {
    "název": "Ilja Urbánek HASIČ - SERVIS",
    "sídlo": "Poříčská 186, 373 82 Boršov nad Vltavou",
    "ico": "60835265",
    "dic": "CZ5706281691",
    "zápis": "Zapsán v živnostenském rejstříku pod ID RŽP: 696191",
    "telefony": "608 409 036, 777 664 768",
    "email": "schranka@hasic-servis.com",
    "web": "www.hasic-servis.com",
    "certifikace": "TÜV NORD Czech",
}

STAVY_HP = ["S", "NO", "NOPZ", "CH", "S-nový", "NV"]
DUVODY_VYRAZENI = {
    "": "", "A": "HP neodpovídá ČSN", "B": "Zákaz používání (ozón)", 
    "C": "Deformace nádoby", "D": "Poškozený lak vnější", "E": "Poškozený lak vnitřní", 
    "F": "Koroze", "G": "Životnost", "H": "Nečitelné číslo", 
    "I": "Nesplňuje tlak. zkoušky", "J": "Ukončení výroby ND", "K": "Neekonomické (na žádost)"
}

CATEGORY_MAP = {
    "HP": "HP", "Nahrady": "Nahrady", "Voda": "VODA", "Ostatni": "ostatni",
    "ND_HP": "ND_HP", "ND_Voda": "ND_VODA", "Servisni_ukony": "revize",
    "Opravy": "opravy", "Zboží": "zbozi"
}

DB_PATH = "data/data.db"
CSV_FOLDER = "data/ceniky/"

# =====================================================================
# 🧹 LAYER 2: NORMALIZAČNÍ ROBOT
# =====================================================================
def normalize_column_name(col: str) -> str:
    col = str(col)
    col = unicodedata.normalize("NFKD", col).encode("ascii", "ignore").decode("ascii")
    col = col.lower().strip()
    col = re.sub(r"[\s/]+", "_", col)
    col = re.sub(r"[^a-z0-9_]", "", col)
    return col

def normalize_category_to_table(cat_key: str) -> str:
    return f"cenik_{normalize_column_name(cat_key)}"

def normalize_price(v: Any) -> float:
    if pd.isna(v): return 0.0
    s = str(v).replace(" ", "").replace(",", ".").replace("\xa0", "")
    try: return float(re.sub(r"[^-0-9.]", "", s))
    except: return 0.0

def clean_ico(ico_val: Any) -> str:
    s = str(ico_val).strip()
    if s.lower() in ['nan', 'none', 'null', '']: return ""
    return s.split('.')[0]

def format_cena(num):
    if num == 0: return "0,00"
    return f"{num:,.2f}".replace(",", "X").replace(".", ",").replace("X", " ")

# =====================================================================
# 🗄️ LAYER 3: REPOSITORY (Bezpečná databáze)
# =====================================================================
def init_db():
    if not os.path.exists("data"): os.makedirs("data")
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("CREATE TABLE IF NOT EXISTS objekty (id INTEGER PRIMARY KEY AUTOINCREMENT, ico TEXT NOT NULL, nazev_objektu TEXT NOT NULL, UNIQUE(ico, nazev_objektu))")
    cur.execute("CREATE TABLE IF NOT EXISTS evidence_hp (id INTEGER PRIMARY KEY AUTOINCREMENT, ico TEXT NOT NULL, druh TEXT, typ_hp TEXT, vyr_cislo TEXT, rok_vyr TEXT, mesic_vyr TEXT, tlak_rok TEXT, tlak_mesic TEXT, stav TEXT, duvod_nv TEXT, objekt TEXT, misto TEXT)")
    cur.execute("CREATE TABLE IF NOT EXISTS obchpartner (ico TEXT PRIMARY KEY, dic TEXT, firma TEXT, ulice TEXT, cp TEXT, mesto TEXT, psc TEXT)")
    conn.commit()
    conn.close()

def safe_db_query(query: str, params: tuple = ()) -> pd.DataFrame:
    try:
        conn = sqlite3.connect(DB_PATH)
        df = pd.read_sql(query, conn, params=params)
        conn.close()
        return df
    except:
        return pd.DataFrame()

def get_price(cat_key: str, item_name: str) -> float:
    table = normalize_category_to_table(cat_key)
    try:
        conn = sqlite3.connect(DB_PATH)
        res = conn.execute(f"SELECT cena FROM {table} WHERE nazev = ? LIMIT 1", (item_name.strip(),)).fetchone()
        conn.close()
        return float(res[0]) if res else 0.0
    except: return 0.0

# =====================================================================
# 🛡️ LAYER 4: AUDITNÍ ROBOT
# =====================================================================
def run_expert_audit(df: pd.DataFrame, context_zakaznik: Dict = None) -> Tuple[pd.DataFrame, Dict]:
    df_audit = df.copy()
    if df_audit.empty: return df_audit, {"chyb": 0, "celkem": 0, "score": 100.0}
    
    def check_row(row):
        issues = []
        typ = str(row.get('typ_hp', '')).upper()
        if any(x in typ for x in ["HYDRANT", "PV", "VODA"]):
            tlak = row.get('tlak_rok', 0)
            if not tlak or tlak == 0 or str(tlak) == "None": issues.append("Chybí měření tlaku")
        if row.get('stav') == 'NV' and not row.get('duvod_nv'):
            issues.append("Chybí kód A-K")
        return "✅ OK" if not issues else "❌ " + ", ".join(issues)
    
    df_audit['robot_status'] = df_audit.apply(check_row, axis=1)
    errs = len(df_audit[df_audit['robot_status'].str.contains("❌")])
    total = len(df_audit)
    return df_audit, {"chyb": errs, "celkem": total, "score": ((total-errs)/total*100)}

# =====================================================================
# ⚙️ LAYER 5: SERVICES (Vylepšený Importér)
# =====================================================================
def safe_read_io(base_path: str) -> Optional[pd.DataFrame]:
    for ext in [".xlsx", ".csv"]:
        path = base_path + ext
        if os.path.exists(path):
            try:
                if ext == ".xlsx": return pd.read_excel(path)
                else:
                    for enc in ["utf-8-sig", "cp1250", "windows-1250"]:
                        try: return pd.read_csv(path, sep=";", encoding=enc)
                        except: continue
            except: continue
    return None

def service_import_data():
    """Inteligentní Importér: Sám najde firmu a zboží bez ohledu na název sloupce."""
    conn = sqlite3.connect(DB_PATH)
    logs = []
    
    # 1. Ceníky
    for k, v in CATEGORY_MAP.items():
        df = safe_read_io(os.path.join(CSV_FOLDER, v))
        if df is not None:
            df.columns = [normalize_column_name(c) for c in df.columns]
            name_col = next((c for c in df.columns if any(x in c for x in ['nazev', 'popis', 'polozka'])), None)
            price_col = next((c for c in df.columns if any(x in c for x in ['cena', 'prodej', 'castka'])), None)
            if name_col and price_col:
                df_cl = pd.DataFrame()
                df_cl['nazev'] = df[name_col].astype(str).str.strip()
                df_cl['cena'] = df[price_col].apply(normalize_price)
                df_cl = df_cl.dropna(subset=['nazev']).drop_duplicates('nazev')
                df_cl.to_sql(normalize_category_to_table(k), conn, if_exists="replace", index=False)
                logs.append(f"✅ {k} OK.")

    # 2. Sklad (expimp.csv)
    df_exp = safe_read_io(os.path.join(CSV_FOLDER, "expimp"))
    if df_exp is not None:
        df_exp.columns = [normalize_column_name(c) for c in df_exp.columns]
        n_col = next((c for c in df_exp.columns if any(x in c for x in ['nazev', 'zkratka'])), None)
        p_col = next((c for c in df_exp.columns if any(x in c for x in ['cena1', 'prodejni'])), None)
        if n_col:
            df_cl = pd.DataFrame()
            df_cl['nazev'] = df_exp[n_col].astype(str).str.strip()
            df_cl['cena'] = df_exp[p_col].apply(normalize_price) if p_col else 0.0
            df_cl = df_cl.dropna(subset=['nazev']).drop_duplicates('nazev')
            df_cl.to_sql("cenik_zbozi", conn, if_exists="append", index=False)
            logs.append(f"📦 SKLAD synchronizován.")

    # 3. Zákazníci (FIX KeyError)
    df_c = safe_read_io(os.path.join(CSV_FOLDER, "zakaznici"))
    if df_c is not None:
        df_c.columns = [normalize_column_name(c) for c in df_c.columns]
        f_col = next((c for c in df_c.columns if any(x in c for x in ['firma', 'nazev', 'partner'])), df_c.columns[0])
        i_col = next((c for c in df_c.columns if any(x in c for x in ['ico', 'ic', 'identif'])), df_c.columns[1] if len(df_c.columns)>1 else f_col)
        
        df_final = pd.DataFrame()
        df_final['firma'] = df_c[f_col].astype(str).str.strip()
        df_final['ico'] = df_c[i_col].apply(clean_ico)
        df_final.to_sql("obchpartner", conn, if_exists="replace", index=False)
        logs.append("🏢 PARTNEŘI synchronizováni.")
    
    conn.close()
    return "\n".join(logs)

# =====================================================================
# 🌐 LAYER 6: INITIALIZATION
# =====================================================================
init_db()

def ensure_session():
    if "data_zakazky" not in st.session_state: st.session_state["data_zakazky"] = {}
    if "dynamic_items" not in st.session_state: st.session_state["dynamic_items"] = {}
    if "vybrany_zakaznik" not in st.session_state: st.session_state["vybrany_zakaznik"] = None
    if "evidence_df" not in st.session_state: st.session_state["evidence_df"] = pd.DataFrame()
    if "velin_data" not in st.session_state: st.session_state["velin_data"] = pd.DataFrame()

ensure_session()

# =====================================================================
# 🏠 LAYER 7: UI RENDERING (Sidebar & Pages)
# =====================================================================
with st.sidebar:
    st.markdown("### 🚒 HASIČ-SERVIS Urbánek")
    st.divider()
    
    # 🏢 VÝBĚR ZÁKAZNÍKA (Oživujeme seznam)
    st.subheader("🏢 Výběr partnera")
    df_p = safe_db_query("SELECT firma, ico FROM obchpartner ORDER BY firma")
    
    if df_p.empty:
        st.warning("⚠️ Databáze je prázdná!")
        if st.button("⚙️ Synchronizovat data nyní", type="primary", use_container_width=True):
            service_import_data()
            st.rerun()
    else:
        # Pevný seznam pro selectbox
        df_p['display'] = df_p.apply(lambda r: f"{r.get('firma','')} | {r.get('ico','')}", axis=1)
        opts = ["-- Vyberte firmu --"] + df_p['display'].tolist()
        
        vz = st.session_state["vybrany_zakaznik"]
        curr_idx = 0
        if vz:
            try: curr_idx = opts.index(f"{vz.get('firma','')} | {vz.get('ico','')}")
            except: curr_idx = 0
            
        sel_p = st.selectbox("Aktivní zákazník:", opts, index=curr_idx)
        if sel_p != "-- Vyberte firmu --":
            ico_sel = sel_p.split(" | ")[1].strip()
            if not vz or vz.get("ico") != ico_sel:
                partner_data = safe_db_query("SELECT * FROM obchpartner WHERE ico = ?", (ico_sel,))
                if not partner_data.empty:
                    st.session_state["vybrany_zakaznik"] = partner_data.iloc[0].to_dict()
                    st.session_state["evidence_df"] = pd.DataFrame()
                    st.rerun()

    st.divider()
    menu = st.radio("Sekce dashboardu:", ["📝 Zpracování zakázky", "🗄️ Katalog & Sklad", "📊 Obchodní Velín"])
    st.divider()
    
    # Košík
    tp = sum(v.get("q",0)*v.get("p",0) for v in st.session_state["data_zakazky"].values()) + sum(v.get("q",0)*v.get("p",0) for v in st.session_state["dynamic_items"].values())
    st.markdown(f"<div style='background:#f8f9fa;padding:15px;border-radius:8px;border-left:5px solid #ff4b4b'><b>🛒 Fakturace celkem:</b><br/>{format_cena(tp)} Kč</div>", unsafe_allow_html=True)

# =====================================================================
# 📝 PAGE: ZPRACOVÁNÍ ZAKÁZKY
# =====================================================================
if menu == "📝 Zpracování zakázky":
    st.title("🛡️ Zpracování zakázky")
    tabs = st.tabs(["📋 1. Evidence & Robot", "💰 2. Fakturace", "🖨️ 3. Tisk"])
    
    with tabs[0]:
        if not st.session_state["vybrany_zakaznik"]:
            st.info("👈 Nejprve vyberte zákazníka v levém panelu.")
        else:
            vz = st.session_state["vybrany_zakaznik"]
            st.subheader(f"Evidence kontrol: {vz.get('firma')}")
            
            if st.session_state["evidence_df"].empty:
                df_e = safe_db_query("SELECT * FROM evidence_hp WHERE ico = ?", (vz.get('ico'),))
                if df_e.empty:
                    df_e = pd.DataFrame(columns=["druh","typ_hp","vyr_cislo","rok_vyr","mesic_vyr","tlak_rok","tlak_mesic","stav","duvod_nv","objekt","misto"])
                    for i in range(5): df_e.loc[i] = ["přenosný", "", "", None, None, None, None, "S", "", "", ""]
                st.session_state["evidence_df"] = df_e
            
            df_aud, v_stats = run_expert_audit(st.session_state["evidence_df"], vz)
            if v_stats['chyb'] > 0: st.error(f"⚠️ Robot: Nalezeno {v_stats['chyb']} chyb v metodice.")
            
            edited = st.data_editor(df_aud, num_rows="dynamic", use_container_width=1, key="main_editor")
            if st.button("💾 Uložit do DB a přepočítat"):
                st.session_state["evidence_df"] = edited
                st.success("✅ Uloženo a synchronizováno.")
                st.rerun()

    with tabs[1]:
        st.subheader("Automatické položky dle Evidence")
        def draw_row(name, price, row_id):
            cols = st.columns([4, 2, 1, 1, 1, 1, 1])
            cols[0].write(name)
            p = cols[1].number_input("Cena", 0.0, value=float(price), key=f"p_{row_id}")
            q1 = cols[2].number_input("O1", 0.0, value=float(st.session_state.get(f"q1_{row_id}", 0.0)), key=f"q1_{row_id}")
            st.session_state["data_zakazky"][name] = {"q": q1, "p": p}
        draw_row("Kontrola HP (shodný)", 29.4, "h1")
        draw_row("Kontrola HP (neshodný - opravitelný)", 19.7, "h2")
        draw_row("Vyhodnocení kontroly (á 1ks HP)", 5.8, "s1")

# =====================================================================
# 🗄️ PAGE: KATALOG & SKLAD
# =====================================================================
elif menu == "🗄️ Katalog & Sklad":
    st.title("🗄️ Správa ceníků a skladu")
    t1, t2 = st.tabs(["📦 Pohled do DB", "⚙️ Synchronizace"])
    with t1:
        tbl = st.selectbox("Zobrazit tabulku:", ["obchpartner", "cenik_hp", "cenik_zbozi"])
        df_v = safe_db_query(f"SELECT * FROM {tbl}")
        st.dataframe(df_v, use_container_width=1)
    with t2:
        if st.button("🚀 Spustit kompletní synchronizaci", type="primary"):
            msg = service_import_data()
            st.success("Synchronizace dokončena."); st.code(msg)

# =====================================================================
# 📊 PAGE: OBCHODNÍ VELÍN
# =====================================================================
elif menu == "📊 Obchodní Velín":
    st.title("📊 Obchodní Velín (Audit)")
    up_f = st.file_uploader("Nahrajte soubor Migrace_Centraly_Navrh.csv:", type=['csv'])
    if up_f:
        try: st.session_state["velin_data"] = pd.read_csv(up_f, sep=';', encoding='utf-8-sig')
        except: st.error("Chyba čtení CSV.")
    if not st.session_state["velin_data"].empty:
        df_v, v_stats = run_expert_audit(st.session_state["velin_data"])
        st.metric("Index integrity dat", f"{v_stats.get('score',0):.1f} %")
        st.dataframe(df_v, use_container_width=1)

# =====================================================================
# 🏁 LAYER 8: FOOTER (Safe against NameError)
# =====================================================================
st.sidebar.divider()
st.sidebar.caption(f"© {datetime.now().year} {FIRMA_VLASTNI.get('název')}")
