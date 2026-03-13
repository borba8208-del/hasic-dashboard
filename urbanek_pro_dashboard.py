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
st.set_page_config(page_title="W-SERVIS Enterprise v51.0", layout="wide", page_icon="🛡️")

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
    col = unicodedata.normalize("NFKD", col)
    col = "".join(c for c in col if not unicodedata.combining(c))
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
    """Robotická pojistka: Vždy vrátí DataFrame, nikdy chybu."""
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
# 🛡️ LAYER 4: AUDITNÍ ROBOT (Vysoká kontrola TÜV NORD)
# =====================================================================
def run_expert_audit(df: pd.DataFrame, context_zakaznik: Dict = None) -> Tuple[pd.DataFrame, Dict]:
    df_audit = df.copy()
    if df_audit.empty: return df_audit, {"chyb": 0, "celkem": 0, "score": 100.0}
    
    def check_row(row):
        issues = []
        # Audit Hydranty (Metodika TÜV NORD)
        typ = str(row.get('typ_hp', '')).upper()
        if any(x in typ for x in ["HYDRANT", "PV", "VODA"]):
            tlak = row.get('tlak_rok', 0)
            if not tlak or tlak == 0 or tlak == "None": issues.append("Chybí měření tlaku")
        # Audit Vyřazení
        if row.get('stav') == 'NV' and not row.get('duvod_nv'):
            issues.append("Chybí kód vyřazení A-K")
        return "✅ OK" if not issues else "❌ " + ", ".join(issues)
    
    df_audit['audit_robot'] = df_audit.apply(check_row, axis=1)
    errs = len(df_audit[df_audit['audit_robot'].str.contains("❌")])
    total = len(df_audit)
    score = ((total - errs) / total * 100) if total > 0 else 100.0
    return df_audit, {"chyb": errs, "celkem": total, "score": score}

# =====================================================================
# ⚙️ LAYER 5: SERVICES (Inteligentní Importér)
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
    """Inteligentní Importér: Prohledá složku a inteligentně namapuje sloupce."""
    conn = sqlite3.connect(DB_PATH)
    logs = []
    
    # 1. Ceníky
    for k, v in CATEGORY_MAP.items():
        df = safe_read_io(os.path.join(CSV_FOLDER, v))
        if df is not None:
            df.columns = [normalize_column_name(c) for c in df.columns]
            name_aliases = ['nazev', 'zbozi_nazev', 'ukon_popis', 'polozka']
            price_aliases = ['cena', 'zbozi_cena', 'ukon_cena', 'cena1', 'cena_prodejni']
            for na in name_aliases:
                if na in df.columns: df.rename(columns={na: 'nazev'}, inplace=True)
            for pa in price_aliases:
                if pa in df.columns: df.rename(columns={pa: 'cena'}, inplace=True)
            if 'cena' in df.columns and 'nazev' in df.columns:
                df['cena'] = df['cena'].apply(normalize_price)
                df = df.dropna(subset=['nazev']).drop_duplicates('nazev')
                df[['nazev', 'cena']].to_sql(normalize_category_to_table(k), conn, if_exists="replace", index=False)
                logs.append(f"✅ Ceník {k} připraven.")

    # 2. Sklad (expimp.csv)
    df_exp = safe_read_io(os.path.join(CSV_FOLDER, "expimp"))
    if df_exp is not None:
        df_exp.columns = [normalize_column_name(c) for c in df_exp.columns]
        if 'nazev' in df_exp.columns or 'zkratka' in df_exp.columns:
            df_cl = pd.DataFrame()
            df_cl['nazev'] = df_exp['nazev'] if 'nazev' in df_exp.columns else df_exp['zkratka']
            df_cl['cena'] = df_exp['cena1'].apply(normalize_price) if 'cena1' in df_exp.columns else 0.0
            df_cl = df_cl.dropna(subset=['nazev']).drop_duplicates('nazev')
            df_cl.to_sql("cenik_zbozi", conn, if_exists="append", index=False)
            logs.append(f"📦 SKLAD z expimp synchronizován.")

    # 3. Zákazníci (Pevná logika proti KeyError)
    df_c = safe_read_io(os.path.join(CSV_FOLDER, "zakaznici"))
    if df_c is not None:
        df_c.columns = [normalize_column_name(c) for c in df_c.columns]
        # Mapa pro firmu
        for c in df_c.columns:
            if any(x in c for x in ['firma', 'partner', 'nazev', 'odberatel']):
                df_c.rename(columns={c: 'firma'}, inplace=True)
            if any(x in c for x in ['ico', 'ic', 'identifikacni']):
                df_c.rename(columns={c: 'ico'}, inplace=True)
        if 'firma' not in df_c.columns: df_c['firma'] = "Neznámý partner"
        if 'ico' not in df_c.columns: df_c['ico'] = "00000000"
        df_c.to_sql("obchpartner", conn, if_exists="replace", index=False)
        logs.append("🏢 DATABÁZE PARTNERŮ oživena.")
    
    conn.close()
    return "\n".join(logs) if logs else "Nebyl nalezen žádný soubor k importu v data/ceniky/"

# =====================================================================
# 🌐 LAYER 6: SESSION INITIALIZATION
# =====================================================================
init_db()

def ensure_session():
    defaults = {
        "data_zakazky": {}, "dynamic_items": {}, "vybrany_zakaznik": None, 
        "evidence_df": pd.DataFrame(), "velin_data": pd.DataFrame(), "initialized_v12": True
    }
    for k, v in defaults.items():
        if k not in st.session_state: st.session_state[k] = v

ensure_session()

# =====================================================================
# 🏠 LAYER 7: UI RENDERING (SIDEBAR & PAGES)
# =====================================================================
with st.sidebar:
    st.image("https://via.placeholder.com/150x80?text=HASI%C4%8C-SERVIS", use_container_width=True)
    st.title("🚒 Centrála Urbánek")
    st.divider()
    
    # 🏢 VÝBĚR ZÁKAZNÍKA (Vracíme na výsluní)
    st.subheader("🏢 Výběr partnera")
    df_p = safe_db_query("SELECT * FROM obchpartner ORDER BY firma")
    
    if df_p.empty:
        st.error("⚠️ Databáze partnerů je prázdná!")
        if st.button("⚙️ Provést první import dat", type="primary", use_container_width=True):
            res = service_import_data()
            st.success("Import hotov.")
            st.rerun()
    else:
        # Vytvoření seznamu pro selectbox
        df_p['display'] = df_p.apply(lambda r: f"{r.get('firma','')} | {r.get('ico','')}", axis=1)
        opts = ["-- Vyberte --"] + df_p['display'].tolist()
        
        # Určení výchozího indexu, pokud už je někdo vybrán
        curr_idx = 0
        vz = st.session_state["vybrany_zakaznik"]
        if vz:
            try: curr_idx = opts.index(f"{vz.get('firma','')} | {vz.get('ico','')}")
            except: curr_idx = 0
            
        sel_p = st.selectbox("Aktivní zakázka pro:", opts, index=curr_idx)
        if sel_p != "-- Vyberte --":
            ico_sel = sel_p.split(" | ")[1].strip()
            if not vz or vz.get("ico") != ico_sel:
                st.session_state["vybrany_zakaznik"] = df_p[df_p['ico'] == ico_sel].iloc[0].to_dict()
                st.session_state["evidence_df"] = pd.DataFrame() # Reset evidence pro novou firmu
                st.rerun()

    st.divider()
    menu = st.radio("Hlavní menu:", ["📝 Zpracování zakázky", "🗄️ Katalog & Sklad", "📊 Obchodní Velín (Audit)"])
    st.divider()
    
    # Sumář košíku v sidebaru
    tp = sum(v["q"]*v["p"] for v in st.session_state["data_zakazky"].values()) + sum(v["q"]*v["p"] for v in st.session_state["dynamic_items"].values())
    st.markdown(f"<div style='background:#f0f2f6;padding:10px;border-radius:5px;border-left:5px solid #ff4b4b'><b>🛒 K úhradě (bez DPH):</b><br/>{format_cena(tp)} Kč</div>", unsafe_allow_html=True)

# =====================================================================
# 📝 PAGE: ZPRACOVÁNÍ ZAKÁZKY
# =====================================================================
if menu == "📝 Zpracování zakázky":
    st.title("🛡️ Zpracování zakázky")
    
    # ZÁLOŽKY JSOU VIDĚT VŽDY (Prevence "rozmydlení")
    t1, t2, t3, t4 = st.tabs(["📋 1. Evidence & Robot", "💰 2. Fakturace", "🛒 3. Zboží", "🖨️ 4. Tisk"])
    
    with t1:
        if not st.session_state["vybrany_zakaznik"]:
            st.warning("👈 Nejprve vyberte zákazníka v levém panelu.")
        else:
            vz = st.session_state["vybrany_zakaznik"]
            st.subheader(f"Kontrola pro: {vz['firma']}")
            
            # Load data
            if st.session_state["evidence_df"].empty:
                df_e = safe_db_query("SELECT * FROM evidence_hp WHERE ico = ?", (vz['ico'],))
                if df_e.empty:
                    df_e = pd.DataFrame(columns=["druh","typ_hp","vyr_cislo","rok_vyr","mesic_vyr","tlak_rok","tlak_mesic","stav","duvod_nv","objekt","misto"])
                    for i in range(5): df_e.loc[i] = ["přenosný", "", "", None, None, None, None, "S", "", "", ""]
                st.session_state["evidence_df"] = df_e

            # Robot Audit
            df_audited, stats = run_expert_audit(st.session_state["evidence_df"], vz)
            if stats['chyb'] > 0: st.error(f"⚠️ Robot: Nalezeno {stats['chyb']} nesouladů s metodikou TÜV NORD.")
            else: st.success("✅ Robot: Data jsou v pořádku.")

            edited = st.data_editor(df_audited, num_rows="dynamic", use_container_width=1, key="main_editor",
                                   column_config={"audit_robot": st.column_config.TextColumn("Robotická kontrola", disabled=True),
                                                 "stav": st.column_config.SelectboxColumn("Stav", options=STAVY_HP),
                                                 "duvod_nv": st.column_config.SelectboxColumn("Kód NV", options=list(DUVODY_VYRAZENI.keys()))})
            
            if st.button("💾 Uložit a přepočítat do Fakturace", type="primary"):
                st.session_state["evidence_df"] = edited
                # Robotizace faktury
                df_c = edited[edited['typ_hp'].fillna("") != ""]
                s_c = len(df_c[df_c['stav'].isin(['S', 'S-nový'])])
                no_c = len(df_c[df_c['stav'].isin(['NO', 'NOPZ'])])
                nv_c = len(df_c[df_c['stav'] == 'NV'])
                st.session_state["q1_h1"] = float(s_c)
                st.session_state["q1_h2"] = float(no_c)
                st.session_state["q1_h3"] = float(nv_c)
                st.session_state["q1_s1"] = float(s_c+no_c+nv_c)
                st.success("✅ Faktura byla zaktualizována.")
                st.rerun()

    with t2:
        st.subheader("Položky Dodacího listu")
        def draw_billing_row(name, price, row_id):
            cols = st.columns([4, 2, 1, 1, 1, 1, 1])
            cols[0].write(name)
            p = cols[1].number_input("Cena", 0.0, value=float(price), key=f"p_{row_id}")
            q1 = cols[2].number_input("O1", 0.0, value=float(st.session_state.get(f"q1_{row_id}", 0.0)), key=f"q1_{row_id}")
            st.session_state["data_zakazky"][name] = {"q": q1, "p": p}
        
        draw_billing_row("Kontrola HP (shodný)", 29.4, "h1")
        draw_billing_row("Kontrola HP (neshodný - opravitelný)", 19.7, "h2")
        draw_billing_row("Kontrola HP (neopravitelný) + zneprovoznění", 23.5, "h3")
        draw_billing_row("Vyhodnocení kontroly (á 1ks HP)", 5.8, "s1")

    with t3:
        st.subheader("🛒 Prodej materiálu a zboží")
        db_z = get_items_from_db(["Zboží"])
        if db_z:
            z_names = [i['nazev'] for i in db_z]
            sel_z = st.selectbox("Vyberte ze skladu:", ["-- Vyberte --"] + z_names)
            if sel_z != "-- Vyberte --":
                item = next(i for i in db_z if i['nazev'] == sel_z)
                st.number_input("Cena", value=item['cena'], key="z_p")
                st.number_input("Množství", value=1.0, key="z_q")
                if st.button("➕ Přidat do košíku"):
                    st.session_state["dynamic_items"][sel_z] = {"q": st.session_state.z_q, "p": st.session_state.z_p}
                    st.rerun()
        
        for k, v in list(st.session_state["dynamic_items"].items()):
            c = st.columns([5, 2, 1])
            c[0].write(f"• {k}"); c[1].write(f"{v['q']} ks x {v['p']} Kč")
            if c[2].button("❌", key=f"del_{k}"): del st.session_state["dynamic_items"][k]; st.rerun()

    with t4:
        if st.session_state["vybrany_zakaznik"]:
            st.success(f"Vše připraveno pro: {st.session_state['vybrany_zakaznik']['firma']}")
            st.button("📄 Generovat Dodací list (PDF)")
            st.button("📄 Generovat Doklad o kontrole (PDF)")
            st.button("📄 Generovat Protokol o vyřazení (Legislativa)")
        else:
            st.error("Chybí výběr zákazníka.")

# =====================================================================
# 🗄️ PAGE: KATALOG & SKLAD
# =====================================================================
elif menu == "🗄️ Katalog & Sklad":
    st.title("🗄️ Správa databáze a skladu")
    t1, t2 = st.tabs(["📦 Pohled do DB", "⚙️ Synchronizace dat"])
    
    with t1:
        tbl = st.selectbox("Vyberte tabulku k zobrazení:", ["obchpartner", "cenik_hp", "cenik_zbozi"])
        df_v = safe_db_query(f"SELECT * FROM {tbl}")
        if df_v.empty: st.warning("⚠️ Tabulka je prázdná. Spusťte synchronizaci.")
        st.dataframe(df_v, use_container_width=1)
        
    with t2:
        st.info("💡 Robotický importér prohledá soubory v `data/ceniky/` a automaticky namapuje sloupce.")
        if st.button("🚀 Spustit inteligentní synchronizaci", type="primary"):
            msg = service_import_data()
            st.success("Synchronizace dokončena."); st.code(msg)

# =====================================================================
# 📊 PAGE: OBCHODNÍ VELÍN
# =====================================================================
elif menu == "📊 Obchodní Velín (Audit)":
    st.title("📊 Obchodní Velín (Audit)")
    up_f = st.file_uploader("Nahrajte Migrace_Centraly_Navrh.csv pro hloubkovou kontrolu:", type=['csv'])
    if up_f:
        try:
            st.session_state["velin_data"] = pd.read_csv(up_f, sep=';', encoding='utf-8-sig')
        except: st.error("Nepodařilo se přečíst CSV. Zkontrolujte formátování.")
    
    if not st.session_state["velin_data"].empty:
        df_v, v_stats = run_expert_audit(st.session_state["velin_data"])
        st.metric("Index integrity dat zakázky", f"{v_stats['score']:.1f} %")
        st.dataframe(df_v, use_container_width=1)

# =====================================================================
# 🏁 LAYER 8: FOOTER (Safe against NameError)
# =====================================================================
st.sidebar.divider()
st.sidebar.caption(f"© {datetime.now().year} {FIRMA_VLASTNI['název']}")
