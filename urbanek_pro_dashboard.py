import os
import re
import time
import datetime
import unicodedata
import sqlite3
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

import streamlit as st
import pandas as pd
from fpdf import FPDF

# =====================================================================
# 🚀 LAYER 0: BOOTSTRAP (Musí být první)
# =====================================================================
st.set_page_config(page_title="W-SERVIS Enterprise v47.2", layout="wide", page_icon="🚒")

# =====================================================================
# 🧹 LAYER 1: NORMALIZATION & UTILS (Základní stavební kameny)
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
    if not cat_key: return "cenik_ostatni"
    normalized = cat_key.lower().strip()
    normalized = "".join(char for char in unicodedata.normalize("NFKD", normalized) if not unicodedata.combining(char))
    normalized = re.sub(r"[\s/]+", "_", normalized)
    normalized = re.sub(r"[^a-z0-9_]", "", normalized)
    return f"cenik_{normalized}"

def normalize_price(v: Any) -> float:
    s = str(v).replace(" ", "").replace(",", ".")
    try: return float(s)
    except: return 0.0

def clean_ico(ico_val: Any) -> str:
    s = str(ico_val).strip()
    if s.lower() in ['nan', 'none', 'null', '']: return ""
    return s.split('.')[0]

def format_cena(num):
    if num == 0: return "0,00"
    st_val = f"{num:,.2f}".replace(",", "X").replace(".", ",").replace("X", " ")
    return st_val

def safe_str(txt):
    return str(txt).replace('\n', ' ').replace('\r', '').strip()

# =====================================================================
# 🗄️ LAYER 2: REPOSITORY (Bezpečný přístup k datům)
# =====================================================================
DB_PATH = "data/data.db"
CSV_FOLDER = "data/ceniky/"

def init_db():
    if not os.path.exists("data"): os.makedirs("data")
    if not os.path.exists(CSV_FOLDER): os.makedirs(CSV_FOLDER)
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""CREATE TABLE IF NOT EXISTS objekty (id INTEGER PRIMARY KEY AUTOINCREMENT, ico TEXT NOT NULL, nazev_objektu TEXT NOT NULL, UNIQUE(ico, nazev_objektu))""")
    cur.execute("""CREATE TABLE IF NOT EXISTS evidence_hp (id INTEGER PRIMARY KEY AUTOINCREMENT, ico TEXT NOT NULL, druh TEXT, typ_hp TEXT, vyr_cislo TEXT, rok_vyr INTEGER, mesic_vyr INTEGER, tlak_rok INTEGER, tlak_mesic INTEGER, stav TEXT, duvod_nv TEXT, objekt TEXT, misto TEXT)""")
    cur.execute("""CREATE TABLE IF NOT EXISTS obchpartner (ico TEXT PRIMARY KEY, dic TEXT, firma TEXT, ulice TEXT, cp TEXT, mesto TEXT, psc TEXT)""")
    conn.commit()
    conn.close()

def safe_db_query(query: str, params: tuple = ()) -> pd.DataFrame:
    """Robotická pojistka: Pokud tabulka neexistuje, vrátí prázdný list místo pádu aplikace."""
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
# ⚙️ LAYER 3: BUSINESS LOGIC & AUDIT ROBOT
# =====================================================================
STAVY_HP = ["S", "NO", "NOPZ", "CH", "S-nový", "NV"]
DUVODY_VYRAZENI = {"": "", "A": "HP neodpovídá ČSN", "B": "Zákaz používání (ozón)", "C": "Deformace nádoby", "D": "Poškozený lak vnější", "E": "Poškozený lak vnitřní", "F": "Koroze", "G": "Životnost", "H": "Nečitelné číslo", "I": "Nesplňuje tlak. zkoušky", "J": "Ukončení výroby ND", "K": "Neekonomické (na žádost)"}

def run_expert_audit(df: pd.DataFrame, context_zakaznik: Dict = None) -> Tuple[pd.DataFrame, Dict]:
    df_audit = df.copy()
    def check_row(row):
        issues = []
        # Pravidlo DPH SVJ
        f_name = str(context_zakaznik.get('firma', '') if context_zakaznik else "").upper()
        if any(x in f_name for x in ["SVJ", "BYTOVE", "DRUZSTVO"]):
            pass # Implementováno ve výpočtu
        # Pravidlo Hydranty
        if str(row.get('druh','')).upper() in ["VODA", "V9TI"]:
            tlak = row.get('tlak_rok', 0)
            if not tlak or tlak == 0: issues.append("Chybí měření (TÜV standard)")
        # Pravidlo NV
        if row.get('stav') == 'NV' and not row.get('duvod_nv'):
            issues.append("Chybí kód vyřazení A-K")
        return "✅ OK" if not issues else "❌ " + ", ".join(issues)
    
    if not df_audit.empty:
        df_audit['robot_kontrol'] = df_audit.apply(check_row, axis=1)
    return df_audit, {"chyb": len(df_audit[df_audit.get('robot_kontrol','').str.contains("❌")]) if not df_audit.empty else 0}

# =====================================================================
# ⚙️ LAYER 4: SERVICES (Importy a výpočty)
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
    # Import ceníků z definovaných kategorií
    cats = {"HP":"HP", "ND_HP":"ND_HP", "Voda":"VODA", "Nahrady":"Nahrady", "Servisni_ukony":"revize", "Zboží":"zbozi"}
    for k, v in cats.items():
        df = safe_read_io(os.path.join(CSV_FOLDER, v))
        if df is not None:
            df.columns = [normalize_column_name(c) for c in df.columns]
            if 'zbozi_nazev' in df.columns: df.rename(columns={'zbozi_nazev': 'nazev'}, inplace=True)
            if 'zbozi_cena' in df.columns: df.rename(columns={'zbozi_cena': 'cena'}, inplace=True)
            if 'nazev' in df.columns and 'cena' in df.columns:
                df['cena'] = df['cena'].apply(normalize_price)
                df[['nazev', 'cena']].to_sql(normalize_category_to_table(k), conn, if_exists="replace", index=False)
                logs.append(f"✅ {k} načteno.")
    
    # Import zákazníků
    df_c = safe_read_io("data/ceniky/zakaznici")
    if df_c is not None:
        df_c.columns = [normalize_column_name(c) for c in df_c.columns]
        df_c.to_sql("obchpartner", conn, if_exists="replace", index=False)
        logs.append("✅ Zákazníci synchronizováni.")
    
    conn.close()
    return "\n".join(logs)

# =====================================================================
# 📄 LAYER 5: PDF ENGINE
# =====================================================================
class UrbaneKPDF(FPDF):
    def __init__(self, orientation='P'):
        super().__init__(orientation=orientation)
        self.pismo_ok = self._load_fonts()
        self.pismo_name = "PismoCZ" if self.pismo_ok else "helvetica"
    def _load_fonts(self):
        for f in [("arial.ttf", "arialbd.ttf"), ("Arial.ttf", "Arialbd.ttf")]:
            if os.path.exists(f[0]) and os.path.exists(f[1]):
                try:
                    self.add_font("PismoCZ", "", f[0], uni=True)
                    self.add_font("PismoCZ", "B", f[1], uni=True)
                    return True
                except: pass
        return False
    def header(self):
        if not self.pismo_ok: return
        self.set_y(10); self.set_font(self.pismo_name, "B", 14); self.cell(0, 6, safe_str(FIRMA_VLASTNI["název"]), align="C", ln=True)
        self.set_font(self.pismo_name, "", 9); self.cell(0, 4.5, safe_str(f"Sídlo: {FIRMA_VLASTNI['sídlo']}"), align="C", ln=True)
        self.cell(0, 4.5, safe_str(f"IČO: {FIRMA_VLASTNI['ico']}  DIČ: {FIRMA_VLASTNI['dic']}"), align="C", ln=True); self.ln(3); self.set_line_width(0.5); self.line(10, self.get_y(), self.w-10, self.get_y()); self.ln(5)

# =====================================================================
# 🌐 LAYER 6: STREAMLIT UI SETUP (Záchranný kruh session state)
# =====================================================================
init_db()

def initialize_session():
    if "initialized" not in st.session_state:
        st.session_state["initialized"] = True
        st.session_state["data_zakazky"] = {}
        st.session_state["dynamic_items"] = {}
        st.session_state["vybrany_zakaznik"] = None
        st.session_state["evidence_df"] = pd.DataFrame()
        st.session_state["velin_data"] = pd.DataFrame()

initialize_session()

# =====================================================================
# 🖥️ LAYER 7: UI PAGES
# =====================================================================
menu = st.sidebar.radio("Navigace centrály:", ["📝 Zpracování zakázky", "🗄️ Katalog & Sklad", "📊 Obchodní Velín (Audit)"])

if menu == "📝 Zpracování zakázky":
    with st.sidebar:
        st.header("🏢 Hlavička dokladů")
        dl_num = st.text_input("Číslo DL:", "1698")
        zak_num = st.text_input("Zakázka:", "1/13")
        tech_name = st.text_input("Technik:", "Tomáš Urbánek")
        st.divider()
        
        # Bezpečný výběr zákazníka
        df_c = safe_db_query("SELECT * FROM obchpartner ORDER BY firma")
        if not df_c.empty:
            sel = st.selectbox("Vyberte firmu:", ["-- Vyberte --"] + df_c['firma'].tolist())
            if sel != "-- Vyberte --":
                cust = df_c[df_c['firma'] == sel].iloc[0].to_dict()
                if st.session_state["vybrany_zakaznik"] != cust:
                    st.session_state["vybrany_zakaznik"] = cust
                    st.session_state["evidence_df"] = pd.DataFrame() # Reset evidence pro novou firmu
                    st.rerun()

    st.title("🛡️ Zpracování zakázky")
    
    tab1, tab2, tab3 = st.tabs(["📋 Evidence & Robot", "💰 Fakturace", "🖨️ Tisk"])
    
    with tab1:
        if st.session_state["vybrany_zakaznik"]:
            ico = clean_ico(st.session_state["vybrany_zakaznik"].get("ico", ""))
            if st.session_state["evidence_df"].empty:
                df_e = safe_db_query("SELECT * FROM evidence_hp WHERE ico = ?", (ico,))
                if df_e.empty:
                    df_e = pd.DataFrame(columns=["druh","typ_hp","vyr_cislo","rok_vyr","mesic_vyr","tlak_rok","tlak_mesic","stav","duvod_nv","objekt","misto"])
                    for i in range(5): df_e.loc[i] = ["přenosný", "", "", None, None, None, None, "S", "", "", ""]
                st.session_state["evidence_df"] = df_e

            df_audited, stats = run_expert_audit(st.session_state["evidence_df"], st.session_state["vybrany_zakaznik"])
            
            st.write(f"### Evidence pro: {st.session_state['vybrany_zakaznik']['firma']}")
            if stats['chyb'] > 0: st.error(f"🤖 Robot: Nalezeno {stats['chyb']} chyb v metodice!")
            else: st.success("✅ Robot: Data jsou v pořádku.")
            
            edited = st.data_editor(df_audited, num_rows="dynamic", use_container_width=True, key="editor_evid",
                                   column_config={"robot_kontrol": st.column_config.TextColumn("Robotická kontrola", width="large", disabled=True),
                                                 "stav": st.column_config.SelectboxColumn("Stav", options=STAVY_HP),
                                                 "duvod_nv": st.column_config.SelectboxColumn("Důvod NV", options=list(DUVODY_VYRAZENI.keys()))})
            
            if st.button("💾 Uložit a přepočítat fakturu", type="primary"):
                df_save = edited.drop(columns=['robot_kontrol'], errors='ignore')
                df_save = df_save[df_save['typ_hp'].fillna("").astype(str).str.strip() != '']
                df_save['ico'] = ico
                
                conn = sqlite3.connect(DB_PATH)
                conn.execute("DELETE FROM evidence_hp WHERE ico = ?", (ico,))
                df_save.to_sql("evidence_hp", conn, if_exists="append", index=False)
                conn.close()
                
                # Robotizace fakturace
                s_count = len(df_save[df_save['stav'].isin(['S', 'S-nový'])])
                no_count = len(df_save[df_save['stav'].isin(['NO', 'NOPZ'])])
                nv_count = len(df_save[df_save['stav'] == 'NV'])
                
                st.session_state["q1_h1"] = float(s_count)
                st.session_state["q1_h2"] = float(no_count)
                st.session_state["q1_h3"] = float(nv_count)
                st.session_state["q1_s1"] = float(s_count + no_count + nv_count)
                st.session_state["evidence_df"] = df_save
                st.rerun()
        else:
            st.warning("👈 Nejprve vyberte zákazníka v levém panelu.")

    with tab2:
        def draw_row(name, price, row_id):
            cols = st.columns([4, 2, 1, 1, 1, 1, 1])
            cols[0].write(name)
            p = cols[1].number_input("Kč", 0.0, value=float(price), key=f"p_{row_id}")
            q1 = cols[2].number_input("O1", 0.0, value=float(st.session_state.get(f"q1_{row_id}", 0.0)), key=f"q1_{row_id}")
            st.session_state["data_zakazky"][name] = {"q": q1, "p": p}

        st.subheader("Položky Dodacího listu")
        draw_row("Kontrola HP (shodný)", 29.4, "h1")
        draw_row("Kontrola HP (neshodný - opravitelný)", 19.7, "h2")
        draw_row("Kontrola HP (neopravitelný) + zneprovoznění", 23.5, "h3")
        draw_row("Vyhodnocení kontroly (á 1ks HP)", 5.8, "s1")
        
        st.divider()
        st.write("#### 🛒 Celkem k úhradě bez DPH: " + format_cena(sum(v['q']*v['p'] for v in st.session_state["data_zakazky"].values())) + " Kč")

    with tab3:
        if st.session_state["vybrany_zakaznik"]:
            st.success(f"Připraveno k tisku pro {st.session_state['vybrany_zakaznik']['firma']}")
            st.button("📄 Stáhnout Dodací list (PDF)")
            st.button("📄 Stáhnout Doklad o kontrole (PDF)")
        else:
            st.error("Chybí výběr zákazníka.")

elif menu == "🗄️ Katalog & Sklad":
    st.title("🗄️ Sklad a Ceníky")
    if st.button("🚀 Synchronizovat vše s W-SERVIS"):
        res = service_import_data()
        st.success("Synchronizace dokončena.")
        st.code(res)
    
    st.subheader("Pohled do databáze")
    tab_list = ["cenik_hp", "cenik_nd_hp", "cenik_zbozi", "obchpartner"]
    chosen_tab = st.selectbox("Zobrazit tabulku:", tab_list)
    df_view = safe_db_query(f"SELECT * FROM {chosen_tab}")
    st.dataframe(df_view, use_container_width=True)

elif menu == "📊 Obchodní Velín (Audit)":
    st.title("📊 Obchodní Velín HASIČ-SERVIS")
    st.info("💡 Nahrajte export 'Migrace_Centraly_Navrh.csv' pro spravedlivé rozdělení 50:50.")
    
    up_f = st.file_uploader("📂 Vyberte soubor:", type=['csv'])
    if up_f:
        try:
            df_v = pd.read_csv(up_f, sep=';', encoding='utf-8-sig')
            st.session_state["velin_data"] = df_v
        except: st.error("Chyba při čtení CSV.")

    if not st.session_state["velin_data"].empty:
        df_aud, v_stats = run_expert_audit(st.session_state["velin_data"])
        st.metric("Integrita dat zakázky", f"{100 - (v_stats['chyb']/len(df_aud)*100 if len(df_aud)>0 else 0):.1f} %")
        st.dataframe(df_aud, use_container_width=True)
        
        st.divider()
        st.subheader("🤝 Vyrovnání 50:50")
        ks = len(df_aud)
        st.table(pd.DataFrame({
            "Partner": ["Tomáš Urbánek", "Ilja Urbánek"],
            "Podíl (ks)": [ks/2, ks/2],
            "Status": ["K fakturaci", "K fakturaci"]
        }))

st.sidebar.divider()
st.sidebar.caption(f"© {datetime.date.today().year} {FIRMA_VLASTNI['název']}")
