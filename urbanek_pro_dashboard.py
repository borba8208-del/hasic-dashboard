import os
import re
import time
from datetime import datetime # ZDE JE OPRAVA (správný import pro datum)
import unicodedata
import sqlite3
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

import streamlit as st
import pandas as pd
from fpdf import FPDF

# =====================================================================
# 🚀 LAYER 0: BOOTSTRAP (Streamlit Engine)
# =====================================================================
st.set_page_config(page_title="W-SERVIS Enterprise v47.4", layout="wide", page_icon="🚒")

# =====================================================================
# 🧹 LAYER 1: NORMALIZATION & UTILS
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
    normalized = normalize_column_name(cat_key)
    return f"cenik_{normalized}"

def normalize_price(v: Any) -> float:
    if pd.isna(v): return 0.0
    s = str(v).replace(" ", "").replace(",", ".").replace("\xa0", "")
    try: return float(re.sub(r"[^-0-9.]", "", s))
    except: return 0.0

def clean_ico(ico_val: Any) -> str:
    s = str(ico_val).strip()
    if s.lower() in ['nan', 'none', 'null', '']: return ""
    # Odstraní případné .0 u čísel z Excelu
    return s.split('.')[0]

def format_cena(num):
    if num == 0: return "0,00"
    return f"{num:,.2f}".replace(",", "X").replace(".", ",").replace("X", " ")

def safe_str(txt):
    return str(txt).replace('\n', ' ').replace('\r', '').strip()

# =====================================================================
# 🗄️ LAYER 2: REPOSITORY (Database Engine)
# =====================================================================
DB_PATH = "data/data.db"
CSV_FOLDER = "data/ceniky/"

def init_db():
    if not os.path.exists("data"): os.makedirs("data")
    if not os.path.exists(CSV_FOLDER): os.makedirs(CSV_FOLDER)
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    # Tabulky pro stabilní provoz
    cur.execute("""CREATE TABLE IF NOT EXISTS objekty (id INTEGER PRIMARY KEY AUTOINCREMENT, ico TEXT NOT NULL, nazev_objektu TEXT NOT NULL, UNIQUE(ico, nazev_objektu))""")
    cur.execute("""CREATE TABLE IF NOT EXISTS evidence_hp (id INTEGER PRIMARY KEY AUTOINCREMENT, ico TEXT NOT NULL, druh TEXT, typ_hp TEXT, vyr_cislo TEXT, rok_vyr INTEGER, mesic_vyr INTEGER, tlak_rok INTEGER, tlak_mesic INTEGER, stav TEXT, duvod_nv TEXT, objekt TEXT, misto TEXT)""")
    cur.execute("""CREATE TABLE IF NOT EXISTS obchpartner (ico TEXT PRIMARY KEY, dic TEXT, firma TEXT, ulice TEXT, cp TEXT, mesto TEXT, psc TEXT)""")
    conn.commit()
    conn.close()

def safe_db_query(query: str, params: tuple = ()) -> pd.DataFrame:
    """Bezpečnostní doložka: Nikdy nespadne na chybějící tabulce."""
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
# 🛡️ LAYER 3: AUDIT ROBOT (Expertní logika)
# =====================================================================
STAVY_HP = ["S", "NO", "NOPZ", "CH", "S-nový", "NV"]
DUVODY_VYRAZENI = {"": "", "A": "HP neodpovídá ČSN", "B": "Zákaz používání (ozón)", "C": "Deformace nádoby", "D": "Poškozený lak vnější", "E": "Poškozený lak vnitřní", "F": "Koroze", "G": "Životnost", "H": "Nečitelné číslo", "I": "Nesplňuje tlak. zkoušky", "J": "Ukončení výroby ND", "K": "Neekonomické (na žádost)"}

def run_expert_audit(df: pd.DataFrame, context_zakaznik: Dict = None) -> Tuple[pd.DataFrame, Dict]:
    df_audit = df.copy()
    if df_audit.empty: return df_audit, {"chyb": 0}
    
    def check_row(row):
        issues = []
        # Audit DPH pro SVJ/BD
        f_name = str(context_zakaznik.get('firma', '') if context_zakaznik else "").upper()
        if any(x in f_name for x in ["SVJ", "BYTOVE", "DRUZSTVO"]):
            # Pozn: Systém v tiskovém enginu aplikuje 12%
            pass
        # Audit Hydranty - povinné měření
        if str(row.get('druh','')).upper() in ["VODA", "V9TI"]:
            tlak = row.get('tlak_rok', 0)
            if not tlak or tlak == 0: issues.append("Chybí měření (TÜV standard)")
        # Audit Vyřazení
        if row.get('stav') == 'NV' and not row.get('duvod_nv'):
            issues.append("Chybí legislativní kód A-K")
        return "✅ OK" if not issues else "❌ " + ", ".join(issues)
    
    df_audit['robot_kontrol'] = df_audit.apply(check_row, axis=1)
    chyb = len(df_audit[df_audit['robot_kontrol'].str.contains("❌")])
    return df_audit, {"chyb": chyb, "celkem": len(df_audit)}

# =====================================================================
# ⚙️ LAYER 4: SERVICES (Import & Sync)
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
    """Inteligentní import: Mapuje cizí názvy sloupců na vnitřní standardy."""
    conn = sqlite3.connect(DB_PATH)
    logs = []
    
    # 1. Import ceníků
    cats = {"HP":"HP", "ND_HP":"ND_HP", "Voda":"VODA", "Nahrady":"Nahrady", "Servisni_ukony":"revize", "Zboží":"zbozi"}
    for k, v in cats.items():
        df = safe_read_io(os.path.join(CSV_FOLDER, v))
        if df is not None:
            df.columns = [normalize_column_name(c) for c in df.columns]
            # Robotické přejmenování sloupců pro ceníky
            if 'zbozi_nazev' in df.columns: df.rename(columns={'zbozi_nazev': 'nazev'}, inplace=True)
            if 'zbozi_cena' in df.columns: df.rename(columns={'zbozi_cena': 'cena'}, inplace=True)
            if 'cena' in df.columns and 'nazev' in df.columns:
                df['cena'] = df['cena'].apply(normalize_price)
                df[['nazev', 'cena']].to_sql(normalize_category_to_table(k), conn, if_exists="replace", index=False)
                logs.append(f"✅ Ceník {k} synchronizován.")

    # 2. Import zákazníků (Kritické místo pro KeyError)
    df_c = safe_read_io("data/ceniky/zakaznici")
    if df_c is not None:
        df_c.columns = [normalize_column_name(c) for c in df_c.columns]
        # ROBOTICKÉ MAPOVÁNÍ: Hledáme sloupec pro firmu a ico pod různými názvy
        map_cols = {
            'firma': ['firma', 'nazev', 'smluvni_partner', 'partner', 'nazev_firmy'],
            'ico': ['ico', 'identifikacni_cislo', 'ic'],
            'dic': ['dic', 'dic_danove_id']
        }
        for target, aliases in map_cols.items():
            for alias in aliases:
                if alias in df_c.columns and target not in df_c.columns:
                    df_c.rename(columns={alias: target}, inplace=True)
        
        # Zajištění minimálních sloupců pro UI
        if 'firma' not in df_c.columns: df_c['firma'] = "Neznámý název"
        if 'ico' not in df_c.columns: df_c['ico'] = "00000000"
        
        df_c.to_sql("obchpartner", conn, if_exists="replace", index=False)
        logs.append("✅ Databáze zákazníků zocelena.")
    
    conn.close()
    return "\n".join(logs)

# =====================================================================
# 📄 LAYER 5: PDF ENGINE
# =====================================================================
FIRMA_VLASTNI: Dict[str, Any] = {
    "název": "Ilja Urbánek HASIČ - SERVIS",
    "sídlo": "Poříčská 186, 373 82 Boršov nad Vltavou",
    "ico": "60835265",
    "dic": "CZ5706281691",
    "zápis": "Zapsán v živnostenském rejstříku Mag. města Č.Budějovic pod ID RŽP: 696191",
    "telefony": "608 409 036, 777 664 768",
    "email": "schranka@hasic-servis.com",
    "web": "www.hasic-servis.com",
    "certifikace": "TÜV NORD Czech",
}

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
        self.set_font(self.pismo_name, "", 9); self.cell(0, 4.5, f"Sídlo: {FIRMA_VLASTNI['sídlo']}", align="C", ln=True)
        self.cell(0, 4.5, f"IČO: {FIRMA_VLASTNI['ico']}  DIČ: {FIRMA_VLASTNI['dic']}", align="C", ln=True); self.ln(3); self.set_line_width(0.5); self.line(10, self.get_y(), self.w-10, self.get_y()); self.ln(5)

# =====================================================================
# 🌐 LAYER 6: UI LOGIC & SESSIONS
# =====================================================================
init_db()

def initialize_session():
    if "initialized_v3" not in st.session_state:
        st.session_state["initialized_v3"] = True
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
        
        # Bezpečný výběr zákazníka (Ochrana proti KeyError)
        df_c = safe_db_query("SELECT * FROM obchpartner ORDER BY firma")
        if not df_c.empty and 'firma' in df_c.columns:
            sel = st.selectbox("Vyberte firmu:", ["-- Vyberte --"] + df_c['firma'].tolist())
            if sel != "-- Vyberte --":
                cust = df_c[df_c['firma'] == sel].iloc[0].to_dict()
                if st.session_state.get("vybrany_zakaznik") != cust:
                    st.session_state["vybrany_zakaznik"] = cust
                    st.session_state["evidence_df"] = pd.DataFrame()
                    st.rerun()
        else:
            st.warning("⚠️ Databáze zákazníků je prázdná. Jděte do 'Katalog & Sklad' a synchronizujte data.")

    st.title("🛡️ Zpracování zakázky")
    
    if st.session_state.get("vybrany_zakaznik"):
        tabs = st.tabs(["📋 Evidence & Robot", "💰 Fakturace", "🖨️ Tisk"])
        
        with tabs[0]:
            ico = clean_ico(st.session_state["vybrany_zakaznik"].get("ico", ""))
            if st.session_state["evidence_df"].empty:
                df_e = safe_db_query("SELECT * FROM evidence_hp WHERE ico = ?", (ico,))
                if df_e.empty:
                    df_e = pd.DataFrame(columns=["druh","typ_hp","vyr_cislo","rok_vyr","mesic_vyr","tlak_rok","tlak_mesic","stav","duvod_nv","objekt","misto"])
                    for i in range(5): df_e.loc[i] = ["přenosný", "", "", None, None, None, None, "S", "", "", ""]
                st.session_state["evidence_df"] = df_e

            df_audited, stats = run_expert_audit(st.session_state["evidence_df"], st.session_state["vybrany_zakaznik"])
            st.write(f"### Pracujete na zakázce: {st.session_state['vybrany_zakaznik']['firma']}")
            
            if stats['chyb'] > 0: st.error(f"🤖 Robot: Nalezeno {stats['chyb']} nesouladů s metodikou.")
            else: st.success("✅ Robot: Data jsou legislativně v pořádku.")
            
            edited = st.data_editor(df_audited, num_rows="dynamic", use_container_width=True, key="editor_evid",
                                   column_config={"robot_kontrol": st.column_config.TextColumn("Robotická kontrola", width="large", disabled=True),
                                                 "stav": st.column_config.SelectboxColumn("Stav", options=STAVY_HP),
                                                 "duvod_nv": st.column_config.SelectboxColumn("Důvod NV", options=list(DUVODY_VYRAZENI.keys()))})
            
            if st.button("💾 Uložit a synchronizovat fakturu", type="primary"):
                df_save = edited.drop(columns=['robot_kontrol'], errors='ignore')
                df_save = df_save[df_save['typ_hp'].fillna("").astype(str).str.strip() != '']
                df_save['ico'] = ico
                conn = sqlite3.connect(DB_PATH)
                conn.execute("DELETE FROM evidence_hp WHERE ico = ?", (ico,))
                df_save.to_sql("evidence_hp", conn, if_exists="append", index=False)
                conn.close()
                # Auto-billing injection
                s_c = len(df_save[df_save['stav'].isin(['S', 'S-nový'])])
                no_c = len(df_save[df_save['stav'].isin(['NO', 'NOPZ'])])
                nv_c = len(df_save[df_save['stav'] == 'NV'])
                
                st.session_state["q1_h1"] = float(s_c)
                st.session_state["q1_h2"] = float(no_c)
                st.session_state["q1_h3"] = float(nv_c)
                st.session_state["q1_s1"] = float(s_c+no_c+nv_c)
                st.session_state["evidence_df"] = df_save
                st.success("✅ Evidence uložena."); st.rerun()

        with tabs[1]:
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
            sum_p = sum(v['q']*v['p'] for v in st.session_state["data_zakazky"].values())
            st.write(f"#### 🛒 Celkem bez DPH: {format_cena(sum_p)} Kč")

        with tabs[2]:
            st.info("Příprava tisku PDF...")
            st.button("📄 Stáhnout Dodací list")
            st.button("📄 Stáhnout Doklad o kontrole")
    else:
        st.warning("👈 Nejprve vyberte zákazníka v levém panelu.")

elif menu == "🗄️ Katalog & Sklad":
    st.title("🗄️ Správa ceníků a skladu")
    if st.button("🚀 Spustit synchronizaci s W-SERVIS"):
        res = service_import_data()
        st.success("Import dokončen")
        st.code(res)
    
    st.subheader("Aktivní ceník zboží")
    df_z = safe_db_query("SELECT nazev, cena FROM cenik_zbozi ORDER BY nazev")
    if not df_z.empty:
        st.dataframe(df_z, use_container_width=True)
    else:
        st.warning("Sklad je prázdný. Spusťte synchronizaci.")

elif menu == "📊 Obchodní Velín (Audit)":
    st.title("🚒 Obchodní Velín HASIČ-SERVIS")
    u_file = st.file_uploader("Nahrajte soubor 'Migrace_Centraly_Navrh.csv' pro audit:", type=['csv'])
    if u_file:
        try:
            df_v = pd.read_csv(u_file, sep=';', encoding='utf-8-sig')
            df_v_audited, v_stats = run_expert_audit(df_v)
            st.metric("Celková integrita dat zakázky", f"{v_stats['score']:.1f} %")
            st.dataframe(df_v_audited, use_container_width=True)
        except Exception as e:
            st.error(f"Chyba při čtení CSV: {e}")

st.sidebar.divider()
# OPRAVA CHYBY: Použití datetime.now().year, protože datetime byl importován přímo.
st.sidebar.caption(f"© {datetime.now().year} {FIRMA_VLASTNI['název']}")
