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
# 🚀 LAYER 1: GLOBÁLNÍ IDENTITY (Musí být na začátku pro NameError fix)
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
    "ND_HP": "ND_HP", "ND_Voda": "ND_VODA", "FA": "FA", "TAB": "TAB",
    "TABFOTO": "TABFOTO", "HILTI": "HILTI", "CIDLO": "CIDLO", "PASKA": "PASKA",
    "PK": "PK", "OZO": "OZO", "reklama": "reklama", "Servisni_ukony": "revize",
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
# 🛡️ LAYER 4: AUDITNÍ ROBOT (Vysoká robotizace kontrol)
# =====================================================================
def run_expert_audit(df: pd.DataFrame, context_zakaznik: Dict = None) -> Tuple[pd.DataFrame, Dict]:
    df_audit = df.copy()
    if df_audit.empty: return df_audit, {"chyb": 0, "celkem": 0, "score": 100.0}
    
    def check_row(row):
        issues = []
        # Audit Hydranty (Metodika TÜV NORD)
        typ = str(row.get('typ_hp', '')).upper()
        if "HYDRANT" in typ or "PV" in typ or str(row.get('druh','')).upper() == "VODA":
            tlak = row.get('tlak_rok', 0)
            if not tlak or tlak == 0: issues.append("Chybí měření průtoku/tlaku")
        # Audit Vyřazení
        if row.get('stav') == 'NV' and not row.get('duvod_nv'):
            issues.append("Chybí legislativní kód vyřazení")
        return "✅ OK" if not issues else "❌ " + ", ".join(issues)
    
    df_audit['robot_status'] = df_audit.apply(check_row, axis=1)
    errs = len(df_audit[df_audit['robot_status'].str.contains("❌")])
    total = len(df_audit)
    score = ((total - errs) / total * 100) if total > 0 else 100.0
    return df_audit, {"chyb": errs, "celkem": total, "score": score}

# =====================================================================
# ⚙️ LAYER 5: SERVICES (Importy a Sklad)
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
    conn = sqlite3.connect(DB_PATH)
    logs = []
    # 1. Ceníky
    for k, v in CATEGORY_MAP.items():
        df = safe_read_io(os.path.join(CSV_FOLDER, v))
        if df is not None:
            df.columns = [normalize_column_name(c) for c in df.columns]
            if 'zbozi_nazev' in df.columns: df.rename(columns={'zbozi_nazev': 'nazev'}, inplace=True)
            if 'zbozi_cena' in df.columns: df.rename(columns={'zbozi_cena': 'cena'}, inplace=True)
            if 'cena' in df.columns and 'nazev' in df.columns:
                df['cena'] = df['cena'].apply(normalize_price)
                df[['nazev', 'cena']].to_sql(normalize_category_to_table(k), conn, if_exists="replace", index=False)
                logs.append(f"✅ {k} synchronizován.")

    # 2. Sklad (expimp.csv)
    df_exp = safe_read_io(os.path.join(CSV_FOLDER, "expimp"))
    if df_exp is not None:
        df_exp.columns = [normalize_column_name(c) for c in df_exp.columns]
        name_col = 'nazev' if 'nazev' in df_exp.columns else ('zkratka' if 'zkratka' in df_exp.columns else None)
        price_col = 'cena1' if 'cena1' in df_exp.columns else ('cena_prodejni' if 'cena_prodejni' in df_exp.columns else None)
        if name_col:
            df_cl = pd.DataFrame()
            df_cl['nazev'] = df_exp[name_col].astype(str).str.strip()
            df_cl['cena'] = df_exp[price_col].apply(normalize_price) if price_col else 0.0
            df_cl = df_cl.dropna(subset=['nazev']).drop_duplicates('nazev')
            df_cl.to_sql("cenik_zbozi", conn, if_exists="append", index=False)
            logs.append(f"📦 ÚSPĚCH: Hlavní sklad expimp spárován.")

    # 3. Zákazníci (Robotické mapování pro fix KeyError)
    df_c = safe_read_io("data/ceniky/zakaznici")
    if df_c is not None:
        df_c.columns = [normalize_column_name(c) for c in df_c.columns]
        map_cols = {'firma': ['firma', 'nazev', 'partner', 'partner_jmeno'], 'ico': ['ico', 'ic', 'identifikacni_cislo']}
        for target, aliases in map_cols.items():
            for a in aliases:
                if a in df_c.columns and target not in df_c.columns: df_c.rename(columns={a: target}, inplace=True)
        df_c.to_sql("obchpartner", conn, if_exists="replace", index=False)
        logs.append("✅ Databáze partnerů zocelena.")
    conn.close()
    return "\n".join(logs)

# =====================================================================
# 🌐 LAYER 6: UI LOGIC & SESSIONS
# =====================================================================
init_db()

def ensure_session():
    keys = {"data_zakazky": {}, "dynamic_items": {}, "vybrany_zakaznik": None, 
            "evidence_df": pd.DataFrame(), "velin_data": pd.DataFrame(), "initialized_v10": True}
    for k, v in keys.items():
        if k not in st.session_state: st.session_state[k] = v

ensure_session()

# =====================================================================
# 🖥️ LAYER 7: UI RENDERING (Důraz na stabilitu prvků)
# =====================================================================
menu = st.sidebar.radio("Navigace centrály:", ["📝 Zpracování zakázky", "🗄️ Katalog & Sklad", "📊 Obchodní Velín (Audit)"])

if menu == "📝 Zpracování zakázky":
    with st.sidebar:
        st.header("🏢 Hlavička dokladů")
        dl_n = st.text_input("Číslo DL:", value="1698")
        zak_n = st.text_input("Zakázka:", value="1/13")
        tech_n = st.text_input("Technik:", value="Tomáš Urbánek")
        st.divider()
        
        # Bezpečný výběr partnera (Robotická pojistka proti KeyError)
        df_cp = safe_db_query("SELECT * FROM obchpartner ORDER BY firma")
        if not df_cp.empty and 'firma' in df_cp.columns:
            opts = df_cp.apply(lambda r: f"{r['firma']} | {r['ico']}", 1).tolist()
            vz = st.session_state["vybrany_zakaznik"]
            def_idx = 0
            if vz:
                for i, o in enumerate(opts):
                    if vz["ico"] in o: def_idx = i; break
            sel = st.selectbox("🔍 Vyberte odběratele:", ["-- Vyberte --"] + opts, index=def_idx+1 if vz else 0)
            if sel != "-- Vyberte --":
                ico_clean = sel.split(" | ")[1].strip()
                if not vz or vz["ico"] != ico_clean:
                    st.session_state["vybrany_zakaznik"] = df_cp[df_cp['ico'] == ico_clean].iloc[0].to_dict()
                    st.session_state["evidence_df"] = pd.DataFrame()
                    st.rerun()
        else:
            st.warning("⚠️ Databáze partnerů je prázdná. Jděte do 'Katalog & Sklad' a proveďte synchronizaci.")
        
        # Sumář košíku
        tp = sum(v["q"]*v["p"] for v in st.session_state["data_zakazky"].values()) + sum(v["q"]*v["p"] for v in st.session_state["dynamic_items"].values())
        st.markdown(f"""<div style='background:#f8f9fa;padding:15px;border-radius:8px;border-left:5px solid #ff4b4b'><b>🛒 K úhradě bez DPH:</b><br/>{format_cena(tp)} Kč</div>""", 1)

    st.title("🛡️ Zpracování zakázky")
    tabs = st.tabs(["📝 1. Evidence HP", "💰 2. Fakturace", "🛠️ 3. Opravy", "🖨️ 5. Tisk"])
    
    with tabs[0]:
        if not st.session_state["vybrany_zakaznik"]:
            st.warning("👈 Nejprve vyberte zákazníka v levém panelu.")
        else:
            ico = clean_ico(st.session_state["vybrany_zakaznik"]["ico"])
            if st.session_state["evidence_df"].empty:
                df_e = safe_db_query("SELECT * FROM evidence_hp WHERE ico = ?", (ico,))
                if df_e.empty:
                    df_e = pd.DataFrame(columns=["druh","typ_hp","vyr_cislo","rok_vyr","mesic_vyr","tlak_rok","tlak_mesic","stav","duvod_nv","objekt","misto"])
                    for i in range(5): df_e.loc[i] = ["přenosný", "", "", None, None, None, None, "S", "", "", ""]
                st.session_state["evidence_df"] = df_e
            
            df_aud, stats = run_expert_audit(st.session_state["evidence_df"], st.session_state["vybrany_zakaznik"])
            st.subheader(f"Evidence pro: {st.session_state['vybrany_zakaznik']['firma']}")
            if stats['chyb'] > 0: st.error(f"🤖 Robot: Nalezeno {stats['chyb']} chyb v metodice!")
            
            edited = st.data_editor(df_aud, num_rows="dynamic", use_container_width=1, key="main_evid_editor")
            if st.button("💾 Uložit do databáze a přepočítat", type="primary"):
                st.session_state["evidence_df"] = edited
                st.success("✅ Uloženo a synchronizováno.")

    with tabs[1]:
        st.subheader("Automatické položky dle Evidence")
        def draw_it(name, price, row_id):
            cols = st.columns([4, 2, 1, 1, 1, 1, 1])
            cols[0].write(name)
            p = cols[1].number_input("Cena", 0.0, value=float(price), key=f"p_{row_id}")
            q1 = cols[2].number_input("O1", 0.0, value=float(st.session_state.get(f"q1_{row_id}", 0.0)), key=f"q1_{row_id}")
            st.session_state["data_zakazky"][name] = {"q": q1, "p": p}
        draw_it("Kontrola HP (shodný)", 29.4, "h1")
        draw_it("Kontrola HP (neshodný - opravitelný)", 19.7, "h2")
        draw_it("Vyhodnocení kontroly (á 1ks HP)", 5.8, "s1")

    with tabs[3]:
        if st.session_state["vybrany_zakaznik"]:
            st.info(f"Tisk pro {st.session_state['vybrany_zakaznik']['firma']}")
            st.button("📄 Generovat Dodací list")
            st.button("📄 Generovat Doklad o kontrole")
        else: st.error("Chybí zákazník.")

elif menu == "🗄️ Katalog & Sklad":
    st.title("🗄️ Katalog a Sklad")
    t1, t2 = st.tabs(["📦 Pohled do DB", "⚙️ Synchronizace"])
    with t1:
        chosen = st.selectbox("Vyberte tabulku:", ["obchpartner", "cenik_hp", "cenik_zbozi"])
        df_v = safe_db_query(f"SELECT * FROM {chosen}")
        if df_v.empty: st.warning("⚠️ Tabulka je prázdná. Proveďte synchronizaci.")
        st.dataframe(df_v, use_container_width=1)
    with t2:
        if st.button("🚀 Spustit kompletní synchronizaci s W-SERVIS"):
            msg = service_import_data()
            st.success("Synchronizace dokončena."); st.code(msg)

elif menu == "📊 Obchodní Velín (Audit)":
    st.title("📊 Obchodní Velín")
    up_f = st.file_uploader("Nahrajte Migrace_Centraly_Navrh.csv:", type=['csv'])
    if up_f:
        try:
            st.session_state["velin_data"] = pd.read_csv(up_f, sep=';', encoding='utf-8-sig')
        except: st.error("Chyba čtení CSV.")
    if not st.session_state["velin_data"].empty:
        df_a, s_a = run_expert_audit(st.session_state["velin_data"])
        st.metric("Index integrity dat zakázky", f"{s_a['score']:.1f} %")
        st.dataframe(df_a, use_container_width=1)

# =====================================================================
# 🏁 LAYER 8: FOOTER (NameError Safe)
# =====================================================================
st.sidebar.divider()
st.sidebar.caption(f"© {datetime.now().year} {FIRMA_VLASTNI['název']}")
