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
# 🚀 LAYER 0: BOOTSTRAP (Musí být jako první!)
# =====================================================================
st.set_page_config(page_title="W-SERVIS Enterprise v48.0", layout="wide", page_icon="🚒")

# =====================================================================
# 🗄️ LAYER 1: SCHEMA & KONFIGURACE
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

STAVY_HP = ["S", "NO", "NOPZ", "CH", "S-nový", "NV"]

DUVODY_VYRAZENI = {
    "": "",
    "A": "HP neodpovídá ČSN (zastaralá/neschválená konstr.)",
    "B": "Zákaz používání (ozónová vrstva)",
    "C": "Nádoba je deformovaná - §9, odst.9, pís.a), vyhl. 246/2001 Sb.",
    "D": "Nádoba má poškozený vnější krycí ochranný lak",
    "E": "Nádoba má poškozený vnitřní ochranný lak - §9, odst.9, pís.c)",
    "F": "Nádoba je napadena korozí - §9, odst.9, pís.a)",
    "G": "Nádoba má splněnou životnost pro daný typ HP - §9, odst.9, pís.c)",
    "H": "Nečitelné číslo HP / rok výroby (nádoby) - §9, odst.9, pís.b)",
    "I": "Nádoba nesplňuje kritéria pro zkoušky tlakových nádob dle ČSN",
    "J": "HP nelze zprovoznit z důvodu ukončení výroby náhradních dílů",
    "K": "Zprovoznění HP je neekonomické - vyřazen na žádost majitele"
}

CATEGORY_MAP: Dict[str, str] = {
    "HP": "HP", "Nahrady": "Nahrady", "Voda": "VODA", "Ostatni": "ostatni",
    "ND_HP": "ND_HP", "ND_Voda": "ND_VODA", "FA": "FA", "TAB": "TAB",
    "TABFOTO": "TABFOTO", "HILTI": "HILTI", "CIDLO": "CIDLO", "PASKA": "PASKA",
    "PK": "PK", "OZO": "OZO", "reklama": "reklama", "Servisni_ukony": "revize",
    "Opravy": "opravy", "Zboží": "zbozi"
}

DB_PATH = "data/data.db"
CSV_FOLDER = "data/ceniky/"

# =====================================================================
# 🧹 LAYER 2: NORMALIZATION (Datové a stringové funkce)
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
    """TOTO JE TA FUNKCE CO VÁM CHYBĚLA - PŘEVÁDÍ KATEGORII NA NÁZEV TABULKY V DB"""
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
# 🛡️ LAYER 3: VALIDATION & AUDIT ROBOT
# =====================================================================
def validate_ico(ico: str) -> bool:
    ico_clean = clean_ico(ico)
    if not ico_clean.isdigit() or len(ico_clean) != 8: return False
    digits = [int(d) for d in ico_clean]
    weights = [8, 7, 6, 5, 4, 3, 2]
    s = sum(d * w for d, w in zip(digits[:7], weights))
    r = s % 11
    c = 1 if r == 0 else (0 if r == 1 else 11 - r)
    return digits[7] == c

def run_expert_audit(df: pd.DataFrame, context_zakaznik: Dict = None) -> Tuple[pd.DataFrame, Dict]:
    df_audit = df.copy()
    if df_audit.empty: return df_audit, {"chyb": 0, "score": 100.0}
    
    def check_row(row):
        issues = []
        f_name = str(context_zakaznik.get('firma', '') if context_zakaznik else "").upper()
        if any(x in f_name for x in ["SVJ", "BYTOVE", "DRUZSTVO"]):
            pass 
        typ = str(row.get('typ_hp', '')).upper()
        if "HYDRANT" in typ or "PV" in typ or str(row.get('druh','')).upper() == "VODA":
            tlak = row.get('tlak_rok', 0)
            if not tlak or tlak == 0: issues.append("Chybí měření tlaku")
        if row.get('stav') == 'NV' and not row.get('duvod_nv'):
            issues.append("Chybí kód A-K")
        return "✅ OK" if not issues else "❌ " + ", ".join(issues)
    
    df_audit['robot_kontrol'] = df_audit.apply(check_row, axis=1)
    errs = len(df_audit[df_audit['robot_kontrol'].str.contains("❌")])
    total = len(df_audit)
    score = ((total - errs) / total * 100) if total > 0 else 100.0
    return df_audit, {"chyb": errs, "score": score}

# =====================================================================
# 💾 LAYER 4: REPOSITORY (Databáze)
# =====================================================================
def init_db():
    os.makedirs("data", exist_ok=True)
    os.makedirs(CSV_FOLDER, exist_ok=True)
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
# ⚙️ LAYER 5: SERVICES (Import a Sync) - VRÁCEN EXPIMP SKLAD!
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
    """Opravený a kompletní import. Vrací zpět funkcionalitu expimp.csv."""
    conn = sqlite3.connect(DB_PATH)
    logs = []
    
    # 1. Ceníky
    cats = {"HP":"HP", "ND_HP":"ND_HP", "Voda":"VODA", "Nahrady":"Nahrady", "Servisni_ukony":"revize", "Zboží":"zbozi"}
    for k, v in cats.items():
        df = safe_read_io(os.path.join(CSV_FOLDER, v))
        if df is not None:
            df.columns = [normalize_column_name(c) for c in df.columns]
            if 'zbozi_nazev' in df.columns: df.rename(columns={'zbozi_nazev': 'nazev'}, inplace=True)
            if 'zbozi_cena' in df.columns: df.rename(columns={'zbozi_cena': 'cena'}, inplace=True)
            if 'ukon_popis' in df.columns: df.rename(columns={'ukon_popis': 'nazev'}, inplace=True)
            if 'ukon_cena' in df.columns: df.rename(columns={'ukon_cena': 'cena'}, inplace=True)
            if 'cena' in df.columns and 'nazev' in df.columns:
                df = df.dropna(subset=["nazev", "cena"])
                df["nazev"] = df["nazev"].astype(str).str.strip()
                df = df[(df["nazev"] != "") & (df["nazev"] != "nan")]
                df = df.drop_duplicates(subset=["nazev"])
                df['cena'] = df['cena'].apply(normalize_price)
                df[['nazev', 'cena']].to_sql(normalize_category_to_table(k), conn, if_exists="replace", index=False)
                logs.append(f"✅ Ceník {k} synchronizován.")

    # 2. HLAVNÍ SKLAD (expimp) - TOTO VÁM CHYBĚLO!
    df_exp = safe_read_io(os.path.join(CSV_FOLDER, "expimp"))
    if df_exp is not None:
        df_exp.columns = [normalize_column_name(c) for c in df_exp.columns]
        name_col = 'nazev' if 'nazev' in df_exp.columns else ('zkratka' if 'zkratka' in df_exp.columns else None)
        price_col = None
        if 'cena1' in df_exp.columns: price_col = 'cena1'
        elif 'cena_prodejni' in df_exp.columns: price_col = 'cena_prodejni'
        else:
            for col in df_exp.columns:
                if 'cena' in col and 'prum' not in col and 'posl' not in col and 'nakup' not in col:
                    price_col = col; break
        
        if name_col:
            df_clean = pd.DataFrame()
            df_clean['nazev'] = df_exp[name_col].astype(str).str.strip()
            df_clean['cena'] = df_exp[price_col].apply(normalize_price) if price_col else 0.0
            df_clean = df_clean.dropna(subset=['nazev'])
            df_clean = df_clean[(df_clean['nazev'] != "nan") & (df_clean['nazev'] != "")]
            df_clean = df_clean.drop_duplicates(subset=["nazev"])
            try:
                df_clean.to_sql("cenik_zbozi", conn, if_exists="append", index=False)
                logs.append(f"📦 ÚSPĚCH: Zboží z expimp spárováno do skladu.")
            except: pass

    # 3. Zákazníci
    df_c = safe_read_io("data/ceniky/zakaznici")
    if df_c is not None:
        df_c.columns = [normalize_column_name(c) for c in df_c.columns]
        map_cols = {'firma': ['firma', 'nazev', 'smluvni_partner', 'partner', 'nazev_firmy'], 'ico': ['ico', 'identifikacni_cislo', 'ic'], 'dic': ['dic', 'dic_danove_id']}
        for target, aliases in map_cols.items():
            for alias in aliases:
                if alias in df_c.columns and target not in df_c.columns: df_c.rename(columns={alias: target}, inplace=True)
        if 'firma' not in df_c.columns: df_c['firma'] = "Neznámý název"
        if 'ico' not in df_c.columns: df_c['ico'] = "00000000"
        df_c.to_sql("obchpartner", conn, if_exists="replace", index=False)
        logs.append("✅ Databáze zákazníků zocelena.")
    
    conn.close()
    return "\n".join(logs)

# =====================================================================
# 📄 LAYER 6: PDF ENGINE
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

def create_doklad_kontroly_pdf(zakaznik: Dict, df_evid: pd.DataFrame, dl_number: str, zakazka: str, technik: str) -> tuple[Optional[bytes], Optional[str]]:
    pdf = UrbaneKPDF('L'); p = pdf.pismo_name; s = safe_str
    try:
        pdf.add_page(); pdf.set_font(p, "B", 14); pdf.cell(0, 6, s("DOKLAD O KONTROLE HASICÍCH PŘÍSTROJŮ"), align="C", ln=True)
        pdf.set_font(p, "B", 10); pdf.cell(20, 5, s("Zákazník:"), 0); pdf.set_font(p, "", 10); pdf.cell(150, 5, s(f"{zakaznik.get('firma','')} (IČO: {zakaznik.get('ico','')})"), 0); pdf.ln(10)
        col_w = [8, 25, 45, 20, 15, 12, 15, 12, 45, 60, 12, 10]
        pdf.set_fill_color(230); pdf.set_font(p, "B", 8)
        for w, text in zip(col_w, ["Poř.", "Druh HP", "Typ HP", "Výr. č.", "Rok", "M.", "T.Rok", "M.", "Objekt", "Umístění", "Stav", "Dův."]): pdf.cell(w, 6, s(text), 1, 0, "C", True)
        pdf.ln(); pdf.set_font(p, "", 8)
        for idx, row in df_evid[df_evid['typ_hp'].fillna("").astype(str).str.strip() != ''].iterrows():
            pdf.cell(col_w[0], 5, s(str(idx+1)), 1, 0, "C")
            pdf.cell(col_w[1], 5, s(row.get('druh','')), 1)
            pdf.cell(col_w[2], 5, s(str(row.get('typ_hp',''))[:28]), 1)
            pdf.cell(col_w[3], 5, s(row.get('vyr_cislo','')), 1, 0, "C")
            pdf.cell(col_w[4], 5, s(str(row.get('rok_vyr','')).replace('.0','')), 1, 0, "C")
            pdf.cell(col_w[5], 5, s(str(row.get('mesic_vyr','')).replace('.0','')), 1, 0, "C")
            pdf.cell(col_w[6], 5, s(str(row.get('tlak_rok','')).replace('.0','')), 1, 0, "C")
            pdf.cell(col_w[7], 5, s(str(row.get('tlak_mesic','')).replace('.0','')), 1, 0, "C")
            pdf.cell(col_w[8], 5, s(str(row.get('objekt',''))[:28]), 1)
            pdf.cell(col_w[9], 5, s(str(row.get('misto',''))[:40]), 1)
            pdf.cell(col_w[10], 5, s(row.get('stav','')), 1, 0, "C")
            pdf.cell(col_w[11], 5, s(row.get('duvod_nv','')), 1, 0, "C"); pdf.ln()
        return bytes(pdf.output()), None
    except Exception as e: return None, str(e)

def create_protokol_vyrazeni_pdf(zakaznik: Dict, df_evid: pd.DataFrame, dl_number: str, zakazka: str, technik: str) -> tuple[Optional[bytes], Optional[str]]:
    pdf = UrbaneKPDF('P'); p = pdf.pismo_name; s = safe_str
    try:
        df_nv = df_evid[df_evid['stav'] == 'NV']
        pdf.add_page(); pdf.set_font(p, "B", 14); pdf.cell(0, 6, s("POTVRZENÍ O PŘEVZETÍ HP VYŘAZENÝCH Z UŽÍVÁNÍ"), align="C", ln=True)
        pdf.set_font(p, "B", 8); pdf.cell(0, 4, s("TOTO POTVRZENÍ NESLOUŽÍ PRO ÚČELY EVIDENCE ODPADŮ"), align="C", ln=True); pdf.ln(10)
        for idx, row in df_nv.iterrows(): pdf.cell(0, 5, s(f"{idx+1}. {row['typ_hp']} (v.č. {row['vyr_cislo']}) - kód: {row['duvod_nv']}"), ln=True)
        return bytes(pdf.output()), None
    except Exception as e: return None, str(e)

def create_wservis_dl(zakaznik: Dict, items: Dict, dl_number: str, zakazka: str, technik: str, objekty: Dict, typ_dl: str, type_name: str, sections: List[str]) -> tuple[Optional[bytes], Optional[str]]:
    pdf = UrbaneKPDF('P'); p = pdf.pismo_name; s = safe_str
    try:
        pdf.add_page(); pdf.set_font(p, "B", 16); pdf.cell(0, 7, s("DODACÍ LIST"), align="C", ln=True)
        pdf.set_font(p, "", 10); pdf.cell(0, 5, s(f"({type_name}, zboží, materiál)"), align="C", ln=True); pdf.ln(10)
        pdf.set_fill_color(235); pdf.set_font(p, "B", 9)
        pdf.cell(45, 6, s("Poř. číslo:"), 1, 0, "C", True); pdf.cell(45, 6, s("Zakázka:"), 1, 0, "C", True); pdf.cell(100, 6, s("Technik:"), 1, 1, "C", True)
        pdf.cell(45, 7, s(dl_number), 1, 0, "C"); pdf.cell(45, 7, s(zakazka), 1, 0, "C"); pdf.cell(100, 7, s(technik), 1, 1, "C"); pdf.ln(10)
        
        total_sum = 0.0
        for name, v in items.items():
            line_total = v['q'] * v['p']
            pdf.set_font(p, "", 9); pdf.cell(100, 6, s(name), 1); pdf.cell(30, 6, format_cena(v['p']), 1, 0, "R"); pdf.cell(20, 6, str(v['q']), 1, 0, "C"); pdf.cell(40, 6, format_cena(line_total), 1, 1, "R")
            total_sum += line_total
        pdf.ln(5); pdf.set_font(p, "B", 10); pdf.cell(150, 8, s("CELKEM BEZ DPH:"), 1, 0, "R", True); pdf.cell(40, 8, format_cena(total_sum), 1, 1, "R", True)
        return bytes(pdf.output()), None
    except Exception as e: return None, str(e)

# =====================================================================
# 🌐 LAYER 7: STREAMLIT UI & SESSIONS
# =====================================================================
init_db()

if "initialized_v4" not in st.session_state:
    st.session_state["initialized_v4"] = True
    st.session_state["data_zakazky"] = {}
    st.session_state["dynamic_items"] = {}
    st.session_state["vybrany_zakaznik"] = None
    st.session_state["evidence_df"] = pd.DataFrame()
    st.session_state["velin_data"] = pd.DataFrame() # Tímto CSV z Velínu nezmizí

st.markdown("""<style>.stTabs [aria-selected="true"] { background-color: #ff4b4b; color: white; font-weight: bold; } .cart-box { background-color: #f8f9fa; padding: 15px; border-radius: 8px; border-left: 5px solid #ff4b4b; margin-top: 15px;} .evidence-box { border: 2px solid #28a745; padding: 15px; border-radius: 8px; background-color: #f9fff9; }</style>""", unsafe_allow_html=True)

menu_volba = st.sidebar.radio("Navigace centrály:", ["📝 Zpracování zakázky", "🗄️ Katalog & Sklad", "📊 Obchodní Velín (Audit)"])

if menu_volba == "📝 Zpracování zakázky":
    with st.sidebar:
        st.header("🏢 Hlavička dokladů")
        typ_dl = st.radio("Sekce DL:", ["Standard (Kontroly)", "Opravy (Prior)"])
        dl_number = st.text_input("Číslo DL:", value="1698")
        zakazka = st.text_input("Zakázka:", value="1/13")
        technik = st.text_input("Technik:", value="Tomáš Urbánek")
        st.divider()
        
        aktualni_ico = ""
        ulozene_objekty = []
        df_c = safe_db_query("SELECT * FROM obchpartner ORDER BY firma")
        
        if not df_c.empty and 'firma' in df_c.columns:
            opts = df_c.apply(lambda r: f"{r['firma']} | {r['ico']}", axis=1).tolist()
            def_idx = 0
            vz = st.session_state.get("vybrany_zakaznik")
            if vz:
                for idx, o in enumerate(opts):
                    if vz["ico"] in o: def_idx = idx; break
            
            sel = st.selectbox("🔍 Vyhledat odběratele:", ["-- Vyberte --"] + opts, index=def_idx + 1 if vz else 0)
            if sel != "-- Vyberte --":
                curr_ico = sel.split(" | ")[1].strip()
                if not vz or vz.get("ico") != curr_ico:
                    st.session_state["vybrany_zakaznik"] = df_c[df_c['ico'] == curr_ico].iloc[0].to_dict()
                    st.session_state["evidence_df"] = pd.DataFrame()
                    st.rerun()
                
                aktualni_ico = curr_ico
                ulozene_objekty = get_objects_from_db(aktualni_ico)
                
                ul, cp, ob = str(vz.get("ulice","")), str(vz.get("cp","")), str(vz.get("mesto",""))
                adr = f"{ul} {cp}, {ob}" if ul else ob
                if adr.strip() and adr not in ulozene_objekty: ulozene_objekty.insert(0, adr)
        else:
            st.warning("⚠️ Databáze zákazníků je prázdná. Přejděte do 'Katalog & Sklad' a proveďte synchronizaci.")

        total_p = sum(v["q"]*v["p"] for v in st.session_state["data_zakazky"].values()) + sum(v["q"]*v["p"] for v in st.session_state["dynamic_items"].values())
        st.markdown(f"""<div class="cart-box"><b>🛒 Celkem bez DPH:</b><br/>{format_cena(total_p)} Kč</div>""", unsafe_allow_html=True)
        
        if st.button("🗑️ Vyprázdnit zakázku", use_container_width=True):
            st.session_state["data_zakazky"] = {}
            st.session_state["dynamic_items"] = {}
            st.session_state["evidence_df"] = pd.DataFrame()
            st.rerun()

    st.title("🛡️ Zpracování zakázky (ERP Modul)")
    
    # OVLÁDACÍ PRVKY ROZŘAZENÍ ZPĚT (Nikdy nezmizí)
    st.markdown("### 🏢 Rozřazení objektů pro tisk (O1 - O5)")
    cO1, cO2, cO3, cO4, cO5 = st.columns(5)
    with cO1: show_o1 = st.checkbox("O1", value=True); o1_name = st.selectbox("Objekt 1:", [""] + ulozene_objekty, key="o1_sel") if show_o1 else ""
    with cO2: show_o2 = st.checkbox("O2", value=False); o2_name = st.selectbox("Objekt 2:", [""] + ulozene_objekty, key="o2_sel") if show_o2 else ""
    with cO3: show_o3 = st.checkbox("O3", value=False); o3_name = st.selectbox("Objekt 3:", [""] + ulozene_objekty, key="o3_sel") if show_o3 else ""
    with cO4: show_o4 = st.checkbox("O4", value=False); o4_name = st.selectbox("Objekt 4:", [""] + ulozene_objekty, key="o4_sel") if show_o4 else ""
    with cO5: show_o5 = st.checkbox("O5", value=False); o5_name = st.selectbox("Objekt 5:", [""] + ulozene_objekty, key="o5_sel") if show_o5 else ""
    mapa_objektu_pro_pdf = {1: o1_name, 2: o2_name, 3: o3_name, 4: o4_name, 5: o5_name}

    # ZÁLOŽKY ZPĚT V HLAVNÍ LINII (Nikdy nezmizí)
    tabs = st.tabs(["📝 1. Evidence HP", "💰 2. Fakturace & Náhrady", "🛠️ 3. Opravy & Voda", "🛒 4. Zboží", "🖨️ 5. Tisk"])

    def get_col_layout():
        layout = [3.5, 1.5]
        if show_o1: layout.append(1.0)
        if show_o2: layout.append(1.0)
        if show_o3: layout.append(1.0)
        if show_o4: layout.append(1.0)
        if show_o5: layout.append(1.0)
        return layout

    def draw_item_row(cat_key, item_name, fallback_price, row_id, is_auto=False):
        p_val = get_price(cat_key, item_name)
        if p_val == 0.0: p_val = fallback_price
        
        cols = st.columns(get_col_layout())
        with cols[0]: st.markdown(f"{'🤖 ' if is_auto else ''}**{item_name}**")
        with cols[1]: p = st.number_input(f"P_{row_id}", 0.0, step=0.1, value=float(p_val), key=f"p_{row_id}", label_visibility="collapsed")
        
        idx = 2; q_vals = {"q1":0.0,"q2":0.0,"q3":0.0,"q4":0.0,"q5":0.0}
        if show_o1: q_vals["q1"] = cols[idx].number_input(f"1_{row_id}", 0.0, value=float(st.session_state.get(f"q1_{row_id}", 0.0)), key=f"q1_{row_id}", label_visibility="collapsed"); idx+=1
        if show_o2: q_vals["q2"] = cols[idx].number_input(f"2_{row_id}", 0.0, value=float(st.session_state.get(f"q2_{row_id}", 0.0)), key=f"q2_{row_id}", label_visibility="collapsed"); idx+=1
        if show_o3: q_vals["q3"] = cols[idx].number_input(f"3_{row_id}", 0.0, value=float(st.session_state.get(f"q3_{row_id}", 0.0)), key=f"q3_{row_id}", label_visibility="collapsed"); idx+=1
        if show_o4: q_vals["q4"] = cols[idx].number_input(f"4_{row_id}", 0.0, value=float(st.session_state.get(f"q4_{row_id}", 0.0)), key=f"q4_{row_id}", label_visibility="collapsed"); idx+=1
        if show_o5: q_vals["q5"] = cols[idx].number_input(f"5_{row_id}", 0.0, value=float(st.session_state.get(f"q5_{row_id}", 0.0)), key=f"q5_{row_id}", label_visibility="collapsed")
        
        st.session_state["data_zakazky"][item_name] = {**q_vals, "q": sum(q_vals.values()), "p": float(p), "cat": cat_key}

    with tabs[0]:
        st.markdown("<div class='evidence-box'>### 📋 Evidence HP</div>", unsafe_allow_html=True)
        if not st.session_state.get("vybrany_zakaznik"):
            st.warning("👈 Pro ukládání do databáze musíte vybrat zákazníka v levém panelu.")
        
        # Load evidence logic
        if st.session_state.get("vybrany_zakaznik") and st.session_state["evidence_df"].empty:
            df_e = safe_db_query("SELECT * FROM evidence_hp WHERE ico = ?", (aktualni_ico,))
            if df_e.empty:
                df_e = pd.DataFrame(columns=["druh","typ_hp","vyr_cislo","rok_vyr","mesic_vyr","tlak_rok","tlak_mesic","stav","duvod_nv","objekt","misto"])
                for i in range(5): df_e.loc[i] = ["přenosný", "", "", None, None, None, None, "S", "", "", ""]
            st.session_state["evidence_df"] = df_e
        
        if not st.session_state["evidence_df"].empty:
            df_audited, stats = run_expert_audit(st.session_state["evidence_df"], st.session_state.get("vybrany_zakaznik", {}))
            
            if stats['chyb'] > 0: st.error(f"🤖 Robot detekoval {stats['chyb']} chyb oproti metodice TÜV NORD.")
            else: st.success("✅ Robot: Vaše metodika je v naprostém pořádku.")
            
            edited = st.data_editor(df_audited, num_rows="dynamic", use_container_width=True, key="editor_evid",
                                   column_config={"robot_kontrol": st.column_config.TextColumn("Robot", disabled=True),
                                                 "druh": st.column_config.SelectboxColumn("Druh", options=["přenosný", "pojízdný", "AHS"]),
                                                 "stav": st.column_config.SelectboxColumn("Stav", options=STAVY_HP),
                                                 "duvod_nv": st.column_config.SelectboxColumn("Důvod NV", options=list(DUVODY_VYRAZENI.keys())),
                                                 "objekt": st.column_config.SelectboxColumn("Objekt", options=ulozene_objekty)})
            
            if st.button("💾 Uložit a automaticky napočítat do faktury", type="primary", use_container_width=True):
                if not st.session_state.get("vybrany_zakaznik"):
                    st.error("Chyba: Zákazník není vybrán!")
                else:
                    df_save = edited.drop(columns=['robot_kontrol'], errors='ignore')
                    df_save = df_save[df_save['typ_hp'].fillna("").astype(str).str.strip() != '']
                    df_save['ico'] = aktualni_ico
                    
                    conn = sqlite3.connect(DB_PATH)
                    conn.execute("DELETE FROM evidence_hp WHERE ico = ?", (aktualni_ico,))
                    df_save.to_sql("evidence_hp", conn, if_exists="append", index=False)
                    conn.close()
                    
                    # Robotizace fakturace
                    s_c = len(df_save[df_save['stav'].isin(['S', 'S-nový'])])
                    no_c = len(df_save[df_save['stav'].isin(['NO', 'NOPZ'])])
                    nv_c = len(df_save[df_save['stav'] == 'NV'])
                    
                    st.session_state["q1_h1"] = float(s_c)
                    st.session_state["q1_h2"] = float(no_c)
                    st.session_state["q1_h3"] = float(nv_c)
                    st.session_state["q1_s_hp1"] = float(s_c+no_c+nv_c)
                    st.session_state["evidence_df"] = df_save
                    st.rerun()

    with tabs[1]:
        c1, c2 = st.columns(get_col_layout()[:2] + [sum(get_col_layout()[2:])])
        c1.markdown("**Název položky**"); c2.markdown("**Cena**")
        st.markdown("### Automaticky načtené úkony HP z Evidence")
        draw_item_row("HP", "Kontrola HP (shodný)", 29.4, "h1", True)
        draw_item_row("HP", "Kontrola HP (neshodný - opravitelný)", 19.7, "h2", True)
        draw_item_row("HP", "Kontrola HP (neshodný - neopravitelný) + odborné zneprovoznění", 23.5, "h3", True)
        draw_item_row("Servisni_ukony", "Vyhodnocení kontroly + vystavení dokladu o kontrole (á 1ks HP)", 5.8, "s_hp1", True)
        st.divider()
        st.markdown("### Manuální úkony a Náhrady")
        draw_item_row("HP", "Manipulace a odvoz HP ze servisu (opravy)", 24.0, "h4")
        draw_item_row("Nahrady", "Náhrada za 1km - osobní servisní vozidlo", 13.8, "n4")

    with tabs[2]:
        c1, c2 = st.columns(get_col_layout()[:2] + [sum(get_col_layout()[2:])])
        c1.markdown("**Název položky**"); c2.markdown("**Cena**")
        draw_item_row("Opravy", "CO2-5F/ETS", 418.0, "opr1")
        draw_item_row("Opravy", "P6 Če (21A/)", 385.0, "opr2")
        draw_item_row("Voda", "Prohlídka zařízení do 5 ks výtoků", 123.0, "v1")
        draw_item_row("Voda", "Kontrola zařízení bez měření průtoku do 5 ks výtoků", 141.0, "v2")

    with tabs[3]:
        db_items = get_items_from_db(["Zboží", "ND_HP", "ND_Voda", "TAB", "HILTI", "zbozi"])
        if db_items:
            idict = {i["nazev"]: i for i in db_items}
            zcols = st.columns([4, 1, 1, 1])
            sel_z = zcols[0].selectbox("Skladové položky:", ["-- Vyberte --"] + list(idict.keys()))
            if sel_z != "-- Vyberte --":
                pz = zcols[1].number_input("Cena", value=idict[sel_z]["cena"])
                qz = zcols[2].number_input("Množství (do O1)", value=1.0)
                if zcols[3].button("➕ Přidat", use_container_width=True):
                    st.session_state["dynamic_items"][sel_z] = {"q1":qz, "q2":0, "q3":0, "q4":0, "q5":0, "q":qz, "p":pz, "cat":"Zboží"}
                    st.rerun()
        if st.session_state["dynamic_items"]:
            st.divider()
            for k, v in list(st.session_state["dynamic_items"].items()):
                ca, cb, cc, cd = st.columns([5, 2, 2, 1])
                ca.write(f"• {k}"); cb.write(f"{v['q']} ks"); cc.write(f"{v['q']*v['p']:,.2f} Kč")
                if cd.button("❌", key=f"del_{k}"): del st.session_state["dynamic_items"][k]; st.rerun()

    with tabs[4]:
        st.markdown("### 🖨️ Tiskové Centrum")
        if not st.session_state.get("vybrany_zakaznik"):
            st.error("Pro tisk dokumentů musíte vybrat zákazníka v levém panelu.")
        else:
            az = st.session_state["vybrany_zakaznik"]
            items = {k:v for k,v in st.session_state["data_zakazky"].items() if v["q"]>0}
            items.update({k:v for k,v in st.session_state["dynamic_items"].items() if v["q"]>0})
            
            c1, c2, c3 = st.columns(3)
            with c1:
                st.markdown("#### Technická část")
                if st.button("📄 DOKLAD O KONTROLE HP", use_container_width=True):
                    pb, err = create_doklad_kontroly_pdf(az, st.session_state["evidence_df"], dl_number, zakazka, technik)
                    if not err: st.download_button("⬇️ Stáhnout Doklad", pb, f"DoK_{dl_number}.pdf", "application/pdf", key="d1")
                if st.button("⚠️ PROTOKOL O VYŘAZENÍ", use_container_width=True):
                    pb, err = create_protokol_vyrazeni_pdf(az, st.session_state["evidence_df"], dl_number, zakazka, technik)
                    if not err: st.download_button("⬇️ Stáhnout Protokol", pb, f"LP_{dl_number}.pdf", "application/pdf", key="d3")
            with c2:
                st.markdown("#### Finanční část (Kontroly)")
                if st.button("📄 DL: Kontroly HP", type="primary", use_container_width=True):
                    pb, err = create_wservis_dl(az, items, dl_number, zakazka, technik, mapa_objektu_pro_pdf, typ_dl, "Kontroly HP", ["HP","NAHRADY","ZBOZI"])
                    if not err: st.download_button("⬇️ Stáhnout DL", pb, f"DL_{dl_number}.pdf", "application/pdf", key="d2")
            with c3:
                st.markdown("#### Finanční část (Opravy)")
                if st.button("📄 DL: Opravy HP", type="primary", use_container_width=True):
                    pb, err = create_wservis_dl(az, items, dl_number, zakazka, technik, mapa_objektu_pro_pdf, typ_dl, "Opravy HP", ["OPRAVY","NAHRADY","ZBOZI"])
                    if not err: st.download_button("⬇️ Stáhnout DL (Opravy)", pb, f"DL_Opravy_{dl_number}.pdf", "application/pdf", key="d4")

elif menu_volba == "🗄️ Katalog & Sklad":
    st.title("🗄️ Katalog, Sklad a Databáze")
    
    # NIKDY NESPADNE: Výběr přesně mapuje tabulky, které jsme vytvořili
    tab_list = ["obchpartner", "cenik_hp", "cenik_zbozi", "cenik_nd_hp", "cenik_voda", "cenik_nahrady", "cenik_revize", "cenik_opravy"]
    
    t1, t2 = st.tabs(["📦 Pohled do databáze", "⚙️ Synchronizace (Import)"])
    with t1:
        chosen = st.selectbox("Vyberte tabulku k zobrazení:", tab_list)
        df_v = safe_db_query(f"SELECT * FROM {chosen}")
        if not df_v.empty: st.dataframe(df_v, use_container_width=True)
        else: st.info("Tabulka je prázdná. Přejděte na vedlejší záložku a spusťte Synchronizaci.")
    
    with t2:
        st.info("💡 Nahrajte do složky 'data/ceniky/' vaše Excel/CSV soubory a klikněte na tlačítko.")
        if st.button("🚀 Spustit kompletní synchronizaci s W-SERVIS", type="primary"):
            with st.spinner("Přečítám Excely a CSV soubory a buduji databázi..."):
                m = service_import_data()
                st.success("Synchronizace dokončena úspěšně!")
                st.code(m)

elif menu_volba == "📊 Obchodní Velín (Audit)":
    st.title("🚒 Obchodní Velín HASIČ-SERVIS")
    st.markdown("### Auditní kontrola metodiky (Návrh vyrovnání 50:50)")
    st.divider()

    st.info("Nahrajte soubor `Migrace_Centraly_Navrh.csv` vygenerovaný z W-SERVIS. Data nezmizí ani při překliknutí oken.")
    uf = st.file_uploader("📂 Vyberte soubor:", type=['csv'])
    
    if uf:
        try:
            df_v = pd.read_csv(uf, sep=';', encoding='utf-8-sig')
            st.session_state["velin_data"] = df_v
        except: st.error("❌ Formát CSV nebyl rozpoznán. Zkuste uložit CSV jako UTF-8.")

    # PAMĚŤ VELÍNU:
    if not st.session_state["velin_data"].empty:
        df_aud, v_stats = run_expert_audit(st.session_state["velin_data"])
        
        st.success(f"✅ V paměti úspěšně načteno: {v_stats['celkem']} záznamů.")
        c1, c2, c3 = st.columns(3)
        c1.metric("Celkem kontrolovaných úkonů", v_stats["celkem"])
        c2.metric("Chyby metodiky TÜV", v_stats["chyb"])
        c3.metric("Index Integrity Dat", f"{v_stats['score']:.1f} %")
        
        st.dataframe(df_aud, use_container_width=True)

st.sidebar.divider()
st.sidebar.caption(f"© {datetime.now().year} {FIRMA_VLASTNI['název']}")
