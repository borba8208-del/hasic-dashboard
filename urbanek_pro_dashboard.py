import os
import re
import time
import datetime
import unicodedata
import sqlite3
from typing import Any, Dict, List, Optional

import streamlit as st
import pandas as pd
from fpdf import FPDF

# ==========================================
# ČÍSELNÍKY DLE W-SERVIS DATABÁZE
# ==========================================
DUVODY_VYRAZENI = {
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
    "HP": "HP",
    "Nahrady": "Nahrady",
    "Voda": "VODA",
    "Ostatni": "ostatni",
    "ND_HP": "ND_HP",
    "ND_Voda": "ND_VODA",
    "FA": "FA",
    "TAB": "TAB",
    "TABFOTO": "TABFOTO",
    "HILTI": "HILTI",
    "CIDLO": "CIDLO",
    "PASKA": "PASKA",
    "PK": "PK",
    "OZO": "OZO",
    "reklama": "reklama",
    "Servisni_ukony": "revize",
    "Opravy": "opravy",
    "Zboží": "zbozi"
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
    "telefony": "608409036 - 777664768",
    "email": "schranka@hasic-servis.com",
    "web": "http://www.hasic-servis.com",
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
            objekt TEXT,
            misto TEXT,
            do_opravy INTEGER DEFAULT 0,
            do_skladu INTEGER DEFAULT 0
        )
    """)
    conn.commit()
    conn.close()

init_db()

if "data_zakazky" not in st.session_state: st.session_state.data_zakazky = {}
if "dynamic_items" not in st.session_state: st.session_state.dynamic_items = {}
if "vybrany_zakaznik" not in st.session_state: st.session_state.vybrany_zakaznik = None
if "vyrazene_kody" not in st.session_state: st.session_state.vyrazene_kody = []
if "loaded_ico" not in st.session_state: st.session_state.loaded_ico = None
if "evidence_df" not in st.session_state: st.session_state.evidence_df = pd.DataFrame()

# ==========================================
# 2. INTELIGENTNÍ IMPORT DATABÁZÍ VČETNĚ UNIVERZÁLNÍHO XML
# ==========================================
def normalize_category_to_table(cat_key: str) -> str:
    if not cat_key: return "cenik_ostatni"
    normalized = cat_key.lower().strip()
    normalized = "".join(char for char in unicodedata.normalize("NFKD", normalized) if not unicodedata.combining(char))
    normalized = re.sub(r"[\s/]+", "_", normalized)
    normalized = re.sub(r"[^a-z0-9_]", "", normalized)
    return f"cenik_{normalized}"

def safe_read_data(base_path: str) -> Optional[pd.DataFrame]:
    """
    Univerzální W-SERVIS datový skener:
    Hledá prioritně Excel, následně se pokusí přečíst a zploštit XML, jako poslední záchrana je CSV.
    """
    excel_path = base_path + ".xlsx"
    xml_path = base_path + ".xml"
    csv_path = base_path + ".csv"
    
    # 1. Zkusí načíst Excel (.xlsx)
    if os.path.exists(excel_path):
        try: return pd.read_excel(excel_path)
        except Exception: pass

    # 2. Zkusí načíst XML (.xml) - Nový univerzální XML dekodér pro W-SERVIS tabulky
    if os.path.exists(xml_path) and os.path.getsize(xml_path) > 0:
        try:
            import xml.etree.ElementTree as ET
            xml_data = None
            # Nalezení správného českého kódování
            for enc in ['utf-8-sig', 'utf-8', 'cp1250', 'windows-1250']:
                try:
                    with open(xml_path, 'r', encoding=enc) as f:
                        xml_data = f.read()
                    break
                except UnicodeDecodeError: continue
            
            if xml_data:
                # Očistíme XML hlavičku, která může způsobovat pády parseru
                xml_data = re.sub(r'<\?xml.*?\?>', '', xml_data, flags=re.IGNORECASE)
                root = ET.fromstring(xml_data)
                
                rows = []
                # Vyhledá všechny záznamy (typicky tag <polozka>)
                for pol in root.findall('.//polozka'):
                    row_data = {}
                    for child in pol:
                        if len(child) > 0: # Zploštění vnořených tagů (např. <ADRESA><FIRMA>...)
                            for subchild in child:
                                row_data[subchild.tag.lower()] = subchild.text if subchild.text else ""
                        else:
                            row_data[child.tag.lower()] = child.text if child.text else ""
                    rows.append(row_data)
                
                if rows:
                    return pd.DataFrame(rows)
        except Exception: 
            pass # Pokud selže XML, propadne to na záchranné CSV
            
    # 3. Zkusí načíst staré CSV (.csv)
    if os.path.exists(csv_path) and os.path.getsize(csv_path) > 0: 
        for enc in ("utf-8-sig", "utf-8", "cp1250", "windows-1250", "iso-8859-2", "latin-1"):
            try:
                df = pd.read_csv(csv_path, sep=";", encoding=enc, on_bad_lines='skip')
                if len(df.columns) == 1 and "," in df.columns[0]:
                    df = pd.read_csv(csv_path, sep=",", encoding=enc, on_bad_lines='skip')
                return df
            except Exception: continue
            
    return None

def clean_ico(ico_val: Any) -> str:
    s = str(ico_val).strip()
    if s.lower() in ['nan', 'none', 'null', '']: return ""
    return s.split('.')[0]

def import_all_ceniky() -> str:
    log_messages: List[str] = []
    connection = sqlite3.connect(DB_PATH)
    try:
        # Import všech menších ceníků a kategorií
        for ui_key, name in CATEGORY_MAP.items():
            base_path = os.path.join(CSV_FOLDER, name)
            table_name = normalize_category_to_table(ui_key)

            df = safe_read_data(base_path)
            if df is None: continue

            df.columns = [str(col).strip().lower() for col in df.columns]
            if 'zbozi_nazev' in df.columns: df.rename(columns={'zbozi_nazev': 'nazev'}, inplace=True)
            if 'zbozi_cena' in df.columns: df.rename(columns={'zbozi_cena': 'cena'}, inplace=True)
            if 'ukon_popis' in df.columns: df.rename(columns={'ukon_popis': 'nazev'}, inplace=True)
            if 'ukon_cena' in df.columns: df.rename(columns={'ukon_cena': 'cena'}, inplace=True)

            if "nazev" not in df.columns or "cena" not in df.columns: continue

            df = df.dropna(subset=["nazev", "cena"])
            df["nazev"] = df["nazev"].astype(str).str.strip()
            df = df[df["nazev"] != "nan"]
            df = df[df["nazev"] != ""]
            
            count_before = len(df)
            df = df.drop_duplicates(subset=["nazev"], keep="first")
            count_after = len(df)

            df["cena"] = df["cena"].astype(str).str.replace(r"\s+", "", regex=True).str.replace(",", ".", regex=False)
            df["cena"] = pd.to_numeric(df["cena"], errors="coerce").fillna(0.0)

            valid_cols = [col for col in df.columns if col in ["nazev", "cena"]]
            try:
                df[valid_cols].to_sql(table_name, connection, if_exists="replace", index=False)
                log_messages.append(f"✅ Načteno: {name} ({len(df)} položek)")
            except Exception as e:
                log_messages.append(f"❌ {name}: Chyba DB – {e}")
                
        # Import centrálního souboru zboží / expimp (nyní s plnou XML podporou!)
        expimp_base = os.path.join(CSV_FOLDER, "expimp")
        df_exp = safe_read_data(expimp_base)
        if df_exp is not None:
            df_exp.columns = [str(c).strip().lower().replace('"', '') for c in df_exp.columns]
            name_col = 'nazev' if 'nazev' in df_exp.columns else ('zkratka' if 'zkratka' in df_exp.columns else None)
            price_col = None
            if 'cena1' in df_exp.columns: price_col = 'cena1'
            elif 'cena_prodejni' in df_exp.columns: price_col = 'cena_prodejni'
            else:
                for col in df_exp.columns:
                    if 'cena' in col and 'prum' not in col and 'posl' not in col and 'nakup' not in col:
                        price_col = col
                        break
            
            if name_col:
                df_clean = pd.DataFrame()
                df_clean['nazev'] = df_exp[name_col].astype(str).str.strip()
                if price_col:
                    df_clean['cena'] = df_exp[price_col].astype(str).str.replace(r"\s+", "", regex=True).str.replace(",", ".", regex=False)
                    df_clean['cena'] = pd.to_numeric(df_clean['cena'], errors="coerce").fillna(0.0)
                else: df_clean['cena'] = 0.0

                df_clean = df_clean.dropna(subset=['nazev'])
                df_clean = df_clean[df_clean['nazev'] != "nan"]
                df_clean = df_clean[df_clean['nazev'] != ""]
                df_clean = df_clean.drop_duplicates(subset=["nazev"], keep="first")
                
                try:
                    df_clean.to_sql("cenik_zbozi", connection, if_exists="append", index=False)
                    log_messages.append(f"📦 ÚSPĚCH: Zboží z expimp spárováno.")
                except Exception as e: pass

        # Import zákazníků (již řešen přes univerzální XML zplošťovač v `safe_read_data`)
        xml_path = os.path.join(CSV_FOLDER, "obchpartner.xml")
        if os.path.exists(xml_path):
            try:
                import xml.etree.ElementTree as ET
                xml_data = None
                for enc in ['utf-8-sig', 'utf-8', 'cp1250', 'windows-1250']:
                    try:
                        with open(xml_path, 'r', encoding=enc) as f:
                            xml_data = f.read()
                        break
                    except UnicodeDecodeError:
                        continue
                
                if xml_data:
                    xml_data = re.sub(r'<\?xml.*?\?>', '', xml_data, flags=re.IGNORECASE)
                    root = ET.fromstring(xml_data)
                    
                    zakaznici = []
                    for pol in root.findall('.//polozka'):
                        ico = pol.findtext('ICO', '')
                        dic = pol.findtext('DIC', '')
                        adresa = pol.find('ADRESA')
                        
                        firma = adresa.findtext('FIRMA', '') if adresa is not None else ''
                        adresa1 = adresa.findtext('ADRESA1', '') if adresa is not None else ''
                        adresa2 = adresa.findtext('ADRESA2', '') if adresa is not None else ''
                        mesto = adresa.findtext('ADRESA3', '') if adresa is not None else ''
                        psc = adresa.findtext('PSC', '') if adresa is not None else ''
                        
                        ulice = adresa1 if adresa1.strip() else adresa2
                        
                        if firma.strip():
                            zakaznici.append({
                                "ICO": clean_ico(ico),
                                "DIC": dic.strip(),
                                "FIRMA": firma.strip(),
                                "ADRESA1": ulice.strip(),
                                "ADRESA3": mesto.strip(),
                                "PSC": psc.strip()
                            })
                    
                    if zakaznici:
                        df_xml = pd.DataFrame(zakaznici)
                        df_xml.to_sql("obchpartner", connection, if_exists="replace", index=False)
                        log_messages.append(f"🏢 ÚSPĚCH: Zákazníci z obchpartner.xml načteni ({len(df_xml)} firem).")
            except Exception as e:
                log_messages.append(f"❌ obchpartner.xml: Nelze zpracovat – {e}")

    finally:
        connection.close()
    return "\n".join(log_messages) if log_messages else "Žádné soubory k načtení."

def get_price(cat_key: str, item_name: str) -> float:
    if not os.path.exists(DB_PATH): return 0.0
    table = normalize_category_to_table(cat_key)
    try:
        conn = sqlite3.connect(DB_PATH)
        try:
            query = f"SELECT cena FROM {table} WHERE nazev = ? LIMIT 1"
            result = conn.execute(query, (item_name.strip(),)).fetchone()
        finally:
            conn.close()
        return float(result[0]) if result else 0.0
    except Exception:
        return 0.0

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

# ==========================================
# 3. LOKÁLNÍ DATABÁZE ZÁKAZNÍKŮ (OFFLINE)
# ==========================================
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
        def clean_col(c): return str(c).strip().lower().replace("č", "c").replace("ř", "r").replace("š", "s")
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

def get_objects_from_db(ico: Any) -> List[str]:
    ico_clean = clean_ico(ico)
    if not os.path.exists(DB_PATH) or not ico_clean: return []
    try:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute("SELECT nazev_objektu FROM objekty WHERE ico = ? ORDER BY nazev_objektu", (ico_clean,))
        rows = cur.fetchall()
        conn.close()
        return [row[0] for row in rows]
    except Exception: return []

def add_object_to_db(ico: Any, nazev_objektu: str) -> bool:
    ico_clean = clean_ico(ico)
    if not ico_clean or not nazev_objektu.strip(): return False
    try:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute("INSERT OR IGNORE INTO objekty (ico, nazev_objektu) VALUES (?, ?)", (ico_clean, nazev_objektu.strip()))
        conn.commit(); conn.close()
        return True
    except Exception: return False

# ==========================================
# 4. PDF ENGINE (CORPORATE GRID)
# ==========================================
class UrbaneKPDF(FPDF):
    def __init__(self) -> None:
        super().__init__()
        self.pismo_ok = False
        self.pismo_name = "ArialCZ"
        font_paths = ["arial.ttf", "ARIAL.TTF", "C:/Windows/Fonts/arial.ttf", "C:/Windows/Fonts/ARIAL.TTF"]
        bold_paths = ["arialbd.ttf", "ARIALBD.TTF", "C:/Windows/Fonts/arialbd.ttf", "C:/Windows/Fonts/ARIALBD.TTF"]
        
        reg_font = next((f for f in font_paths if os.path.exists(f)), None)
        bold_font = next((f for f in bold_paths if os.path.exists(f)), None)
        
        if not reg_font or not bold_font:
            dejavu_reg = "DejaVuSans.ttf"
            dejavu_bold = "DejaVuSans-Bold.ttf"
            if not os.path.exists(dejavu_reg):
                try:
                    import urllib.request
                    urllib.request.urlretrieve("https://github.com/matumo/DejaVuSans/raw/master/Fonts/DejaVuSans.ttf", dejavu_reg)
                    urllib.request.urlretrieve("https://github.com/matumo/DejaVuSans/raw/master/Fonts/DejaVuSans-Bold.ttf", dejavu_bold)
                except Exception: pass
            if os.path.exists(dejavu_reg) and os.path.exists(dejavu_bold):
                reg_font = dejavu_reg
                bold_font = dejavu_bold

        if reg_font and bold_font:
            try:
                self.add_font(self.pismo_name, "", reg_font)
                self.add_font(self.pismo_name, "B", bold_font)
                self.pismo_ok = True
            except Exception:
                self.pismo_ok = False

    def header(self) -> None:
        pismo = self.pismo_name if self.pismo_ok else "helvetica"
        if os.path.exists("logo.png"):
            self.image("logo.png", x=10, y=8, w=22)
            self.set_xy(38, 10)
        else: self.set_xy(10, 10)
            
        self.set_font(pismo, "B", 12)
        self.cell(0, 5, FIRMA_VLASTNI["název"], ln=True)
        self.set_x(38 if os.path.exists("logo.png") else 10)
        self.set_font(pismo, "", 9)
        self.cell(0, 4, f"{FIRMA_VLASTNI['sídlo']}", ln=True)
        self.set_x(38 if os.path.exists("logo.png") else 10)
        self.cell(0, 4, f"IČO: {FIRMA_VLASTNI['ico']}   DIČ: {FIRMA_VLASTNI['dic']}", ln=True)
        self.set_x(38 if os.path.exists("logo.png") else 10)
        self.cell(0, 4, f"{FIRMA_VLASTNI['zápis']}", ln=True)
        self.set_x(38 if os.path.exists("logo.png") else 10)
        
        self.cell(0, 4, f"Mobil: {FIRMA_VLASTNI['telefony']}", ln=True)
        self.set_x(38 if os.path.exists("logo.png") else 10)
        self.cell(0, 4, f"Email: {FIRMA_VLASTNI['email']} | WEB: {FIRMA_VLASTNI['web']}", ln=True)
        self.line(10, 35, 200, 35)
        self.ln(5)

    def footer(self) -> None:
        pass

def create_wservis_dl(zakaznik: Dict[str, Any], items_dict: Dict[str, Any], dl_number: str, zakazka: str, technik: str, objekty_map: Dict[int, str], typ_dl: str, vyrazene_kody: List[str]) -> Optional[bytes]:
    pdf = UrbaneKPDF()
    pismo = pdf.pismo_name if pdf.pismo_ok else "helvetica"
    pdf.add_page()
    
    def fmt_price(num):
        if num == 0: return "0,00"
        s = f"{num:,.2f}".replace(",", "X").replace(".", ",").replace("X", " ")
        if s.endswith(",00"): return s[:-3]
        if s.endswith("0") and "," in s: return s[:-1]
        return s

    def fmt_q(val):
        if not val or val == 0: return ""
        v_str = f"{val:.2f}".replace('.', ',')
        if v_str.endswith(",00"): return v_str[:-3]
        if v_str.endswith("0") and "," in v_str: return v_str[:-1]
        return v_str

    def fmt_tot(num): 
        if num == 0: return "0,00"
        return f"{num:,.2f}".replace(",", "X").replace(".", ",").replace("X", " ")

    def get_safe_str(d, key):
        v = d.get(key, "")
        return "" if pd.isna(v) or str(v).lower() in ["nan", "none", "null"] else str(v).strip()

    y_dl = pdf.get_y()
    pdf.set_font(pismo, "B", 12)
    pdf.cell(50, 5, "DODACÍ LIST")
    pdf.set_font(pismo, "", 9)
    pdf.cell(50, 5, "(práce, zboží, materiál)")

    pdf.set_xy(110, y_dl)
    pdf.set_font(pismo, "B", 9)
    pdf.set_fill_color(235, 235, 235)
    str_dl_typ = "Poř. číslo:" if "Standard" in typ_dl else "Číslo DL:"
    pdf.cell(25, 5, str_dl_typ, border=1, align="C", fill=True)
    pdf.cell(25, 5, "Zakázka:", border=1, align="C", fill=True)
    pdf.cell(40, 5, "Kontrolní technik:", border=1, align="C", fill=True, ln=True)
    
    pdf.set_xy(110, y_dl + 5)
    pdf.set_font(pismo, "B", 9)
    pdf.cell(25, 6, dl_number, border=1, align="C")
    pdf.cell(25, 6, zakazka, border=1, align="C")
    pdf.cell(40, 6, technik, border=1, align="C", ln=True)
    pdf.ln(8)

    def draw_category(cat_num_title: str, item_cats: List[str]):
        cat_items = [[k, v] for k, v in items_dict.items() if v["cat"] in item_cats and v.get("q", 0) > 0]
        if not cat_items: return 0.0

        pdf.set_font(pismo, "B", 9)
        pdf.set_fill_color(210, 220, 230)
        pdf.cell(190, 6, f" {cat_num_title}", border=1, fill=True, ln=True)
        
        pdf.set_fill_color(240, 240, 240)
        pdf.set_font(pismo, "B", 8)
        pdf.cell(85, 5, " Název položky", border=1, fill=True)
        pdf.cell(17, 5, "Cena/ks", border=1, align="C", fill=True)
        pdf.cell(8, 5, "O1", border=1, align="C", fill=True)
        pdf.cell(8, 5, "O2", border=1, align="C", fill=True)
        pdf.cell(8, 5, "O3", border=1, align="C", fill=True)
        pdf.cell(8, 5, "O4", border=1, align="C", fill=True)
        pdf.cell(8, 5, "O5", border=1, align="C", fill=True)
        pdf.cell(15, 5, "ks/výk", border=1, align="C", fill=True)
        pdf.cell(33, 5, "CELKEM (Kč)", border=1, align="C", fill=True, ln=True)

        cat_total = 0.0
        pdf.set_font(pismo, "", 9)
        for name, vals in cat_items:
            q1 = vals.get("q1", 0)
            q2 = vals.get("q2", 0)
            q3 = vals.get("q3", 0)
            q4 = vals.get("q4", 0)
            q5 = vals.get("q5", 0)
            qty = vals.get("q", 0)
            price = vals.get("p", 0)
            
            line_total = qty * price
            cat_total += line_total
            
            name_disp = " " + name[:58] + ("..." if len(name) > 58 else "")
            
            pdf.cell(85, 6, name_disp, border=1)
            pdf.cell(17, 6, fmt_price(price), border=1, align="R")
            pdf.cell(8, 6, fmt_q(q1), border=1, align="C")
            pdf.cell(8, 6, fmt_q(q2), border=1, align="C")
            pdf.cell(8, 6, fmt_q(q3), border=1, align="C")
            pdf.cell(8, 6, fmt_q(q4), border=1, align="C")
            pdf.cell(8, 6, fmt_q(q5), border=1, align="C")
            pdf.cell(15, 6, fmt_q(qty), border=1, align="C")
            pdf.cell(33, 6, fmt_tot(line_total), border=1, align="R", ln=True)
            
        pdf.set_font(pismo, "B", 9)
        pdf.set_fill_color(245, 245, 245)
        nazev_celkem = cat_num_title.split('. ', 1)[-1] if '. ' in cat_num_title else cat_num_title
        pdf.cell(157, 6, f"CELKEM za {nazev_celkem}: ", border=1, align="R", fill=True)
        pdf.cell(33, 6, fmt_tot(cat_total), border=1, align="R", fill=True, ln=True)
        pdf.ln(4)
        return cat_total

    total_sum = 0.0
    if typ_dl == "Standard (Kontroly)":
        total_sum += draw_category("1. KONTROLY HASÍCÍCH PŘÍSTROJŮ", ["HP"])
    else: 
        total_sum += draw_category("1. KONTROLY OPRAVENÝCH HP", ["HP"])
        
    total_sum += draw_category("1. KONTROLY ZAŘÍZENÍ PRO ZÁSOBOVÁNÍ POŽÁRNÍ VODOU", ["Voda"])
    total_sum += draw_category("2. VYHODNOCENÍ KONTROLY + NÁHRADY", ["Nahrady", "Servisni_ukony"])
    total_sum += draw_category("3. OPRAVY HASICÍCH PŘÍSTROJŮ", ["Opravy"])
    total_sum += draw_category("4. PRODEJ ZBOŽÍ, MATERIÁLU A ND", ["ND_HP", "ND_Voda", "TAB", "TABFOTO", "HILTI", "CIDLO", "PASKA", "PK", "reklama", "FA", "Zboží"])
    total_sum += draw_category("5. OSTATNÍ A OZO", ["Ostatni", "OZO"])

    pdf.set_font(pismo, "B", 10)
    pdf.set_fill_color(220, 220, 220)
    pdf.cell(157, 8, "CELKEM K ÚHRADĚ BEZ DPH: ", border=1, align="R", fill=True)
    pdf.cell(33, 8, f"{fmt_tot(total_sum)} Kč", border=1, align="R", fill=True, ln=True)
    pdf.ln(8)

    # ----------------------------------------------
    # PATIČKA - ODBĚRATEL, MAPA OBJEKTŮ A PODPISY
    # ----------------------------------------------
    firma = get_safe_str(zakaznik, 'FIRMA')
    ico = clean_ico(zakaznik.get('ICO'))
    dic = get_safe_str(zakaznik, 'DIC')
    if not firma or firma == ico: firma = f"Neznámý název (IČO: {ico})"

    ul = get_safe_str(zakaznik, "ULICE")
    cp = get_safe_str(zakaznik, "CP")
    co = get_safe_str(zakaznik, "CO")
    adr1 = get_safe_str(zakaznik, "ADRESA1")
    
    if ul and cp:
        adr_line1 = f"{ul} {cp}"
        if co and co != "0": adr_line1 += f"/{co}"
    elif ul: adr_line1 = ul
    elif adr1: adr_line1 = adr1
    else: adr_line1 = ""
        
    ob = get_safe_str(zakaznik, "ADRESA3")
    ps = get_safe_str(zakaznik, "PSC")
    adr_line2 = f"{ps} {ob}".strip()

    pdf.set_font(pismo, "B", 9)
    pdf.set_fill_color(235, 235, 235)
    pdf.cell(95, 6, " Odběratel:", border=1, fill=True)
    pdf.cell(95, 6, " Umístění kontrolovaných HP/PV v objektech:", border=1, fill=True, ln=True)

    pdf.set_font(pismo, "", 9)
    pdf.cell(95, 5, f"  Firma:  {firma[:45]}", border="L R")
    pdf.cell(95, 5, f"  1:  {objekty_map.get(1, '')[:45]}", border="L R", ln=True)
    
    pdf.cell(95, 5, f"  Ulice:  {adr_line1[:45]}", border="L R")
    pdf.cell(95, 5, f"  2:  {objekty_map.get(2, '')[:45]}", border="L R", ln=True)
    
    pdf.cell(95, 5, f"  Město:  {adr_line2[:45]}", border="L R")
    pdf.cell(95, 5, f"  3:  {objekty_map.get(3, '')[:45]}", border="L R", ln=True)
    
    pdf.cell(95, 5, f"  IČO:    {ico}", border="L R")
    pdf.cell(95, 5, f"  4:  {objekty_map.get(4, '')[:45]}", border="L R", ln=True)
    
    pdf.cell(95, 6, f"  DIČ:    {dic}", border="L B R")
    pdf.cell(95, 6, f"  5:  {objekty_map.get(5, '')[:45]}", border="L B R", ln=True)
            
    pdf.ln(4)

    pdf.set_font(pismo, "B", 9)
    pdf.set_fill_color(235, 235, 235)
    pdf.cell(190, 6, " Záznam o kontrole a předání:", border=1, fill=True, ln=True)

    y_sig = pdf.get_y()
    pdf.rect(10, y_sig, 190, 24)

    pdf.set_xy(10, y_sig + 2)
    pdf.set_font(pismo, "B", 8)
    pdf.cell(95, 5, "  Za zhotovitele (Předal):", ln=False)
    pdf.cell(95, 5, "  Za odběratele (Převzal):", ln=True)

    pdf.cell(95, 5, "  Kontrolní technik: Tomáš Urbánek", ln=False)
    pdf.cell(95, 5, "  Jméno hůlkovým písmem:", ln=True)

    pdf.cell(95, 5, "  Odborně způsobilá osoba v PO: Ilja Urbánek", ln=False)
    pdf.cell(95, 5, "  ...........................................................", ln=True)

    pdf.ln(1)
    pdf.cell(95, 5, "  ...........................................................", ln=False)
    pdf.cell(95, 5, "  ...........................................................", ln=True)

    # OPRAVA: Odstraněna kurzíva ("I"), nahrazena standardním fontem (""), aby PDF tiskárna nepadala
    pdf.set_font(pismo, "", 7)
    pdf.cell(95, 3, "   Podpisy a razítka zhotovitele", ln=False)
    pdf.cell(95, 3, "   Podpis a razítko odběratele", ln=True)
    pdf.set_y(y_sig + 26)
    
    if vyrazene_kody:
        pdf.ln(2)
        pdf.set_font(pismo, "B", 8)
        pdf.set_fill_color(245, 230, 230)
        pdf.cell(190, 5, " Zaznamenané důvody vyřazení neopravitelných HP (neslouží jako doklad pro evidenci odpadů):", border="L T R", fill=True, ln=True)
        pdf.set_font(pismo, "", 8)
        for i, kod in enumerate(vyrazene_kody):
            text_duvodu = DUVODY_VYRAZENI.get(kod, "")
            b_style = "L B R" if i == len(vyrazene_kody) - 1 else "L R"
            pdf.cell(190, 4, f"  Kód {kod}: {text_duvodu}", border=b_style, ln=True)

    pdf.ln(4)
    wservis_stamp = f"Zpracováno programem HASIČ-SERVIS Dashboard (Architektura W-SERVIS), verze: 35.0 Universal XML / {datetime.date.today().strftime('%d.%m.%Y %H:%M:%S')}"
    pdf.cell(0, 4, wservis_stamp, ln=True)

    try: 
        return bytes(pdf.output())
    except Exception: 
        return None

# ==========================================
# 5. STREAMLIT UI - DYNAMIC MATRIX & EVIDENCE
# ==========================================
st.set_page_config(page_title="W-SERVIS Enterprise v33.0", layout="wide", page_icon="🛡️")

st.markdown("""
<style>
    .stTabs [data-baseweb="tab-list"] { gap: 8px; }
    .stTabs [data-baseweb="tab"] { background-color: #f0f2f6; border-radius: 4px 4px 0 0; padding: 10px 20px; }
    .stTabs [aria-selected="true"] { background-color: #ff4b4b; color: white; font-weight: bold; }
    .cart-box { background-color: #f8f9fa; padding: 15px; border-radius: 8px; border-left: 5px solid #ff4b4b; margin-top: 15px; }
    .evidence-box { border: 2px solid #28a745; padding: 15px; border-radius: 8px; margin-bottom: 20px; background-color: #f9fff9;}
</style>
""", unsafe_allow_html=True)

def load_all_customers() -> Optional[pd.DataFrame]:
    if not os.path.exists(DB_PATH): return None
    try:
        conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
        df = pd.read_sql("SELECT * FROM obchpartner", conn)
        conn.close(); return df
    except Exception: return None

df_customers = load_all_customers()

menu_volba = st.sidebar.radio("Navigace systému:", ["📝 Tvorba Dodacího listu", "🗄️ Katalog a Evidence (Náhrada Access)"])

celkem_polozek = 0
celkem_cena = 0.0
for k, v in st.session_state.data_zakazky.items():
    if v.get("q", 0) > 0: 
        celkem_polozek += v["q"]
        celkem_cena += (v["q"] * v.get("p", 0))
for k, v in st.session_state.dynamic_items.items():
    celkem_polozek += v.get("q", 0)
    celkem_cena += (v.get("q", 0) * v.get("p", 0))

if menu_volba == "📝 Tvorba Dodacího listu":
    with st.sidebar:
        st.header("🏢 Hlavička Dodacího listu")
        typ_dl = st.radio("Hlavička 1. sekce:", ["Standard (Kontroly)", "Opravy (Prior)"])
        dl_number = st.text_input("Číslo DL / Poř.číslo:", value="1698")
        zakazka = st.text_input("Číslo zakázky:", value="1/13")
        technik = st.text_input("Jméno kontrolního technika:", value="Tomáš Urbánek")
        st.divider()
        
        aktualni_ico = ""
        ulozene_objekty = []
        
        if df_customers is not None:
            filt = df_customers.copy()
            filt["FIRMA"] = filt["FIRMA"].fillna("Neznámý název")
            filt["clean_ico"] = filt["ICO"].apply(clean_ico)
            filt = filt.drop_duplicates(subset=["clean_ico", "FIRMA"])
            filt = filt.sort_values(by="FIRMA", key=lambda s: s.astype(str).str.lower())

            if not filt.empty:
                def format_cust(row):
                    f = str(row.get('FIRMA', '')).strip()
                    i = str(row.get('clean_ico', '')).strip()
                    if not f or f.lower() == "nan" or f == i: f = "Neznámý název"
                    return f"{f}  |  IČO: {i}"

                opts = filt.apply(format_cust, axis=1).tolist()
                default_idx = None
                if st.session_state.vybrany_zakaznik:
                    curr_ico = clean_ico(st.session_state.vybrany_zakaznik.get("ICO", ""))
                    for idx_opt, opt in enumerate(opts):
                        if f"IČO: {curr_ico}" in opt:
                            default_idx = idx_opt
                            break

                sel = st.selectbox("🔍 Vyhledat odběratele:", options=opts, index=default_idx if default_idx is not None else 0)
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
                
                # --- NOVINKA: Automatická nabídka adresy klienta jako objektu ---
                ul_kl = str(curr.get("ULICE", "")).strip()
                cp_kl = str(curr.get("CP", "")).strip()
                co_kl = str(curr.get("CO", "")).strip()
                ob_kl = str(curr.get("ADRESA3", "")).strip()

                adr_slozena = ""
                if ul_kl and cp_kl:
                    adr_slozena = f"{ul_kl} {cp_kl}"
                    if co_kl and co_kl != "0": adr_slozena += f"/{co_kl}"
                elif ul_kl: adr_slozena = ul_kl
                elif cp_kl: adr_slozena = cp_kl

                if adr_slozena and ob_kl: adr_slozena += f", {ob_kl}"
                elif ob_kl: adr_slozena = ob_kl

                if adr_slozena and adr_slozena not in ulozene_objekty:
                    ulozene_objekty.insert(0, adr_slozena)

            else: st.warning("Nenalezeno.")

        st.subheader("🏢 Správa objektů v DB")
        if aktualni_ico:
            with st.expander("➕ Přidat nový objekt k zákazníkovi"):
                with st.form("add_obj_form", clear_on_submit=True):
                    novy_objekt = st.text_input("Název objektu")
                    if st.form_submit_button("Uložit do paměti"):
                        if novy_objekt.strip():
                            add_object_to_db(aktualni_ico, novy_objekt)
                            st.rerun()

        st.markdown(f"""
        <div class="cart-box">
            <b>🛒 Živý přehled DL</b><br/>
            Položek: {int(celkem_polozek)} ks<br/>
            Celkem: {celkem_cena:,.2f} Kč
        </div>
        """, unsafe_allow_html=True)
        
        if st.button("🗑️ Vyprázdnit DL (Začít znovu)", use_container_width=True):
            st.session_state.data_zakazky = {}
            st.session_state.dynamic_items = {}
            st.session_state.vyrazene_kody = []
            st.rerun()

    st.title("🛡️ Tvorba Dodacího Listu (W-SERVIS)")
    st.caption("Verze 33.0 Corporate Strict | Sjednocená terminologie (Kontroly) a nová PDF Mřížka")

    st.markdown("### 🏢 Umístění a rozřazení objektů (O1 - O5)")
    st.info("Zvolte si, pro jaké objekty nyní tvoříte Dodací list. Tyto objekty se v PDF propíšou pod čísly 1 až 5. Vypnutím nepotřebných sloupců se roztáhne prostor a vrátí se tlačítka `+` a `-`.")
    
    col_o1, col_o2, col_o3, col_o4, col_o5 = st.columns(5)
    with col_o1: 
        show_o1 = st.checkbox("✅ Sloupec 1 (O1)", value=True)
        o1_name = st.selectbox("Objekt 1:", [""] + ulozene_objekty, key="o1_sel") if show_o1 else ""
    with col_o2: 
        show_o2 = st.checkbox("✅ Sloupec 2 (O2)", value=False)
        o2_name = st.selectbox("Objekt 2:", [""] + ulozene_objekty, key="o2_sel") if show_o2 else ""
    with col_o3: 
        show_o3 = st.checkbox("✅ Sloupec 3 (O3)", value=False)
        o3_name = st.selectbox("Objekt 3:", [""] + ulozene_objekty, key="o3_sel") if show_o3 else ""
    with col_o4: 
        show_o4 = st.checkbox("✅ Sloupec 4 (O4)", value=False)
        o4_name = st.selectbox("Objekt 4:", [""] + ulozene_objekty, key="o4_sel") if show_o4 else ""
    with col_o5: 
        show_o5 = st.checkbox("✅ Sloupec 5 (O5)", value=False)
        o5_name = st.selectbox("Objekt 5:", [""] + ulozene_objekty, key="o5_sel") if show_o5 else ""
        
    mapa_objektu_pro_pdf = {1: o1_name, 2: o2_name, 3: o3_name, 4: o4_name, 5: o5_name}
    st.divider()

    tabs = st.tabs(["🔥 1. HP Kontroly", "🚰 2. PV Kontroly", "🛠️ 3. HP Opravy", "🚗 4. Náhrady", "🛒 5. Zboží", "🧾 6. Tisk"])

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

    def item_row(cat_key: str, item_name: str, fallback_price: float, row_id: str, step_val: float = 1.0) -> None:
        p_val = get_price(cat_key, item_name)
        if p_val == 0.0: p_val = fallback_price

        cols = st.columns(get_col_layout())
        with cols[0]: st.write(f"{item_name}")
        with cols[1]: p = st.number_input(f"P_{row_id}", min_value=0.0, step=0.1, value=float(p_val), key=f"p_{row_id}", label_visibility="collapsed")
        
        idx = 2
        q1 = q2 = q3 = q4 = q5 = 0.0
        old_val = st.session_state.data_zakazky.get(item_name, {})
        
        if show_o1:
            with cols[idx]: q1 = st.number_input(f"1_{row_id}", min_value=0.0, step=float(step_val), value=float(old_val.get("q1", 0.0)), key=f"q1_{row_id}", label_visibility="collapsed")
            idx+=1
        if show_o2:
            with cols[idx]: q2 = st.number_input(f"2_{row_id}", min_value=0.0, step=float(step_val), value=float(old_val.get("q2", 0.0)), key=f"q2_{row_id}", label_visibility="collapsed")
            idx+=1
        if show_o3:
            with cols[idx]: q3 = st.number_input(f"3_{row_id}", min_value=0.0, step=float(step_val), value=float(old_val.get("q3", 0.0)), key=f"q3_{row_id}", label_visibility="collapsed")
            idx+=1
        if show_o4:
            with cols[idx]: q4 = st.number_input(f"4_{row_id}", min_value=0.0, step=float(step_val), value=float(old_val.get("q4", 0.0)), key=f"q4_{row_id}", label_visibility="collapsed")
            idx+=1
        if show_o5:
            with cols[idx]: q5 = st.number_input(f"5_{row_id}", min_value=0.0, step=float(step_val), value=float(old_val.get("q5", 0.0)), key=f"q5_{row_id}", label_visibility="collapsed")
            idx+=1
        
        q_tot = q1 + q2 + q3 + q4 + q5
        st.session_state.data_zakazky[item_name] = {
            "q1": q1, "q2": q2, "q3": q3, "q4": q4, "q5": q5, "q": q_tot, "p": float(p), "cat": cat_key
        }

    with tabs[0]:
        st.markdown("<div class='evidence-box'>", unsafe_allow_html=True)
        st.markdown("### 📋 Zpráva o kontrole HP (Evidence přístrojů)")
        
        if not st.session_state.vybrany_zakaznik:
            st.info("Zvolte odběratele v levém panelu, aby se otevřela evidence jeho přístrojů.")
        else:
            if st.session_state.loaded_ico != aktualni_ico:
                conn = sqlite3.connect(DB_PATH)
                df_evid = pd.read_sql("SELECT druh, typ_hp, vyr_cislo, rok_vyr, mesic_vyr, tlak_rok, tlak_mesic, stav, objekt, misto, do_opravy, do_skladu FROM evidence_hp WHERE ico = ?", conn, params=(aktualni_ico,))
                if df_evid.empty:
                    df_evid = pd.DataFrame(columns=["druh", "typ_hp", "vyr_cislo", "rok_vyr", "mesic_vyr", "tlak_rok", "tlak_mesic", "stav", "objekt", "misto", "do_opravy", "do_skladu"])
                else:
                    df_evid["do_opravy"] = df_evid["do_opravy"].astype(bool)
                    df_evid["do_skladu"] = df_evid["do_skladu"].astype(bool)
                
                st.session_state.evidence_df = df_evid
                st.session_state.loaded_ico = aktualni_ico
                conn.close()
            
            st.caption("Vyplňte seznam kontrolovaných přístrojů. Funguje to jako Excel - můžete přidávat řádky donekonečna.")
            
            edited_evid = st.data_editor(
                st.session_state.evidence_df,
                num_rows="dynamic",
                use_container_width=True,
                key="evidence_editor_safe",
                column_config={
                    "druh": st.column_config.SelectboxColumn("Druh", options=["přenosný", "pojízdný", "přívěsný", "AHS"], width="small"),
                    "typ_hp": st.column_config.TextColumn("Typ HP", width="medium"),
                    "vyr_cislo": st.column_config.TextColumn("Výr. číslo", width="small"),
                    "rok_vyr": st.column_config.NumberColumn("Rok výr.", format="%d", min_value=1900, max_value=2100, width="small"),
                    "mesic_vyr": st.column_config.NumberColumn("Měs.", min_value=1, max_value=12, width="small"),
                    "tlak_rok": st.column_config.NumberColumn("Rok tlak.", format="%d", min_value=1900, max_value=2100, width="small"),
                    "tlak_mesic": st.column_config.NumberColumn("Měs.", min_value=1, max_value=12, width="small"),
                    "stav": st.column_config.SelectboxColumn("Stav", options=["S", "NO", "NOPZ", "CH", "S-nový", "NV"], width="small"),
                    "objekt": st.column_config.SelectboxColumn("Objekt", options=ulozene_objekty if ulozene_objekty else [""], width="medium"),
                    "misto": st.column_config.TextColumn("Umístění", width="medium"),
                    "do_opravy": st.column_config.CheckboxColumn("Do opr.", default=False, width="small"),
                    "do_skladu": st.column_config.CheckboxColumn("Sklad", default=False, width="small"),
                }
            )
            
            if st.button("💾 Uložit Zprávu o kontrole do Databáze zákazníka", type="primary"):
                clean_evid = edited_evid.dropna(how='all').copy()
                clean_evid["do_opravy"] = clean_evid["do_opravy"].fillna(False).astype(int)
                clean_evid["do_skladu"] = clean_evid["do_skladu"].fillna(False).astype(int)
                clean_evid["ico"] = aktualni_ico
                
                conn = sqlite3.connect(DB_PATH)
                cur = conn.cursor()
                cur.execute("DELETE FROM evidence_hp WHERE ico = ?", (aktualni_ico,))
                conn.commit()  
                clean_evid.to_sql("evidence_hp", conn, if_exists="append", index=False)
                conn.close()
                st.session_state.evidence_df = edited_evid
                st.success("Evidence přístrojů byla úspěšně uložena! Můžete pokračovat níže tvorbou Dodacího listu.")
                
        st.markdown("</div>", unsafe_allow_html=True)
        
        st.markdown("### 💰 Fakturace úkonů (Dodací list)")
        render_table_header()
        item_row("HP", "Kontrola HP (shodný)", 29.40, "h1")
        item_row("HP", "Kontrola HP (neshodný - opravitelný)", 19.70, "h2")
        item_row("HP", "Kontrola HP (neshodný - neopravitelný) + odborné zneprovoznění", 23.50, "h3")
        
        mnozstvi_neopravitelne = st.session_state.data_zakazky.get("Kontrola HP (neshodný - neopravitelný) + odborné zneprovoznění", {}).get("q", 0)
        if mnozstvi_neopravitelne > 0:
            st.warning("⚠️ Zadejte prosím důvody vyřazení neopravitelných přístrojů pro tisk do Dodacího listu.")
            vybrane_kody = st.multiselect("Důvody vyřazení (A-K):", options=list(DUVODY_VYRAZENI.keys()), format_func=lambda x: f"Kód {x} - {DUVODY_VYRAZENI[x]}")
            st.session_state.vyrazene_kody = vybrane_kody
        else:
            st.session_state.vyrazene_kody = []

        item_row("HP", "Manipulace a odvoz HP ze servisu (opravy)", 24.00, "h4")
        item_row("HP", "Manipulace a odvoz HP k násl. údržbě-TZ,opravě-plnění,demontáži", 24.00, "h5")
        item_row("HP", "Hod.sazba (pochůzky po objektu/ manipulace s HP/PV + další=dohod", 450.00, "h6", step_val=0.1)

    with tabs[1]:
        st.subheader("1. KONTROLY ZAŘÍZENÍ PRO ZÁSOBOVÁNÍ POŽÁRNÍ VODOU")
        render_table_header()
        item_row("Voda", "Prohlídka zařízení do 5 ks výtoků", 123.00, "v1")
        item_row("Voda", "Kontrola zařízení bez měření průtoku do 5 ks výtoků", 141.00, "v2")
        item_row("Voda", "Prohlídka zařízení od 6 do 10 ks výtoků", 159.00, "v3")
        item_row("Voda", "Kontrola zařízení bez měření průtoku od 6 do 10 ks výtoků", 246.00, "v4")
        item_row("Voda", "Měření průtoku a tlaku spec. zařízením (vnitřní hydrant D/C)", 95.00, "v5")
        item_row("Voda", "Měření průtoku a tlaku spec. zařízením (vnější hydrant)", 179.00, "v6")

    with tabs[2]:
        st.subheader("3. OPRAVY HASICÍCH PŘÍSTROJŮ")
        render_table_header()
        item_row("Opravy", "CO2-5F/ETS", 418.00, "opr1")
        item_row("Opravy", "P6 Če (21A/)", 385.00, "opr2")
        item_row("Opravy", "S1,5 Kod", 280.00, "opr3")
        item_row("Opravy", "S2 KT 02.06/EN3", 314.00, "opr4")
        item_row("Opravy", "S5 Kte", 418.00, "opr5")
        item_row("Opravy", "S6KT", 385.00, "opr6")

    with tabs[3]:
        st.subheader("2. VYHODNOCENÍ KONTROLY + NÁHRADY")
        render_table_header()
        item_row("Servisni_ukony", "Vyhodnocení kontroly + vystavení dokladu o kontrole (á 1ks HP)", 5.80, "s_hp1")
        item_row("Servisni_ukony", "Vyhodnocení kontroly zařízení do 5 ks výtoků", 85.00, "s1")
        item_row("Servisni_ukony", "Vyhodnocení kontroly zařízení od 6 do 10 ks výtoků", 117.00, "s2")
        item_row("Servisni_ukony", "Vyhotovení zprávy o kontrole zařízení pro zásob.pož.vodou", 170.00, "s3")
        item_row("Nahrady", "Náhrada za 1km - osobní servisní vozidlo", 6.00, "n4")
        item_row("Nahrady", "Náhrada za 1km - osobní servisní vozidlo + přívěs", 16.00, "n5")
        item_row("Nahrady", "Náhrada za 1km - nákladní servisní vozidlo do 3,5 tun", 15.90, "n6")
        item_row("Nahrady", "Náhrada za 1km - nákladní servisní vozidlo do 3,5 tun + přívěs", 18.00, "n7")
        item_row("Nahrady", "Převzetí HP vyřazeného z užívání dodavatelem", 88.00, "n1")
        item_row("Nahrady", "Označení - vylepení koleček o kontrole (á 2ks / HP)", 3.50, "n2")
        item_row("Nahrady", "Označení - vylepení štítku o kontrole (á 1ks / HP)", 8.00, "n3")
        item_row("Nahrady", "Náhrada za použití komunikačního kanálu pro zjištění...", 48.00, "n8")

    with tabs[4]:
        st.subheader("4. PRODEJ ZBOŽÍ A MATERIÁLU")
        zbozi_kategorie = ["Zboží", "ND_HP", "ND_Voda", "TAB", "TABFOTO", "HILTI", "CIDLO", "PASKA", "PK", "reklama", "FA", "Ostatni", "OZO", "zbozi"]
        db_items = get_items_from_db(zbozi_kategorie)
        
        if not db_items:
            st.warning("⚠️ Sklad je prázdný. Klikněte vlevo v Evidenci na Synchronizovat.")
        else:
            items_dict_lookup = {item["nazev"]: item for item in db_items}
            
            z_layout = get_col_layout()
            z_layout.append(1.0) 
            z_cols = st.columns(z_layout)
            
            with z_cols[0]: zvolena_polozka = st.selectbox("Vyberte ze skladu:", ["-- Vyberte --"] + list(items_dict_lookup.keys()))
            
            if zvolena_polozka != "-- Vyberte --":
                def_cena = items_dict_lookup[zvolena_polozka]["cena"]
                with z_cols[1]: cena_input = st.number_input("Cena", value=def_cena, step=1.0, key="zb_cena")
                
                idx = 2
                mq1 = mq2 = mq3 = mq4 = mq5 = 0.0
                if show_o1:
                    with z_cols[idx]: mq1 = st.number_input("O1", value=1.0, min_value=0.0, step=1.0, key="zb1"); idx+=1
                if show_o2:
                    with z_cols[idx]: mq2 = st.number_input("O2", value=0.0, min_value=0.0, step=1.0, key="zb2"); idx+=1
                if show_o3:
                    with z_cols[idx]: mq3 = st.number_input("O3", value=0.0, min_value=0.0, step=1.0, key="zb3"); idx+=1
                if show_o4:
                    with z_cols[idx]: mq4 = st.number_input("O4", value=0.0, min_value=0.0, step=1.0, key="zb4"); idx+=1
                if show_o5:
                    with z_cols[idx]: mq5 = st.number_input("O5", value=0.0, min_value=0.0, step=1.0, key="zb5"); idx+=1
                
                with z_cols[idx]:
                    st.write("")
                    if st.button("➕ Přidat"):
                        interni_kat = items_dict_lookup[zvolena_polozka]["internal_cat"]
                        if interni_kat == "zbozi" or interni_kat not in CATEGORY_MAP.values(): interni_kat = "Zboží"
                        mq_tot = mq1 + mq2 + mq3 + mq4 + mq5
                        
                        if zvolena_polozka in st.session_state.dynamic_items:
                            di = st.session_state.dynamic_items[zvolena_polozka]
                            di["q1"] = di.get("q1",0) + mq1
                            di["q2"] = di.get("q2",0) + mq2
                            di["q3"] = di.get("q3",0) + mq3
                            di["q4"] = di.get("q4",0) + mq4
                            di["q5"] = di.get("q5",0) + mq5
                            di["q"] = di.get("q",0) + mq_tot
                            di["p"] = cena_input
                        else:
                            st.session_state.dynamic_items[zvolena_polozka] = {
                                "q1": mq1, "q2": mq2, "q3": mq3, "q4": mq4, "q5": mq5, "q": mq_tot, "p": cena_input, "cat": interni_kat
                            }
                        st.rerun()

        if st.session_state.dynamic_items:
            st.divider()
            for k, v in list(st.session_state.dynamic_items.items()):
                ca, cb, cc, cd = st.columns([5, 2, 2, 1])
                
                o_strs = []
                if show_o1: o_strs.append(f"O1: {v.get('q1',0)}")
                if show_o2: o_strs.append(f"O2: {v.get('q2',0)}")
                if show_o3: o_strs.append(f"O3: {v.get('q3',0)}")
                if show_o4: o_strs.append(f"O4: {v.get('q4',0)}")
                if show_o5: o_strs.append(f"O5: {v.get('q5',0)}")
                o_text = f" ({', '.join(o_strs)})" if o_strs else ""
                
                ca.write(f"• {k}{o_text}")
                cb.write(f"Celkem: {v.get('q',0)} ks")
                cc.write(f"{v.get('q',0) * v.get('p',0):,.2f} Kč")
                if cd.button("❌", key=f"del_{k}"):
                    del st.session_state.dynamic_items[k]
                    st.rerun()

    with tabs[5]:
        active_items = {}
        for k, v in st.session_state.data_zakazky.items():
            if v.get("q", 0) > 0: active_items[k] = v
        for k, v in st.session_state.dynamic_items.items():
            active_items[k] = v

        if not active_items:
            st.warning("Dodací list je prázdný.")
        else:
            firma = st.session_state.vybrany_zakaznik.get("FIRMA", "Neznámý") if st.session_state.vybrany_zakaznik else "Neznámý"
            st.write(f"### Souhrn pro: {firma}")
            
            c_f1, c_f2 = st.columns(2)
            with c_f1: st.metric("CELKEM BEZ DPH", f"{celkem_cena:,.2f} Kč")
            with c_f2:
                if st.button("📄 VYGENEROVAT ČISTÝ DODACÍ LIST (PDF)", type="primary"):
                    if not st.session_state.vybrany_zakaznik:
                        st.error("Vyberte zákazníka!")
                    else:
                        pdf_doc = create_wservis_dl(
                            st.session_state.vybrany_zakaznik, active_items, dl_number, zakazka, technik, mapa_objektu_pro_pdf, typ_dl, st.session_state.vyrazene_kody
                        )
                        if pdf_doc:
                            st.download_button("⬇️ STÁHNOUT PDF", data=pdf_doc, file_name=f"DL_{dl_number}_{firma.replace(' ','_')}.pdf")

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

st.sidebar.divider()
st.sidebar.caption(f"© {datetime.date.today().year} {FIRMA_VLASTNI['název']}")
