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

# ==========================================
# ČÍSELNÍKY DLE W-SERVIS DATABÁZE
# ==========================================
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

STAVY_HP = ["S", "NO", "NOPZ", "CH", "S-nový", "NV"]

CATEGORY_MAP: Dict[str, str] = {
    "HP": "HP", "Nahrady": "Nahrady", "Voda": "VODA", "Ostatni": "ostatni",
    "ND_HP": "ND_HP", "ND_Voda": "ND_VODA", "FA": "FA", "TAB": "TAB",
    "TABFOTO": "TABFOTO", "HILTI": "HILTI", "CIDLO": "CIDLO", "PASKA": "PASKA",
    "PK": "PK", "OZO": "OZO", "reklama": "reklama", "Servisni_ukony": "revize",
    "Opravy": "opravy", "Zboží": "zbozi"
}

# ==========================================
# 1. KONFIGURACE FIRMY A DB
# ==========================================
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

DB_PATH = "data/data.db"
CSV_FOLDER = "data/ceniky/"

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

init_db()

# Inicializace session state
if "data_zakazky" not in st.session_state: st.session_state.data_zakazky = {}
if "dynamic_items" not in st.session_state: st.session_state.dynamic_items = {}
if "vybrany_zakaznik" not in st.session_state: st.session_state.vybrany_zakaznik = None
if "loaded_ico" not in st.session_state: st.session_state.loaded_ico = None
if "evidence_df" not in st.session_state: st.session_state.evidence_df = pd.DataFrame()
if "auto_kalkulace" not in st.session_state: 
    st.session_state.auto_kalkulace = {"S": 0, "NO": 0, "NV": 0, "total": 0, "vyrazene_kody": set()}

# ==========================================
# 2. DATABÁZOVÉ A POMOCNÉ FUNKCE
# ==========================================
def normalize_category_to_table(cat_key: str) -> str:
    if not cat_key: return "cenik_ostatni"
    normalized = cat_key.lower().strip()
    normalized = "".join(char for char in unicodedata.normalize("NFKD", normalized) if not unicodedata.combining(char))
    normalized = re.sub(r"[\s/]+", "_", normalized)
    normalized = re.sub(r"[^a-z0-9_]", "", normalized)
    return f"cenik_{normalized}"

def safe_read_data(base_path: str) -> Optional[pd.DataFrame]:
    excel_path = base_path + ".xlsx"
    xml_path = base_path + ".xml"
    csv_path = base_path + ".csv"
    if os.path.exists(excel_path):
        try: return pd.read_excel(excel_path)
        except Exception: pass
    if os.path.exists(csv_path): 
        for enc in ("utf-8-sig", "utf-8", "cp1250", "windows-1250"):
            try: return pd.read_csv(csv_path, sep=";", encoding=enc, on_bad_lines='skip')
            except Exception: continue
    return None

def clean_ico(ico_val: Any) -> str:
    s = str(ico_val).strip()
    if s.lower() in ['nan', 'none', 'null', '']: return ""
    return s.split('.')[0]

def validate_ico(ico: Any) -> bool:
    ico_str = clean_ico(ico)
    if not ico_str.isdigit() or len(ico_str) != 8: return False
    digits = [int(d) for d in ico_str]
    weights = [8, 7, 6, 5, 4, 3, 2]
    s = sum(d * w for d, w in zip(digits[:7], weights))
    r = s % 11
    c = 1 if r == 0 else (0 if r == 1 else 11 - r)
    return digits[7] == c

def load_local_customers() -> Optional[pd.DataFrame]:
    base_path = os.path.join("data", "ceniky", "zakaznici")
    df = safe_read_data(base_path)
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

def setup_pdf_fonts(pdf: FPDF) -> bool:
    font_files = [("arial.ttf", "arialbd.ttf"), ("ARIAL.TTF", "ARIALBD.TTF"), ("Arial.ttf", "Arialbd.ttf")]
    for reg_font, bold_font in font_files:
        if os.path.exists(reg_font) and os.path.exists(bold_font):
            try:
                pdf.add_font("PismoCZ", "", reg_font, uni=True)
                pdf.add_font("PismoCZ", "B", bold_font, uni=True)
                return True
            except Exception: pass
    try:
        if not os.path.exists("dejavu.ttf"):
            req = urllib.request.Request("https://raw.githubusercontent.com/matumo/DejaVuSans/master/Fonts/DejaVuSans.ttf", headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=10) as response, open("dejavu.ttf", 'wb') as out_file: out_file.write(response.read())
        if not os.path.exists("dejavu-bold.ttf"):    
            req_b = urllib.request.Request("https://raw.githubusercontent.com/matumo/DejaVuSans/master/Fonts/DejaVuSans-Bold.ttf", headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req_b, timeout=10) as response, open("dejavu-bold.ttf", 'wb') as out_file: out_file.write(response.read())
        pdf.add_font("PismoCZ", "", "dejavu.ttf", uni=True)
        pdf.add_font("PismoCZ", "B", "dejavu-bold.ttf", uni=True)
        return True
    except Exception: pass
    return False

def safe_str(txt, is_pismo_ok):
    return str(txt).replace('\n', ' ').replace('\r', '').strip()

def format_cena(num):
    if num == 0: return "0,00"
    st = f"{num:,.2f}".replace(",", "X").replace(".", ",").replace("X", " ")
    if st.endswith(",00"): return st[:-3]
    if st.endswith("0") and "," in st: return st[:-1]
    return st

# ==========================================
# 3. PDF ENGINE (GENERÁTORY DOKUMENTŮ)
# ==========================================

class UrbaneKPDF_Letterhead(FPDF):
    """Základní třída pro dokumenty s majestátní hlavičkou (DL, Protokoly)"""
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

    def footer(self) -> None:
        pass

# --- 3A. DOKLAD O KONTROLE HP (Landscape) ---
def create_doklad_kontroly_pdf(zakaznik: Dict, df_evid: pd.DataFrame, dl_number: str, zakazka: str, technik: str) -> tuple[Optional[bytes], Optional[str]]:
    pdf = UrbaneKPDF_Letterhead(orientation='L')
    if not pdf.pismo_ok: return None, "Chyba fontu (Arial.ttf)."
    p = pdf.pismo_name
    def s(t): return safe_str(t, True)

    try:
        pdf.add_page()
        
        # Nadpis
        pdf.set_font(p, "B", 14)
        pdf.cell(0, 6, s("DOKLAD O KONTROLE HASICÍCH PŘÍSTROJŮ"), align="C", ln=True)
        pdf.set_font(p, "", 9)
        pdf.cell(0, 5, s("(dle zákona číslo 133 / 85 Sb. a vyhlášky číslo 246 / 2001 Sb.)"), align="C", ln=True)
        pdf.ln(4)

        # Hlavička zákazníka
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

        # Tabulka Evidence (Široká)
        col_w = [8, 25, 45, 20, 15, 12, 15, 12, 45, 60, 12, 10]
        h_cols = ["Poř.", "Druh HP", "Typ HP", "Výr. číslo", "Rok výr.", "Měs.", "Tlak.rok", "Měs.", "Objekt", "Umístění", "Stav", "Dův."]
        
        pdf.set_fill_color(230, 230, 230)
        pdf.set_font(p, "B", 8)
        for w, text in zip(col_w, h_cols):
            pdf.cell(w, 6, s(text), border=1, align="C", fill=True)
        pdf.ln()

        pdf.set_font(p, "", 8)
        for idx, row in df_evid.iterrows():
            pdf.cell(col_w[0], 5, s(str(idx+1)), border=1, align="C")
            pdf.cell(col_w[1], 5, s(row.get('druh','')), border=1)
            typ_text = str(row.get('typ_hp',''))
            pdf.cell(col_w[2], 5, s(typ_text[:28]), border=1)
            pdf.cell(col_w[3], 5, s(row.get('vyr_cislo','')), border=1, align="C")
            
            rv = str(row.get('rok_vyr','')).replace('.0','')
            if rv == 'nan' or rv == '0': rv = ''
            pdf.cell(col_w[4], 5, s(rv), border=1, align="C")
            
            mv = str(row.get('mesic_vyr','')).replace('.0','')
            if mv == 'nan' or mv == '0': mv = ''
            pdf.cell(col_w[5], 5, s(mv), border=1, align="C")
            
            tr = str(row.get('tlak_rok','')).replace('.0','')
            if tr == 'nan' or tr == '0': tr = ''
            pdf.cell(col_w[6], 5, s(tr), border=1, align="C")
            
            tm = str(row.get('tlak_mesic','')).replace('.0','')
            if tm == 'nan' or tm == '0': tm = ''
            pdf.cell(col_w[7], 5, s(tm), border=1, align="C")
            
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

# --- 3B. PROTOKOL O VYŘAZENÍ (Portrait) ---
def create_protokol_vyrazeni_pdf(zakaznik: Dict, df_evid: pd.DataFrame, dl_number: str, zakazka: str, technik: str) -> tuple[Optional[bytes], Optional[str]]:
    pdf = UrbaneKPDF_Letterhead(orientation='P')
    if not pdf.pismo_ok: return None, "Chyba fontu."
    p = pdf.pismo_name
    def s(t): return safe_str(t, True)

    try:
        # Filtrujeme pouze NV
        df_nv = df_evid[df_evid['stav'] == 'NV']
        
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
                if rv == 'nan' or rv == '0': rv = ''
                pdf.cell(col_w[3], 5, s(rv), border=1, align="C")
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


# --- 3C. DODACÍ LIST (Původní robustní generátor) ---
def create_wservis_dl(zakaznik: Dict, items_dict: Dict, dl_number: str, zakazka: str, technik: str, objekty_map: Dict, typ_dl: str, dl_type_name: str, included_sections: List[str]) -> tuple[Optional[bytes], Optional[str]]:
    pdf = UrbaneKPDF_Letterhead(orientation='P')
    if not pdf.pismo_ok: return None, "Chyba fontu."
    try:
        p = pdf.pismo_name
        pdf.add_page()
        def s(t): return safe_str(t, True)

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

        pdf.set_font(p, "B", 10)
        pdf.set_fill_color(220, 220, 220)
        pdf.cell(157, 8, s("CELKEM K ÚHRADĚ BEZ DPH: "), border=1, align="R", fill=True)
        pdf.cell(33, 8, s(f"{format_cena(total_sum)} Kč"), border=1, align="R", fill=True, ln=True)
        pdf.ln(8)

        # Patička
        firma = get_safe_str(zakaznik, 'FIRMA')
        ico = clean_ico(zakaznik.get('ICO'))
        dic = get_safe_str(zakaznik, 'DIC')
        ul, cp, co = get_safe_str(zakaznik, "ULICE"), get_safe_str(zakaznik, "CP"), get_safe_str(zakaznik, "CO")
        adr1 = get_safe_str(zakaznik, "ADRESA1")
        if ul and cp: adr_line1 = f"{ul} {cp}" + (f"/{co}" if co and co != "0" else "")
        elif ul: adr_line1 = ul
        elif adr1: adr_line1 = adr1
        else: adr_line1 = ""
        adr_line2 = f"{get_safe_str(zakaznik, 'PSC')} {get_safe_str(zakaznik, 'ADRESA3')}".strip()

        y_base = pdf.get_y()
        if y_base > 220:
            pdf.add_page(); pdf.set_font(p, "", 9); y_base = pdf.get_y()
            
        pdf.rect(10, y_base, 95, 32); pdf.rect(105, y_base, 95, 32)
        
        f_umisteni = " Umístění kontrolovaných HP/PV v objektech:"
        if dl_type_name == "Kontroly PV": f_umisteni = " Umístění kontrolovaných PV v objektech:"
        elif dl_type_name == "Opravy HP": f_umisteni = " Umístění opravených HP v objektech:"
        elif dl_type_name == "Kontroly HP": f_umisteni = " Umístění kontrolovaných HP v objektech:"

        pdf.set_fill_color(235, 235, 235); pdf.set_font(p, "B", 9)
        pdf.set_xy(10, y_base); pdf.cell(95, 6, s(" Odběratel:"), border=1, fill=True)
        pdf.set_xy(105, y_base); pdf.cell(95, 6, s(f_umisteni), border=1, fill=True)

        pdf.set_font(p, "", 9)
        pdf.set_xy(10, y_base + 6); pdf.cell(95, 5, s(f"  Firma: {firma[:45]}"))
        pdf.set_xy(105, y_base + 6); pdf.cell(95, 5, s(f"  O1: {objekty_map.get(1, '')[:45]}"))
        pdf.set_xy(10, y_base + 11); pdf.cell(95, 5, s(f"  Ulice: {adr_line1[:45]}"))
        pdf.set_xy(105, y_base + 11); pdf.cell(95, 5, s(f"  O2: {objekty_map.get(2, '')[:45]}"))
        pdf.set_xy(10, y_base + 16); pdf.cell(95, 5, s(f"  Město: {adr_line2[:45]}"))
        pdf.set_xy(105, y_base + 16); pdf.cell(95, 5, s(f"  O3: {objekty_map.get(3, '')[:45]}"))
        pdf.set_xy(10, y_base + 21); pdf.cell(95, 5, s(f"  IČO: {ico}"))
        pdf.set_xy(105, y_base + 21); pdf.cell(95, 5, s(f"  O4: {objekty_map.get(4, '')[:45]}"))
        pdf.set_xy(10, y_base + 26); pdf.cell(95, 5, s(f"  DIČ: {dic}"))
        pdf.set_xy(105, y_base + 26); pdf.cell(95, 5, s(f"  O5: {objekty_map.get(5, '')[:45]}"))

        y_sig = y_base + 35
        if y_sig + 30 > 280:
            pdf.add_page(); pdf.set_font(p, "", 9); y_sig = pdf.get_y()

        pdf.rect(10, y_sig, 190, 26)
        pdf.set_font(p, "B", 9)
        pdf.set_xy(10, y_sig); pdf.cell(190, 6, s(" Záznam o kontrole a předání:"), border=1, fill=True)
        pdf.set_font(p, "B", 8)
        pdf.set_xy(10, y_sig + 6); pdf.cell(95, 5, s("  Za zhotovitele (Předal):"), border="R"); pdf.cell(95, 5, s("  Za odběratele (Převzal):"))
        pdf.set_font(p, "", 9)
        pdf.set_xy(10, y_sig + 11); pdf.cell(95, 5, s(f"  Kontrolní technik: {technik}"), border="R"); pdf.cell(95, 5, s("  Jméno hůlkovým písmem:"))
        pdf.set_xy(10, y_sig + 16); pdf.cell(95, 5, s("  Odborně způsobilá osoba v PO: Ilja Urbánek"), border="R"); pdf.cell(95, 5, s("  ........................................................................."))
        pdf.set_font(p, "", 7)
        pdf.set_xy(10, y_sig + 21); pdf.cell(95, 5, s("   Podpisy a razítka zhotovitele"), border="R"); pdf.cell(95, 5, s("   Podpis a razítko odběratele"))

        return bytes(pdf.output()), None
    except Exception as e: return None, str(e)


# ==========================================
# 4. STREAMLIT UI - APLIKACE
# ==========================================
st.set_page_config(page_title="W-SERVIS Enterprise v44.0", layout="wide", page_icon="🛡️")

st.markdown("""
<style>
    .stTabs [data-baseweb="tab-list"] { gap: 8px; }
    .stTabs [data-baseweb="tab"] { background-color: #f0f2f6; border-radius: 4px 4px 0 0; padding: 10px 20px; }
    .stTabs [aria-selected="true"] { background-color: #ff4b4b; color: white; font-weight: bold; }
    .cart-box { background-color: #f8f9fa; padding: 15px; border-radius: 8px; border-left: 5px solid #ff4b4b; margin-top: 15px; }
    .evidence-box { border: 2px solid #28a745; padding: 15px; border-radius: 8px; margin-bottom: 20px; background-color: #f9fff9;}
    li[role="option"] span { white-space: normal !important; overflow: visible !important; text-overflow: unset !important; }
</style>
""", unsafe_allow_html=True)

df_customers = load_all_customers() if 'load_all_customers' in globals() else None
if not df_customers is not None:
    try:
        conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
        df_customers = pd.read_sql("SELECT * FROM obchpartner", conn)
        conn.close()
    except: pass

menu_volba = st.sidebar.radio("Navigace systému:", [
    "📝 Zpracování zakázky (Evidence & DL)", 
    "🗄️ Katalog a Sklad (Ceníky)",
    "📊 Obchodní Velín (50:50)"
])

if menu_volba == "📝 Zpracování zakázky (Evidence & DL)":
    with st.sidebar:
        st.header("🏢 Hlavička dokladů")
        typ_dl = st.radio("Hlavička 1. sekce DL:", ["Standard (Kontroly)", "Opravy (Prior)"])
        dl_number = st.text_input("Číslo dokladu / DL:", value="1698")
        zakazka = st.text_input("Číslo zakázky:", value="1/13")
        technik = st.text_input("Kontrolní technik:", value="Tomáš Urbánek")
        st.divider()
        
        aktualni_ico = ""
        ulozene_objekty = []
        
        if df_customers is not None and not df_customers.empty:
            filt = df_customers.copy()
            filt["FIRMA"] = filt["FIRMA"].fillna("Neznámý název")
            filt["clean_ico"] = filt["ICO"].apply(clean_ico)
            filt = filt.drop_duplicates(subset=["clean_ico", "FIRMA"])
            filt = filt.sort_values(by="FIRMA", key=lambda s: s.astype(str).str.lower())

            def format_cust(row):
                f = str(row.get('FIRMA', '')).strip()
                i = str(row.get('clean_ico', '')).strip()
                return f"{f}  |  IČO: {i}"

            opts = filt.apply(format_cust, axis=1).tolist()
            default_idx = 0
            if st.session_state.vybrany_zakaznik:
                curr_ico = clean_ico(st.session_state.vybrany_zakaznik.get("ICO", ""))
                for idx_opt, opt in enumerate(opts):
                    if f"IČO: {curr_ico}" in opt:
                        default_idx = idx_opt; break

            sel = st.selectbox("🔍 Vyhledat odběratele:", options=opts, index=default_idx)
            idx = opts.index(sel)
            curr = filt.iloc[idx].to_dict()

            if (st.session_state.vybrany_zakaznik is None or clean_ico(st.session_state.vybrany_zakaznik.get("ICO")) != clean_ico(curr.get("ICO"))):
                ico_val = clean_ico(curr.get("ICO"))
                with st.spinner("Načítám detaily..."):
                    local_data = build_form_data_from_customer(ico_val)
                    if local_data: curr.update(local_data)
                st.session_state.vybrany_zakaznik = curr.copy()
                
            aktualni_ico = clean_ico(curr.get("ICO"))
            ulozene_objekty = get_objects_from_db(aktualni_ico)
            
            ul_kl, cp_kl, co_kl = str(curr.get("ULICE", "")).strip(), str(curr.get("CP", "")).strip(), str(curr.get("CO", "")).strip()
            ob_kl = str(curr.get("ADRESA3", "")).strip()
            adr_slozena = f"{ul_kl} {cp_kl}" + (f"/{co_kl}" if co_kl and co_kl != "0" else "") if ul_kl else ""
            if adr_slozena and ob_kl: adr_slozena += f", {ob_kl}"
            elif ob_kl: adr_slozena = ob_kl
            if adr_slozena and adr_slozena not in ulozene_objekty: ulozene_objekty.insert(0, adr_slozena)

        st.subheader("🏢 Správa objektů v DB")
        if aktualni_ico:
            with st.expander("➕ Přidat nový objekt"):
                with st.form("add_obj_form", clear_on_submit=True):
                    novy_objekt = st.text_input("Název objektu (Budova/Adresa)")
                    if st.form_submit_button("Uložit do paměti") and novy_objekt.strip():
                        add_object_to_db(aktualni_ico, novy_objekt)
                        st.rerun()

        celkem_cena = sum(v["q"] * v.get("p", 0) for v in st.session_state.data_zakazky.values())
        celkem_cena += sum(v["q"] * v.get("p", 0) for v in st.session_state.dynamic_items.values())
        
        st.markdown(f"""
        <div class="cart-box">
            <b>🛒 Fakturace celkem:</b><br/>
            {format_cena(celkem_cena)} Kč bez DPH
        </div>
        """, unsafe_allow_html=True)
        
        if st.button("🗑️ Vyprázdnit zakázku", use_container_width=True):
            st.session_state.data_zakazky = {}; st.session_state.dynamic_items = {}
            st.session_state.auto_kalkulace = {"S": 0, "NO": 0, "NV": 0, "total": 0, "vyrazene_kody": set()}
            st.session_state.evidence_df = pd.DataFrame()
            st.rerun()

    st.title("🛡️ Zpracování zakázky (ERP Modul)")
    st.caption("Verze 44.0 Automated Evidence Engine | Tabulka Evidence automaticky počítá DL")

    st.markdown("### 🏢 Rozřazení objektů pro tisk (O1 - O5)")
    col_o1, col_o2, col_o3, col_o4, col_o5 = st.columns(5)
    with col_o1: show_o1 = st.checkbox("O1", value=True); o1_name = st.selectbox("Objekt 1:", [""] + ulozene_objekty, key="o1_sel") if show_o1 else ""
    with col_o2: show_o2 = st.checkbox("O2", value=False); o2_name = st.selectbox("Objekt 2:", [""] + ulozene_objekty, key="o2_sel") if show_o2 else ""
    with col_o3: show_o3 = st.checkbox("O3", value=False); o3_name = st.selectbox("Objekt 3:", [""] + ulozene_objekty, key="o3_sel") if show_o3 else ""
    with col_o4: show_o4 = st.checkbox("O4", value=False); o4_name = st.selectbox("Objekt 4:", [""] + ulozene_objekty, key="o4_sel") if show_o4 else ""
    with col_o5: show_o5 = st.checkbox("O5", value=False); o5_name = st.selectbox("Objekt 5:", [""] + ulozene_objekty, key="o5_sel") if show_o5 else ""
    mapa_objektu_pro_pdf = {1: o1_name, 2: o2_name, 3: o3_name, 4: o4_name, 5: o5_name}

    tabs = st.tabs(["📝 1. Evidence HP (Hlavní pracoviště)", "💰 2. Auto-Fakturace a Náhrady", "🛠️ 3. Opravy a Voda", "🛒 4. Zboží a Materiál", "🖨️ 5. TISK DOKLADŮ"])

    def get_col_layout():
        layout = [3.5, 1.5]
        if show_o1: layout.append(1.0)
        if show_o2: layout.append(1.0)
        if show_o3: layout.append(1.0)
        if show_o4: layout.append(1.0)
        if show_o5: layout.append(1.0)
        return layout

    def render_table_header():
        cols = st.columns(get_col_layout())
        cols[0].markdown("**Název položky**")
        cols[1].markdown("**Cena bez DPH**")
        idx = 2
        if show_o1: cols[idx].markdown("**O1**"); idx+=1
        if show_o2: cols[idx].markdown("**O2**"); idx+=1
        if show_o3: cols[idx].markdown("**O3**"); idx+=1
        if show_o4: cols[idx].markdown("**O4**"); idx+=1
        if show_o5: cols[idx].markdown("**O5**"); idx+=1

    def item_row(cat_key: str, item_name: str, fallback_price: float, row_id: str, step_val: float = 1.0, is_auto: bool = False) -> None:
        p_val = get_price(cat_key, item_name)
        if p_val == 0.0: p_val = fallback_price

        cols = st.columns(get_col_layout())
        with cols[0]: 
            if is_auto: st.markdown(f"🤖 **{item_name}** *(Auto)*")
            else: st.write(f"{item_name}")
        with cols[1]: p = st.number_input(f"P_{row_id}", min_value=0.0, step=0.1, value=float(p_val), key=f"p_{row_id}", label_visibility="collapsed")
        
        idx = 2; q1 = q2 = q3 = q4 = q5 = 0.0
        old_val = st.session_state.data_zakazky.get(item_name, {})
        
        if show_o1:
            with cols[idx]: q1 = st.number_input(f"1_{row_id}", min_value=0.0, step=float(step_val), value=float(old_val.get("q1", 0.0)), key=f"q1_{row_id}", label_visibility="collapsed"); idx+=1
        if show_o2:
            with cols[idx]: q2 = st.number_input(f"2_{row_id}", min_value=0.0, step=float(step_val), value=float(old_val.get("q2", 0.0)), key=f"q2_{row_id}", label_visibility="collapsed"); idx+=1
        if show_o3:
            with cols[idx]: q3 = st.number_input(f"3_{row_id}", min_value=0.0, step=float(step_val), value=float(old_val.get("q3", 0.0)), key=f"q3_{row_id}", label_visibility="collapsed"); idx+=1
        if show_o4:
            with cols[idx]: q4 = st.number_input(f"4_{row_id}", min_value=0.0, step=float(step_val), value=float(old_val.get("q4", 0.0)), key=f"q4_{row_id}", label_visibility="collapsed"); idx+=1
        if show_o5:
            with cols[idx]: q5 = st.number_input(f"5_{row_id}", min_value=0.0, step=float(step_val), value=float(old_val.get("q5", 0.0)), key=f"q5_{row_id}", label_visibility="collapsed"); idx+=1
        
        q_tot = q1 + q2 + q3 + q4 + q5
        st.session_state.data_zakazky[item_name] = {"q1": q1, "q2": q2, "q3": q3, "q4": q4, "q5": q5, "q": q_tot, "p": float(p), "cat": cat_key}

    with tabs[0]:
        st.markdown("<div class='evidence-box'>", unsafe_allow_html=True)
        st.markdown("### 📋 Technická Evidence HP (Doklad o kontrole)")
        st.info("Zde zapište všechny kontrolované přístroje. Systém následně z této tabulky **automaticky** vytvoří položky do Dodacího listu.")
        
        if not st.session_state.vybrany_zakaznik:
            st.warning("Vyberte zákazníka v levém panelu.")
        else:
            if st.session_state.loaded_ico != aktualni_ico or st.session_state.evidence_df.empty:
                conn = sqlite3.connect(DB_PATH)
                df_evid = pd.read_sql("SELECT druh, typ_hp, vyr_cislo, rok_vyr, mesic_vyr, tlak_rok, tlak_mesic, stav, duvod_nv, objekt, misto FROM evidence_hp WHERE ico = ?", conn, params=(aktualni_ico,))
                if df_evid.empty:
                    df_evid = pd.DataFrame(columns=["druh", "typ_hp", "vyr_cislo", "rok_vyr", "mesic_vyr", "tlak_rok", "tlak_mesic", "stav", "duvod_nv", "objekt", "misto"])
                    df_evid.loc[0] = ["přenosný", "", "", None, None, None, None, "S", "", "", ""]
                    df_evid.loc[1] = ["přenosný", "", "", None, None, None, None, "S", "", "", ""]
                    df_evid.loc[2] = ["přenosný", "", "", None, None, None, None, "S", "", "", ""]
                st.session_state.evidence_df = df_evid
                st.session_state.loaded_ico = aktualni_ico
                conn.close()
            
            edited_evid = st.data_editor(
                st.session_state.evidence_df,
                num_rows="dynamic",
                use_container_width=True,
                key="evidence_editor_safe",
                column_config={
                    "druh": st.column_config.SelectboxColumn("Druh", options=["přenosný", "pojízdný", "přívěsný", "AHS"], width="small"),
                    "typ_hp": st.column_config.TextColumn("Typ HP", width="medium"),
                    "vyr_cislo": st.column_config.TextColumn("Výr. číslo", width="small"),
                    "rok_vyr": st.column_config.NumberColumn("Rok", format="%d", width="small"),
                    "mesic_vyr": st.column_config.NumberColumn("Měs.", width="small"),
                    "tlak_rok": st.column_config.NumberColumn("Tlak. Rok", format="%d", width="small"),
                    "tlak_mesic": st.column_config.NumberColumn("Tlak. Měs", width="small"),
                    "stav": st.column_config.SelectboxColumn("Stav", options=STAVY_HP, width="small", required=True),
                    "duvod_nv": st.column_config.SelectboxColumn("Důvod NV", options=list(DUVODY_VYRAZENI.keys()), width="small"),
                    "objekt": st.column_config.SelectboxColumn("Objekt (Budova)", options=ulozene_objekty if ulozene_objekty else [""], width="medium"),
                    "misto": st.column_config.TextColumn("Přesné umístění", width="medium"),
                }
            )
            
            if st.button("💾 Uložit evidenci a PŘEPOČÍTAT Fakturaci", type="primary", use_container_width=True):
                clean_evid = edited_evid.dropna(subset=['stav']).copy()
                clean_evid["ico"] = aktualni_ico
                
                conn = sqlite3.connect(DB_PATH)
                conn.execute("DELETE FROM evidence_hp WHERE ico = ?", (aktualni_ico,))
                conn.commit()  
                clean_evid.to_sql("evidence_hp", conn, if_exists="append", index=False)
                conn.close()
                st.session_state.evidence_df = edited_evid

                st.session_state.auto_kalkulace = {
                    "S": len(clean_evid[clean_evid['stav'].isin(['S', 'S-nový'])]),
                    "NO": len(clean_evid[clean_evid['stav'].isin(['NO', 'NOPZ'])]),
                    "NV": len(clean_evid[clean_evid['stav'] == 'NV']),
                    "total": len(clean_evid[clean_evid['stav'] != 'CH']),
                    "vyrazene_kody": set(clean_evid[clean_evid['stav'] == 'NV']['duvod_nv'].dropna().tolist())
                }
                
                p_s = get_price("HP", "Kontrola HP (shodný)") or 29.40
                p_no = get_price("HP", "Kontrola HP (neshodný - opravitelný)") or 19.70
                p_nv = get_price("HP", "Kontrola HP (neshodný - neopravitelný) + odborné zneprovoznění") or 23.50
                p_pausal = get_price("Servisni_ukony", "Vyhodnocení kontroly + vystavení dokladu o kontrole (á 1ks HP)") or 5.80

                st.session_state.data_zakazky["Kontrola HP (shodný)"] = {"q1": st.session_state.auto_kalkulace["S"], "q2":0, "q3":0, "q4":0, "q5":0, "q": st.session_state.auto_kalkulace["S"], "p": p_s, "cat": "HP"}
                st.session_state.data_zakazky["Kontrola HP (neshodný - opravitelný)"] = {"q1": st.session_state.auto_kalkulace["NO"], "q2":0, "q3":0, "q4":0, "q5":0, "q": st.session_state.auto_kalkulace["NO"], "p": p_no, "cat": "HP"}
                st.session_state.data_zakazky["Kontrola HP (neshodný - neopravitelný) + odborné zneprovoznění"] = {"q1": st.session_state.auto_kalkulace["NV"], "q2":0, "q3":0, "q4":0, "q5":0, "q": st.session_state.auto_kalkulace["NV"], "p": p_nv, "cat": "HP"}
                st.session_state.data_zakazky["Vyhodnocení kontroly + vystavení dokladu o kontrole (á 1ks HP)"] = {"q1": st.session_state.auto_kalkulace["total"], "q2":0, "q3":0, "q4":0, "q5":0, "q": st.session_state.auto_kalkulace["total"], "p": p_pausal, "cat": "Servisni_ukony"}
                
                st.success(f"✅ Kalkulace hotova! Nalezeno {st.session_state.auto_kalkulace['total']} přístrojů. Přepněte se do záložky '2. Auto-Fakturace'.")
                
        st.markdown("</div>", unsafe_allow_html=True)

    with tabs[1]:
        st.markdown("### Automaticky načtené úkony HP z Evidence")
        render_table_header()
        item_row("HP", "Kontrola HP (shodný)", 29.40, "h1", is_auto=True)
        item_row("HP", "Kontrola HP (neshodný - opravitelný)", 19.70, "h2", is_auto=True)
        item_row("HP", "Kontrola HP (neshodný - neopravitelný) + odborné zneprovoznění", 23.50, "h3", is_auto=True)
        item_row("Servisni_ukony", "Vyhodnocení kontroly + vystavení dokladu o kontrole (á 1ks HP)", 5.80, "s_hp1", is_auto=True)
        
        st.divider()
        st.markdown("### Manuální úkony a Náhrady (Cestovné)")
        render_table_header()
        item_row("HP", "Manipulace a odvoz HP ze servisu (opravy)", 24.00, "h4")
        item_row("HP", "Manipulace a odvoz HP k násl. údržbě-TZ,opravě-plnění,demontáži", 24.00, "h5")
        item_row("HP", "Hod.sazba (pochůzky po objektu/ manipulace s HP/PV + další=dohod", 450.00, "h6", step_val=0.1)
        item_row("Nahrady", "Náhrada za 1km - osobní servisní vozidlo", 6.00, "n4")
        item_row("Nahrady", "Náhrada za 1km - nákladní servisní vozidlo do 3,5 tun", 15.90, "n6")
        item_row("Nahrady", "Převzetí HP vyřazeného z užívání dodavatelem", 88.00, "n1")
        item_row("Nahrady", "Označení - vylepení štítku o kontrole (á 1ks / HP)", 8.00, "n3")

    with tabs[2]:
        st.subheader("OPRAVY HASICÍCH PŘÍSTROJŮ A VODA")
        render_table_header()
        item_row("Opravy", "CO2-5F/ETS", 418.00, "opr1")
        item_row("Opravy", "P6 Če (21A/)", 385.00, "opr2")
        item_row("Opravy", "S1,5 Kod", 280.00, "opr3")
        item_row("Opravy", "S5 Kte", 418.00, "opr5")
        item_row("Voda", "Prohlídka zařízení do 5 ks výtoků", 123.00, "v1")
        item_row("Voda", "Kontrola zařízení bez měření průtoku do 5 ks výtoků", 141.00, "v2")
        item_row("Voda", "Měření průtoku a tlaku spec. zařízením (vnitřní hydrant D/C)", 95.00, "v5")

    with tabs[3]:
        st.subheader("PRODEJ ZBOŽÍ A MATERIÁLU")
        db_items = get_items_from_db(["Zboží", "ND_HP", "ND_Voda", "TAB", "HILTI", "CIDLO", "PASKA", "Ostatni", "zbozi"])
        if not db_items: st.warning("Sklad je prázdný.")
        else:
            items_dict_lookup = {i["nazev"]: i for i in db_items}
            z_cols = st.columns(get_col_layout() + [1.0])
            with z_cols[0]: zvolena_polozka = st.selectbox("Vyberte ze skladu:", ["-- Vyberte --"] + list(items_dict_lookup.keys()))
            
            if zvolena_polozka != "-- Vyberte --":
                with z_cols[1]: cena_input = st.number_input("Cena", value=items_dict_lookup[zvolena_polozka]["cena"], step=1.0, key="zb_cena")
                idx = 2; mq1=mq2=mq3=mq4=mq5=0.0
                if show_o1:
                    with z_cols[idx]: mq1 = st.number_input("O1", value=1.0, min_value=0.0, step=1.0, key="zb1"); idx+=1
                if show_o2:
                    with z_cols[idx]: mq2 = st.number_input("O2", value=0.0, min_value=0.0, step=1.0, key="zb2"); idx+=1
                if show_o3:
                    with z_cols[idx]: mq3 = st.number_input("O3", value=0.0, min_value=0.0, step=1.0, key="zb3"); idx+=1
                with z_cols[idx]:
                    st.write(""); 
                    if st.button("➕ Přidat"):
                        cat = items_dict_lookup[zvolena_polozka]["internal_cat"]
                        st.session_state.dynamic_items[zvolena_polozka] = {"q1": mq1, "q2": mq2, "q3": mq3, "q4": mq4, "q5": mq5, "q": mq1+mq2+mq3+mq4+mq5, "p": cena_input, "cat": "Zboží" if cat not in CATEGORY_MAP.values() else cat}
                        st.rerun()

        if st.session_state.dynamic_items:
            st.divider()
            for k, v in list(st.session_state.dynamic_items.items()):
                ca, cb, cc, cd = st.columns([5, 2, 2, 1])
                ca.write(f"• {k}"); cb.write(f"{v.get('q',0)} ks"); cc.write(f"{v.get('q',0) * v.get('p',0):,.2f} Kč")
                if cd.button("❌", key=f"del_{k}"): del st.session_state.dynamic_items[k]; st.rerun()

    with tabs[4]:
        st.markdown("### 🖨️ Tiskové Centrum")
        st.info("Zde si můžete vygenerovat a stáhnout všechny potřebné dokumenty pro tuto zakázku. Data se berou ze záložek Evidence a Fakturace.")
        
        firma = st.session_state.vybrany_zakaznik.get("FIRMA", "Neznámý") if st.session_state.vybrany_zakaznik else "Neznámý"
        if not st.session_state.vybrany_zakaznik: st.error("Nejprve vyberte zákazníka v levém panelu!")
        
        active_items = {k:v for k,v in st.session_state.data_zakazky.items() if v.get("q", 0) > 0}
        active_items.update({k:v for k,v in st.session_state.dynamic_items.items() if v.get("q", 0) > 0})

        kody_k_tisku = list(st.session_state.auto_kalkulace.get("vyrazene_kody", set()))

        c1, c2, c3 = st.columns(3)
        
        with c1:
            st.markdown("#### 1. Technická část")
            if st.button("📄 DOKLAD O KONTROLE HP", type="secondary", use_container_width=True):
                if st.session_state.evidence_df.empty: st.error("Evidence je prázdná!")
                else:
                    pdf_bytes, err = create_doklad_kontroly_pdf(st.session_state.vybrany_zakaznik, st.session_state.evidence_df, dl_number, zakazka, technik)
                    if err: st.error(err)
                    else: st.download_button("⬇️ Uložit Doklad o kontrole", data=pdf_bytes, file_name=f"DoK_{dl_number}_{firma}.pdf", mime="application/pdf")
                    
            if st.button("⚠️ PROTOKOL O VYŘAZENÍ", type="secondary", use_container_width=True):
                if st.session_state.evidence_df.empty: st.error("Evidence je prázdná!")
                else:
                    pdf_bytes, err = create_protokol_vyrazeni_pdf(st.session_state.vybrany_zakaznik, st.session_state.evidence_df, dl_number, zakazka, technik)
                    if err: st.error(err)
                    else: st.download_button("⬇️ Uložit Protokol", data=pdf_bytes, file_name=f"LP_{dl_number}_{firma}.pdf", mime="application/pdf")

        with c2:
            st.markdown("#### 2. Finanční část (Kontroly)")
            if st.button("📄 DL: Kontroly HP a Zboží", type="primary", use_container_width=True):
                pdf_bytes, err = create_wservis_dl(st.session_state.vybrany_zakaznik, active_items, dl_number, zakazka, technik, mapa_objektu_pro_pdf, typ_dl, kody_k_tisku, "Kontroly HP", ["HP", "NAHRADY", "ZBOZI"])
                if err: st.error(err)
                else: st.download_button("⬇️ Uložit DL (Kontroly)", data=pdf_bytes, file_name=f"DL_Kontroly_{dl_number}_{firma}.pdf", mime="application/pdf")

        with c3:
            st.markdown("#### 3. Finanční část (Opravy)")
            if st.button("📄 DL: Samostatné Opravy HP", type="primary", use_container_width=True):
                pdf_bytes, err = create_wservis_dl(st.session_state.vybrany_zakaznik, active_items, dl_number, zakazka, technik, mapa_objektu_pro_pdf, typ_dl, [], "Opravy HP", ["OPRAVY", "NAHRADY", "ZBOZI"])
                if err: st.error(err)
                else: st.download_button("⬇️ Uložit DL (Opravy)", data=pdf_bytes, file_name=f"DL_Opravy_{dl_number}_{firma}.pdf", mime="application/pdf")

elif menu_volba == "🗄️ Katalog a Evidence (Náhrada Access)":
    st.title("🗄️ Katalog, Sklad a Databáze")
    
    evid_tabs = st.tabs(["📦 Správa Zboží a Ceníku", "🏢 Databáze Zákazníků", "⚙️ Import z W-SERVIS"])

    with evid_tabs[0]:
        st.markdown("### Nová karta položky (Zboží / ND)")
        st.info("Zde vytvoříte novou položku. Bude okamžitě dostupná v roletce u Dodacího listu.")
        
        with st.expander("➕ Otevřít formulář pro novou položku", expanded=False):
            with st.form("nove_zbozi_form", clear_on_submit=True):
                c1, c2, c3 = st.columns([3, 1.5, 1.5])
                with c1: form_nazev = st.text_input("Název položky (např. Hasicí přístroj P6, Tabulka fotolumin)", max_chars=150)
                with c2: form_cena = st.number_input("Cena bez DPH (Kč)", min_value=0.0, step=10.0, format="%.2f")
                with c3: form_kat = st.selectbox("Kategorie / Druh", ["Zboží", "ND_HP", "ND_Voda", "TAB", "HILTI", "CIDLO", "PASKA", "Ostatni"])

                if st.form_submit_button("💾 Uložit novou položku do DB"):
                    if form_nazev.strip():
                        conn = sqlite3.connect(DB_PATH)
                        cur = conn.cursor()
                        table_target = normalize_category_to_table(form_kat)
                        cur.execute(f"CREATE TABLE IF NOT EXISTS {table_target} (nazev TEXT, cena REAL)")
                        cur.execute(f"INSERT INTO {table_target} (nazev, cena) VALUES (?, ?)", (form_nazev.strip(), form_cena))
                        conn.commit()
                        conn.close()
                        st.success(f"Položka '{form_nazev}' byla úspěšně přidána do ceníku!")
                    else:
                        st.error("Název položky nesmí být prázdný!")

        st.markdown("### 📋 Aktivní ceník (Editovatelná tabulka)")
        if os.path.exists(DB_PATH):
            conn = sqlite3.connect(DB_PATH)
            try:
                df_zbozi = pd.read_sql("SELECT nazev as 'Název položky', cena as 'Cena bez DPH (Kč)' FROM cenik_zbozi ORDER BY nazev", conn)
                edited_zbozi = st.data_editor(
                    df_zbozi, 
                    use_container_width=True, 
                    num_rows="dynamic", 
                    key="editor_zbozi",
                    column_config={
                        "Název položky": st.column_config.TextColumn(required=True),
                        "Cena bez DPH (Kč)": st.column_config.NumberColumn(min_value=0.0, format="%.2f Kč")
                    }
                )
                
                if st.button("💾 Uložit změny provedené v tabulce"):
                    edited_zbozi.columns = ["nazev", "cena"]
                    edited_zbozi = edited_zbozi.dropna(subset=["nazev"])
                    edited_zbozi.to_sql("cenik_zbozi", conn, if_exists="replace", index=False)
                    st.success("Změny v ceníku byly trvale uloženy!")
            except Exception:
                st.warning("Ceník Zboží je zatím prázdný.")
            conn.close()

    with evid_tabs[1]:
        st.markdown("### Databáze uložených zákazníků")
        if df_customers is not None and not df_customers.empty:
            view_df = df_customers.copy()
            if "clean_ico" in view_df.columns: view_df = view_df.drop(columns=["clean_ico"])
            view_df = view_df.fillna("")
            
            dostupne_sloupce = view_df.columns.tolist()
            zobrazit_sloupce = [col for col in ["ICO", "FIRMA", "ULICE", "ADRESA1", "ADRESA2", "ADRESA3", "PSC", "DIC"] if col in dostupne_sloupce]
            
            if not zobrazit_sloupce:
                zobrazit_sloupce = dostupne_sloupce
                
            st.dataframe(view_df[zobrazit_sloupce], use_container_width=True, height=500)
        else:
            st.info("Zatím nejsou nahráni žádní zákazníci.")

    with evid_tabs[2]:
        st.markdown("### Hromadný import ceníků")
        st.info("Nahrajte do složky 'data/ceniky/' vaše Excel soubory, exportní soubor 'expimp.csv' a také soubor 'obchpartner.xml'.")
        if st.button("🚀 Spustit kompletní synchronizaci", type="primary"):
            with st.spinner("Aktualizuji databázi (překládám kódování u zákazníků)..."):
                log = import_all_ceniky()
                st.success("Hotovo!")
                st.code(log)

elif menu_volba == "📊 Obchodní Velín (50:50)":
    st.title("🚒 HASIČ-SERVIS URBÁNEK - Obchodní velín")
    st.markdown("### Návrh ke schválení pro spravedlivé rozdělení 50:50 (Tomáš a Ilja Urbánkovi)")
    st.markdown("---")

    st.info("💡 **Přístup odkudkoliv:** Aplikace je nyní nezávislá na tom, u jakého PC sedíte. Stačí sem myší přetáhnout vygenerovaný CSV soubor.")
    uploaded_file = st.file_uploader("📂 Nahrajte soubor 'Migrace_Centraly_Navrh.csv' z vašeho PC:", type=['csv'])

    df_velin = pd.DataFrame()
    if uploaded_file is not None:
        try: df_velin = pd.read_csv(uploaded_file, sep=';', encoding='utf-8-sig')
        except Exception: st.error("Nepodařilo se načíst soubor.")

    if not df_velin.empty:
        st.success(f"✅ Úspěšně načteno {len(df_velin)} auditovaných záznamů.")
        col1, col2, col3 = st.columns(3)
        col1.metric("Celkem kontrol provozuschopnosti", len(df_velin))
        col2.metric("Odborný standard PV", "Měření průtoku a tlaku")
        col3.metric("Legislativa (Neopravitelné NV)", "Mimo evidenci odpadů")
        st.dataframe(df_velin, use_container_width=True)

st.sidebar.divider()
st.sidebar.caption(f"© {datetime.date.today().year} {FIRMA_VLASTNI['název']}")
