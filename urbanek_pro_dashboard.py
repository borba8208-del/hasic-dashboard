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

# NASTAVENÍ STRÁNKY MUSÍ BÝT PRVNÍ PŘÍKAZ STREAMLITU (Ochrana proti pádům)
st.set_page_config(page_title="W-SERVIS Enterprise v46.3", layout="wide", page_icon="🛡️")

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
    """OPRAVENO: Klíčová funkce pro mapování kategorií na DB tabulky."""
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
# 🛡️ LAYER 3: VALIDATION (Business pravidla)
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

# =====================================================================
# 💾 LAYER 4: REPOSITORY (Databázové operace SQLite)
# =====================================================================
def init_db():
    os.makedirs("data", exist_ok=True)
    os.makedirs(CSV_FOLDER, exist_ok=True)
    open(DB_PATH, 'a').close()
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS objekty (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ico TEXT NOT NULL,
            nazev_objektu TEXT NOT NULL,
            UNIQUE(ico, nazev_objektu)
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS evidence_hp (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ico TEXT NOT NULL,
            druh TEXT,
            typ_hp TEXT,
            vyr_cislo TEXT,
            rok_vyr INTEGER,
            mesic_vyr INTEGER,
            tlak_rok INTEGER,
            tlak_mesic INTEGER,
            stav TEXT,
            duvod_nv TEXT,
            objekt TEXT,
            misto TEXT
        )
    """)
    conn.commit()
    conn.close()

def get_price(cat_key: str, item_name: str) -> float:
    if not os.path.exists(DB_PATH): return 0.0
    table = normalize_category_to_table(cat_key)
    try:
        conn = sqlite3.connect(DB_PATH)
        res = conn.execute(f"SELECT cena FROM {table} WHERE nazev = ? LIMIT 1", (item_name.strip(),)).fetchone()
        conn.close()
        return float(res[0]) if res else 0.0
    except Exception: return 0.0

def get_items_from_db(categories: List[str]) -> List[Dict]:
    items = []
    if not os.path.exists(DB_PATH): return items
    conn = sqlite3.connect(DB_PATH)
    seen_names = set()
    for cat in categories:
        tbl = normalize_category_to_table(cat)
        try:
            res = conn.execute(f"SELECT nazev, cena FROM {tbl} ORDER BY nazev").fetchall()
            for r in res:
                if r[0] not in seen_names:
                    items.append({"nazev": r[0], "cena": float(r[1]), "internal_cat": cat})
                    seen_names.add(r[0])
        except Exception: pass
    conn.close()
    items.sort(key=lambda x: x["nazev"])
    return items

def get_objects_from_db(ico: Any) -> List[str]:
    ico_clean = clean_ico(ico)
    if not os.path.exists(DB_PATH) or not ico_clean: return []
    try:
        conn = sqlite3.connect(DB_PATH)
        rows = conn.execute("SELECT nazev_objektu FROM objekty WHERE ico = ? ORDER BY nazev_objektu", (ico_clean,)).fetchall()
        conn.close()
        return [row[0] for row in rows]
    except Exception: return []

def add_object_to_db(ico: Any, nazev_objektu: str) -> bool:
    ico_clean = clean_ico(ico)
    if not ico_clean or not nazev_objektu.strip(): return False
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.execute("INSERT OR IGNORE INTO objekty (ico, nazev_objektu) VALUES (?, ?)", (ico_clean, nazev_objektu.strip()))
        conn.commit(); conn.close()
        return True
    except Exception: return False

def save_hp_evidence_to_db(ico: str, df_clean: pd.DataFrame):
    conn = sqlite3.connect(DB_PATH)
    conn.execute("DELETE FROM evidence_hp WHERE ico = ?", (ico,))
    conn.commit()  
    df_clean.to_sql("evidence_hp", conn, if_exists="append", index=False)
    conn.close()

# =====================================================================
# ⚙️ LAYER 5: SERVICES (Doménová a importní logika)
# =====================================================================
def safe_read_data_io(base_path: str) -> Optional[pd.DataFrame]:
    """Služba pro čtení dat z různých zdrojů."""
    excel_path = base_path + ".xlsx"
    xml_path = base_path + ".xml"
    csv_path = base_path + ".csv"
    if os.path.exists(excel_path):
        try: return pd.read_excel(excel_path)
        except Exception: pass
    if os.path.exists(xml_path) and os.path.getsize(xml_path) > 0:
        try:
            import xml.etree.ElementTree as ET
            xml_data = None
            for enc in ['utf-8-sig', 'utf-8', 'cp1250', 'windows-1250']:
                try:
                    with open(xml_path, 'r', encoding=enc) as f: xml_data = f.read()
                    break
                except UnicodeDecodeError: continue
            if xml_data:
                xml_data = re.sub(r'<\?xml.*?\?>', '', xml_data, flags=re.IGNORECASE)
                root = ET.fromstring(xml_data)
                rows = []
                for pol in root.findall('.//polozka'):
                    row_data = {}
                    for child in pol:
                        if len(child) > 0: 
                            for subchild in child: row_data[subchild.tag.lower()] = subchild.text if subchild.text else ""
                        else: row_data[child.tag.lower()] = child.text if child.text else ""
                    rows.append(row_data)
                if rows: return pd.DataFrame(rows)
        except Exception: pass
    if os.path.exists(csv_path): 
        for enc in ("utf-8-sig", "utf-8", "cp1250", "windows-1250"):
            try: return pd.read_csv(csv_path, sep=";", encoding=enc, on_bad_lines='skip')
            except Exception: continue
    return None

def load_local_customers() -> Optional[pd.DataFrame]:
    """Služba pro načtení lokálních zákazníků."""
    base_path = os.path.join("data", "ceniky", "zakaznici")
    df = safe_read_data_io(base_path)
    if df is not None and not df.empty:
        def clean_col(c): 
            s = str(c).strip().lower()
            return "".join(char for char in unicodedata.normalize("NFKD", s) if not unicodedata.combining(char))
        df.columns = [clean_col(c) for c in df.columns]
        if "ico" in df.columns: df["ico"] = df["ico"].apply(clean_ico)
        return df
    return None

def find_customer_by_ico_local(ico: Any) -> Optional[Dict[str, Any]]:
    ico_clean = clean_ico(ico)
    if not validate_ico(ico_clean): return None
    df = load_local_customers()
    if df is None or "ico" not in df.columns: return None
    row = df[df["ico"] == ico_clean]
    if row.empty: return None
    return row.iloc[0].to_dict()

def build_form_data_from_customer(ico: Any) -> Optional[Dict[str, Any]]:
    cust = find_customer_by_ico_local(ico)
    if not cust: return None
    return {
        "ICO": clean_ico(cust.get("ico", "")),
        "DIC": cust.get("dic", ""),
        "FIRMA": cust.get("firma", ""),
        "ULICE": cust.get("ulice", ""),
        "CP": cust.get("cp", ""),
        "CO": cust.get("co", ""),
        "ADRESA3": cust.get("mesto", ""),
        "PSC": cust.get("psc", ""),
        "KONTAKT": cust.get("kontakt", ""),
        "TELEFON": cust.get("telefon", ""),
        "EMAIL": cust.get("email", ""),
        "UCET": cust.get("ucet", ""),
        "POZNAMKA": cust.get("poznamka", "")
    }

def service_import_all_ceniky() -> str:
    """Komplexní služba pro import ceníků s normalizací."""
    log_messages: List[str] = []
    connection = sqlite3.connect(DB_PATH)
    try:
        for ui_key, name in CATEGORY_MAP.items():
            base_path = os.path.join(CSV_FOLDER, name)
            table_name = normalize_category_to_table(ui_key)
            df = safe_read_data_io(base_path)
            if df is None: continue

            df.columns = [normalize_column_name(col) for col in df.columns]
            if 'zbozi_nazev' in df.columns: df.rename(columns={'zbozi_nazev': 'nazev'}, inplace=True)
            if 'zbozi_cena' in df.columns: df.rename(columns={'zbozi_cena': 'cena'}, inplace=True)
            if 'ukon_popis' in df.columns: df.rename(columns={'ukon_popis': 'nazev'}, inplace=True)
            if 'ukon_cena' in df.columns: df.rename(columns={'ukon_cena': 'cena'}, inplace=True)

            if "nazev" not in df.columns or "cena" not in df.columns: continue

            df = df.dropna(subset=["nazev", "cena"])
            df["nazev"] = df["nazev"].astype(str).str.strip()
            df = df[df["nazev"] != "nan"]
            df = df[df["nazev"] != ""]
            df = df.drop_duplicates(subset=["nazev"], keep="first")
            df["cena"] = df["cena"].apply(normalize_price)

            valid_cols = [col for col in df.columns if col in ["nazev", "cena"]]
            try:
                df[valid_cols].to_sql(table_name, connection, if_exists="replace", index=False)
                log_messages.append(f"✅ Načteno: {name} ({len(df)} položek)")
            except Exception as e: log_messages.append(f"❌ {name}: Chyba DB – {e}")
                
        # Import Skladu
        expimp_base = os.path.join(CSV_FOLDER, "expimp")
        df_exp = safe_read_data_io(expimp_base)
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
                df_clean = df_clean[df_clean['nazev'] != "nan"]
                df_clean = df_clean[df_clean['nazev'] != ""]
                df_clean = df_clean.drop_duplicates(subset=["nazev"], keep="first")
                try:
                    df_clean.to_sql("cenik_zbozi", connection, if_exists="append", index=False)
                    log_messages.append(f"📦 ÚSPĚCH: Zboží z expimp spárováno.")
                except Exception: pass

    finally:
        connection.close()
    return "\n".join(log_messages) if log_messages else "Žádné soubory k načtení."

def service_calculate_billing(df_evid: pd.DataFrame) -> Tuple[Dict, str]:
    """Hlavní byznys logika: Počítá fakturaci z předané tabulky evidence."""
    df_clean = df_evid.copy()
    df_clean['typ_hp'] = df_clean['typ_hp'].fillna("").astype(str).str.strip()
    df_clean = df_clean[df_clean['typ_hp'] != '']

    errors = []
    for idx, row in df_clean.iterrows():
        ok, msg = validate_stav_and_duvod(row.get('stav',''), row.get('duvod_nv',''))
        if not ok: errors.append(f"Řádek {idx+1} ({row.get('typ_hp')}): {msg}")

    if errors:
        return {}, "CHYBA VALIDACE:\n" + "\n".join(errors)

    S_count = len(df_clean[df_clean['stav'].isin(['S', 'S-nový'])])
    NO_count = len(df_clean[df_clean['stav'].isin(['NO', 'NOPZ'])])
    NV_count = len(df_clean[df_clean['stav'] == 'NV'])
    total_count = len(df_clean[df_clean['stav'] != 'CH'])
    vyrazene_kody = set(df_clean[df_clean['stav'] == 'NV']['duvod_nv'].dropna().tolist())

    calc_data = {
        "S": S_count, "NO": NO_count, "NV": NV_count, "total": total_count, "vyrazene_kody": vyrazene_kody,
        "df_clean": df_clean
    }
    return calc_data, ""

# =====================================================================
# 📄 LAYER 6: PDF ENGINE (Generátory dokumentů a tisk)
# =====================================================================
def setup_pdf_fonts(pdf: FPDF) -> bool:
    font_files = [("arial.ttf", "arialbd.ttf"), ("ARIAL.TTF", "ARIALBD.TTF"), ("Arial.ttf", "Arialbd.ttf")]
    for reg_font, bold_font in font_files:
        if os.path.exists(reg_font) and os.path.exists(bold_font):
            try:
                pdf.add_font("PismoCZ", "", reg_font, uni=True)
                pdf.add_font("PismoCZ", "B", bold_font, uni=True)
                return True
            except Exception: pass
    return False

class UrbaneKPDF_Letterhead(FPDF):
    def __init__(self, orientation='P'):
        super().__init__(orientation=orientation)
        self.pismo_ok = setup_pdf_fonts(self)
        self.pismo_name = "PismoCZ" if self.pismo_ok else "helvetica"

    def header(self) -> None:
        if not self.pismo_ok: return 
        p = self.pismo_name
        if os.path.exists("logo.png"): self.image("logo.png", x=10, y=10, w=24)
        
        self.set_y(10)
        self.set_font(p, "B", 14)
        self.cell(0, 6, FIRMA_VLASTNI["název"], align="C", ln=True)
        self.set_font(p, "", 9)
        self.cell(0, 4.5, f"Sídlo: {FIRMA_VLASTNI['sídlo']}", align="C", ln=True)
        self.cell(0, 4.5, f"IČO: {FIRMA_VLASTNI['ico']}   DIČ: {FIRMA_VLASTNI['dic']}", align="C", ln=True)
        self.cell(0, 4.5, FIRMA_VLASTNI['zápis'], align="C", ln=True)
        self.cell(0, 4.5, f"Mobil: {FIRMA_VLASTNI['telefony']}", align="C", ln=True)
        self.cell(0, 4.5, f"E-mail: {FIRMA_VLASTNI['email']} | WEB: {FIRMA_VLASTNI['web']}", align="C", ln=True)
        
        self.ln(3)
        self.set_line_width(0.5)
        self.line(10, self.get_y(), self.w - 10, self.get_y())
        self.set_line_width(0.2)
        self.ln(5)

    def footer(self) -> None: pass

def create_doklad_kontroly_pdf(zakaznik: Dict, df_evid: pd.DataFrame, dl_number: str, zakazka: str, technik: str) -> tuple[Optional[bytes], Optional[str]]:
    pdf = UrbaneKPDF_Letterhead(orientation='L')
    if not pdf.pismo_ok: return None, "Chyba fontu (Arial.ttf nenalezen)."
    p = pdf.pismo_name
    s = safe_str

    try:
        pdf.add_page()
        pdf.set_font(p, "B", 14)
        pdf.cell(0, 6, s("DOKLAD O KONTROLE HASICÍCH PŘÍSTROJŮ"), align="C", ln=True)
        pdf.set_font(p, "", 9)
        pdf.cell(0, 5, s("(dle zákona číslo 133 / 85 Sb. a vyhlášky číslo 246 / 2001 Sb.)"), align="C", ln=True)
        pdf.ln(4)

        firma = str(zakaznik.get('FIRMA', ''))
        ico = str(zakaznik.get('ICO', ''))
        pdf.set_font(p, "B", 10)
        pdf.cell(20, 5, s("Zákazník:"), ln=False)
        pdf.set_font(p, "", 10)
        pdf.cell(150, 5, s(f"{firma} (IČO: {ico})"), ln=False)
        
        pdf.set_font(p, "B", 10)
        pdf.cell(30, 5, s("Číslo dokladu:"), ln=False)
        pdf.set_font(p, "", 10)
        pdf.cell(40, 5, s(dl_number), ln=True)
        
        pdf.set_font(p, "B", 10)
        pdf.cell(30, 5, s("Kontrolní technik:"), ln=False)
        pdf.set_font(p, "", 10)
        pdf.cell(140, 5, s(technik), ln=False)
        
        pdf.set_font(p, "B", 10)
        pdf.cell(30, 5, s("Zakázka:"), ln=False)
        pdf.set_font(p, "", 10)
        pdf.cell(40, 5, s(zakazka), ln=True)
        pdf.ln(4)

        col_w = [8, 25, 45, 20, 15, 12, 15, 12, 45, 60, 12, 10]
        h_cols = ["Poř.", "Druh HP", "Typ HP", "Výr. číslo", "Rok výr.", "Měs.", "Tlak.rok", "Měs.", "Objekt", "Umístění", "Stav", "Dův."]
        
        pdf.set_fill_color(230, 230, 230)
        pdf.set_font(p, "B", 8)
        for w, text in zip(col_w, h_cols):
            pdf.cell(w, 6, s(text), border=1, align="C", fill=True)
        pdf.ln()

        pdf.set_font(p, "", 8)
        df_clean = df_evid[df_evid['typ_hp'].fillna("").astype(str).str.strip() != '']
        
        for idx, row in df_clean.iterrows():
            pdf.cell(col_w[0], 5, s(str(idx+1)), border=1, align="C")
            pdf.cell(col_w[1], 5, s(row.get('druh','')), border=1)
            pdf.cell(col_w[2], 5, s(str(row.get('typ_hp',''))[:28]), border=1)
            pdf.cell(col_w[3], 5, s(row.get('vyr_cislo','')), border=1, align="C")
            
            rv = str(row.get('rok_vyr','')).replace('.0','')
            pdf.cell(col_w[4], 5, s(rv if rv not in ['nan','0'] else ''), border=1, align="C")
            mv = str(row.get('mesic_vyr','')).replace('.0','')
            pdf.cell(col_w[5], 5, s(mv if mv not in ['nan','0'] else ''), border=1, align="C")
            tr = str(row.get('tlak_rok','')).replace('.0','')
            pdf.cell(col_w[6], 5, s(tr if tr not in ['nan','0'] else ''), border=1, align="C")
            tm = str(row.get('tlak_mesic','')).replace('.0','')
            pdf.cell(col_w[7], 5, s(tm if tm not in ['nan','0'] else ''), border=1, align="C")
            
            pdf.cell(col_w[8], 5, s(str(row.get('objekt',''))[:28]), border=1)
            pdf.cell(col_w[9], 5, s(str(row.get('misto',''))[:40]), border=1)
            pdf.cell(col_w[10], 5, s(row.get('stav','')), border=1, align="C")
            pdf.cell(col_w[11], 5, s(row.get('duvod_nv','')), border=1, align="C")
            pdf.ln()

        pdf.ln(5)
        pdf.set_font(p, "I", 7)
        pdf.cell(0, 4, s("Vysvětlivky: S = provozuSchopný; NO = Neshodný Opravitelný (dílna); NV = Neshodný Vyřazený (neopravitelný); S-nový = nově dodaný."), ln=True)

        return bytes(pdf.output()), None
    except Exception as e: return None, str(e)

def create_protokol_vyrazeni_pdf(zakaznik: Dict, df_evid: pd.DataFrame, dl_number: str, zakazka: str, technik: str) -> tuple[Optional[bytes], Optional[str]]:
    pdf = UrbaneKPDF_Letterhead(orientation='P')
    if not pdf.pismo_ok: return None, "Chyba fontu."
    p = pdf.pismo_name
    s = safe_str

    try:
        df_clean = df_evid[df_evid['typ_hp'].fillna("").astype(str).str.strip() != '']
        df_nv = df_clean[df_clean['stav'] == 'NV']
        
        pdf.add_page()
        pdf.set_font(p, "B", 14)
        pdf.cell(0, 6, s("POTVRZENÍ O PŘEVZETÍ HP VYŘAZENÝCH Z UŽÍVÁNÍ"), align="C", ln=True)
        pdf.set_font(p, "B", 8)
        pdf.cell(0, 4, s("TOTO POTVRZENÍ NESLOUŽÍ PRO ÚČELY EVIDENCE ODPADŮ VYŽADOVANÉ ZÁK.Č. 185/2001 Sb."), align="C", ln=True)
        pdf.ln(6)

        pdf.set_font(p, "B", 9)
        pdf.cell(40, 5, s("Zákazník:"), ln=False)
        pdf.set_font(p, "", 9)
        pdf.cell(100, 5, s(zakaznik.get('FIRMA', '')), ln=True)
        
        pdf.set_font(p, "B", 9)
        pdf.cell(40, 5, s("Číslo dokladu/Zakázka:"), ln=False)
        pdf.set_font(p, "", 9)
        pdf.cell(100, 5, s(f"{dl_number} / {zakazka}"), ln=True)
        pdf.ln(5)

        if df_nv.empty:
            pdf.set_font(p, "I", 10)
            pdf.cell(0, 10, s("V této zakázce nebyly zjištěny žádné neopravitelné hasicí přístroje k vyřazení."), align="C", ln=True)
        else:
            col_w = [10, 35, 25, 15, 45, 45, 15]
            h_cols = ["Poř.", "Typ HP", "Výr. číslo", "Rok", "Objekt", "Umístění", "Kód Dův."]
            
            pdf.set_fill_color(245, 230, 230)
            pdf.set_font(p, "B", 8)
            for w, text in zip(col_w, h_cols):
                pdf.cell(w, 6, s(text), border=1, align="C", fill=True)
            pdf.ln()

            pdf.set_font(p, "", 8)
            used_codes = set()
            for idx, row in df_nv.iterrows():
                pdf.cell(col_w[0], 5, s(str(idx+1)), border=1, align="C")
                pdf.cell(col_w[1], 5, s(str(row.get('typ_hp',''))[:20]), border=1)
                pdf.cell(col_w[2], 5, s(row.get('vyr_cislo','')), border=1, align="C")
                rv = str(row.get('rok_vyr','')).replace('.0','')
                pdf.cell(col_w[3], 5, s(rv if rv not in ['nan','0'] else ''), border=1, align="C")
                pdf.cell(col_w[4], 5, s(str(row.get('objekt',''))[:25]), border=1)
                pdf.cell(col_w[5], 5, s(str(row.get('misto',''))[:25]), border=1)
                kod = str(row.get('duvod_nv','')).strip()
                pdf.cell(col_w[6], 5, s(kod), border=1, align="C")
                pdf.ln()
                if kod in DUVODY_VYRAZENI and kod != "": used_codes.add(kod)

            pdf.ln(5)
            if used_codes:
                pdf.set_font(p, "B", 8)
                pdf.cell(0, 5, s("Vysvětlení kódů vyřazení:"), ln=True)
                pdf.set_font(p, "", 8)
                for code in sorted(list(used_codes)):
                    pdf.cell(0, 4, s(f"Kód {code}: {DUVODY_VYRAZENI[code]}"), ln=True)

        return bytes(pdf.output()), None
    except Exception as e: return None, str(e)

def create_wservis_dl(zakaznik: Dict, items_dict: Dict, dl_number: str, zakazka: str, technik: str, objekty_map: Dict, typ_dl: str, dl_type_name: str, included_sections: List[str]) -> tuple[Optional[bytes], Optional[str]]:
    pdf = UrbaneKPDF_Letterhead(orientation='P')
    if not pdf.pismo_ok: return None, "Chyba fontu."
    try:
        p = pdf.pismo_name
        pdf.add_page()
        s = safe_str

        pdf.set_font(p, "B", 16)
        pdf.cell(0, 7, s("DODACÍ LIST"), align="C", ln=True)
        pdf.set_font(p, "", 10)
        sub_title = "(práce, zboží, materiál)"
        if dl_type_name == "Kontroly HP": sub_title = "(Kontroly HP, zboží, materiál)"
        elif dl_type_name == "Kontroly PV": sub_title = "(Kontroly PV, zboží, materiál)"
        elif dl_type_name == "Opravy HP": sub_title = "(Opravy HP, zboží, materiál)"
        pdf.cell(0, 5, s(sub_title), align="C", ln=True)
        pdf.ln(5)

        y_meta = pdf.get_y()
        pdf.set_font(p, "B", 9)
        pdf.set_fill_color(235, 235, 235)
        str_dl_typ = "Poř. číslo:" if "Standard" in typ_dl else "Číslo DL:"
        pdf.cell(45, 6, s(str_dl_typ), border=1, align="C", fill=True)
        pdf.cell(45, 6, s("Zakázka:"), border=1, align="C", fill=True)
        pdf.cell(100, 6, s("Kontrolní technik:"), border=1, align="C", fill=True, ln=True)
        
        pdf.set_font(p, "B", 10)
        pdf.cell(45, 7, s(str(dl_number)), border=1, align="C")
        pdf.cell(45, 7, s(str(zakazka)), border=1, align="C")
        pdf.cell(100, 7, s(str(technik)), border=1, align="C", ln=True)
        pdf.ln(6)

        w_name, w_cena, w_col, w_ks, w_celk = 85, 17, 8, 15, 33

        def draw_category(cat_num_title: str, item_cats: List[str]):
            cat_items = [[k, v] for k, v in items_dict.items() if v["cat"] in item_cats and v.get("q", 0) > 0]
            if not cat_items: return False, 0.0

            pdf.set_font(p, "B", 9)
            pdf.set_fill_color(210, 220, 230)
            pdf.cell(190, 6, s(f" {cat_num_title}"), border=1, fill=True, ln=True)
            
            pdf.set_fill_color(240, 240, 240)
            pdf.set_font(p, "B", 8)
            pdf.cell(w_name, 5, s(" Název položky"), border=1, fill=True)
            pdf.cell(w_cena, 5, s("Cena/ks"), border=1, align="C", fill=True)
            pdf.cell(w_col, 5, "O1", border=1, align="C", fill=True)
            pdf.cell(w_col, 5, "O2", border=1, align="C", fill=True)
            pdf.cell(w_col, 5, "O3", border=1, align="C", fill=True)
            pdf.cell(w_col, 5, "O4", border=1, align="C", fill=True)
            pdf.cell(w_col, 5, "O5", border=1, align="C", fill=True)
            pdf.cell(w_ks, 5, s("ks/výk"), border=1, align="C", fill=True)
            pdf.cell(w_celk, 5, s("CELKEM (Kč)"), border=1, align="C", fill=True, ln=True)

            cat_total = 0.0
            pdf.set_font(p, "", 9)
            for name, vals in cat_items:
                qty = vals.get("q", 0)
                price = vals.get("p", 0)
                line_total = qty * price
                cat_total += line_total
                
                clean_name = str(name).strip()
                name_disp = " " + clean_name[:48] + ("..." if len(clean_name) > 48 else "")
                
                pdf.cell(w_name, 6, name_disp, border=1)
                pdf.cell(w_cena, 6, s(format_cena(price)), border=1, align="R")
                pdf.cell(w_col, 6, s(format_cena(vals.get("q1", 0)).replace(',00','')), border=1, align="C")
                pdf.cell(w_col, 6, s(format_cena(vals.get("q2", 0)).replace(',00','')), border=1, align="C")
                pdf.cell(w_col, 6, s(format_cena(vals.get("q3", 0)).replace(',00','')), border=1, align="C")
                pdf.cell(w_col, 6, s(format_cena(vals.get("q4", 0)).replace(',00','')), border=1, align="C")
                pdf.cell(w_col, 6, s(format_cena(vals.get("q5", 0)).replace(',00','')), border=1, align="C")
                pdf.cell(w_ks, 6, s(format_cena(qty).replace(',00','')), border=1, align="C")
                pdf.cell(w_celk, 6, s(format_cena(line_total)), border=1, align="R", ln=True)
                
            pdf.set_font(p, "B", 9)
            pdf.set_fill_color(245, 245, 245)
            n_c = cat_num_title.split('. ', 1)[-1] if '. ' in cat_num_title else cat_num_title
            
            pdf.cell(w_name + w_cena + (5*w_col) + w_ks, 6, s(f"CELKEM za {n_c}: "), border=1, align="R", fill=True)
            pdf.cell(w_celk, 6, s(format_cena(cat_total)), border=1, align="R", fill=True, ln=True)
            pdf.ln(4)
            return True, cat_total

        total_sum = 0.0; sec_num = 1
        if "HP" in included_sections:
            t = f"{sec_num}. KONTROLY HASICÍCH PŘÍSTROJŮ" if "Standard" in typ_dl else f"{sec_num}. KONTROLY OPRAVENÝCH HP"
            d, v = draw_category(t, ["HP"])
            if d: sec_num += 1; total_sum += v
        if "PV" in included_sections:
            d, v = draw_category(f"{sec_num}. KONTROLY ZAŘÍZENÍ PRO ZÁSOBOVÁNÍ POŽÁRNÍ VODOU", ["Voda"])
            if d: sec_num += 1; total_sum += v
        if "OPRAVY" in included_sections:
            d, v = draw_category(f"{sec_num}. OPRAVY HASICÍCH PŘÍSTROJŮ", ["Opravy"])
            if d: sec_num += 1; total_sum += v
        if "NAHRADY" in included_sections:
            d, v = draw_category(f"{sec_num}. VYHODNOCENÍ KONTROLY + NÁHRADY", ["Nahrady", "Servisni_ukony"])
            if d: sec_num += 1; total_sum += v
        if "ZBOZI" in included_sections:
            d, v = draw_category(f"{sec_num}. PRODEJ ZBOŽÍ, MATERIÁLU A ND", ["ND_HP", "ND_Voda", "TAB", "TABFOTO", "HILTI", "CIDLO", "PASKA", "PK", "reklama", "FA", "Zboží", "Ostatni", "OZO"])
            if d: sec_num += 1; total_sum += v

        pdf.set_font(p, "B", 10); pdf.set_fill_color(220, 220, 220)
        pdf.cell(157, 8, s("CELKEM K ÚHRADĚ BEZ DPH: "), border=1, align="R", fill=True)
        pdf.cell(33, 8, s(f"{format_cena(total_sum)} Kč"), border=1, align="R", fill=True, ln=True); pdf.ln(8)

        # Patička
        ul, cp, co = str(zakaznik.get("ULICE","")), str(zakaznik.get("CP","")), str(zakaznik.get("CO",""))
        adr_line1 = f"{ul} {cp}" + (f"/{co}" if co and co != "0" else "") if ul else ""
        adr_line2 = f"{zakaznik.get('PSC', '')} {zakaznik.get('ADRESA3', '')}".strip()
        y_base = pdf.get_y()
        if y_base > 220: pdf.add_page(); pdf.set_font(p, "", 9); y_base = pdf.get_y()
        pdf.rect(10, y_base, 95, 32); pdf.rect(105, y_base, 95, 32)
        f_umisteni = " Umístění kontrolovaných HP/PV v objektech:"
        if dl_type_name == "Kontroly PV": f_umisteni = " Umístění kontrolovaných PV v objektech:"
        elif dl_type_name == "Opravy HP": f_umisteni = " Umístění opravených HP v objektech:"
        elif dl_type_name == "Kontroly HP": f_umisteni = " Umístění kontrolovaných HP v objektech:"
        pdf.set_fill_color(235, 235, 235); pdf.set_font(p, "B", 9)
        pdf.set_xy(10, y_base); pdf.cell(95, 6, s(" Odběratel:"), border=1, fill=True)
        pdf.set_xy(105, y_base); pdf.cell(95, 6, s(f_umisteni), border=1, fill=True)
        pdf.set_font(p, "", 9)
        pdf.set_xy(10, y_base + 6); pdf.cell(95, 5, s(f"  Firma: {zakaznik.get('FIRMA', '')[:45]}"))
        pdf.set_xy(105, y_base + 6); pdf.cell(95, 5, s(f"  O1: {objekty_map.get(1, '')[:45]}"))
        pdf.set_xy(10, y_base + 11); pdf.cell(95, 5, s(f"  Ulice: {adr_line1[:45]}"))
        pdf.set_xy(105, y_base + 11); pdf.cell(95, 5, s(f"  O2: {objekty_map.get(2, '')[:45]}"))
        pdf.set_xy(10, y_base + 16); pdf.cell(95, 5, s(f"  Město: {adr_line2[:45]}"))
        pdf.set_xy(105, y_base + 16); pdf.cell(95, 5, s(f"  O3: {objekty_map.get(3, '')[:45]}"))
        pdf.set_xy(10, y_base + 21); pdf.cell(95, 5, s(f"  IČO: {zakaznik.get('ICO', '')}"))
        pdf.set_xy(105, y_base + 21); pdf.cell(95, 5, s(f"  O4: {objekty_map.get(4, '')[:45]}"))
        pdf.set_xy(10, y_base + 26); pdf.cell(95, 5, s(f"  DIČ: {zakaznik.get('DIC', '')}"))
        pdf.set_xy(105, y_base + 26); pdf.cell(95, 5, s(f"  O5: {objekty_map.get(5, '')[:45]}"))
        y_sig = y_base + 35
        if y_sig + 30 > 280: pdf.add_page(); pdf.set_font(p, "", 9); y_sig = pdf.get_y()
        pdf.rect(10, y_sig, 190, 26); pdf.set_font(p, "B", 9)
        pdf.set_xy(10, y_sig); pdf.cell(190, 6, s(" Záznam o kontrole a předání:"), border=1, fill=True)
        pdf.set_font(p, "B", 8); pdf.set_xy(10, y_sig + 6); pdf.cell(95, 5, s("  Za zhotovitele (Předal):"), border="R"); pdf.cell(95, 5, s("  Za odběratele (Převzal):"))
        pdf.set_font(p, "", 9); pdf.set_xy(10, y_sig + 11); pdf.cell(95, 5, s(f"  Kontrolní technik: {technik}"), border="R"); pdf.cell(95, 5, s("  Jméno hůlkovým písmem:"))
        pdf.set_xy(10, y_sig + 16); pdf.cell(95, 5, s("  Odborně způsobilá osoba v PO: Ilja Urbánek"), border="R"); pdf.cell(95, 5, s("  ........................................................................."))
        pdf.set_font(p, "", 7); pdf.set_xy(10, y_sig + 21); pdf.cell(95, 5, s("   Podpisy a razítka zhotovitele"), border="R"); pdf.cell(95, 5, s("   Podpis a razítko odběratele"))
        return bytes(pdf.output()), None
    except Exception as e: return None, str(e)

# =====================================================================
# 🌐 LAYER 7: STREAMLIT UI (Front-End)
# =====================================================================
init_db()

# Bezpečné založení Session State
if "data_zakazky" not in st.session_state: st.session_state["data_zakazky"] = {}
if "dynamic_items" not in st.session_state: st.session_state["dynamic_items"] = {}
if "vybrany_zakaznik" not in st.session_state: st.session_state["vybrany_zakaznik"] = None
if "loaded_ico" not in st.session_state: st.session_state["loaded_ico"] = None
if "evidence_df" not in st.session_state: st.session_state["evidence_df"] = pd.DataFrame()
if "auto_kalkulace" not in st.session_state: 
    st.session_state["auto_kalkulace"] = {"S": 0, "NO": 0, "NV": 0, "total": 0, "vyrazene_kody": set()}

st.markdown("""<style> .stTabs [aria-selected="true"] { background-color: #ff4b4b; color: white; font-weight: bold; } .cart-box { background-color: #f8f9fa; padding: 15px; border-radius: 8px; border-left: 5px solid #ff4b4b; } .evidence-box { border: 2px solid #28a745; padding: 15px; border-radius: 8px; background-color: #f9fff9; } </style>""", unsafe_allow_html=True)

df_customers = load_local_customers()
if df_customers is None:
    try:
        conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
        df_customers = pd.read_sql("SELECT * FROM obchpartner", conn); conn.close()
    except: pass

menu_volba = st.sidebar.radio("Navigace systému:", ["📝 Zpracování zakázky (Evidence & DL)", "🗄️ Katalog a Sklad (Ceníky)", "📊 Obchodní Velín (50:50)"])

if menu_volba == "📝 Zpracování zakázky (Evidence & DL)":
    with st.sidebar:
        st.header("🏢 Hlavička dokladů")
        typ_dl = st.radio("Hlavička 1. sekce DL:", ["Standard (Kontroly)", "Opravy (Prior)"])
        dl_number = st.text_input("Číslo dokladu / DL:", value="1698")
        zakazka = st.text_input("Číslo zakázky:", value="1/13")
        technik = st.text_input("Kontrolní technik:", value="Tomáš Urbánek"); st.divider()
        aktualni_ico = ""; ulozene_objekty = []
        
        if df_customers is not None and not df_customers.empty:
            filt = df_customers.copy()
            filt["FIRMA"] = filt["FIRMA"].fillna("Neznámý název")
            filt["clean_ico"] = filt["ICO"].apply(clean_ico)
            filt = filt.drop_duplicates(subset=["clean_ico", "FIRMA"]).sort_values(by="FIRMA")
            opts = filt.apply(lambda r: f"{r['FIRMA']}  |  IČO: {r['clean_ico']}", axis=1).tolist()
            def_idx = 0; vybrany_zak = st.session_state.get("vybrany_zakaznik")
            if vybrany_zak:
                curr_ico = clean_ico(vybrany_zak.get("ICO", ""))
                for idx_opt, opt in enumerate(opts):
                    if f"IČO: {curr_ico}" in opt: def_idx = idx_opt; break
            sel = st.selectbox("🔍 Vyhledat odběratele:", options=opts, index=def_idx)
            curr = filt.iloc[opts.index(sel)].to_dict()
            if (vybrany_zak is None or clean_ico(vybrany_zak.get("ICO")) != clean_ico(curr.get("ICO"))):
                local_data = build_form_data_from_customer(clean_ico(curr.get("ICO")))
                if local_data: curr.update(local_data)
                st.session_state["vybrany_zakaznik"] = curr.copy()
            aktualni_ico = clean_ico(curr.get("ICO")); ulozene_objekty = get_objects_from_db(aktualni_ico)
            ul_kl, cp_kl, ob_kl = str(curr.get("ULICE","")), str(curr.get("CP","")), str(curr.get("ADRESA3",""))
            adr_slozena = f"{ul_kl} {cp_kl}, {ob_kl}" if ul_kl else ob_kl
            if adr_slozena and adr_slozena not in ulozene_objekty: ulozene_objekty.insert(0, adr_slozena)

        st.subheader("🏢 Správa objektů")
        if aktualni_ico:
            with st.expander("➕ Přidat nový objekt"):
                with st.form("add_obj_form", clear_on_submit=True):
                    novy_objekt = st.text_input("Název objektu")
                    if st.form_submit_button("Uložit") and novy_objekt.strip():
                        add_object_to_db(aktualni_ico, novy_objekt); st.rerun()

        celkem_cena = sum(v["q"] * v.get("p", 0) for v in st.session_state.get("data_zakazky", {}).values())
        celkem_cena += sum(v["q"] * v.get("p", 0) for v in st.session_state.get("dynamic_items", {}).values())
        st.markdown(f"""<div class="cart-box"><b>🛒 Fakturace celkem:</b><br/>{format_cena(celkem_cena)} Kč bez DPH</div>""", unsafe_allow_html=True)
        if st.button("🗑️ Vyprázdnit zakázku", use_container_width=True):
            st.session_state["data_zakazky"] = {}; st.session_state["dynamic_items"] = {}
            st.session_state["auto_kalkulace"] = {"S": 0, "NO": 0, "NV": 0, "total": 0, "vyrazene_kody": set()}
            st.session_state["evidence_df"] = pd.DataFrame(); st.rerun()

    st.title("🛡️ Zpracování zakázky (ERP Modul)"); st.caption("Verze 46.3 Logic Integrity Restore")
    st.markdown("### 🏢 Rozřazení objektů (O1 - O5)")
    cO1, cO2, cO3, cO4, cO5 = st.columns(5)
    with cO1: show_o1 = st.checkbox("O1", value=True); o1_name = st.selectbox("Objekt 1:", [""] + ulozene_objekty, key="o1_sel") if show_o1 else ""
    with cO2: show_o2 = st.checkbox("O2", value=False); o2_name = st.selectbox("Objekt 2:", [""] + ulozene_objekty, key="o2_sel") if show_o2 else ""
    with cO3: show_o3 = st.checkbox("O3", value=False); o3_name = st.selectbox("Objekt 3:", [""] + ulozene_objekty, key="o3_sel") if show_o3 else ""
    with cO4: show_o4 = st.checkbox("O4", value=False); o4_name = st.selectbox("Objekt 4:", [""] + ulozene_objekty, key="o4_sel") if show_o4 else ""
    with cO5: show_o5 = st.checkbox("O5", value=False); o5_name = st.selectbox("Objekt 5:", [""] + ulozene_objekty, key="o5_sel") if show_o5 else ""
    mapa_objektu_pro_pdf = {1: o1_name, 2: o2_name, 3: o3_name, 4: o4_name, 5: o5_name}

    tabs = st.tabs(["📝 1. Evidence HP", "💰 2. Auto-Fakturace & Náhrady", "🛠️ 3. Opravy & Voda", "🛒 4. Zboží & Materiál", "🖨️ 5. TISK DOKLADŮ"])

    with tabs[0]:
        if "show_success" in st.session_state: st.success(st.session_state.pop("show_success"))
        st.markdown("<div class='evidence-box'>", unsafe_allow_html=True); st.markdown("### 📋 Technická Evidence HP")
        if not st.session_state.get("vybrany_zakaznik"): st.warning("Vyberte zákazníka v levém panelu.")
        else:
            if st.session_state.get("loaded_ico") != aktualni_ico or st.session_state.get("evidence_df", pd.DataFrame()).empty:
                conn = sqlite3.connect(DB_PATH); df_evid = pd.read_sql("SELECT druh, typ_hp, vyr_cislo, rok_vyr, mesic_vyr, tlak_rok, tlak_mesic, stav, duvod_nv, objekt, misto FROM evidence_hp WHERE ico = ?", conn, params=(aktualni_ico,))
                if df_evid.empty:
                    df_evid = pd.DataFrame(columns=["druh", "typ_hp", "vyr_cislo", "rok_vyr", "mesic_vyr", "tlak_rok", "tlak_mesic", "stav", "duvod_nv", "objekt", "misto"])
                    for i in range(5): df_evid.loc[i] = ["přenosný", "", "", None, None, None, None, "S", "", "", ""]
                st.session_state["evidence_df"] = df_evid; st.session_state["loaded_ico"] = aktualni_ico; conn.close()
            edited_evid = st.data_editor(st.session_state.get("evidence_df", pd.DataFrame()), num_rows="dynamic", use_container_width=True, key="evidence_editor_safe", column_config={
                "druh": st.column_config.SelectboxColumn("Druh", options=["přenosný", "pojízdný", "přívěsný", "AHS"], width="small"), "typ_hp": st.column_config.TextColumn("Typ HP", width="medium"), "vyr_cislo": st.column_config.TextColumn("Výr. číslo", width="small"), "rok_vyr": st.column_config.NumberColumn("Rok", format="%d", width="small"), "mesic_vyr": st.column_config.NumberColumn("Měs.", width="small"), "tlak_rok": st.column_config.NumberColumn("Tlak. Rok", format="%d", width="small"), "tlak_mesic": st.column_config.NumberColumn("Tlak. Měs", width="small"), "stav": st.column_config.SelectboxColumn("Stav", options=STAVY_HP, width="small", required=True), "duvod_nv": st.column_config.SelectboxColumn("Důvod NV", options=list(DUVODY_VYRAZENI.keys()), width="small"), "objekt": st.column_config.SelectboxColumn("Objekt (Budova)", options=ulozene_objekty if ulozene_objekty else [""], width="medium"), "misto": st.column_config.TextColumn("Přesné umístění", width="medium")})
            if st.button("💾 Uložit evidenci a PŘEPOČÍTAT Fakturaci", type="primary", use_container_width=True):
                calc_data, err_msg = service_calculate_billing(edited_evid)
                if err_msg: st.error(err_msg)
                else:
                    save_hp_evidence_to_db(aktualni_ico, calc_data["df_clean"]); st.session_state["evidence_df"] = edited_evid; st.session_state["auto_kalkulace"] = calc_data
                    st.session_state["q1_h1"] = float(calc_data["S"]); st.session_state["q1_h2"] = float(calc_data["NO"]); st.session_state["q1_h3"] = float(calc_data["NV"]); st.session_state["q1_s_hp1"] = float(calc_data["total"])
                    for r in ["h1", "h2", "h3", "s_hp1"]:
                        for i in range(2, 6): st.session_state[f"q{i}_{r}"] = 0.0
                    st.session_state["show_success"] = f"✅ Kalkulace hotova! Nalezeno {calc_data['total']} přístrojů. Fakturace byla bezpečně propsána."; st.rerun()
        st.markdown("</div>", unsafe_allow_html=True)

    def draw_item_row(cat, name, price, row_id, is_auto=False):
        p_val = get_price(cat, name) or price; cols = st.columns([3.5, 1.5] + ([1.0]*sum([show_o1, show_o2, show_o3, show_o4, show_o5])))
        with cols[0]: st.markdown(f"{'🤖 ' if is_auto else ''}**{name}**")
        with cols[1]: p = st.number_input(f"P_{row_id}", 0.0, step=0.1, value=float(p_val), key=f"p_{row_id}", label_visibility="collapsed")
        idx = 2; q_tot = 0.0; q_vals = {}
        for i in range(1, 6):
            if globals()[f"show_o{i}"]:
                q = st.number_input(f"{i}_{row_id}", 0.0, value=float(st.session_state.get(f"q{i}_{row_id}", 0.0)), key=f"q{i}_{row_id}", label_visibility="collapsed")
                q_vals[f"q{i}"] = q; q_tot += q; idx += 1
            else: q_vals[f"q{i}"] = 0.0
        st.session_state["data_zakazky"][name] = {**q_vals, "q": q_tot, "p": p, "cat": cat}

    with tabs[1]:
        st.markdown("### Automaticky načtené úkony"); draw_item_row("HP", "Kontrola HP (shodný)", 29.4, "h1", True); draw_item_row("HP", "Kontrola HP (neshodný - opravitelný)", 19.7, "h2", True); draw_item_row("HP", "Kontrola HP (neshodný - neopravitelný) + odborné zneprovoznění", 23.5, "h3", True); draw_item_row("Servisni_ukony", "Vyhodnocení kontroly + vystavení dokladu o kontrole (á 1ks HP)", 5.8, "s_hp1", True)
        st.divider(); st.markdown("### Manuální úkony & Náhrady"); draw_item_row("HP", "Manipulace a odvoz HP ze servisu (opravy)", 24.0, "h4"); draw_item_row("Nahrady", "Náhrada za 1km - osobní servisní vozidlo", 6.0, "n4"); draw_item_row("Nahrady", "Převzetí HP vyřazeného z užívání dodavatelem", 88.0, "n1")

    with tabs[2]:
        st.subheader("Opravy & Voda"); draw_item_row("Opravy", "CO2-5F/ETS", 418.0, "opr1"); draw_item_row("Voda", "Kontrola zařízení bez měření průtoku do 5 ks výtoků", 141.0, "v2")

    with tabs[3]:
        st.subheader("Prodej zboží"); db_items = get_items_from_db(["Zboží", "ND_HP", "ND_Voda", "TAB", "HILTI", "zbozi"])
        if db_items:
            items_dict = {i["nazev"]: i for i in db_items}
            zcols = st.columns([3, 1, 1, 1]); sel_z = zcols[0].selectbox("Vyberte:", ["-- Vyberte --"] + list(items_dict.keys()))
            if sel_z != "-- Vyberte --":
                pz = zcols[1].number_input("Cena", value=items_dict[sel_z]["cena"]); qz = zcols[2].number_input("O1", value=1.0)
                if zcols[3].button("➕ Přidat"): st.session_state["dynamic_items"][sel_z] = {"q1":qz, "q2":0, "q3":0, "q4":0, "q5":0, "q":qz, "p":pz, "cat":"Zboží"}; st.rerun()
        if st.session_state.get("dynamic_items"):
            for k, v in list(st.session_state["dynamic_items"].items()):
                ca, cb, cc, cd = st.columns([5, 2, 2, 1]); ca.write(f"• {k}"); cb.write(f"{v['q']} ks"); cc.write(f"{v['q']*v['p']:,.2f} Kč"); 
                if cd.button("❌", key=f"del_{k}"): del st.session_state["dynamic_items"][k]; st.rerun()

    with tabs[4]:
        st.markdown("### 🖨️ Tiskové Centrum"); if not st.session_state.get("vybrany_zakaznik"): st.error("Vyberte zákazníka!")
        else:
            akt_zak = st.session_state["vybrany_zakaznik"]; akt_firm = akt_zak.get("FIRMA","Neznamy")
            a_items = {k:v for k,v in st.session_state.get("data_zakazky",{}).items() if v.get("q",0)>0}
            a_items.update({k:v for k,v in st.session_state.get("dynamic_items",{}).items() if v.get("q",0)>0})
            c1, c2, c3 = st.columns(3)
            with c1:
                if st.button("📄 DOKLAD O KONTROLE HP", use_container_width=True):
                    pb, err = create_doklad_kontroly_pdf(akt_zak, st.session_state["evidence_df"], dl_number, zakazka, technik)
                    if not err: st.download_button("⬇️ Stáhnout DoK", pb, f"DoK_{dl_number}.pdf", "application/pdf", key="dok_dl")
            with c2:
                if st.button("📄 DL: Kontroly HP", type="primary", use_container_width=True):
                    pb, err = create_wservis_dl(akt_zak, a_items, dl_number, zakazka, technik, mapa_objektu_pro_pdf, typ_dl, "Kontroly HP", ["HP", "NAHRADY", "ZBOZI"])
                    if not err: st.download_button("⬇️ Stáhnout DL", pb, f"DL_{dl_number}.pdf", "application/pdf", key="dl_main")
            with c3:
                if st.button("⚠️ PROTOKOL O VYŘAZENÍ", use_container_width=True):
                    pb, err = create_protokol_vyrazeni_pdf(akt_zak, st.session_state["evidence_df"], dl_number, zakazka, technik)
                    if not err: st.download_button("⬇️ Stáhnout Protokol", pb, f"LP_{dl_number}.pdf", "application/pdf", key="lp_dl")

elif menu_volba == "🗄️ Katalog a Sklad (Ceníky)":
    st.title("🗄️ Katalog a Sklad")
    et = st.tabs(["📦 Ceník", "⚙️ Import"])
    with et[0]:
        if os.path.exists(DB_PATH):
            conn = sqlite3.connect(DB_PATH); dfz = pd.read_sql("SELECT nazev, cena FROM cenik_zbozi ORDER BY nazev", conn); conn.close()
            st.data_editor(dfz, use_container_width=True)
    with et[1]:
        if st.button("🚀 Synchronizovat s W-SERVIS (Import)"):
            msg = service_import_all_ceniky(); st.success("Synchronizace dokončena"); st.code(msg)

elif menu_volba == "📊 Obchodní Velín (50:50)":
    st.title("📊 Obchodní Velín"); u_file = st.file_uploader("Nahrajte Migrace_Centraly_Navrh.csv", type=['csv'])
    if u_file:
        dfv = pd.read_csv(u_file, sep=';', encoding='utf-8-sig'); st.success(f"Načteno {len(dfv)} záznamů"); st.metric("Celkem úkonů", len(dfv)); st.dataframe(dfv)

st.sidebar.divider(); st.sidebar.caption(f"© {datetime.date.today().year} {FIRMA_VLASTNI['název']}")
