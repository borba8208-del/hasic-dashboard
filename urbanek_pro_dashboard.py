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
# CATEGORY_MAP A SKUPINY PRO ROLETKOVÉ MENU
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
    "Opravy": "opravy",
    "Zboží": "zbozi"
}

# Skupiny, které uvidíte v roletkovém menu
UI_GROUPS = {
    "🔥 1. KONTROLY HP A MANIPULACE": ["HP"],
    "🚰 2. KONTROLY POŽÁRNÍCH VODOVODŮ": ["Voda"],
    "🛠️ 3. OPRAVY HASICÍCH PŘÍSTROJŮ": ["Opravy"],
    "🚗 4. NÁHRADY A VYHODNOCENÍ": ["Nahrady", "Servisni_ukony"],
    "🛒 5. PRODEJ ZBOŽÍ A ND": ["Zboží", "ND_HP", "ND_Voda", "TAB", "TABFOTO", "HILTI", "CIDLO", "PASKA", "PK", "reklama", "FA"],
    "💼 6. OSTATNÍ A OZO": ["Ostatni", "OZO"]
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

# SESSION STATE (Paměť "Košíku" Dodacího listu)
if "dl_items" not in st.session_state:
    st.session_state.dl_items = {}
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
                continue

            df = safe_read_csv(file_path)
            if df is None:
                log_messages.append(f"❌ {csv_name}.csv: Nelze načíst.")
                continue

            df.columns = [str(col).strip().lower() for col in df.columns]
            if "nazev" not in df.columns or "cena" not in df.columns:
                continue

            df["nazev"] = df["nazev"].astype(str).str.strip()
            df = df.drop_duplicates(subset=["nazev"], keep="first")

            df["cena"] = df["cena"].astype(str).str.replace(",", ".", regex=False)
            df["cena"] = pd.to_numeric(df["cena"], errors="coerce").fillna(0.0)

            valid_cols = [col for col in df.columns if col in ["nazev", "cena"]]
            try:
                df[valid_cols].to_sql(table_name, connection, if_exists="replace", index=False)
                log_messages.append(f"✅ Načteno: {csv_name} ({len(df)} položek)")
            except Exception as e:
                log_messages.append(f"❌ Chyba DB – {e}")
    finally:
        connection.close()
    return "\n".join(log_messages) if log_messages else "Žádné ceníky k načtení."

def get_items_from_db(categories: List[str]) -> List[Dict]:
    """Získá všechny položky z DB pro dané kategorie."""
    items = []
    if not os.path.exists(DB_PATH): return items
    
    conn = sqlite3.connect(DB_PATH)
    for cat in categories:
        tbl = normalize_category_to_table(cat)
        try:
            res = conn.execute(f"SELECT nazev, cena FROM {tbl} ORDER BY nazev").fetchall()
            for r in res:
                items.append({"nazev": r[0], "cena": float(r[1]), "internal_cat": cat})
        except Exception:
            pass
    conn.close()
    return items

# ==========================================
# 3. ARES API A ZÁKAZNÍCI
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
        cur.execute(
            "UPDATE obchpartner SET FIRMA=?, ADRESA1=?, ADRESA2=?, ADRESA3=?, PSC=?, DIC=? WHERE ICO=?",
            (zakaznik.get("FIRMA"), zakaznik.get("ULICE"), zakaznik.get("CP"), zakaznik.get("ADRESA3"), zakaznik.get("PSC"), zakaznik.get("DIC"), zakaznik.get("ICO")),
        )
        conn.commit(); conn.close()
        return True
    except Exception: return False

# ==========================================
# 4. PDF ENGINE (ČISTÝ W-SERVIS DODACÍ LIST)
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

def create_wservis_dl(zakaznik: Dict[str, Any], items_dict: Dict[str, Any], dl_number: str, zakazka: str, technik: str, objekty: str, typ_dl: str) -> Optional[bytes]:
    pdf = UrbaneKPDF()
    pismo = pdf.pismo_name if pdf.pismo_ok else "helvetica"
    pdf.add_page()
    
    y_dl = pdf.get_y()
    pdf.set_font(pismo, "B", 12)
    pdf.cell(50, 5, "DODACÍ LIST")
    pdf.set_font(pismo, "", 9)
    pdf.cell(50, 5, "(práce, zboží, materiál)")

    pdf.set_xy(110, y_dl)
    pdf.cell(25, 4, "Číslo DL")
    pdf.cell(25, 4, "Číslo zakázky")
    pdf.cell(40, 4, "Jméno reviz. technika", ln=True)

    pdf.set_xy(110, y_dl + 4)
    pdf.set_font(pismo, "B", 10)
    pdf.cell(25, 5, dl_number)
    pdf.cell(25, 5, zakazka)
    pdf.cell(40, 5, technik, ln=True)
    pdf.ln(5)

    def draw_category(cat_num_title: str, item_cats: List[str]):
        cat_items = [[k, v["q"], v["p"]] for k, v in items_dict.items() if v["cat"] in item_cats and v["q"] > 0]
        if not cat_items: return 0.0

        pdf.set_font(pismo, "B", 9)
        pdf.cell(90, 5, f" {cat_num_title}", border=0)
        pdf.set_font(pismo, "", 8)
        pdf.cell(25, 5, "Cena", align="R")
        pdf.cell(45, 5, "ks/výk. - jednotlivé objekty", align="R")
        pdf.cell(30, 5, "CELKEM", align="R", ln=True)
        
        pdf.cell(90, 4, "", border=0)
        pdf.cell(25, 4, "bez DPH", align="R")
        pdf.cell(45, 4, "1  2  3  4  5     ks/výk", align="R")
        pdf.cell(30, 4, "Kč bez DPH", align="R", ln=True)

        cat_total = 0.0
        pdf.set_font(pismo, "", 9)
        for name, qty, price in cat_items:
            line_total = qty * price
            cat_total += line_total
            
            name_disp = "  " + name[:60] + ("..." if len(name) > 60 else "")
            pdf.cell(90, 5, name_disp)
            pdf.cell(25, 5, f"{price:,.1f}".replace('.', ','), align="R")
            
            pdf.cell(25, 5, "") 
            q_disp = f"{qty:,.2f}".rstrip("0").rstrip(".") if qty % 1 != 0 else f"{int(qty)}"
            pdf.cell(20, 5, q_disp, align="R")
            
            pdf.cell(30, 5, f"{line_total:,.2f}".replace('.', ','), align="R", ln=True)
            
        pdf.ln(2)
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
    pdf.cell(155, 6, "C E L K E M   K   Ú H R A D Ě   B E Z   D P H", align="R")
    pdf.cell(35, 6, f"{total_sum:,.2f} Kč".replace('.', ','), align="R", ln=True)
    pdf.ln(12)

    ul = zakaznik.get("ULICE", "") or ""
    cp = zakaznik.get("CP", "") or ""
    co = zakaznik.get("CO", "") or ""
    ob = zakaznik.get("ADRESA3", "") or ""
    ps = zakaznik.get("PSC", "") or ""
    adr_line1 = f"{ul} {cp}".strip()
    if co and co not in ["None", "", "nan", "0"]: adr_line1 += f"/{co}"
    adr_line2 = f"{ps} {ob}".strip()

    objekty_list = objekty.split('\n') if objekty else []

    pdf.set_font(pismo, "B", 9)
    pdf.cell(70, 5, "Odběratel:", ln=False)
    pdf.cell(75, 5, "Umístění kontrolovaných HP/PV v objektech:", ln=False)
    pdf.cell(45, 5, "Potvrzení reviz.technika:", ln=True)

    pdf.set_font(pismo, "", 9)
    pdf.cell(70, 5, zakaznik.get('FIRMA','')[:40], ln=False)
    pdf.cell(75, 5, objekty_list[0] if len(objekty_list)>0 else "", ln=False)
    pdf.cell(45, 5, "", ln=True)
    
    pdf.cell(70, 5, adr_line1[:40], ln=False)
    pdf.cell(75, 5, objekty_list[1] if len(objekty_list)>1 else "", ln=False)
    pdf.cell(45, 5, "Datum:", ln=True)
    
    pdf.cell(70, 5, adr_line2[:40], ln=False)
    pdf.cell(75, 5, objekty_list[2] if len(objekty_list)>2 else "", ln=False)
    pdf.cell(45, 5, f"{datetime.date.today().strftime('%d.%m.%Y')}", ln=True)
    
    pdf.cell(70, 5, f"IČO: {zakaznik.get('ICO','')}  DIČ: {zakaznik.get('DIC','')}", ln=False)
    pdf.cell(75, 5, objekty_list[3] if len(objekty_list)>3 else "", ln=False)
    pdf.cell(45, 5, "", ln=True)
    pdf.ln(12)

    pdf.set_font(pismo, "", 7)
    note1 = "Poznámka:  HP - SHODNÝ  - splňuje veškeré podmínky stanovené odbornými pokyny výrobce,  HP - NESHODNÝ  - nesplňuje podmínky stanovené odbornými pokyny "
    note2 = "výrobce - je a) opravitelný nebo b) neopravitelný - nutné vyřazení z provozu a odborná ekologická likvidace"
    pdf.multi_cell(0, 3, note1 + "\n" + note2)
    pdf.ln(2)
    
    wservis_stamp = f"Zpracováno programem \"Evidence kontrol a oprav HP\" od W-SERVIS PC Ing.Vladimír Vašek, Hluboká nad Vltavou, verze: 6.789999 / {datetime.date.today().strftime('%d.%m.%Y %H:%M:%S')}"
    pdf.cell(0, 4, wservis_stamp, ln=True)

    try: 
        return bytes(pdf.output())
    except Exception: 
        return None

# ==========================================
# 5. STREAMLIT UI - PROFESIONÁLNÍ OVLÁDACÍ PANEL
# ==========================================
st.set_page_config(page_title="W-SERVIS DL Master v15.0", layout="wide", page_icon="🛡️")

def load_all_customers() -> Optional[pd.DataFrame]:
    if not os.path.exists(DB_PATH): return None
    try:
        conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
        df = pd.read_sql("SELECT * FROM obchpartner", conn)
        conn.close(); return df
    except Exception: return None

df_customers = load_all_customers()

# --- SIDEBAR (LEVÝ PANEL) ---
with st.sidebar:
    st.header("🏢 Nastavení Dodacího listu")
    
    typ_dl = st.radio("Hlavička 1. sekce:", ["Standard (Kontroly)", "Opravy (Prior)"])
    dl_number = st.text_input("Číslo DL:", value="1698")
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
                    ares = get_company_from_ares(curr["ICO"])
                    if ares:
                        curr.update(ares); update_customer_in_db(curr)
                st.session_state.vybrany_zakaznik = curr.copy()
        else: st.warning("Nenalezeno.")

    st.divider()
    objekty_text = st.text_area("Umístění v objektech:", value="Hotel hlavní budova č.1\nHotel budova č.2", height=80)

    with st.expander("⚙️ Správa Ceníků (CSV)"):
        if st.button("🚀 Synchronizovat s DB"):
            log = import_all_ceniky()
            st.success("Synchronizace dokončena!")
            st.code(log)

# --- MAIN AREA (HLAVNÍ PANEL) ---
st.title("🛡️ Tvorba Dodacího Listu (W-SERVIS)")
st.caption("Verze 15.0 | Nyní s dynamickým roletkovým menu a čistým DL výstupem")

# --- KROK 1: PŘIDÁNÍ POLOŽKY (ROLETKOVÉ MENU) ---
st.subheader("1. Přidat položku na Dodací list")

col1, col2, col3, col4, col5 = st.columns([3, 4, 1.5, 1.5, 2])

with col1:
    zvolena_skupina = st.selectbox("1. Vyberte kategorii:", list(UI_GROUPS.keys()))

db_items = get_items_from_db(UI_GROUPS[zvolena_skupina])

with col2:
    if not db_items:
        st.warning("⚠️ Databáze je prázdná. Nahrajte CSV.")
        zvolena_polozka = None
    else:
        # Vytvoření slovníku pro snadné vyhledání ceny a kategorie dle názvu
        items_dict_lookup = {item["nazev"]: item for item in db_items}
        zvolena_polozka = st.selectbox("2. Vyberte položku:", list(items_dict_lookup.keys()))

with col3:
    if zvolena_polozka:
        def_cena = items_dict_lookup[zvolena_polozka]["cena"]
    else:
        def_cena = 0.0
    cena_input = st.number_input("Cena/ks (Kč)", value=def_cena, step=1.0)

with col4:
    mnozstvi_input = st.number_input("Množství", value=1.0, min_value=0.1, step=1.0)

with col5:
    st.write("") # Zarovnání tlačítka s inputy
    st.write("")
    if st.button("➕ Přidat na DL", type="primary", use_container_width=True):
        if zvolena_polozka:
            interni_kat = items_dict_lookup[zvolena_polozka]["internal_cat"]
            # Pokud už položka v DL je, přičteme množství a aktualizujeme cenu
            if zvolena_polozka in st.session_state.dl_items:
                st.session_state.dl_items[zvolena_polozka]["q"] += mnozstvi_input
                st.session_state.dl_items[zvolena_polozka]["p"] = cena_input
            else:
                st.session_state.dl_items[zvolena_polozka] = {
                    "q": mnozstvi_input, 
                    "p": cena_input, 
                    "cat": interni_kat
                }
            st.rerun()

st.divider()

# --- KROK 2: OBSAH KOŠÍKU (DODACÍHO LISTU) ---
st.subheader("2. Aktuální obsah Dodacího listu")

if not st.session_state.dl_items:
    st.info("Dodací list je zatím prázdný. Přidejte položky pomocí menu výše.")
else:
    grand_total = 0.0
    
    # Zobrazení položek s možností smazání
    for name, data in list(st.session_state.dl_items.items()):
        c1, c2, c3, c4, c5 = st.columns([5, 1.5, 1.5, 1.5, 1])
        c1.write(f"**{name}**")
        
        q_disp = f"{data['q']:.2f}".rstrip("0").rstrip(".") if data['q'] % 1 != 0 else f"{int(data['q'])}"
        c2.write(f"{q_disp} ks/výk")
        c3.write(f"{data['p']:.2f} Kč")
        
        line_tot = data['q'] * data['p']
        c4.write(f"**{line_tot:,.2f} Kč**")
        grand_total += line_tot
        
        if c5.button("❌", key=f"del_{name}"):
            del st.session_state.dl_items[name]
            st.rerun()
            
    st.divider()
    
    # --- KROK 3: TISK ---
    c_f1, c_f2, c_f3 = st.columns([1, 1, 1])
    with c_f1:
        firma = st.session_state.vybrany_zakaznik.get("FIRMA", "Neznámý") if st.session_state.vybrany_zakaznik else "Nevybrán zákazník"
        st.write(f"**Odběratel:** {firma}")
    with c_f2:
        st.metric("CELKEM BEZ DPH", f"{grand_total:,.2f} Kč")
    with c_f3:
        if st.button("📄 VYGENEROVAT DODACÍ LIST (PDF)", use_container_width=True):
            if not st.session_state.vybrany_zakaznik:
                st.error("Vyberte zákazníka v bočním panelu!")
            else:
                pdf_doc = create_wservis_dl(
                    st.session_state.vybrany_zakaznik, 
                    st.session_state.dl_items, 
                    dl_number, zakazka, technik, objekty_text, typ_dl
                )
                if pdf_doc:
                    st.download_button("⬇️ STÁHNOUT PDF", data=pdf_doc, file_name=f"DL_{dl_number}_{firma[:10]}.pdf", use_container_width=True)

    if st.button("🗑️ Vyprázdnit celý Dodací list"):
        st.session_state.dl_items = {}
        st.rerun()

st.caption(f"© {datetime.date.today().year} {FIRMA_VLASTNI['název']}")
