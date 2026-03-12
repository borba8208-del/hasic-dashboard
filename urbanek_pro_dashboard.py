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
# 1. KONFIGURACE FIRMY
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
    conn.commit()
    conn.close()

init_db()

if "data_zakazky" not in st.session_state:
    st.session_state.data_zakazky = {}
if "dynamic_items" not in st.session_state:
    st.session_state.dynamic_items = {}
if "vybrany_zakaznik" not in st.session_state:
    st.session_state.vybrany_zakaznik = None
if "vyrazene_kody" not in st.session_state:
    st.session_state.vyrazene_kody = []

# ==========================================
# 2. CENÍKY – PŘÍSNÁ KONTROLA A IMPORT EXPORTU
# ==========================================
def normalize_category_to_table(cat_key: str) -> str:
    if not cat_key: return "cenik_ostatni"
    normalized = cat_key.lower().strip()
    normalized = "".join(char for char in unicodedata.normalize("NFKD", normalized) if not unicodedata.combining(char))
    normalized = re.sub(r"[\s/]+", "_", normalized)
    normalized = re.sub(r"[^a-z0-9_]", "", normalized)
    return f"cenik_{normalized}"

def safe_read_csv(path: str) -> Optional[pd.DataFrame]:
    if not os.path.exists(path) or os.path.getsize(path) == 0: 
        return None
    for enc in ("utf-8", "cp1250", "windows-1250"):
        try:
            return pd.read_csv(path, sep=";", encoding=enc, on_bad_lines='skip')
        except Exception:
            continue
    return None

def import_all_ceniky() -> str:
    log_messages: List[str] = []
    connection = sqlite3.connect(DB_PATH)
    try:
        # 1. Načtení standardních ceníků
        for ui_key, csv_name in CATEGORY_MAP.items():
            file_path = os.path.join(CSV_FOLDER, f"{csv_name}.csv")
            table_name = normalize_category_to_table(ui_key)

            df = safe_read_csv(file_path)
            if df is None: continue

            df.columns = [str(col).strip().lower() for col in df.columns]
            if "nazev" not in df.columns or "cena" not in df.columns:
                continue

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
                dup_info = f" (Očištěno o {count_before - count_after} duplicit)" if count_before > count_after else ""
                log_messages.append(f"✅ Načteno: {csv_name} ({len(df)} položek){dup_info}")
            except Exception as e:
                log_messages.append(f"❌ {csv_name}: Chyba DB – {e}")
                
        # 2. Načtení velkého exportu (expimp.csv)
        expimp_path = os.path.join(CSV_FOLDER, "expimp.csv")
        if os.path.exists(expimp_path):
            df_exp = safe_read_csv(expimp_path)
            if df_exp is not None:
                # Očistíme názvy sloupců od případných uvozovek a mezer
                df_exp.columns = [str(c).strip().lower().replace('"', '') for c in df_exp.columns]
                
                # Zkusíme najít sloupec s názvem (nazev nebo zkratka)
                name_col = 'nazev' if 'nazev' in df_exp.columns else ('zkratka' if 'zkratka' in df_exp.columns else None)
                
                # Zkusíme najít sloupec s prodejní cenou (vynecháme nákupní a průměrné)
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
                    else:
                        df_clean['cena'] = 0.0 # Pokud systém nerozluští cenu, nechá 0 a technik si ji zadá ručně

                    df_clean = df_clean.dropna(subset=['nazev'])
                    df_clean = df_clean[df_clean['nazev'] != "nan"]
                    df_clean = df_clean[df_clean['nazev'] != ""]
                    df_clean = df_clean.drop_duplicates(subset=["nazev"], keep="first")
                    
                    try:
                        # Všechny položky z expimp nalijeme do tabulky cenik_zbozi
                        df_clean.to_sql("cenik_zbozi", connection, if_exists="append", index=False)
                        log_messages.append(f"📦 ÚSPĚCH: Velký export expimp.csv úspěšně spárován! Do katalogu přidáno {len(df_clean)} položek.")
                    except Exception as e:
                        log_messages.append(f"❌ expimp.csv: Chyba při zápisu – {e}")

    finally:
        connection.close()
    return "\n".join(log_messages) if log_messages else "Žádné ceníky k načtení."

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
    
    # Chceme vyfiltrovat unikátní názvy, pokud by se duplikovaly z expimp.csv a Zbozi.csv
    seen_names = set()
    
    for cat in categories:
        tbl = normalize_category_to_table(cat)
        try:
            res = conn.execute(f"SELECT nazev, cena FROM {tbl} ORDER BY nazev").fetchall()
            for r in res:
                if r[0] not in seen_names:
                    items.append({"nazev": r[0], "cena": float(r[1]), "internal_cat": cat})
                    seen_names.add(r[0])
        except Exception:
            pass
    conn.close()
    
    # Seřadit abecedně pro lepší hledání v roletce
    items.sort(key=lambda x: x["nazev"])
    return items

# ==========================================
# 3. ARES API A ZÁKAZNÍCI
# ==========================================
import requests

def get_company_from_ares(ico: str | int) -> Optional[Dict[str, Any]]:
    ico_clean = str(ico).strip().zfill(8)
    url = f"https://ares.gov.cz/ekonomicke-subjekty-v-be/rest/ekonomicke-subjekty/{ico_clean}"
    try:
        response = requests.get(url, timeout=5)
        if response.status_code != 200: return None
        data = response.json()
        es = data.get("ekonomickySubjekt", {})
        sidlo = es.get("sidlo", {})
        return {
            "FIRMA": es.get("obchodniJmeno", ""),
            "DIC": es.get("dic", ""),
            "ULICE": sidlo.get("nazevUlice", ""),
            "CP": sidlo.get("cisloDomovni", ""),
            "CO": sidlo.get("cisloOrientacni", ""),
            "ADRESA3": sidlo.get("nazevObce", ""),
            "PSC": sidlo.get("psc", ""),
            "ARES_OK": True,
        }
    except Exception: return None

def update_customer_in_db(zakaznik: Dict[str, Any]) -> bool:
    if not os.path.exists(DB_PATH): return False
    try:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cols = [row[1] for row in cur.execute("PRAGMA table_info(obchpartner)")]
        for c in ["ADRESA1", "ADRESA2"]:
            if c not in cols: cur.execute(f"ALTER TABLE obchpartner ADD COLUMN {c} TEXT")
        cur.execute(
            "UPDATE obchpartner SET FIRMA=?, ADRESA1=?, ADRESA2=?, ADRESA3=?, PSC=?, DIC=? WHERE ICO=?",
            (zakaznik.get("FIRMA"), zakaznik.get("ULICE"), zakaznik.get("CP"), zakaznik.get("ADRESA3"), zakaznik.get("PSC"), zakaznik.get("DIC"), zakaznik.get("ICO")),
        )
        conn.commit(); conn.close()
        return True
    except Exception: return False

def get_objects_from_db(ico: str) -> List[str]:
    if not os.path.exists(DB_PATH): return []
    try:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute("SELECT nazev_objektu FROM objekty WHERE ico = ? ORDER BY nazev_objektu", (str(ico),))
        rows = cur.fetchall()
        conn.close()
        return [row[0] for row in rows]
    except Exception:
        return []

def add_object_to_db(ico: str, nazev_objektu: str) -> bool:
    try:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute("INSERT OR IGNORE INTO objekty (ico, nazev_objektu) VALUES (?, ?)", (str(ico), nazev_objektu.strip()))
        conn.commit()
        conn.close()
        return True
    except Exception:
        return False

# ==========================================
# 4. PDF ENGINE (W-SERVIS PIXEL PERFECT)
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
        else:
            self.set_xy(10, 10)
            
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
        self.cell(0, 4, f"Tel: , tel./fax: , mobil: {FIRMA_VLASTNI['telefony']}", ln=True)
        self.set_x(38 if os.path.exists("logo.png") else 10)
        self.cell(0, 4, f"Email: {FIRMA_VLASTNI['email']}, WEB: {FIRMA_VLASTNI['web']}", ln=True)
        self.line(10, 35, 200, 35)
        self.ln(5)

    def footer(self) -> None:
        pass

def create_wservis_dl(zakaznik: Dict[str, Any], items_dict: Dict[str, Any], dl_number: str, zakazka: str, technik: str, objekty: str, typ_dl: str, vyrazene_kody: List[str]) -> Optional[bytes]:
    pdf = UrbaneKPDF()
    pismo = pdf.pismo_name if pdf.pismo_ok else "helvetica"
    pdf.add_page()
    
    def fmt_price(num):
        s = f"{num:,.2f}".replace(",", "X").replace(".", ",").replace("X", " ")
        if s.endswith(",00"): return s[:-3]
        if s.endswith("0") and "," in s: return s[:-1]
        return s

    def fmt_tot(num):
        return f"{num:,.2f}".replace(",", "X").replace(".", ",").replace("X", " ")

    # HLAVIČKA
    y_dl = pdf.get_y()
    pdf.set_font(pismo, "B", 12)
    pdf.cell(50, 5, "DODACÍ LIST")
    pdf.set_font(pismo, "", 9)
    pdf.cell(50, 5, "(práce, zboží, materiál)")

    pdf.set_xy(110, y_dl)
    pdf.cell(25, 4, "Poř.číslo" if "Standard" in typ_dl else "Číslo DL")
    pdf.cell(30, 4, "Číslo zakázky")
    pdf.cell(35, 4, "Jméno reviz. technika", ln=True)

    pdf.set_xy(110, y_dl + 4)
    pdf.set_font(pismo, "B", 10)
    pdf.cell(25, 5, dl_number)
    pdf.cell(30, 5, zakazka)
    pdf.cell(35, 5, technik, ln=True)
    pdf.ln(5)

    def draw_category(cat_num_title: str, item_cats: List[str]):
        cat_items = [[k, v["q"], v["p"]] for k, v in items_dict.items() if v["cat"] in item_cats and v["q"] > 0]
        if not cat_items: return 0.0

        pdf.set_font(pismo, "B", 9)
        pdf.cell(95, 4, f" {cat_num_title}", border=0)
        pdf.set_font(pismo, "", 8)
        pdf.cell(20, 4, "Cena", align="R")
        pdf.cell(45, 4, "ks/výk. - jednotlivé objekty", align="R")
        pdf.cell(30, 4, "CELKEM", align="R", ln=True)
        
        pdf.cell(95, 4, "", border=0)
        pdf.cell(20, 4, "bez DPH", align="R")
        pdf.cell(30, 4, "1  2  3  4  5", align="R")
        pdf.cell(15, 4, "ks/výk", align="R")
        pdf.cell(30, 4, "Kč bez DPH", align="R", ln=True)

        cat_total = 0.0
        pdf.set_font(pismo, "", 9)
        for name, qty, price in cat_items:
            line_total = qty * price
            cat_total += line_total
            
            name_disp = "  " + name[:65] + ("..." if len(name) > 65 else "")
            pdf.cell(95, 5, name_disp)
            pdf.cell(20, 5, fmt_price(price), align="R")
            
            pdf.cell(30, 5, "", align="R") 
            q_disp = f"{qty:,.2f}".rstrip("0").rstrip(".") if qty % 1 != 0 else f"{int(qty)}"
            pdf.cell(15, 5, q_disp, align="R")
            
            pdf.cell(30, 5, fmt_tot(line_total), align="R", ln=True)
            
        pdf.ln(1)
        pdf.set_font(pismo, "B", 9)
        nazev_celkem = cat_num_title.split('. ', 1)[-1] if '. ' in cat_num_title else cat_num_title
        pdf.cell(160, 5, f"C E L K E M   {nazev_celkem}", align="R")
        pdf.cell(30, 5, fmt_tot(cat_total), align="R", ln=True)
        pdf.ln(3)
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

    pdf.ln(4)
    pdf.set_font(pismo, "B", 10)
    pdf.cell(160, 6, "C E L K E M   K   Ú H R A D Ě   B E Z   D P H", align="R")
    pdf.cell(30, 6, f"{fmt_tot(total_sum)} Kč", align="R", ln=True)
    pdf.ln(12)

    ul = zakaznik.get("ULICE", "") or ""
    cp = zakaznik.get("CP", "") or ""
    co = zakaznik.get("CO", "") or ""
    ob = zakaznik.get("ADRESA3", "") or ""
    ps = zakaznik.get("PSC", "") or ""
    adr_line1 = f"{ul} {cp}".strip()
    if co and co not in ["None", "", "nan", "0"]: adr_line1 += f"/{co}"
    adr_line2 = f"{ps} {ob}".strip()

    objekty_list = [o.strip() for o in objekty.split('\n') if o.strip()]
    while len(objekty_list) < 4:
        objekty_list.append("") 

    pdf.set_font(pismo, "B", 9)
    pdf.cell(70, 5, "Odběratel:", ln=False)
    pdf.cell(75, 5, "Umístění kontrolovaných HP/PV v objektech:", ln=False)
    pdf.cell(45, 5, "Potvrzení reviz.technika:", ln=True)

    pdf.set_font(pismo, "", 9)
    pdf.cell(70, 5, zakaznik.get('FIRMA','')[:40], ln=False)
    pdf.cell(75, 5, objekty_list[0], ln=False)
    pdf.cell(45, 5, "", ln=True)
    
    pdf.cell(70, 5, adr_line1[:40], ln=False)
    pdf.cell(75, 5, objekty_list[1], ln=False)
    pdf.cell(45, 5, "Datum:", ln=True)
    
    pdf.cell(70, 5, adr_line2[:40], ln=False)
    pdf.cell(75, 5, objekty_list[2], ln=False)
    pdf.cell(45, 5, f"{datetime.date.today().strftime('%d.%m.%Y')}", ln=True)
    
    pdf.cell(70, 5, f"IČO: {zakaznik.get('ICO','')}  DIČ: {zakaznik.get('DIC','')}", ln=False)
    pdf.cell(75, 5, objekty_list[3], ln=False)
    pdf.cell(45, 5, "", ln=True)
    
    if len(objekty_list) > 4:
        for obj in objekty_list[4:]:
            if obj:
                pdf.cell(70, 5, "", ln=False) 
                pdf.cell(75, 5, obj, ln=False)
                pdf.cell(45, 5, "", ln=True)
            
    pdf.ln(10)

    pdf.set_font(pismo, "", 7)
    note1 = "Poznámka:  HP - SHODNÝ  - splňuje veškeré podmínky stanovené odbornými pokyny výrobce,  HP - NESHODNÝ  - nesplňuje podmínky stanovené odbornými pokyny "
    note2 = "výrobce - je a) opravitelný nebo b) neopravitelný - nutné vyřazení z provozu a odborná ekologická likvidace (neslouží jako doklad pro státní evidenci odpadů)."
    pdf.multi_cell(0, 3, note1 + "\n" + note2)
    
    if vyrazene_kody:
        pdf.ln(2)
        pdf.set_font(pismo, "B", 7)
        pdf.cell(0, 3, "Zaznamenané důvody vyřazení neopravitelných HP:", ln=True)
        pdf.set_font(pismo, "", 7)
        for kod in vyrazene_kody:
            text_duvodu = DUVODY_VYRAZENI.get(kod, "")
            pdf.cell(0, 3, f"  Kód {kod} - {text_duvodu}", ln=True)

    pdf.ln(3)
    wservis_stamp = f"Zpracováno programem HASIČ-SERVIS Dashboard (Architektura W-SERVIS), verze: 22.0 / {datetime.date.today().strftime('%d.%m.%Y %H:%M:%S')}"
    pdf.cell(0, 4, wservis_stamp, ln=True)

    try: 
        return bytes(pdf.output())
    except Exception: 
        return None

# ==========================================
# 5. STREAMLIT UI - HLAVNÍ NAVIGACE A OBSAH
# ==========================================
st.set_page_config(page_title="W-SERVIS Master v22.0", layout="wide", page_icon="🛡️")

def load_all_customers() -> Optional[pd.DataFrame]:
    if not os.path.exists(DB_PATH): return None
    try:
        conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
        df = pd.read_sql("SELECT * FROM obchpartner", conn)
        conn.close(); return df
    except Exception: return None

df_customers = load_all_customers()

# --- HLAVNÍ MENU ---
menu_volba = st.sidebar.radio("Navigace systému:", ["📝 Tvorba Dodacího listu", "🗄️ Katalog a Evidence"])

if menu_volba == "📝 Tvorba Dodacího listu":
    # --- SIDEBAR PRO DL ---
    with st.sidebar:
        st.header("🏢 Hlavička Dodacího listu")
        typ_dl = st.radio("Hlavička 1. sekce:", ["Standard (Kontroly)", "Opravy (Prior)"])
        dl_number = st.text_input("Číslo DL / Poř.číslo:", value="1698")
        zakazka = st.text_input("Číslo zakázky:", value="1/13")
        technik = st.text_input("Jméno reviz. technika:", value="v.z. Tomáš Urbánek")
        st.divider()
        
        if df_customers is not None:
            sq = st.text_input("🔍 Vyhledat zákazníka:")
            sq_lower = sq.lower().strip()
            if sq_lower:
                mask = (df_customers["ICO"].astype(str).str.contains(sq_lower, na=False) | 
                        df_customers["FIRMA"].str.lower().str.contains(sq_lower, na=False))
            else: mask = pd.Series([True] * len(df_customers))
            
            filt = df_customers[mask].sort_values(by="FIRMA", key=lambda s: s.str.lower())

            if not filt.empty:
                opts = filt["FIRMA"] + " (" + filt["ICO"].astype(str) + ")"
                sel = st.selectbox("Zvolte odběratele:", opts)
                idx = opts.tolist().index(sel)
                curr = filt.iloc[idx].to_dict()

                if (st.session_state.vybrany_zakaznik is None or st.session_state.vybrany_zakaznik.get("ICO") != curr.get("ICO")):
                    if not curr.get("ARES_OK"):
                        with st.spinner("Ladění adresy přes ARES..."):
                            ares = get_company_from_ares(curr["ICO"])
                            if ares:
                                curr.update(ares); update_customer_in_db(curr)
                    st.session_state.vybrany_zakaznik = curr.copy()
            else: st.warning("Nenalezeno.")

        st.divider()
        st.subheader("🏢 Umístění v objektech")
        objekty_text = ""
        
        if st.session_state.vybrany_zakaznik:
            aktualni_ico = st.session_state.vybrany_zakaznik.get("ICO")
            ulozene_objekty = get_objects_from_db(aktualni_ico)
            
            if ulozene_objekty:
                vybrane_objekty = st.multiselect(
                    "Vyberte objekty z paměti pro tento DL:",
                    options=ulozene_objekty,
                    default=[]
                )
                objekty_text = "\n".join(vybrane_objekty)
            else:
                st.info("Zákazník zatím nemá uložené žádné objekty.")
                
            with st.expander("➕ Přidat nový objekt k zákazníkovi"):
                with st.form("add_obj_form", clear_on_submit=True):
                    novy_objekt = st.text_input("Název objektu (např. OD Prior - 1. patro)")
                    if st.form_submit_button("Uložit do paměti"):
                        if novy_objekt.strip():
                            add_object_to_db(aktualni_ico, novy_objekt)
                            st.success(f"Objekt přidán!")
                            st.rerun()

    # --- MAIN AREA PRO DL ---
    st.title("🛡️ Tvorba Dodacího Listu (W-SERVIS)")
    st.caption("Verze 22.0 | Integrován import z expimp.csv (Velkoobchodní skener)")

    tabs = st.tabs(["🔥 1. HP Kontroly", "🚰 2. PV Kontroly", "🛠️ 3. HP Opravy", "🚗 4. Náhrady", "🛒 5. Zboží", "🧾 6. Tisk"])

    def item_row(cat_key: str, item_name: str, fallback_price: float, row_id: str, step_val: float = 1.0) -> None:
        p_val = get_price(cat_key, item_name)
        if p_val == 0.0: p_val = fallback_price

        col1, col2, col3 = st.columns([3, 1, 1])
        with col1: st.write(f"**{item_name}**")
        with col2: q = st.number_input(f"Ks_{row_id}", min_value=0.0, step=float(step_val), key=f"q_{row_id}", label_visibility="collapsed")
        with col3: p = st.number_input(f"P_{row_id}", min_value=0.0, step=0.1, value=float(p_val), key=f"p_{row_id}", label_visibility="collapsed")
        
        st.session_state.data_zakazky[item_name] = {"q": float(q), "p": float(p), "cat": cat_key}

    with tabs[0]:
        st.subheader("1. KONTROLY HASÍCÍCH PŘÍSTROJŮ")
        item_row("HP", "Kontrola HP (shodný)", 29.40, "h1")
        item_row("HP", "Kontrola HP (neshodný - opravitelný)", 19.70, "h2")
        item_row("HP", "Kontrola HP (neshodný - neopravitelný) + odborné zneprovoznění", 23.50, "h3")
        
        mnozstvi_neopravitelne = st.session_state.data_zakazky.get("Kontrola HP (neshodný - neopravitelný) + odborné zneprovoznění", {}).get("q", 0)
        if mnozstvi_neopravitelne > 0:
            st.warning("⚠️ Byly zadány neopravitelné přístroje (NV). Zadejte prosím důvody vyřazení.")
            vybrane_kody = st.multiselect(
                "Důvody vyřazení (A-K):",
                options=list(DUVODY_VYRAZENI.keys()),
                format_func=lambda x: f"Kód {x} - {DUVODY_VYRAZENI[x]}"
            )
            st.session_state.vyrazene_kody = vybrane_kody
        else:
            st.session_state.vyrazene_kody = []

        item_row("HP", "Manipulace a odvoz HP ze servisu (opravy)", 24.00, "h4")
        item_row("HP", "Manipulace a odvoz HP k násl. údržbě-TZ,opravě-plnění,demontáži", 24.00, "h5")
        item_row("HP", "Hod.sazba (pochůzky po objektu/ manipulace s HP/PV + další=dohod", 450.00, "h6", step_val=0.1)

    with tabs[1]:
        st.subheader("1. KONTROLY ZAŘÍZENÍ PRO ZÁSOBOVÁNÍ POŽÁRNÍ VODOU")
        item_row("Voda", "Prohlídka zařízení do 5 ks výtoků", 123.00, "v1")
        item_row("Voda", "Kontrola zařízení bez měření průtoku do 5 ks výtoků", 141.00, "v2")
        item_row("Voda", "Prohlídka zařízení od 6 do 10 ks výtoků", 159.00, "v3")
        item_row("Voda", "Kontrola zařízení bez měření průtoku od 6 do 10 ks výtoků", 246.00, "v4")
        item_row("Voda", "Měření průtoku á 1 ks vnitřní hydrant.systémů typu D/C", 95.00, "v5")
        item_row("Voda", "Měření průtoku á 1 ks vnější odběrní místo - podzemní hydrant", 179.00, "v6")

    with tabs[2]:
        st.subheader("3. OPRAVY HASICÍCH PŘÍSTROJŮ")
        item_row("Opravy", "CO2-5F/ETS", 418.00, "opr1")
        item_row("Opravy", "P6 Če (21A/)", 385.00, "opr2")
        item_row("Opravy", "S1,5 Kod", 280.00, "opr3")
        item_row("Opravy", "S2 KT 02.06/EN3", 314.00, "opr4")
        item_row("Opravy", "S5 Kte", 418.00, "opr5")
        item_row("Opravy", "S6KT", 385.00, "opr6")

    with tabs[3]:
        st.subheader("2. VYHODNOCENÍ KONTROLY + NÁHRADY")
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
        st.subheader("4. PRODEJ ZBOŽÍ, MATERIÁLU A ND (Dynamická nabídka)")
        st.info("Zde se automaticky zobrazuje zboží načtené z vašich ceníků a ze souboru **expimp.csv**.")
        zbozi_kategorie = ["Zboží", "ND_HP", "ND_Voda", "TAB", "TABFOTO", "HILTI", "CIDLO", "PASKA", "PK", "reklama", "FA", "Ostatni", "OZO", "zbozi"]
        db_items = get_items_from_db(zbozi_kategorie)
        
        if not db_items:
            st.warning("⚠️ Nejsou nahrány žádné ceníky pro Zboží. Klikněte vlevo na Synchronizovat s DB.")
        else:
            items_dict_lookup = {item["nazev"]: item for item in db_items}
            c1, c2, c3, c4 = st.columns([4, 1.5, 1.5, 1.5])
            with c1:
                zvolena_polozka = st.selectbox("Vyberte položku ze skladu:", ["-- Vyberte --"] + list(items_dict_lookup.keys()))
            
            if zvolena_polozka != "-- Vyberte --":
                def_cena = items_dict_lookup[zvolena_polozka]["cena"]
                with c2: cena_input = st.number_input("Cena/ks (Kč)", value=def_cena, step=1.0)
                with c3: mnozstvi_input = st.number_input("Množství", value=1.0, min_value=0.1, step=1.0)
                with c4:
                    st.write("")
                    if st.button("➕ Přidat", use_container_width=True):
                        interni_kat = items_dict_lookup[zvolena_polozka]["internal_cat"]
                        # Zbozi from expimp gets "Zboží" label for PDF
                        if interni_kat == "zbozi" or interni_kat not in CATEGORY_MAP.values():
                            interni_kat = "Zboží"
                            
                        if zvolena_polozka in st.session_state.dynamic_items:
                            st.session_state.dynamic_items[zvolena_polozka]["q"] += mnozstvi_input
                            st.session_state.dynamic_items[zvolena_polozka]["p"] = cena_input
                        else:
                            st.session_state.dynamic_items[zvolena_polozka] = {"q": mnozstvi_input, "p": cena_input, "cat": interni_kat}
                        st.rerun()

        if st.session_state.dynamic_items:
            st.divider()
            st.write("🛒 **Zboží připravené na Dodací list:**")
            for k, v in list(st.session_state.dynamic_items.items()):
                ca, cb, cc, cd = st.columns([5, 2, 2, 1])
                ca.write(f"• {k}")
                cb.write(f"{v['q']} ks")
                cc.write(f"{v['q']*v['p']:.2f} Kč")
                if cd.button("❌", key=f"del_{k}"):
                    del st.session_state.dynamic_items[k]
                    st.rerun()

    with tabs[5]:
        active_items = {}
        for k, v in st.session_state.data_zakazky.items():
            if v["q"] > 0: active_items[k] = v
        for k, v in st.session_state.dynamic_items.items():
            active_items[k] = v

        if not active_items:
            st.warning("Dodací list je prázdný. Zadejte hodnoty v záložkách.")
        else:
            grand_total = sum(vals["q"] * vals["p"] for vals in active_items.values())
            firma = st.session_state.vybrany_zakaznik.get("FIRMA", "Neznámý") if st.session_state.vybrany_zakaznik else "Neznámý"
            
            st.write(f"### Souhrn pro: {firma}")
            if st.session_state.vyrazene_kody:
                st.info(f"K Dodacímu listu budou připojeny tyto kódy vyřazení: {', '.join(st.session_state.vyrazene_kody)}")
            
            c_f1, c_f2 = st.columns(2)
            with c_f1: st.metric("CELKEM ZA DODACÍ LIST BEZ DPH", f"{grand_total:,.2f} Kč")
            with c_f2:
                if st.button("📄 VYGENEROVAT ČISTÝ DODACÍ LIST (PDF)"):
                    if not st.session_state.vybrany_zakaznik:
                        st.error("Nejprve vyberte zákazníka v postranním panelu.")
                    else:
                        pdf_doc = create_wservis_dl(
                            st.session_state.vybrany_zakaznik, active_items, dl_number, zakazka, technik, objekty_text, typ_dl, st.session_state.vyrazene_kody
                        )
                        if pdf_doc:
                            st.download_button("⬇️ STÁHNOUT PDF", data=pdf_doc, file_name=f"DL_{dl_number}_{firma.replace(' ','_')}.pdf")

elif menu_volba == "🗄️ Katalog a Evidence":
    st.title("🗄️ Správa Databáze")
    
    with st.expander("⚙️ Import dat z W-SERVIS (Synchronizace)"):
        st.info("Nahrajte do složky 'data/ceniky/' vaše ceníky nebo velký exportní soubor 'expimp.csv' a klikněte na tlačítko níže.")
        if st.button("🚀 Spustit kompletní synchronizaci databáze", type="primary"):
            with st.spinner("Zpracovávám data..."):
                log = import_all_ceniky()
                st.success("Synchronizace dokončena!")
                st.code(log)

st.sidebar.divider()
st.sidebar.caption(f"© {datetime.date.today().year} {FIRMA_VLASTNI['název']}")
