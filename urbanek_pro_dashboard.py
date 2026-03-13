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

# NASTAVENÍ STRÁNKY (První příkaz Streamlitu)
st.set_page_config(page_title="W-SERVIS Enterprise v47.0", layout="wide", page_icon="🚒")

# =====================================================================
# 🗄️ LAYER 1: SCHEMA (Definice a konfigurace)
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
# 🧹 LAYER 2: NORMALIZATION (Čištění dat a převody)
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
    st = f"{num:,.2f}".replace(",", "X").replace(".", ",").replace("X", " ")
    if st.endswith(",00"): return st[:-3]
    if st.endswith("0") and "," in st: return st[:-1]
    return st

def safe_str(txt, is_pismo_ok=True):
    return str(txt).replace('\n', ' ').replace('\r', '').strip()

# =====================================================================
# 🛡️ LAYER 3: VALIDATION (Business pravidla & Audit Robot)
# =====================================================================
def validate_stav_and_duvod(stav: str, duvod: str) -> Tuple[bool, str]:
    if stav and stav not in STAVY_HP:
        return False, f"Neznámý stav HP: {stav}"
    if stav == "NV" and not duvod:
        return False, "Stav NV musí mít přiřazen kód důvodu vyřazení."
    if stav != "NV" and duvod:
        return False, "Důvod vyřazení lze uvést pouze u stavu NV."
    if duvod and duvod not in DUVODY_VYRAZENI:
        return False, f"Neznámý kód důvodu vyřazení: {duvod}"
    return True, ""

def run_expert_audit(df: pd.DataFrame) -> Tuple[pd.DataFrame, Dict]:
    """
    Implementace expertní logiky HASIČ-SERVIS URBÁNEK pro hloubkovou kontrolu dat.
    """
    df_audit = df.copy()
    audit_log = []
    
    # Normalizace sloupců pro jistotu
    df_audit.columns = [normalize_column_name(c) for c in df_audit.columns]
    
    def check_row(row):
        chyby = []
        
        # 1. Pravidlo: Snížená sazba DPH pro SVJ a Bytové domy (12%)
        nazev_firmy = str(row.get('firma_nazev', row.get('partner', ''))).upper()
        dph = normalize_price(row.get('dph_sazba', 0))
        if ("SVJ" in nazev_firmy or "BYTOVÉ" in nazev_firmy or "S.V.J." in nazev_firmy):
            if dph != 12 and dph != 0: # 0 je bráno jako "zatím nevyplněno"
                chyby.append("Chyba DPH: U SVJ/BD musí být snížená sazba 12%")

        # 2. Pravidlo: Kontrola měření u Hydrantů (PV) - Metodika TÜV NORD
        typ_ukonu = str(row.get('typ_hp', '')).upper()
        if "HYDRANT" in typ_ukonu or "PV" in typ_ukonu:
            tlak = row.get('tlak_mpa', row.get('tlak', None))
            prutok = row.get('prutok_ls', row.get('prutok', None))
            if pd.isna(tlak) or pd.isna(prutok):
                chyby.append("Chybí měření: U hydrantů je vyžadován tlak a průtok (TÜV standard)")

        # 3. Pravidlo: Vyřazení HP a kódy A-K
        stav = str(row.get('stav', '')).upper()
        if stav == "NV" or "VYŘAZEN" in stav:
            kod = str(row.get('kod_vyrazeni', row.get('duvod_nv', ''))).strip()
            if not kod or kod not in "ABCDEFGHIJK":
                chyby.append("Legislativní chyba: Chybí nebo je neplatný kód vyřazení (A-K)")

        return "✅ V pořádku" if not chyby else "❌ " + " | ".join(chyby)

    df_audit['auditni_status'] = df_audit.apply(check_row, axis=1)
    
    # Statistiky
    total = len(df_audit)
    errors = len(df_audit[df_audit['auditni_status'].str.contains("❌")])
    integrity = ((total - errors) / total * 100) if total > 0 else 100
    
    stats = {
        "celkem": total,
        "chyb": errors,
        "integrity": integrity
    }
    
    return df_audit, stats

# =====================================================================
# 💾 LAYER 4: REPOSITORY (Databázové operace SQLite)
# =====================================================================
def init_db():
    if not os.path.exists("data"): os.makedirs("data")
    if not os.path.exists(CSV_FOLDER): os.makedirs(CSV_FOLDER)
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""CREATE TABLE IF NOT EXISTS objekty (id INTEGER PRIMARY KEY AUTOINCREMENT, ico TEXT NOT NULL, nazev_objektu TEXT NOT NULL, UNIQUE(ico, nazev_objektu))""")
    cur.execute("""CREATE TABLE IF NOT EXISTS evidence_hp (id INTEGER PRIMARY KEY AUTOINCREMENT, ico TEXT NOT NULL, druh TEXT, typ_hp TEXT, vyr_cislo TEXT, rok_vyr INTEGER, mesic_vyr INTEGER, tlak_rok INTEGER, tlak_mesic INTEGER, stav TEXT, duvod_nv TEXT, objekt TEXT, misto TEXT)""")
    conn.commit()
    conn.close()

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

def save_hp_evidence_to_db(ico: str, df_clean: pd.DataFrame):
    conn = sqlite3.connect(DB_PATH)
    conn.execute("DELETE FROM evidence_hp WHERE ico = ?", (ico,))
    conn.commit()  
    df_clean.to_sql("evidence_hp", conn, if_exists="append", index=False)
    conn.close()

# =====================================================================
# ⚙️ LAYER 5: SERVICES (Doménová a importní logika)
# =====================================================================
def load_local_customers() -> Optional[pd.DataFrame]:
    base_path = os.path.join("data", "ceniky", "zakaznici")
    df = safe_read_data_io(base_path)
    if df is not None and not df.empty:
        df.columns = [normalize_column_name(c) for c in df.columns]
        if "ico" in df.columns: df["ico"] = df["ico"].apply(clean_ico)
        return df
    return None

def build_form_data_from_customer(ico: Any) -> Optional[Dict[str, Any]]:
    ico_clean = clean_ico(ico)
    df = load_local_customers()
    if df is None or "ico" not in df.columns: return None
    row = df[df["ico"] == ico_clean]
    if row.empty: return None
    cust = row.iloc[0].to_dict()
    return {
        "ICO": clean_ico(cust.get("ico", "")), "DIC": cust.get("dic", ""), "FIRMA": cust.get("firma", ""),
        "ULICE": cust.get("ulice", ""), "CP": cust.get("cp", ""), "CO": cust.get("co", ""),
        "ADRESA3": cust.get("mesto", ""), "PSC": cust.get("psc", ""), "KONTAKT": cust.get("kontakt", ""),
        "TELEFON": cust.get("telefon", ""), "EMAIL": cust.get("email", ""), "UCET": cust.get("ucet", ""),
        "POZNAMKA": cust.get("poznamka", "")
    }

def service_import_all_ceniky() -> str:
    log_messages: List[str] = []
    connection = sqlite3.connect(DB_PATH)
    try:
        for ui_key, name in CATEGORY_MAP.items():
            df = safe_read_data_io(os.path.join(CSV_FOLDER, name))
            if df is None: continue
            df.columns = [normalize_column_name(col) for col in df.columns]
            if 'zbozi_nazev' in df.columns: df.rename(columns={'zbozi_nazev': 'nazev'}, inplace=True)
            if 'zbozi_cena' in df.columns: df.rename(columns={'zbozi_cena': 'cena'}, inplace=True)
            if "nazev" not in df.columns or "cena" not in df.columns: continue
            df = df.dropna(subset=["nazev", "cena"])
            df["nazev"] = df["nazev"].astype(str).str.strip()
            df = df.drop_duplicates(subset=["nazev"])
            df["cena"] = df["cena"].apply(normalize_price)
            df[["nazev", "cena"]].to_sql(normalize_category_to_table(ui_key), connection, if_exists="replace", index=False)
            log_messages.append(f"✅ Načteno: {name} ({len(df)} položek)")
    finally: connection.close()
    return "\n".join(log_messages)

def service_calculate_billing(df_evid: pd.DataFrame) -> Tuple[Dict, str]:
    df_clean = df_evid.copy()
    df_clean['typ_hp'] = df_clean['typ_hp'].fillna("").astype(str).str.strip()
    df_clean = df_clean[df_clean['typ_hp'] != '']
    errors = []
    for idx, row in df_clean.iterrows():
        ok, msg = validate_stav_and_duvod(row.get('stav',''), row.get('duvod_nv',''))
        if not ok: errors.append(f"Řádek {idx+1}: {msg}")
    if errors: return {}, "CHYBA VALIDACE:\n" + "\n".join(errors)
    
    S = len(df_clean[df_clean['stav'].isin(['S', 'S-nový'])])
    NO = len(df_clean[df_clean['stav'].isin(['NO', 'NOPZ'])])
    NV = len(df_clean[df_clean['stav'] == 'NV'])
    total = len(df_clean[df_clean['stav'] != 'CH'])
    
    return {"S":S, "NO":NO, "NV":NV, "total":total, "vyrazene_kody": set(df_clean[df_clean['stav'] == 'NV']['duvod_nv'].dropna().tolist()), "df_clean": df_clean}, ""

def safe_read_data_io(base_path: str) -> Optional[pd.DataFrame]:
    excel_path = base_path + ".xlsx"
    csv_path = base_path + ".csv"
    if os.path.exists(excel_path):
        try: return pd.read_excel(excel_path)
        except: pass
    if os.path.exists(csv_path): 
        for enc in ("utf-8-sig", "utf-8", "cp1250", "windows-1250"):
            try: return pd.read_csv(csv_path, sep=";", encoding=enc, on_bad_lines='skip')
            except: continue
    return None

# =====================================================================
# 📄 LAYER 6: PDF ENGINE
# =====================================================================
def setup_pdf_fonts(pdf: FPDF) -> bool:
    for reg, bld in [("arial.ttf", "arialbd.ttf"), ("Arial.ttf", "Arialbd.ttf")]:
        if os.path.exists(reg) and os.path.exists(bld):
            try:
                pdf.add_font("PismoCZ", "", reg, uni=True)
                pdf.add_font("PismoCZ", "B", bld, uni=True)
                return True
            except: pass
    return False

class UrbaneKPDF_Letterhead(FPDF):
    def __init__(self, orientation='P'):
        super().__init__(orientation=orientation)
        self.pismo_ok = setup_pdf_fonts(self)
        self.pismo_name = "PismoCZ" if self.pismo_ok else "helvetica"
    def header(self):
        if not self.pismo_ok: return
        p = self.pismo_name
        if os.path.exists("logo.png"): self.image("logo.png", 10, 10, 24)
        self.set_y(10); self.set_font(p, "B", 14); self.cell(0, 6, FIRMA_VLASTNI["název"], align="C", ln=True)
        self.set_font(p, "", 9); self.cell(0, 4.5, f"Sídlo: {FIRMA_VLASTNI['sídlo']}", align="C", ln=True)
        self.cell(0, 4.5, f"IČO: {FIRMA_VLASTNI['ico']}  DIČ: {FIRMA_VLASTNI['dic']}", align="C", ln=True)
        self.cell(0, 4.5, FIRMA_VLASTNI['zápis'], align="C", ln=True)
        self.ln(3); self.set_line_width(0.5); self.line(10, self.get_y(), self.w - 10, self.get_y()); self.ln(5)

def create_doklad_kontroly_pdf(zakaznik: Dict, df_evid: pd.DataFrame, dl_number: str, zakazka: str, technik: str) -> tuple[Optional[bytes], Optional[str]]:
    pdf = UrbaneKPDF_Letterhead('L'); p = pdf.pismo_name; s = safe_str
    try:
        pdf.add_page(); pdf.set_font(p, "B", 14); pdf.cell(0, 6, s("DOKLAD O KONTROLE HASICÍCH PŘÍSTROJŮ"), align="C", ln=True)
        pdf.set_font(p, "B", 10); pdf.cell(20, 5, s("Zákazník:"), 0); pdf.set_font(p, "", 10); pdf.cell(150, 5, s(f"{zakaznik.get('FIRMA','')} (IČO: {zakaznik.get('ICO','')})"), 0); pdf.ln(10)
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
    pdf = UrbaneKPDF_Letterhead('P'); p = pdf.pismo_name; s = safe_str
    try:
        df_nv = df_evid[df_evid['stav'] == 'NV']
        pdf.add_page(); pdf.set_font(p, "B", 14); pdf.cell(0, 6, s("POTVRZENÍ O PŘEVZETÍ HP VYŘAZENÝCH Z UŽÍVÁNÍ"), align="C", ln=True)
        pdf.set_font(p, "B", 8); pdf.cell(0, 4, s("TOTO POTVRZENÍ NESLOUŽÍ PRO ÚČELY EVIDENCE ODPADŮ"), align="C", ln=True); pdf.ln(10)
        for idx, row in df_nv.iterrows(): pdf.cell(0, 5, s(f"{idx+1}. {row['typ_hp']} (v.č. {row['vyr_cislo']}) - kód: {row['duvod_nv']}"), ln=True)
        return bytes(pdf.output()), None
    except Exception as e: return None, str(e)

def create_wservis_dl(zakaznik: Dict, items: Dict, dl_number: str, zakazka: str, technik: str, objekty: Dict, typ_dl: str, type_name: str, sections: List[str]) -> tuple[Optional[bytes], Optional[str]]:
    pdf = UrbaneKPDF_Letterhead('P'); p = pdf.pismo_name; s = safe_str
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
# 🌐 LAYER 7: STREAMLIT UI
# =====================================================================
init_db()

def ensure_session_state():
    defaults = {"data_zakazky": {}, "dynamic_items": {}, "vybrany_zakaznik": None, "loaded_ico": None, 
                "evidence_df": pd.DataFrame(), "auto_kalkulace": {"S": 0, "NO": 0, "NV": 0, "total": 0, "vyrazene_kody": set()}}
    for key, val in defaults.items():
        if key not in st.session_state: st.session_state[key] = val

ensure_session_state()

df_customers = load_local_customers()
if df_customers is None:
    try:
        conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
        df_customers = pd.read_sql("SELECT * FROM obchpartner", conn); conn.close()
    except: pass

menu_volba = st.sidebar.radio("Navigace systému:", ["📝 Zpracování zakázky (Evidence & DL)", "🗄️ Katalog a Sklad (Ceníky)", "📊 Obchodní Velín (Audit)"])

if menu_volba == "📝 Zpracování zakázky (Evidence & DL)":
    with st.sidebar:
        st.header("🏢 Hlavička dokladů")
        typ_dl = st.radio("Sekce DL:", ["Standard (Kontroly)", "Opravy (Prior)"])
        dl_number = st.text_input("Číslo DL:", value="1698"); zakazka = st.text_input("Zakázka:", value="1/13"); technik = st.text_input("Technik:", value="Tomáš Urbánek")
        st.divider(); aktualni_ico = ""; ulozene_objekty = []
        if df_customers is not None and not df_customers.empty:
            filt = df_customers.copy(); filt["FIRMA"] = filt["FIRMA"].fillna("Neznámý"); filt["clean_ico"] = filt["ICO"].apply(clean_ico)
            filt = filt.drop_duplicates(subset=["clean_ico", "FIRMA"]).sort_values("FIRMA")
            opts = filt.apply(lambda r: f"{r['FIRMA']} | {r['clean_ico']}", 1).tolist()
            def_idx = 0; vz = st.session_state.get("vybrany_zakaznik")
            if vz:
                for idx, o in enumerate(opts):
                    if vz["ICO"] in o: def_idx = idx; break
            sel = st.selectbox("🔍 Odběratel:", opts, index=def_idx)
            curr = filt.iloc[opts.index(sel)].to_dict()
            if vz is None or clean_ico(vz.get("ICO")) != clean_ico(curr.get("ICO")):
                ld = build_form_data_from_customer(curr.get("clean_ico")); curr.update(ld if ld else {})
                st.session_state["vybrany_zakaznik"] = curr.copy()
            aktualni_ico = clean_ico(curr.get("clean_ico")); ulozene_objekty = get_objects_from_db(aktualni_ico)
        
        total_p = sum(v["q"]*v["p"] for v in st.session_state["data_zakazky"].values()) + sum(v["q"]*v["p"] for v in st.session_state["dynamic_items"].values())
        st.markdown(f"""<div class="cart-box"><b>🛒 Celkem bez DPH:</b><br/>{format_cena(total_p)} Kč</div>""", 1)
        if st.button("🗑️ Vyprázdnit", use_container_width=1):
            st.session_state["data_zakazky"] = {}; st.session_state["dynamic_items"] = {}; st.session_state["evidence_df"] = pd.DataFrame(); st.rerun()

    tabs = st.tabs(["📝 1. Evidence HP", "💰 2. Fakturace & Náhrady", "🛠️ 3. Opravy & Voda", "🛒 4. Zboží", "🖨️ 5. Tisk"])

    with tabs[0]:
        if "show_success" in st.session_state: st.success(st.session_state.pop("show_success"))
        st.markdown("<div class='evidence-box'>### 📋 Evidence HP</div>", 1)
        if not st.session_state.get("vybrany_zakaznik"): st.warning("Vyberte zákazníka.")
        else:
            if st.session_state.get("loaded_ico") != aktualni_ico or st.session_state["evidence_df"].empty:
                conn = sqlite3.connect(DB_PATH); df_e = pd.read_sql("SELECT * FROM evidence_hp WHERE ico = ?", conn, params=(aktualni_ico,)); conn.close()
                if df_e.empty: df_e = pd.DataFrame(columns=["druh","typ_hp","vyr_cislo","rok_vyr","mesic_vyr","tlak_rok","tlak_mesic","stav","duvod_nv","objekt","misto"])
                for i in range(len(df_e), 5): df_e.loc[i] = ["přenosný", "", "", None, None, None, None, "S", "", "", ""]
                st.session_state["evidence_df"] = df_e; st.session_state["loaded_ico"] = aktualni_ico
            edited = st.data_editor(st.session_state["evidence_df"], num_rows="dynamic", use_container_width=1, key="evid_edit", column_config={
                "druh": st.column_config.SelectboxColumn("Druh", options=["přenosný", "pojízdný", "AHS"]), "stav": st.column_config.SelectboxColumn("Stav", options=STAVY_HP),
                "duvod_nv": st.column_config.SelectboxColumn("Důvod NV", options=list(DUVODY_VYRAZENI.keys())), "objekt": st.column_config.SelectboxColumn("Objekt", options=ulozene_objekty)})
            if st.button("💾 Uložit a Přepočítat", type="primary", use_container_width=1):
                res, err = service_calculate_billing(edited)
                if err: st.error(err); st.rerun()
                else:
                    save_hp_evidence_to_db(aktualni_ico, res["df_clean"]); st.session_state["evidence_df"] = edited; st.session_state["auto_kalkulace"] = res
                    st.session_state["q1_h1"] = float(res["S"]); st.session_state["q1_h2"] = float(res["NO"]); st.session_state["q1_h3"] = float(res["NV"]); st.session_state["q1_s_hp1"] = float(res["total"])
                    st.session_state["show_success"] = f"✅ Hotovo! Nalezeno {res['total']} HP. Fakturace byla synchronizována."; st.rerun()

    def draw_item(cat, name, price, row_id, is_auto=0):
        p_val = get_price(cat, name) or price; cols = st.columns([3.5, 1.5, 1.0, 1.0, 1.0, 1.0, 1.0])
        cols[0].markdown(f"{'🤖 ' if is_auto else ''}**{name}**")
        p = cols[1].number_input(f"P_{row_id}", 0.0, step=0.1, value=float(p_val), key=f"p_{row_id}", label_visibility="collapsed")
        q_v = {"q1":0.0,"q2":0.0,"q3":0.0,"q4":0.0,"q5":0.0}
        for i in range(1, 6):
            q_v[f"q{i}"] = cols[i+1].number_input(f"{i}_{row_id}", 0.0, value=float(st.session_state.get(f"q{i}_{row_id}", 0.0)), key=f"q{i}_{row_id}", label_visibility="collapsed")
        st.session_state["data_zakazky"][name] = {**q_v, "q": sum(q_v.values()), "p": p, "cat": cat}

    with tabs[1]:
        st.markdown("### Automaticky načtené úkony (O1)"); draw_item("HP", "Kontrola HP (shodný)", 29.4, "h1", 1); draw_item("HP", "Kontrola HP (neshodný - opravitelný)", 19.7, "h2", 1); draw_item("HP", "Kontrola HP (neshodný - neopravitelný) + odborné zneprovoznění", 23.5, "h3", 1); draw_item("Servisni_ukony", "Vyhodnocení kontroly + vystavení dokladu o kontrole (á 1ks HP)", 5.8, "s_hp1", 1)
        st.divider(); st.markdown("### Manuální úkony"); draw_item("HP", "Manipulace a odvoz HP", 24.0, "h4"); draw_item("Nahrady", "Náhrada za 1km - osobní auto", 13.8, "n4")
    with tabs[2]: draw_item("Opravy", "CO2-5F/ETS", 418.0, "opr1"); draw_item("Voda", "Kontrola zařízení bez měření průtoku", 141.0, "v2")
    with tabs[3]:
        db_i = get_items_from_db(["Zboží", "ND_HP", "zbozi"])
        if db_i:
            idict = {i["nazev"]: i for i in db_i}
            zcols = st.columns([3, 1, 1, 1]); sel_z = zcols[0].selectbox("Sklad:", ["-- Vyberte --"] + list(idict.keys()))
            if sel_z != "-- Vyberte --":
                pz = zcols[1].number_input("Cena", value=idict[sel_z]["cena"]); qz = zcols[2].number_input("O1", value=1.0)
                if zcols[3].button("➕"): st.session_state["dynamic_items"][sel_z] = {"q1":qz,"q2":0,"q3":0,"q4":0,"q5":0,"q":qz,"p":pz,"cat":"Zboží"}; st.rerun()
        for k, v in list(st.session_state["dynamic_items"].items()):
            ca, cb, cc, cd = st.columns([5, 2, 2, 1]); ca.write(f"• {k}"); cb.write(f"{v['q']} ks"); cc.write(f"{v['q']*v['p']:,.2f} Kč"); 
            if cd.button("❌", key=f"del_{k}"): del st.session_state["dynamic_items"][k]; st.rerun()
    with tabs[4]:
        st.markdown("### 🖨️ Tiskové Centrum")
        if not st.session_state.get("vybrany_zakaznik"): st.error("Vyberte zákazníka!")
        else:
            az = st.session_state["vybrany_zakaznik"]; items = {k:v for k,v in st.session_state["data_zakazky"].items() if v["q"]>0}
            items.update({k:v for k,v in st.session_state["dynamic_items"].items() if v["q"]>0})
            c1, c2, c3 = st.columns(3)
            with c1:
                if st.button("📄 DOKLAD O KONTROLE HP", use_container_width=1):
                    pb, err = create_doklad_kontroly_pdf(az, st.session_state["evidence_df"], dl_number, zakazka, technik)
                    if not err: st.download_button("⬇️ Stáhnout DoK", pb, f"DoK_{dl_number}.pdf", "application/pdf", key="d1")
            with c2:
                if st.button("📄 DL: Kontroly HP", type="primary", use_container_width=1):
                    pb, err = create_wservis_dl(az, items, dl_number, zakazka, technik, {}, typ_dl, "Kontroly HP", ["HP","NAHRADY","ZBOZI"])
                    if not err: st.download_button("⬇️ Stáhnout DL", pb, f"DL_{dl_number}.pdf", "application/pdf", key="d2")
            with c3:
                if st.button("⚠️ PROTOKOL O VYŘAZENÍ", use_container_width=1):
                    pb, err = create_protokol_vyrazeni_pdf(az, st.session_state["evidence_df"], dl_number, zakazka, technik)
                    if not err: st.download_button("⬇️ Stáhnout Protokol", pb, f"LP_{dl_number}.pdf", "application/pdf", key="d3")

elif menu_volba == "🗄️ Katalog a Sklad (Ceníky)":
    st.title("🗄️ Katalog a Sklad")
    t1, t2 = st.tabs(["📦 Ceník", "⚙️ Import"])
    with t1:
        conn = sqlite3.connect(DB_PATH); dfz = pd.read_sql("SELECT nazev, cena FROM cenik_zbozi ORDER BY nazev", conn); conn.close()
        st.data_editor(dfz, use_container_width=1)
    with t2:
        if st.button("🚀 Synchronizovat (W-SERVIS Import)"):
            m = service_import_all_ceniky(); st.success("Synchronizace hotova"); st.code(m)

elif menu_volba == "📊 Obchodní Velín (Audit)":
    st.title("🚒 HASIČ-SERVIS URBÁNEK - Obchodní velín")
    st.markdown("### Návrh ke schválení pro spravedlivé rozdělení 50:50 (Tomáš a Ilja Urbánkovi)")
    st.divider()
    
    st.info("💡 **Auditní robot:** Nahrajte soubor 'Migrace_Centraly_Navrh.csv' pro automatickou hloubkovou kontrolu metodiky TÜV NORD.")
    u_file = st.file_uploader("📂 Nahrajte auditní export z W-SERVIS (CSV):", type=['csv'])
    
    if u_file:
        try:
            df_velin_raw = pd.read_csv(u_file, sep=';', encoding='utf-8-sig')
            df_velin, stats = run_expert_audit(df_velin_raw)
            
            c1, c2, c3 = st.columns(3)
            c1.metric("Celkem úkonů", stats["celkem"])
            c2.metric("Nalezeno chyb", stats["chyb"], delta=None if stats["chyb"]==0 else f"-{stats['chyb']}", delta_color="inverse")
            c3.metric("Index integrity dat", f"{stats['integrity']:.1f} %")
            
            if stats["chyb"] > 0:
                st.error(f"⚠️ Robot detekoval {stats['chyb']} nesouladů s firemními pravidly. Viz tabulka níže.")
            else:
                st.success("✅ Audit proběhl úspěšně. Všechna data odpovídají standardům HASIČ-SERVIS.")

            st.markdown("### 📋 Detailní přehled s výsledky auditu")
            # Podmíněné obarvení stavů
            def color_audit(val):
                color = 'green' if '✅' in str(val) else 'red'
                return f'color: {color}; font-weight: bold'
            
            st.dataframe(df_velin.style.applymap(color_audit, subset=['auditni_status']), use_container_width=True)
            
            st.divider()
            st.markdown("### 🤝 Návrh finančního vyrovnání (50:50)")
            partneri = {
                "Partner": ["Tomáš Urbánek (50 %)", "Ilja Urbánek (50 %)"],
                "Podíl na úkonech (ks)": [stats["celkem"] / 2, stats["celkem"] / 2],
                "Status": ["Připraveno k fakturaci", "Připraveno k fakturaci"]
            }
            st.table(pd.DataFrame(partneri))
            
        except Exception as e:
            st.error(f"❌ Chyba při zpracování auditního souboru: {e}")

st.sidebar.divider(); st.sidebar.caption(f"© {datetime.date.today().year} {FIRMA_VLASTNI['název']}")
