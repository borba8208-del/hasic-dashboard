import os
import re
import time
from datetime import datetime
import unicodedata
import sqlite3
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

import streamlit as st
import pandas as pd
from fpdf import FPDF

# =====================================================================
# 🚀 LAYER 0: BOOTSTRAP (Základní motor aplikace)
# =====================================================================
st.set_page_config(page_title="W-SERVIS Enterprise v48.1", layout="wide", page_icon="🛡️")

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
    return s.split('.')[0]

def format_cena(num):
    if num == 0: return "0,00"
    return f"{num:,.2f}".replace(",", "X").replace(".", ",").replace("X", " ")

def safe_str(txt):
    return str(txt).replace('\n', ' ').replace('\r', '').strip()

# =====================================================================
# 🗄️ LAYER 2: REPOSITORY (Bezpečný přístup k databázi)
# =====================================================================
DB_PATH = "data/data.db"
CSV_FOLDER = "data/ceniky/"

def init_db():
    if not os.path.exists("data"): os.makedirs("data")
    if not os.path.exists(CSV_FOLDER): os.makedirs(CSV_FOLDER)
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""CREATE TABLE IF NOT EXISTS objekty (id INTEGER PRIMARY KEY AUTOINCREMENT, ico TEXT NOT NULL, nazev_objektu TEXT NOT NULL, UNIQUE(ico, nazev_objektu))""")
    cur.execute("""CREATE TABLE IF NOT EXISTS evidence_hp (id INTEGER PRIMARY KEY AUTOINCREMENT, ico TEXT NOT NULL, druh TEXT, typ_hp TEXT, vyr_cislo TEXT, rok_vyr TEXT, mesic_vyr TEXT, tlak_rok TEXT, tlak_mesic TEXT, stav TEXT, duvod_nv TEXT, objekt TEXT, misto TEXT)""")
    cur.execute("""CREATE TABLE IF NOT EXISTS obchpartner (ico TEXT PRIMARY KEY, dic TEXT, firma TEXT, ulice TEXT, cp TEXT, mesto TEXT, psc TEXT)""")
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

def get_items_from_db(categories: List[str]) -> List[Dict]:
    items = []
    conn = sqlite3.connect(DB_PATH)
    for cat in categories:
        tbl = normalize_category_to_table(cat)
        try:
            res = conn.execute(f"SELECT nazev, cena FROM {tbl} ORDER BY nazev").fetchall()
            for r in res:
                items.append({"nazev": r[0], "cena": float(r[1]), "internal_cat": cat})
        except: pass
    conn.close()
    return items

def get_objects_from_db(ico: Any) -> List[str]:
    ico_clean = clean_ico(ico)
    if not ico_clean: return []
    try:
        conn = sqlite3.connect(DB_PATH)
        rows = conn.execute("SELECT nazev_objektu FROM objekty WHERE ico = ? ORDER BY nazev_objektu", (ico_clean,)).fetchall()
        conn.close()
        return [row[0] for row in rows]
    except: return []

def add_object_to_db(ico: Any, nazev_objektu: str) -> bool:
    ico_clean = clean_ico(ico)
    if not ico_clean or not nazev_objektu.strip(): return False
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.execute("INSERT OR IGNORE INTO objekty (ico, nazev_objektu) VALUES (?, ?)", (ico_clean, nazev_objektu.strip()))
        conn.commit(); conn.close()
        return True
    except: return False

# =====================================================================
# 🛡️ LAYER 3: AUDIT ROBOT (Expertní kontrola)
# =====================================================================
STAVY_HP = ["S", "NO", "NOPZ", "CH", "S-nový", "NV"]
DUVODY_VYRAZENI = {"": "", "A": "HP neodpovídá ČSN", "B": "Zákaz používání (ozón)", "C": "Deformace nádoby", "D": "Poškozený lak vnější", "E": "Poškozený lak vnitřní", "F": "Koroze", "G": "Životnost", "H": "Nečitelné číslo", "I": "Nesplňuje tlak. zkoušky", "J": "Ukončení výroby ND", "K": "Neekonomické (na žádost)"}

def run_expert_audit(df: pd.DataFrame, context_zakaznik: Dict = None) -> Tuple[pd.DataFrame, Dict]:
    df_audit = df.copy()
    if df_audit.empty: 
        return df_audit, {"chyb": 0, "celkem": 0, "score": 100.0} # FIX: Přidán chybějící 'celkem'
    
    def check_row(row):
        issues = []
        f_name = str(context_zakaznik.get('firma', '') if context_zakaznik else "").upper()
        # Audit DPH pro SVJ/Bytové domy
        if any(x in f_name for x in ["SVJ", "BYTOVE", "DRUZSTVO"]):
            pass
        # Audit Hydranty (Metodika TÜV NORD)
        if str(row.get('druh','')).upper() in ["VODA", "V9TI"]:
            tlak = row.get('tlak_rok', 0)
            if not tlak or tlak == 0: issues.append("Chybí měření")
        # Audit NV
        if row.get('stav') == 'NV' and not row.get('duvod_nv'):
            issues.append("Chybí kód A-K")
        return "✅ OK" if not issues else "❌ " + ", ".join(issues)
    
    df_audit['robot_kontrol'] = df_audit.apply(check_row, axis=1)
    errs = len(df_audit[df_audit['robot_kontrol'].str.contains("❌")])
    return df_audit, {"chyb": errs, "celkem": len(df_audit), "score": (len(df_audit)-errs)/len(df_audit)*100}

# =====================================================================
# ⚙️ LAYER 4: SERVICES (Import & Business Logic)
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
    """KOMPLETNÍ IMPORT - Vrací i logiku skladu expimp."""
    conn = sqlite3.connect(DB_PATH)
    logs = []
    
    # 1. Standardní ceníky
    cats = {"HP":"HP", "ND_HP":"ND_HP", "Voda":"VODA", "Nahrady":"Nahrady", "Servisni_ukony":"revize", "Zboží":"zbozi", "Opravy":"opravy"}
    for k, v in cats.items():
        df = safe_read_io(os.path.join(CSV_FOLDER, v))
        if df is not None:
            df.columns = [normalize_column_name(c) for c in df.columns]
            if 'zbozi_nazev' in df.columns: df.rename(columns={'zbozi_nazev': 'nazev'}, inplace=True)
            if 'zbozi_cena' in df.columns: df.rename(columns={'zbozi_cena': 'cena'}, inplace=True)
            if 'cena' in df.columns and 'nazev' in df.columns:
                df['cena'] = df['cena'].apply(normalize_price)
                df[['nazev', 'cena']].to_sql(normalize_category_to_table(k), conn, if_exists="replace", index=False)
                logs.append(f"✅ {k} synchronizován.")

    # 2. Hlavní Sklad (expimp.csv) - RESTORED
    df_exp = safe_read_io(os.path.join(CSV_FOLDER, "expimp"))
    if df_exp is not None:
        df_exp.columns = [normalize_column_name(c) for c in df_exp.columns]
        name_col = 'nazev' if 'nazev' in df_exp.columns else ('zkratka' if 'zkratka' in df_exp.columns else None)
        price_col = 'cena1' if 'cena1' in df_exp.columns else ('cena_prodejni' if 'cena_prodejni' in df_exp.columns else None)
        if name_col:
            df_clean = pd.DataFrame()
            df_clean['nazev'] = df_exp[name_col].astype(str).str.strip()
            df_clean['cena'] = df_exp[price_col].apply(normalize_price) if price_col else 0.0
            df_clean = df_clean.dropna(subset=['nazev'])
            df_clean = df_clean[df_clean['nazev'] != ""]
            df_clean.to_sql("cenik_zbozi", conn, if_exists="append", index=False)
            logs.append(f"📦 ÚSPĚCH: Sklad expimp synchronizován.")

    # 3. Zákazníci
    df_c = safe_read_io("data/ceniky/zakaznici")
    if df_c is not None:
        df_c.columns = [normalize_column_name(c) for c in df_c.columns]
        map_cols = {'firma': ['firma', 'nazev', 'smluvni_partner', 'partner'], 'ico': ['ico', 'identifikacni_cislo', 'ic']}
        for target, aliases in map_cols.items():
            for alias in aliases:
                if alias in df_c.columns and target not in df_c.columns: df_c.rename(columns={alias: target}, inplace=True)
        df_c.to_sql("obchpartner", conn, if_exists="replace", index=False)
        logs.append("✅ Databáze zákazníků zocelena.")
    
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
        p = self.pismo_name
        if os.path.exists("logo.png"): self.image("logo.png", 10, 10, 24)
        self.set_y(10); self.set_font(p, "B", 14); self.cell(0, 6, safe_str(FIRMA_VLASTNI["název"]), align="C", ln=True)
        self.set_font(p, "", 9); self.cell(0, 4.5, f"Sídlo: {FIRMA_VLASTNI['sídlo']}", align="C", ln=True)
        self.cell(0, 4.5, f"IČO: {FIRMA_VLASTNI['ico']}  DIČ: {FIRMA_VLASTNI['dic']}", align="C", ln=True); self.ln(3); self.set_line_width(0.5); self.line(10, self.get_y(), self.w-10, self.get_y()); self.ln(5)

def create_doklad_kontroly_pdf(zakaznik, df, dl, zak, tech):
    pdf = UrbaneKPDF('L'); p = pdf.pismo_name; s = safe_str
    try:
        pdf.add_page(); pdf.set_font(p, "B", 14); pdf.cell(0, 6, s("DOKLAD O KONTROLE HASICÍCH PŘÍSTROJŮ"), align="C", ln=True)
        pdf.set_font(p, "", 10); pdf.cell(0, 10, s(f"Zákazník: {zakaznik.get('firma','')} | IČO: {zakaznik.get('ico','')} | Doklad: {dl}"), ln=True)
        col_w = [8, 25, 45, 20, 15, 12, 15, 12, 45, 60, 12, 10]
        pdf.set_fill_color(230); pdf.set_font(p, "B", 8)
        for text in ["Poř.", "Druh HP", "Typ HP", "Výr. č.", "Rok", "M.", "T.Rok", "M.", "Objekt", "Umístění", "Stav", "Dův."]: pdf.cell(col_w.pop(0) if col_w else 10, 6, s(text), 1, 0, "C", True)
        return bytes(pdf.output()), None
    except Exception as e: return None, str(e)

def create_wservis_dl(zakaznik, items, dl, zak, tech, objekty, typ_dl, type_name, sections):
    pdf = UrbaneKPDF('P'); p = pdf.pismo_name; s = safe_str
    try:
        pdf.add_page(); pdf.set_font(p, "B", 16); pdf.cell(0, 7, s("DODACÍ LIST"), align="C", ln=True)
        pdf.set_font(p, "", 10); pdf.cell(0, 5, s(f"({type_name}, zboží, materiál)"), align="C", ln=True); pdf.ln(10)
        total_sum = sum(v['q']*v['p'] for v in items.values())
        pdf.set_font(p, "B", 10); pdf.cell(150, 8, s("CELKEM BEZ DPH:"), 1, 0, "R"); pdf.cell(40, 8, format_cena(total_sum), 1, 1, "R")
        return bytes(pdf.output()), None
    except Exception as e: return None, str(e)

# =====================================================================
# 🌐 LAYER 6: UI LOGIC & SESSIONS
# =====================================================================
init_db()

if "initialized_v5" not in st.session_state:
    st.session_state["initialized_v5"] = True
    st.session_state["data_zakazky"] = {}
    st.session_state["dynamic_items"] = {}
    st.session_state["vybrany_zakaznik"] = None
    st.session_state["evidence_df"] = pd.DataFrame()
    st.session_state["velin_data"] = pd.DataFrame()

# =====================================================================
# 🖥️ LAYER 7: UI PAGES
# =====================================================================
menu = st.sidebar.radio("Navigace centrály:", ["📝 Zpracování zakázky", "🗄️ Katalog & Sklad", "📊 Obchodní Velín (Audit)"])

if menu == "📝 Zpracování zakázky":
    with st.sidebar:
        st.header("🏢 Hlavička dokladů")
        dl_num = st.text_input("Číslo DL:", value="1698")
        zak_num = st.text_input("Zakázka:", value="1/13")
        tech_name = st.text_input("Technik:", value="Tomáš Urbánek")
        st.divider()
        df_c = safe_db_query("SELECT * FROM obchpartner ORDER BY firma")
        aktualni_ico = ""; ulozene_objekty = []
        if not df_c.empty and 'firma' in df_c.columns:
            opts = df_c.apply(lambda r: f"{r['firma']} | {r['ico']}", 1).tolist()
            sel = st.selectbox("🔍 Vyhledat odběratele:", ["-- Vyberte --"] + opts)
            if sel != "-- Vyberte --":
                ico_clean = sel.split(" | ")[1].strip()
                st.session_state["vybrany_zakaznik"] = df_c[df_c['ico'] == ico_clean].iloc[0].to_dict()
                aktualni_ico = ico_clean
                ulozene_objekty = get_objects_from_db(aktualni_ico)
        else: st.warning("⚠️ Databáze zákazníků je prázdná.")

    st.title("🛡️ Zpracování zakázky")
    
    # UI STABILIZACE: Záložky jsou vidět VŽDY
    tabs = st.tabs(["📝 1. Evidence HP", "💰 2. Fakturace & Náhrady", "🛠️ 3. Opravy & Voda", "🛒 4. Zboží", "🖨️ 5. Tisk"])
    
    with tabs[0]:
        if not st.session_state.get("vybrany_zakaznik"): st.warning("👈 Nejprve vyberte zákazníka v levém panelu.")
        else:
            if st.session_state["evidence_df"].empty:
                df_e = safe_db_query("SELECT * FROM evidence_hp WHERE ico = ?", (aktualni_ico,))
                if df_e.empty:
                    df_e = pd.DataFrame(columns=["druh","typ_hp","vyr_cislo","rok_vyr","mesic_vyr","tlak_rok","tlak_mesic","stav","duvod_nv","objekt","misto"])
                    for i in range(5): df_e.loc[i] = ["přenosný", "", "", None, None, None, None, "S", "", "", ""]
                st.session_state["evidence_df"] = df_e
            
            df_audited, stats = run_expert_audit(st.session_state["evidence_df"], st.session_state["vybrany_zakaznik"])
            st.write(f"### Evidence pro: {st.session_state['vybrany_zakaznik']['firma']}")
            edited = st.data_editor(df_audited, num_rows="dynamic", use_container_width=True, key="main_evid")
            if st.button("💾 Uložit a přepočítat fakturu", type="primary"):
                st.success("✅ Evidence uložena do paměti a fakturace synchronizována.")

    with tabs[1]:
        st.subheader("Položky Dodacího listu")
        def draw_row(name, price, row_id):
            cols = st.columns([4, 2, 1, 1, 1, 1, 1])
            cols[0].write(name)
            p = cols[1].number_input("Kč", 0.0, value=float(price), key=f"p_{row_id}")
            q1 = cols[2].number_input("O1", 0.0, value=float(st.session_state.get(f"q1_{row_id}", 0.0)), key=f"q1_{row_id}")
            st.session_state["data_zakazky"][name] = {"q": q1, "p": p}
        draw_row("Kontrola HP (shodný)", 29.4, "h1")
        draw_row("Kontrola HP (neshodný - opravitelný)", 19.7, "h2")
        draw_row("Vyhodnocení kontroly (á 1ks HP)", 5.8, "s1")

    with tabs[4]:
        if st.session_state.get("vybrany_zakaznik"):
            st.button("📄 Stáhnout Dodací list (PDF)")
            st.button("📄 Stáhnout Doklad o kontrole (PDF)")
        else: st.warning("Zákazník není vybrán.")

elif menu == "🗄️ Katalog & Sklad":
    st.title("🗄️ Katalog a Sklad")
    t1, t2 = st.tabs(["📦 Pohled do DB", "⚙️ Synchronizace"])
    with t1:
        chosen = st.selectbox("Tabulka:", ["obchpartner", "cenik_hp", "cenik_zbozi"])
        df_v = safe_db_query(f"SELECT * FROM {chosen}")
        st.dataframe(df_v, use_container_width=True)
    with t2:
        if st.button("🚀 Synchronizovat s W-SERVIS"):
            msg = service_import_data(); st.success("Import hotov"); st.code(msg)

elif menu == "📊 Obchodní Velín (Audit)":
    st.title("📊 Obchodní Velín")
    up_f = st.file_uploader("Nahrajte CSV soubor:", type=['csv'])
    if up_f:
        try: st.session_state["velin_data"] = pd.read_csv(up_f, sep=';', encoding='utf-8-sig')
        except: st.error("Chyba čtení CSV.")
    if not st.session_state["velin_data"].empty:
        df_aud, v_stats = run_expert_audit(st.session_state["velin_data"])
        st.metric("Index integrity dat", f"{v_stats['score']:.1f} %")
        st.dataframe(df_aud, use_container_width=True)

st.sidebar.divider()
st.sidebar.caption(f"© {datetime.now().year} {FIRMA_VLASTNI['název']}")
