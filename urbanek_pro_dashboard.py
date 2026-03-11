import os
import re
import time
import datetime
import unicodedata
import sqlite3
from typing import Any, Dict, List, Optional

import streamlit as st
import pandas as pd
import requests
from fpdf import FPDF

# ==========================================
# CATEGORY_MAP – JEDINÝ ZDROJ PRAVDY
# ==========================================
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
    "Opravy": "opravy" # Přidána kategorie pro opravy HP
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

os.makedirs("data", exist_ok=True)
os.makedirs(CSV_FOLDER, exist_ok=True)

if "data_zakazky" not in st.session_state:
    st.session_state.data_zakazky = {}
if "vybrany_zakaznik" not in st.session_state:
    st.session_state.vybrany_zakaznik = None

# ==========================================
# 2. CENÍKY – IMPORT A SQL LOGIKA
# ==========================================
def normalize_category_to_table(cat_key: str) -> str:
    if not cat_key: return "cenik_ostatni"
    normalized = cat_key.lower().strip()
    normalized = "".join(char for char in unicodedata.normalize("NFKD", normalized) if not unicodedata.combining(char))
    normalized = re.sub(r"[\s/]+", "_", normalized)
    normalized = re.sub(r"[^a-z0-9_]", "", normalized)
    return f"cenik_{normalized}"

def safe_read_csv(path: str) -> Optional[pd.DataFrame]:
    if not os.path.exists(path): return None
    for enc in ("utf-8", "cp1250"):
        try:
            return pd.read_csv(path, sep=";", encoding=enc)
        except Exception:
            continue
    return None

def import_all_ceniky() -> str:
    log_messages: List[str] = []
    open(DB_PATH, 'a').close()
    connection = sqlite3.connect(DB_PATH)
    try:
        for ui_key, csv_name in CATEGORY_MAP.items():
            file_path = os.path.join(CSV_FOLDER, f"{csv_name}.csv")
            table_name = normalize_category_to_table(ui_key)
            if not os.path.exists(file_path):
                log_messages.append(f"⚠️ {csv_name}.csv: Nenalezen")
                continue
            df = safe_read_csv(file_path)
            if df is None:
                log_messages.append(f"❌ {csv_name}.csv: Nelze načíst.")
                continue
            df.columns = [str(col).strip().lower() for col in df.columns]
            if "nazev" not in df.columns or "cena" not in df.columns:
                log_messages.append(f"❌ {csv_name}.csv: Chybí sloupce.")
                continue
            df["nazev"] = df["nazev"].astype(str).str.strip()
            count_before = len(df)
            df = df.drop_duplicates(subset=["nazev"], keep="first")
            count_after = len(df)
            df["cena"] = df["cena"].astype(str).str.replace(",", ".", regex=False)
            df["cena"] = pd.to_numeric(df["cena"], errors="coerce").fillna(0.0)
            valid_cols = [col for col in df.columns if col in ["nazev", "cena", "jednotka"]]
            if "jednotka" not in valid_cols:
                df["jednotka"] = "ks"
                valid_cols.append("jednotka")
            try:
                df[valid_cols].to_sql(table_name, connection, if_exists="replace", index=False)
                dup_info = f" (odstraněno {count_before - count_after} duplicit)" if count_before > count_after else ""
                log_messages.append(f"✅ {table_name}: {len(df)} položek{dup_info}")
            except Exception as e:
                log_messages.append(f"❌ {table_name}: Chyba DB – {e}")
    finally:
        connection.close()
    return "\n".join(log_messages)

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

# ==========================================
# 3. ARES API 
# ==========================================
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
        cur.execute("""
            UPDATE obchpartner SET FIRMA=?, ADRESA1=?, ADRESA2=?, ADRESA3=?, PSC=?, DIC=? WHERE ICO=?
        """, (zakaznik.get("FIRMA"), zakaznik.get("ULICE"), zakaznik.get("CP"), zakaznik.get("ADRESA3"), zakaznik.get("PSC"), zakaznik.get("DIC"), zakaznik.get("ICO")))
        conn.commit(); conn.close(); return True
    except Exception: return False

def repair_all_customers_with_ares() -> str:
    if not os.path.exists(DB_PATH): return "Databáze neexistuje."
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    try:
        rows = cur.execute("SELECT ICO FROM obchpartner").fetchall()
    except Exception:
        conn.close(); return "Tabulka obchpartner neexistuje."
    
    fixed = 0; skipped = 0; total = len(rows)
    prog = st.progress(0.0)
    for i, (ico,) in enumerate(rows):
        ico_str = str(ico).strip()
        if len(ico_str) < 6:
            skipped += 1; continue
        ares = get_company_from_ares(ico_str)
        if ares:
            ares["ICO"] = ico_str
            if update_customer_in_db(ares): fixed += 1
        else: skipped += 1
        prog.progress((i + 1) / max(total, 1))
        if i % 10 == 0: time.sleep(0.05)
    conn.close()
    return f"Vyčištěno {fixed} záznamů přes ARES, přeskočeno {skipped}."

# ==========================================
# 4. PDF ENGINE (PŘESNÁ KOPIE W-SERVIS DL)
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
        
        # Odsazení pro čistou hlavičku ala W-SERVIS
        self.set_font(pismo, "B", 12)
        self.cell(0, 5, FIRMA_VLASTNI["název"], ln=True)
        
        self.set_font(pismo, "", 9)
        self.cell(0, 4, f"{FIRMA_VLASTNI['sídlo']}", ln=True)
        self.cell(0, 4, f"IČO: {FIRMA_VLASTNI['ico']}, DIČ: {FIRMA_VLASTNI['dic']}", ln=True)
        self.cell(0, 4, f"{FIRMA_VLASTNI['zápis']}", ln=True)
        self.cell(0, 4, f"Tel: , tel./fax: , mobil: {FIRMA_VLASTNI['telefony']}", ln=True)
        self.cell(0, 4, f"Email: {FIRMA_VLASTNI['email']}, WEB: {FIRMA_VLASTNI['web']}", ln=True)
        self.ln(5)

    def footer(self) -> None:
        pass # V dodacím listu W-Servis není standardní patička s číslem stránky, je to nahoře nebo volně


def create_wservis_dl(zakaznik: Dict[str, Any], items_dict: Dict[str, Any], dl_number: str, zakazka: str, technik: str, objekty: str, typ_dl: str) -> Optional[bytes]:
    pdf = UrbaneKPDF()
    pismo = pdf.pismo_name if pdf.pismo_ok else "helvetica"
    pdf.add_page()
    
    # 1. Část: Hlavičková tabulka
    pdf.set_font(pismo, "B", 11)
    
    # Trik na zarovnání nadpisů tabulky
    y_start = pdf.get_y()
    pdf.set_xy(10, y_start)
    pdf.cell(50, 5, "DODACÍ LIST", ln=False)
    pdf.set_font(pismo, "", 9)
    pdf.cell(40, 5, "(práce, zboží, materiál)", ln=False)
    
    pdf.set_xy(100, y_start - 3)
    pdf.cell(30, 4, "Číslo DL", border=0, align="L", ln=True)
    pdf.set_xy(100, y_start + 1)
    pdf.set_font(pismo, "B", 11)
    pdf.cell(30, 5, dl_number, border=0, align="L", ln=True)

    pdf.set_xy(130, y_start - 3)
    pdf.set_font(pismo, "", 9)
    pdf.cell(30, 4, "Číslo zakázky", border=0, align="L", ln=True)
    pdf.set_xy(130, y_start + 1)
    pdf.set_font(pismo, "B", 10)
    pdf.cell(30, 5, zakazka, border=0, align="L", ln=True)

    pdf.set_xy(160, y_start - 3)
    pdf.set_font(pismo, "", 9)
    pdf.cell(40, 4, "Jméno reviz. technika", border=0, align="L", ln=True)
    pdf.set_xy(160, y_start + 1)
    pdf.set_font(pismo, "", 10)
    pdf.cell(40, 5, technik, border=0, align="L", ln=True)
    
    pdf.ln(5)

    # 2. Část: Hlavička tabulky položek (W-SERVIS STYL)
    pdf.set_font(pismo, "", 9)
    
    y_head = pdf.get_y()
    pdf.set_xy(100, y_head)
    pdf.cell(20, 5, "Cena", align="C")
    pdf.set_xy(100, y_head + 4)
    pdf.cell(20, 5, "bez DPH", align="C")

    pdf.set_xy(120, y_head)
    pdf.cell(50, 5, "ks/výk. - jednotlivé objekty", align="L")
    pdf.set_xy(120, y_head + 4)
    # Zjednodušeně 1,2,3,4,5,ks/výk
    pdf.cell(50, 5, "1  2  3  4  5    ks/výk", align="L")

    pdf.set_xy(175, y_head)
    pdf.cell(25, 5, "CELKEM", align="R")
    pdf.set_xy(175, y_head + 4)
    pdf.cell(25, 5, "Kč bez DPH", align="R")
    
    pdf.set_xy(10, y_head + 10) # Přesun pod hlavičku

    # --- FUNKCE PRO VYKRESLENÍ KATEGORIE ---
    def draw_category(cat_num_title: str, item_cats: List[str]):
        cat_items = [[k, v["q"], v["p"]] for k, v in items_dict.items() if v["cat"] in item_cats and v["q"] > 0]
        if not cat_items: return 0.0

        pdf.set_font(pismo, "B", 9)
        pdf.cell(90, 6, cat_num_title, ln=True)
        pdf.set_font(pismo, "", 9)

        cat_total = 0.0
        for name, qty, price in cat_items:
            line_total = qty * price
            cat_total += line_total
            
            # Název
            name_disp = " " + name[:50] + ("..." if len(name) > 50 else "")
            pdf.cell(90, 5, name_disp)
            
            # Cena
            pdf.cell(20, 5, f"{price:,.1f}".replace('.', ','), align="R")
            
            # Sloupec "ks/výk. - jednotlivé objekty" - simulace W-SERVIS výpisu
            # Do posledního sloupce (celkové ks) dáme množství
            q_disp = f"{qty:,.2f}".rstrip("0").rstrip(".") if qty % 1 != 0 else f"{int(qty)}"
            
            # Vyplnění "teček/čárek" pro objekty 1,2,3,4,5 a pak celkový počet
            # V základu to hodíme jako celkový počet nakonec
            pdf.cell(10, 5, "") # Mezera pro "1 2 3 4 5"
            
            if "1km" in name.lower() or "hod" in name.lower():
                # U kilometrů a hodin to W-Servis cpal trochu divně, zarovnáme k pravé straně objektů
                pdf.cell(40, 5, f"{q_disp}", align="R") 
            else:
                 pdf.cell(40, 5, f"{q_disp}", align="R")

            # Celkem Kč
            pdf.cell(30, 5, f"{line_total:,.2f}".replace('.', ','), align="R", ln=True)
        return cat_total

    # Vykreslení kategorií
    total_sum = 0.0
    
    # 1. HP
    if typ_dl == "Standard":
        total_sum += draw_category("1. KONTROLY HASÍCÍCH PŘÍSTROJŮ", ["HP"])
    else: # Pokud si uživatel vybere, že dělá DL pro opravny (Prior)
        total_sum += draw_category("1. KONTROLY OPRAVENÝCH HP", ["HP"])
        
    # 2. Voda (Někdy to mají jako 1. Kontroly PV, spojíme to do logické řady)
    total_sum += draw_category("1. KONTROLY ZAŘÍZENÍ PRO ZÁSOBOVÁNÍ POŽÁRNÍ VODOU", ["Voda"])
    
    # Náhrady / Vyhodnocení
    total_sum += draw_category("2. NÁHRADY A VYHODNOCENÍ KONTROLY", ["Nahrady", "Servisni_ukony"])
    
    # Opravy HP
    total_sum += draw_category("3. OPRAVY HASICÍCH PŘÍSTROJŮ", ["Opravy"])

    # Prodej
    total_sum += draw_category("4. PRODEJ ZBOŽÍ, MATERIÁLU A ND", ["ND_HP", "ND_Voda", "TAB", "TABFOTO", "HILTI", "CIDLO", "PASKA", "PK", "reklama", "FA", "Zboží"])

    # Ostatní
    total_sum += draw_category("5. OSTATNÍ A OZO", ["Ostatni", "OZO"])

    # --- SOUČTOVÁ ŘÁDKA ---
    pdf.ln(2)
    pdf.set_font(pismo, "B", 10)
    pdf.cell(140, 6, "C E L K E M   K   Ú H R A D Ě   B E Z   D P H", align="R")
    pdf.cell(50, 6, f"{total_sum:,.2f}".replace('.', ','), align="R", ln=True)
    pdf.ln(10)

    # --- ODBĚRATEL & PODPISY ---
    pdf.set_font(pismo, "B", 9)
    pdf.cell(20, 5, "Odběratel:")
    
    pdf.set_font(pismo, "", 9)
    pdf.cell(70, 5, zakaznik.get("ICO", ""))
    
    # Umístění uprostřed
    pdf.set_font(pismo, "B", 9)
    pdf.cell(60, 5, "Umístění kontrolovaných HP/PV v objektech:")
    
    pdf.cell(40, 5, "Potvrzení reviz.technika:", ln=True)

    # Odběratel - Název a adresa
    pdf.set_font(pismo, "", 9)
    pdf.cell(90, 5, zakaznik.get('FIRMA',''))
    
    # Vypsání objektů z pole
    objekty_list = objekty.split('\n') if objekty else []
    if len(objekty_list) > 0:
        pdf.cell(60, 5, f"{objekty_list[0]}:")
    else:
        pdf.cell(60, 5, "")
        
    pdf.cell(40, 5, "", ln=True) # Místo pro podpis RT
    
    # Adresa odběratele
    pdf.cell(90, 5, f"{ul} {cp}".strip())
    if len(objekty_list) > 1:
        pdf.cell(60, 5, f"{objekty_list[1]}:")
    else:
        pdf.cell(60, 5, "")
    pdf.cell(40, 5, "Datum:", ln=True)

    pdf.cell(90, 5, f"{ps} {ob}".strip())
    if len(objekty_list) > 2:
        pdf.cell(60, 5, f"{objekty_list[2]}:")
    else:
        pdf.cell(60, 5, "")
    pdf.cell(40, 5, f"{datetime.date.today().strftime('%d.%m.%Y')}", ln=True)

    pdf.ln(20)

    # --- POZNÁMKA A PATIČKA DLE W-SERVIS ---
    pdf.set_font(pismo, "", 7)
    note1 = "Poznámka:  HP - SHODNÝ  - splňuje veškeré podmínky stanovené odbornými pokyny výrobce,  HP - NESHODNÝ  - nesplňuje podmínky stanovené odbornými pokyny "
    note2 = "výrobce - je a) opravitelný nebo b) neopravitelný - nutné vyřazení z provozu a odborná ekologická likvidace"
    pdf.multi_cell(0, 4, note1 + "\n" + note2)
    
    # Ta slavná věta z W-Servis
    wservis_stamp = f"Zpracováno programem \"Evidence kontrol a oprav HP\" od W-SERVIS PC Ing.Vladimír Vašek, Hluboká nad Vltavou, verze: 6.789999 / {datetime.date.today().strftime('%d.%m.%Y %H:%M:%S')}"
    pdf.cell(0, 4, wservis_stamp, ln=True)


    try: 
        return bytes(pdf.output())
    except Exception: 
        return None

# ==========================================
# 5. STREAMLIT UI 
# ==========================================
st.set_page_config(page_title="W-SERVIS Clone DL v11.0", layout="wide", page_icon="🛡️")

def load_all_customers() -> Optional[pd.DataFrame]:
    if not os.path.exists(DB_PATH): return None
    try:
        conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
        df = pd.read_sql("SELECT * FROM obchpartner", conn)
        conn.close(); return df
    except Exception: return None

df_customers = load_all_customers()

with st.sidebar:
    st.header("🏢 Hlavička DL")
    
    # Údaje přímo do tabulky v PDF
    typ_dl = st.radio("Typ Dodacího listu:", ["Standard", "Opravy (Prior)"])
    dl_number = st.text_input("Číslo DL:", value="1698")
    zakazka = st.text_input("Číslo zakázky:", value="1/13")
    technik = st.text_input("Jméno reviz. technika:", value="v.z. Tomáš Urbánek")
    
    st.divider()
    if df_customers is not None:
        sq = st.text_input("🔍 Vyhledat partnera (IČO/Název):")
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
    objekty_text = st.text_area("Umístění kontrolovaných HP v objektech (každý na nový řádek):", value="Hotel hlavní budova č.1\nHotel budova č.2\nHotel budova č.3", height=100)

    with st.expander("⚙️ Pokročilá správa (Ceníky)"):
        if st.button("🚀 Synchronizovat ceníky (CSV)"):
            log = import_all_ceniky()
            st.code(log); st.rerun()

st.title("🛡️ Dodací Listy (W-SERVIS Clone)")
st.caption("Generátor v11.0 | Věrná kopie původního systému | Ceny z databáze")

tabs = st.tabs(["🔥 HP Kontroly", "🛠️ HP Opravy", "🚰 PV Kontroly", "📦 Náhrady & Značení", "🛒 Prodej ND", "🧾 Vytvořit DL"])

def item_row(cat_key: str, item_name: str, fallback_price: float, row_id: str, step_val: float = 1.0) -> None:
    p_val = get_price(cat_key, item_name)
    if p_val == 0.0: p_val = fallback_price

    col1, col2, col3 = st.columns([3, 1, 1])
    with col1: st.write(f"**{item_name}**")
    with col2: q = st.number_input(f"Q_{row_id}", min_value=0.0, step=float(step_val), key=f"q_{row_id}", label_visibility="collapsed")
    with col3: p = st.number_input(f"P_{row_id}", min_value=0.0, step=0.1, value=float(p_val), key=f"p_{row_id}", label_visibility="collapsed")
    
    st.session_state.data_zakazky[item_name] = {"q": float(q), "p": float(p), "cat": cat_key}

with tabs[0]:
    st.subheader("1. KONTROLY HASÍCÍCH PŘÍSTROJŮ")
    item_row("HP", "Kontrola HP (shodný)", 29.40, "h1")
    item_row("HP", "Kontrola HP (neshodný - opravitelný)", 19.70, "h2")
    item_row("HP", "Kontrola HP (neshodný - neopravitelný) + odborné zneprovoznění", 23.50, "h3")
    item_row("HP", "Manipulace a odvoz HP k násl. údržbě-TZ,opravě-plnění,demontáži", 24.00, "h4")
    item_row("HP", "Hodinová sazba za provedení prací - mimo uvedených", 450.00, "h5", step_val=0.5)

with tabs[1]:
    st.subheader("3. OPRAVY HASICÍCH PŘÍSTROJŮ")
    st.info("Vyplňujte při tvorbě DL pro opravy (např. Prior)")
    item_row("Opravy", "P6 Če (21A/)", 385.00, "opr1")
    item_row("Opravy", "CO2-5F/ETS", 418.00, "opr2")
    item_row("Opravy", "S1,5 Kod", 280.00, "opr3")
    item_row("Opravy", "S2 KT 02.06/EN3", 314.00, "opr4")
    item_row("Opravy", "S5 Kte", 418.00, "opr5")
    item_row("Opravy", "S6KT", 385.00, "opr6")

with tabs[2]:
    st.subheader("1. KONTROLY ZAŘÍZENÍ PRO ZÁSOBOVÁNÍ POŽÁRNÍ VODOU")
    item_row("Voda", "Prohlídka zařízení od 6 do 10 ks výtoků", 159.00, "v1")
    item_row("Voda", "Kontrola zařízení bez měření průtoku od 6 do 10 ks výtoků", 246.00, "v2")
    item_row("Voda", "Měření průtoku á 1 ks vnitřní hydrant.systémů typu D/C", 95.00, "v3")
    item_row("Voda", "Měření průtoku á 1 ks vnější odběrní místo - podzemní hydrant", 179.00, "v4")

with tabs[3]:
    st.subheader("2. NÁHRADY A VYHODNOCENÍ")
    item_row("Servisni_ukony", "Vyhodnocení kontroly zařízení od 6 do 10 ks výtoků", 117.00, "s1")
    item_row("Servisni_ukony", "Vyhotovení zprávy o kontrole zařízení pro zásob.pož.vodou", 170.00, "s2")
    item_row("Nahrady", "Převzetí HP vyřazeného z užívání dodavatelem", 88.00, "n1")
    item_row("Nahrady", "Označení - vylepení koleček o kontrole (á 2ks / HP)", 3.50, "n2")
    item_row("Nahrady", "Označení - vylepení štítku o kontrole (á 1ks / HP)", 8.00, "n3")
    item_row("Nahrady", "Náhrada za 1km - osobní servisní vozidlo", 6.00, "n4")

with tabs[4]:
    st.subheader("4. PRODEJ ZBOŽÍ A ND")
    item_row("Zboží", "Has.přístr. RAIMA P6 (34A,233B,C)", 1090.00, "zb1")
    item_row("Zboží", "Hasicí přístroj V9LE Tepostop", 1015.12, "zb2")
    item_row("ND_HP", "Skříň na HP 9kg KOM 9 AZ/O", 820.16, "nd1")

with tabs[5]:
    active_items = {k: v for k, v in st.session_state.data_zakazky.items() if v["q"] > 0}
    if not active_items:
        st.warning("Zadejte položky pro vygenerování dodacího listu.")
    else:
        grand_total = sum(vals["q"] * vals["p"] for vals in active_items.values())
        firma = st.session_state.vybrany_zakaznik.get("FIRMA", "Neznámý") if st.session_state.vybrany_zakaznik else "Neznámý"
        
        st.write(f"### Náhled pro: {firma}")
        st.metric("CELKEM K FAKTURACI (BEZ DPH)", f"{grand_total:,.2f} Kč")

        if st.button("📄 VYGENEROVAT DODACÍ LIST DLE W-SERVIS (PDF)"):
            if not st.session_state.vybrany_zakaznik:
                st.error("Nejprve vyberte zákazníka v postranním panelu.")
            else:
                pdf_doc = create_wservis_dl(
                    st.session_state.vybrany_zakaznik, 
                    st.session_state.data_zakazky, 
                    dl_number, 
                    zakazka, 
                    technik, 
                    objekty_text,
                    typ_dl
                )
                if pdf_doc:
                    st.download_button("⬇️ STÁHNOUT DODACÍ LIST", data=pdf_doc, file_name=f"DL_{dl_number}_{firma.replace(' ','_')}.pdf")
                else:
                    st.error("Chyba při generování PDF.")

st.divider()
st.caption(f"© {datetime.date.today().year} {FIRMA_VLASTNI['název']}")
