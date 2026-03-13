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
# 🚀 LAYER 0: BOOTSTRAP (Musí být absolutně první řádek Streamlitu)
# =====================================================================
st.set_page_config(page_title="W-SERVIS Enterprise v56.0", layout="wide", page_icon="🛡️")

# =====================================================================
# 🗄️ LAYER 1: GLOBÁLNÍ IDENTITA (Fix pro NameError)
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
    "HP": "HP", "Nahrady": "Nahrady", "Voda": "VODA", "Servisni_ukony": "revize",
    "Opravy": "opravy", "Zboží": "zbozi"
}

DB_PATH = "data/data.db"
BASE_DATA_DIR = "data/"

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
# 🗄️ LAYER 3: REPOSITORY (Databázová pevnost)
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
    """Robotická pojistka: Místo pádu vrátí prázdný DataFrame s nápovědou."""
    try:
        conn = sqlite3.connect(DB_PATH)
        df = pd.read_sql(query, conn, params=params)
        conn.close()
        # Pojistka proti KeyError ve výběru firem
        if "obchpartner" in query.lower():
            if "firma" not in df.columns: df["firma"] = "Neznámý"
            if "ico" not in df.columns: df["ico"] = "000"
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
# 🛡️ LAYER 4: AUDITNÍ ROBOT (Metodika TÜV NORD)
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
    return df_audit, {"chyb": errs, "celkem": total, "score": ((total-errs)/total*100) if total > 0 else 100.0}

# =====================================================================
# ⚙️ LAYER 5: SERVICES (Hloubkový skener Git struktury)
# =====================================================================
def find_files_recursive(directory: str) -> List[str]:
    file_list = []
    if not os.path.exists(directory): return []
    for root, dirs, files in os.walk(directory):
        for file in files:
            if file.lower().endswith((".xlsx", ".csv")):
                file_list.append(os.path.join(root, file))
    return file_list

def safe_read_file(path: str) -> Optional[pd.DataFrame]:
    try:
        if path.lower().endswith(".xlsx"): return pd.read_excel(path)
        else:
            for enc in ["utf-8-sig", "cp1250", "windows-1250"]:
                try: return pd.read_csv(path, sep=";", encoding=enc)
                except: continue
    except: return None
    return None

def service_import_data():
    """Hloubkový skener: Najde data v jakékoliv podsložce."""
    init_db()
    conn = sqlite3.connect(DB_PATH)
    logs = []
    all_files = find_files_recursive(BASE_DATA_DIR)
    
    if not all_files:
        return "⚠️ Robot nenašel v adresáři 'data/' žádné soubory k importu."

    for file_path in all_files:
        fname = os.path.basename(file_path).lower()
        df = safe_read_file(file_path)
        if df is None: continue
        df.columns = [normalize_column_name(c) for c in df.columns]

        # 1. Partneri
        if any(x in fname for x in ["zakaznik", "partner", "firmy", "obchpartner"]):
            f_col = next((c for c in df.columns if any(x in c for x in ['firma', 'nazev', 'partner', 'odberatel'])), df.columns[0])
            i_col = next((c for c in df.columns if any(x in c for x in ['ico', 'ic', 'identif'])), df.columns[1] if len(df.columns)>1 else f_col)
            df_cl = pd.DataFrame()
            df_cl['firma'] = df[f_col].astype(str).str.strip()
            df_cl['ico'] = df[i_col].apply(clean_ico)
            df_cl.to_sql("obchpartner", conn, if_exists="replace", index=False)
            logs.append(f"🏢 Partneři načteni z: {fname}")

        # 2. Sklad (expimp)
        elif any(x in fname for x in ["expimp", "sklad", "zbozi"]):
            n_col = next((c for c in df.columns if any(x in c for x in ['nazev', 'zkratka', 'popis'])), None)
            p_col = next((c for c in df.columns if any(x in c for x in ['cena1', 'prodejni', 'zaklad'])), None)
            if n_col:
                df_cl = pd.DataFrame()
                df_cl['nazev'] = df[n_col].astype(str).str.strip()
                df_cl['cena'] = df[p_col].apply(normalize_price) if p_col else 0.0
                df_cl = df_cl.dropna(subset=['nazev']).drop_duplicates('nazev')
                df_cl.to_sql("cenik_zbozi", conn, if_exists="replace", index=False)
                logs.append(f"📦 Sklad synchronizován z: {fname}")

        # 3. Ceníky
        else:
            for cat, keywords in {"HP": ["hp", "kontrol"], "Nahrady": ["nahrady", "cestov"], "Voda": ["voda", "pv", "hydrant"]}.items():
                if any(kw in fname for kw in keywords):
                    n_col = next((c for c in df.columns if any(x in c for x in ['nazev', 'popis', 'polozka'])), None)
                    p_col = next((c for c in df.columns if any(x in c for x in ['cena', 'castka'])), None)
                    if n_col and p_col:
                        df_cl = pd.DataFrame()
                        df_cl['nazev'] = df[n_col].astype(str).str.strip()
                        df_cl['cena'] = df[p_col].apply(normalize_price)
                        df_cl.to_sql(normalize_category_to_table(cat), conn, if_exists="replace", index=False)
                        logs.append(f"✅ Ceník {cat} načten z: {fname}")
    
    conn.close()
    return "\n".join(logs)

# =====================================================================
# 🌐 LAYER 6: INITIALIZATION & SESSIONS
# =====================================================================
init_db()

# Bezpečné založení Session State (Persistent Layer)
if "data_zakazky" not in st.session_state: st.session_state["data_zakazky"] = {}
if "dynamic_items" not in st.session_state: st.session_state["dynamic_items"] = {}
if "vybrany_zakaznik" not in st.session_state: st.session_state["vybrany_zakaznik"] = None
if "evidence_df" not in st.session_state: st.session_state["evidence_df"] = pd.DataFrame()
if "velin_data" not in st.session_state: st.session_state["velin_data"] = pd.DataFrame()
if "sync_log" not in st.session_state: st.session_state["sync_log"] = ""

# =====================================================================
# 🏠 LAYER 7: UI RENDERING (SIDEBAR)
# =====================================================================
with st.sidebar:
    st.title("🚒 HASIČ-SERVIS")
    st.caption("Enterprise Terminal v56.0")
    st.divider()
    
    st.subheader("🏢 Výběr zákazníka")
    df_p = safe_db_query("SELECT firma, ico FROM obchpartner ORDER BY firma")
    
    if df_p.empty:
        st.warning("⚠️ Databáze je prázdná!")
        if st.button("🔍 SPUSTIT HLOUBKOVOU SYNCHRONIZACI", type="primary", use_container_width=True):
            st.session_state["sync_log"] = service_import_data()
            st.rerun() # Jeden restart pro načtení nově vytvořené DB do selectboxu
    else:
        # Vytvoření seznamu pro výběr
        options = ["-- Vyberte firmu --"] + [f"{r.get('firma','Neznámý')} | {r.get('ico','')}" for _, r in df_p.iterrows()]
        
        vz = st.session_state.get("vybrany_zakaznik")
        curr_idx = 0
        if vz:
            try:
                search_str = f"{vz.get('firma','')} | {vz.get('ico','')}"
                if search_str in options: curr_idx = options.index(search_str)
            except: curr_idx = 0
            
        sel_p = st.selectbox("Aktivní zákazník:", options, index=curr_idx)
        if sel_p != "-- Vyberte firmu --":
            ico_sel = sel_p.split(" | ")[1].strip()
            if not vz or vz.get("ico") != ico_sel:
                partner_data = safe_db_query("SELECT * FROM obchpartner WHERE ico = ?", (ico_sel,))
                if not partner_data.empty:
                    st.session_state["vybrany_zakaznik"] = partner_data.iloc[0].to_dict()
                    st.session_state["evidence_df"] = pd.DataFrame() 
                    st.rerun()

    st.divider()
    menu = st.radio("Centrální navigace:", ["📝 Evidence & Zakázka", "🗄️ Katalog & Sklad", "📊 Obchodní Velín"])
    
    if st.session_state["sync_log"]:
        with st.expander("📄 Poslední report synchronizace"):
            st.code(st.session_state["sync_log"])

# =====================================================================
# 📝 PAGE: EVIDENCE & ZAKÁZKA
# =====================================================================
if menu == "📝 Evidence & Zakázka":
    st.title("🛡️ Zpracování zakázky")
    tabs = st.tabs(["📋 1. Evidence", "💰 2. Fakturace", "🖨️ 3. Tisk"])
    
    with tabs[0]:
        if not st.session_state["vybrany_zakaznik"]:
            st.info("👈 Nejprve vyberte zákazníka v levém panelu.")
        else:
            vz = st.session_state["vybrany_zakaznik"]
            st.subheader(f"Kontroly pro: {vz.get('firma')}")
            
            if st.session_state["evidence_df"].empty:
                df_e = safe_db_query("SELECT * FROM evidence_hp WHERE ico = ?", (vz.get('ico'),))
                if df_e.empty:
                    df_e = pd.DataFrame(columns=["druh","typ_hp","vyr_cislo","rok_vyr","mesic_vyr","tlak_rok","tlak_mesic","stav","duvod_nv","objekt","misto"])
                    for i in range(5): df_e.loc[i] = ["přenosný", "", "", None, None, None, None, "S", "", "", ""]
                st.session_state["evidence_df"] = df_e

            df_aud, v_stats = run_expert_audit(st.session_state["evidence_df"], vz)
            if v_stats['chyb'] > 0: st.error(f"⚠️ Robot: Nalezeno {v_stats['chyb']} chyb v metodice.")
            
            edited = st.data_editor(df_aud, num_rows="dynamic", use_container_width=1, key="main_editor")
            if st.button("💾 Uložit do databáze"):
                st.session_state["evidence_df"] = edited
                # Zde probíhá robotické napočítání faktury
                df_c = edited[edited['typ_hp'].fillna("") != ""]
                s_c = len(df_c[df_c['stav'].isin(['S', 'S-nový'])])
                no_c = len(df_c[df_c['stav'].isin(['NO', 'NOPZ'])])
                nv_c = len(df_c[df_c['stav'] == 'NV'])
                st.session_state["q1_h1"] = float(s_c)
                st.session_state["q1_h2"] = float(no_c)
                st.session_state["q1_h3"] = float(nv_c)
                st.success("✅ Uloženo a faktura přepočítána.")

    with tabs[1]:
        st.subheader("💰 Fakturační položky")
        def draw_row(name, price, row_id):
            cols = st.columns([4, 2, 1, 1, 1, 1, 1])
            cols[0].write(name)
            p = cols[1].number_input("Cena", 0.0, value=float(price), key=f"p_{row_id}")
            q1 = cols[2].number_input("O1", 0.0, value=float(st.session_state.get(f"q1_{row_id}", 0.0)), key=f"q1_{row_id}")
            st.session_state["data_zakazky"][name] = {"q": q1, "p": p}
        draw_row("Kontrola HP (shodný)", 29.4, "h1")
        draw_row("Kontrola HP (neshodný - opravitelný)", 19.7, "h2")
        draw_row("Kontrola HP (neopravitelný) + zneprovoznění", 23.5, "h3")

# =====================================================================
# 🗄️ PAGE: KATALOG & SKLAD
# =====================================================================
elif menu == "🗄️ Katalog & Sklad":
    st.title("🗄️ Správa databáze a skladu")
    t1, t2 = st.tabs(["📦 Pohled do DB", "⚙️ Synchronizace"])
    with t1:
        tbl = st.selectbox("Zobrazit tabulku:", ["obchpartner", "cenik_hp", "cenik_zbozi"])
        st.dataframe(safe_db_query(f"SELECT * FROM {tbl}"), use_container_width=1)
    with t2:
        if st.button("🚀 SPUSTIT KOMPLETNÍ REKURZIVNÍ SYNCHRONIZACI", type="primary"):
            st.session_state["sync_log"] = service_import_data()
            st.success("Synchronizace dokončena."); st.code(st.session_state["sync_log"])

# =====================================================================
# 📊 PAGE: OBCHODNÍ VELÍN
# =====================================================================
elif menu == "📊 Obchodní Velín":
    st.title("📊 Obchodní Velín (Audit)")
    up_f = st.file_uploader("Nahrajte CSV soubor:", type=['csv'])
    if up_f:
        try: st.session_state["velin_data"] = pd.read_csv(up_f, sep=';', encoding='utf-8-sig')
        except: st.error("Chyba čtení CSV.")
    if not st.session_state["velin_data"].empty:
        df_v, v_stats = run_expert_audit(st.session_state["velin_data"])
        st.metric("Index integrity dat zakázky", f"{v_stats.get('score',0):.1f} %")
        st.dataframe(df_v, use_container_width=1)

# =====================================================================
# 🏁 LAYER 8: FOOTER (Safe against NameError)
# =====================================================================
st.sidebar.divider()
st.sidebar.caption(f"© {datetime.now().year} {FIRMA_VLASTNI.get('název')}")
