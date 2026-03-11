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
}

# ==========================================
# 1. KONFIGURACE FIRMY
# ==========================================
FIRMA_VLASTNI: Dict[str, Any] = {
    "název": "Ilja Urbánek - HASIČ-SERVIS",
    "sídlo": "Poříčská 186, 373 82 Boršov nad Vltavou",
    "ico": "60835265",
    "dic": "CZ5706281691",
    "telefony": "608409036 - 777664768",
    "email": "schranka@hasic-servis.com",
    "web": "http://www.hasic-servis.com",
    "certifikace": "TÜV NORD Czech",
    "založeno": 1994,
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
        
        # Logo and Company Name
        if os.path.exists("logo.png"):
            self.image("logo.png", x=10, y=8, w=25)
            self.set_xy(40, 10)
        else:
            self.set_xy(10, 10)
            
        self.set_font(pismo, "B", 14)
        self.cell(0, 6, FIRMA_VLASTNI["název"], ln=True)
        
        # Address and Contacts (Exact copy from CSV snippet)
        self.set_x(40 if os.path.exists("logo.png") else 10)
        self.set_font(pismo, "", 9)
        self.cell(0, 5, f"{FIRMA_VLASTNI['sídlo']} | IČO: {FIRMA_VLASTNI['ico']}, DIČ: {FIRMA_VLASTNI['dic']}", ln=True)
        self.set_x(40 if os.path.exists("logo.png") else 10)
        self.cell(0, 5, f"Tel: {FIRMA_VLASTNI['telefony']}", ln=True)
        self.set_x(40 if os.path.exists("logo.png") else 10)
        self.cell(0, 5, f"Email: {FIRMA_VLASTNI['email']}, WEB: {FIRMA_VLASTNI['web']}", ln=True)
        
        self.line(10, 32, 200, 32)
        self.ln(5)

    def footer(self) -> None:
        self.set_y(-15)
        self.set_font(self.pismo_name if self.pismo_ok else "helvetica", "", 7)
        self.cell(0, 10, f"Zpracováno programem HASIČ-SERVIS Dashboard | Strana {self.page_no()}", align="C")

def create_delivery_note(zakaznik: Dict[str, Any], items_dict: Dict[str, Any], dl_number: str, zakazka: str, technik: str, objekty: str) -> Optional[bytes]:
    pdf = UrbaneKPDF()
    pismo = pdf.pismo_name if pdf.pismo_ok else "helvetica"
    pdf.add_page()
    
    # Title
    pdf.set_font(pismo, "B", 14)
    pdf.cell(0, 8, "DODACÍ LIST (práce, zboží, materiál)", ln=True, align="L")
    pdf.ln(2)

    # Meta Table (Exact copy from CSV)
    pdf.set_font(pismo, "B", 9)
    pdf.cell(30, 6, "Číslo DL", border=1, align="C")
    pdf.cell(40, 6, "Číslo zakázky", border=1, align="C")
    pdf.cell(60, 6, "Jméno reviz. technika", border=1, align="C")
    pdf.ln()
    
    pdf.set_font(pismo, "", 9)
    pdf.cell(30, 6, dl_number, border=1, align="C")
    pdf.cell(40, 6, zakazka, border=1, align="C")
    pdf.cell(60, 6, technik, border=1, align="C")
    pdf.ln(10)

    # Customer Info
    pdf.set_font(pismo, "B", 10)
    pdf.cell(0, 5, f"Odběratel:", ln=True)
    pdf.set_font(pismo, "", 10)
    pdf.cell(0, 5, f"{zakaznik.get('FIRMA','')}", ln=True)
    
    ul = zakaznik.get("ULICE", "") or ""
    cp = zakaznik.get("CP", "") or ""
    co = zakaznik.get("CO", "") or ""
    ob = zakaznik.get("ADRESA3", "") or ""
    ps = zakaznik.get("PSC", "") or ""

    adr_line1 = f"{ul} {cp}".strip()
    if co and co not in ["None", "", "nan", "0"]: adr_line1 += f"/{co}"
    if adr_line1:
        pdf.cell(0, 5, adr_line1, ln=True)
    pdf.cell(0, 5, f"{ps} {ob}".strip(), ln=True)
    pdf.cell(0, 5, f"IČO: {zakaznik.get('ICO','')}   DIČ: {zakaznik.get('DIC','')}", ln=True)
    pdf.ln(5)

    # Objekty
    if objekty:
        pdf.set_font(pismo, "B", 9)
        pdf.cell(0, 5, "Umístění kontrolovaných zařízení v objektech:", ln=True)
        pdf.set_font(pismo, "", 9)
        pdf.multi_cell(0, 5, objekty)
        pdf.ln(3)

    # --- ITEMS TABLE ---
    def draw_category_items(cat_title: str, item_keys: List[str]):
        # Filter items for this category that have qty > 0
        cat_items = [[k, v["q"], v["p"]] for k, v in items_dict.items() if v["cat"] in item_keys and v["q"] > 0]
        
        if not cat_items:
            return 0.0

        # Subheader for category
        pdf.set_font(pismo, "B", 9)
        pdf.cell(100, 6, f"{cat_title}", border=0)
        pdf.cell(30, 6, "Cena bez DPH", border=0, align="R")
        pdf.cell(25, 6, "Množství", border=0, align="C")
        pdf.cell(35, 6, "CELKEM Kč", border=0, align="R")
        pdf.ln()
        
        cat_total = 0.0
        pdf.set_font(pismo, "", 9)
        for name, qty, price in cat_items:
            line_total = qty * price
            cat_total += line_total
            
            # Formating
            name_disp = name[:55] + ("..." if len(name) > 55 else "")
            q_disp = f"{qty:,.2f}".rstrip("0").rstrip(".") if qty % 1 != 0 else f"{int(qty)}"
            if "1km" in name.lower(): mj = "km"
            elif "paušál" in name.lower() or "činnost" in name.lower(): mj = "výk"
            else: mj = "ks"

            pdf.cell(100, 5, f"  {name_disp}", border=0)
            pdf.cell(30, 5, f"{price:,.2f}", border=0, align="R")
            pdf.cell(25, 5, f"{q_disp} {mj}", border=0, align="C")
            pdf.cell(35, 5, f"{line_total:,.2f}", border=0, align="R")
            pdf.ln()
            
        pdf.set_font(pismo, "B", 9)
        pdf.cell(155, 6, f"CELKEM {cat_title}:", align="R")
        pdf.cell(35, 6, f"{cat_total:,.2f}", align="R")
        pdf.ln(8)
        
        return cat_total

    # Draw categories exactly as in W-SERVIS
    pdf.set_line_width(0.2)
    pdf.line(10, pdf.get_y(), 200, pdf.get_y())
    pdf.ln(2)

    total_sum = 0.0
    total_sum += draw_category_items("1. KONTROLY HASICÍCH PŘÍSTROJŮ", ["HP"])
    total_sum += draw_category_items("2. NÁHRADY A SERVIS", ["Nahrady", "Servisni_ukony"])
    total_sum += draw_category_items("3. KONTROLY ZAŘÍZENÍ ZÁSOB. POŽ. VODOU", ["Voda"])
    total_sum += draw_category_items("4. PRODEJ ZBOŽÍ, MATERIÁLU A ND", ["ND_HP", "ND_Voda", "TAB", "TABFOTO", "HILTI", "CIDLO", "PASKA", "PK", "reklama", "FA"])
    total_sum += draw_category_items("5. OSTATNÍ A OZO", ["Ostatni", "OZO"])

    pdf.line(10, pdf.get_y(), 200, pdf.get_y())
    pdf.ln(4)

    # Grand Total
    pdf.set_font(pismo, "B", 12)
    pdf.cell(155, 8, "C E L K E M   B E Z   D P H :", align="R")
    pdf.cell(35, 8, f"{total_sum:,.2f} Kč", align="R")
    pdf.ln(15)

    # Signatures
    pdf.set_font(pismo, "B", 9)
    pdf.cell(70, 5, "Odběratel:", border=0)
    pdf.cell(50, 5, "Datum:", border=0)
    pdf.cell(70, 5, "Potvrzení reviz.technika:", border=0, ln=True)
    pdf.ln(15) # Space for signatures
    pdf.line(10, pdf.get_y(), 60, pdf.get_y())
    pdf.line(80, pdf.get_y(), 120, pdf.get_y())
    pdf.line(130, pdf.get_y(), 190, pdf.get_y())
    pdf.ln(10)

    # Exact W-SERVIS Notes
    pdf.set_font(pismo, "", 7)
    note1 = "Poznámka: HP - SHODNÝ - splňuje veškeré podmínky stanovené odbornými pokyny výrobce, HP - NESHODNÝ - nesplňuje podmínky stanovené odbornými pokyny "
    note2 = "výrobce - je a) opravitelný nebo b) neopravitelný - nutné vyřazení z provozu a odborná ekologická likvidace."
    pdf.multi_cell(0, 4, note1 + "\n" + note2)

    try: 
        return bytes(pdf.output())
    except Exception: 
        return None

# ==========================================
# 5. STREAMLIT UI 
# ==========================================
st.set_page_config(page_title="Urbánek Master Pro v10.1", layout="wide", page_icon="🛡️")

def load_all_customers() -> Optional[pd.DataFrame]:
    if not os.path.exists(DB_PATH): return None
    try:
        conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
        df = pd.read_sql("SELECT * FROM obchpartner", conn)
        conn.close(); return df
    except Exception: return None

df_customers = load_all_customers()

with st.sidebar:
    st.header("🏢 Hlavička Dodacího listu")
    
    # Údaje přímo do tabulky v PDF
    dl_number = st.text_input("Číslo DL:", value="7738")
    zakazka = st.text_input("Číslo zakázky:", value="1/11")
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
    objekty_text = st.text_area("Umístění (Objekty):", value="Hotel hlavní budova č.1\nHotel budova č.2", height=100)

    with st.expander("⚙️ Pokročilá správa (Ceníky)"):
        if st.button("🚀 Synchronizovat ceníky (CSV)"):
            log = import_all_ceniky()
            st.code(log); st.rerun()

st.title("🛡️ HASIČ-SERVIS URBÁNEK")
st.caption("Generátor Dodacích listů v10.1 | Věrná kopie W-SERVIS výstupu")

tabs = st.tabs(["🔥 HP", "🚰 PV", "📦 Náhrady", "🖼️ Značení", "🛠️ ND & Ostatní", "🧾 Vytvořit DL"])

def item_row(cat_key: str, item_name: str, fallback_price: float, row_id: str, step_val: float = 1.0) -> None:
    p_val = get_price(cat_key, item_name)
    if p_val == 0.0: p_val = fallback_price

    col1, col2, col3 = st.columns([3, 1, 1])
    with col1: st.write(f"**{item_name}**")
    with col2: q = st.number_input(f"Q_{row_id}", min_value=0.0, step=float(step_val), key=f"q_{row_id}", label_visibility="collapsed")
    with col3: p = st.number_input(f"P_{row_id}", min_value=0.0, step=0.1, value=float(p_val), key=f"p_{row_id}", label_visibility="collapsed")
    
    st.session_state.data_zakazky[item_name] = {"q": float(q), "p": float(p), "cat": cat_key}

with tabs[0]:
    st.subheader("1. KONTROLY HASICÍCH PŘÍSTROJŮ")
    item_row("HP", "Kontrola HP (shodný)", 29.40, "h1")
    item_row("HP", "Kontrola HP (neshodný - opravitelný)", 19.70, "h2")
    item_row("HP", "Kontrola HP (neshodný - neopravitelný) + odborné zneprovoznění", 23.50, "h3")
    item_row("HP", "Manipulace a odvoz HP k násl. údržbě-TZ,opravě-plnění,demontáži", 24.00, "h4")
    item_row("HP", "Hodinová sazba za provedení prací - mimo uvedených", 450.00, "h5", step_val=0.5)

with tabs[1]:
    st.subheader("3. KONTROLY ZAŘÍZENÍ ZÁSOB. POŽ. VODOU")
    item_row("Voda", "Prohlídka zařízení od 11 do 20 ks výtoků", 193.00, "v1")
    item_row("Voda", "Měření průtoku á 1 ks vnitřní hydrant. systémů", 95.00, "v3")

with tabs[2]:
    st.subheader("2. NÁHRADY A SERVIS")
    item_row("Nahrady", "Převzetí HP vyřazeného z užívání dodavatelem", 88.00, "n1")
    item_row("Nahrady", "Označení - vylepení koleček o kontrole (á 2ks / HP)", 3.50, "n2")
    item_row("Nahrady", "Náhrada za 1km - osobní servisní vozidlo", 13.80, "n4")

with tabs[3]:
    st.subheader("4. BEZPEČNOSTNÍ TABULKY A ZNAČENÍ")
    item_row("TAB", "Tabulka - Hasicí přístroj (plast)", 25.00, "t1")
    item_row("TABFOTO", "Info.plast.fotolumin. 300x150mm", 65.00, "tf1")

with tabs[4]:
    st.subheader("4. PRODEJ ZBOŽÍ A ND / 5. OSTATNÍ")
    item_row("ND_HP", "Věšák Delta W+PG NEURUPPIN", 35.00, "nd1")
    item_row("Ostatni", "Technicko organizační činnost v PO", 4488.00, "o1")

with tabs[5]:
    active_items = {k: v for k, v in st.session_state.data_zakazky.items() if v["q"] > 0}
    if not active_items:
        st.warning("Zadejte položky pro vygenerování dodacího listu.")
    else:
        grand_total = sum(vals["q"] * vals["p"] for vals in active_items.values())
        firma = st.session_state.vybrany_zakaznik.get("FIRMA", "Neznámý") if st.session_state.vybrany_zakaznik else "Neznámý"
        
        st.write(f"### Přehled pro: {firma}")
        st.metric("CELKEM K FAKTURACI (BEZ DPH)", f"{grand_total:,.2f} Kč")

        if st.button("📄 VYGENEROVAT DODACÍ LIST (PDF)"):
            if not st.session_state.vybrany_zakaznik:
                st.error("Nejprve vyberte zákazníka v postranním panelu.")
            else:
                pdf_doc = create_delivery_note(
                    st.session_state.vybrany_zakaznik, 
                    st.session_state.data_zakazky, 
                    dl_number, 
                    zakazka, 
                    technik, 
                    objekty_text
                )
                if pdf_doc:
                    st.download_button("⬇️ STÁHNOUT DODACÍ LIST", data=pdf_doc, file_name=f"DL_{dl_number}_{firma.replace(' ','_')}.pdf")
                else:
                    st.error("Chyba při generování PDF.")

st.divider()
st.caption(f"© {datetime.date.today().year} {FIRMA_VLASTNI['název']}")
