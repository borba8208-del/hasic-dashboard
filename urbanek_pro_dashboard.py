import streamlit as st
import datetime
from fpdf import FPDF
import sqlite3
import pandas as pd
import os
import requests
import json
import time
import unicodedata
import re

# ==========================================
# CATEGORY_MAP – JEDINÝ ZDROJ PRAVDY
# ==========================================
# Mapuje UI klíč na fyzický název CSV souboru (bez .csv)
CATEGORY_MAP = {
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
    "Servisni_ukony": "revize"  # Zdrojový soubor revize.csv, v UI jako Servisní úkony
}

# ==========================================
# 1. KONFIGURACE FIRMY
# ==========================================
FIRMA_VLASTNI = {
    "název": "Ilja Urbánek - HASIČ-SERVIS",
    "sídlo": "Poříčská 186, 373 82 Boršov nad Vltavou",
    "ico": "60835265",
    "dic": "CZ5706281691",
    "certifikace": "TÜV NORD Czech",
    "založeno": 1994
}

DB_PATH = "data/data.db"
CSV_FOLDER = "data/ceniky/"

if not os.path.exists("data"): os.makedirs("data")
if not os.path.exists(CSV_FOLDER): os.makedirs(CSV_FOLDER)

# Inicializace stavů session
if "data_zakazky" not in st.session_state:
    st.session_state.data_zakazky = {}
if "vybrany_zakaznik" not in st.session_state:
    st.session_state.vybrany_zakaznik = None

# ==========================================
# 2. LOGIKA CENÍKŮ (SQL + NORMALIZACE + FIX DUPLICIT)
# ==========================================
def normalize_category_to_table(cat_key: str) -> str:
    """Převede klíč na bezpečný název SQL tabulky."""
    if not cat_key: return "cenik_ostatni"
    normalized = cat_key.lower().strip()
    normalized = "".join(char for char in unicodedata.normalize("NFKD", normalized) if not unicodedata.combining(char))
    normalized = re.sub(r"[\s/]+", "_", normalized)
    normalized = re.sub(r"[^a-z0-9_]", "", normalized)
    return f"cenik_{normalized}"

def import_all_ceniky() -> str:
    """Synchronizuje CSV s DB, řeší kódování a odstraňuje duplicity položek."""
    log_messages = []
    connection = sqlite3.connect(DB_PATH)
    for ui_key, csv_name in CATEGORY_MAP.items():
        file_path = os.path.join(CSV_FOLDER, f"{csv_name}.csv")
        table_name = normalize_category_to_table(ui_key)
        if not os.path.exists(file_path):
            log_messages.append(f"⚠️ {csv_name}.csv: Nenalezen")
            continue
        try:
            # Podpora pro UTF-8 i CP1250 (Excel)
            try:
                df = pd.read_csv(file_path, sep=";", encoding="utf-8")
            except UnicodeDecodeError:
                df = pd.read_csv(file_path, sep=";", encoding="cp1250")
            
            df.columns = [col.strip().lower() for col in df.columns]
            
            if "nazev" in df.columns and "cena" in df.columns:
                # FIX DUPLICIT: Robot nahlásil duplicity v HP, VODA, TABFOTO
                count_before = len(df)
                df["nazev"] = df["nazev"].astype(str).str.strip()
                df = df.drop_duplicates(subset=['nazev'], keep='first')
                count_after = len(df)
                
                # Formátování cen
                df["cena"] = pd.to_numeric(df["cena"].astype(str).str.replace(',', '.'), errors='coerce').fillna(0.0)
                
                valid_cols = [col for col in df.columns if col in ["nazev", "cena", "jednotka"]]
                if "jednotka" not in valid_cols:
                    df["jednotka"] = "ks"
                    valid_cols.append("jednotka")
                
                df[valid_cols].to_sql(table_name, connection, if_exists="replace", index=False)
                
                dup_msg = f" (odstraněno {count_before - count_after} duplicit)" if count_before > count_after else ""
                log_messages.append(f"✅ {table_name}: {len(df)} položek{dup_msg}")
            else:
                log_messages.append(f"❌ {csv_name}.csv: Chybí sloupce nazev/cena")
        except Exception as error:
            log_messages.append(f"❌ {csv_name}.csv: {str(error)}")
    connection.close()
    return "\n".join(log_messages)

def get_price(cat_key: str, item_name: str) -> float:
    """Získá cenu položky z konkrétní tabulky."""
    table = normalize_category_to_table(cat_key)
    try:
        conn = sqlite3.connect(DB_PATH)
        query = f"SELECT cena FROM {table} WHERE nazev = ? LIMIT 1"
        result = conn.execute(query, (item_name.strip(),)).fetchone()
        conn.close()
        return float(result[0]) if result else 0.0
    except: return 0.0

# ==========================================
# 3. ARES A DATABÁZE PARTNERŮ
# ==========================================
def get_company_from_ares(ico: str | int):
    ico_clean = str(ico).strip().zfill(8)
    url = f"https://ares.gov.cz/ekonomicke-subjekty-v-be/rest/ekonomicke-subjekty/{ico_clean}"
    try:
        response = requests.get(url, timeout=5)
        if response.status_code == 200:
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
    except: pass
    return None

def update_customer_in_db(zakaznik: dict) -> bool:
    try:
        conn = sqlite3.connect(DB_PATH); cur = conn.cursor()
        cols = [row[1] for row in cur.execute("PRAGMA table_info(obchpartner)")]
        for c in ["ADRESA1", "ADRESA2"]:
            if c not in cols: cur.execute(f"ALTER TABLE obchpartner ADD COLUMN {c} TEXT")
        cur.execute("""
            UPDATE obchpartner SET FIRMA=?, ADRESA1=?, ADRESA2=?, ADRESA3=?, PSC=?, DIC=? WHERE ICO=?
        """, (zakaznik.get("FIRMA"), zakaznik.get("ULICE"), zakaznik.get("CP"), zakaznik.get("ADRESA3"), zakaznik.get("PSC"), zakaznik.get("DIC"), zakaznik.get("ICO")))
        conn.commit(); conn.close(); return True
    except: return False

# ==========================================
# 4. PDF ENGINE (PROFESIONÁLNÍ)
# ==========================================
class UrbaneKPDF(FPDF):
    def __init__(self):
        super().__init__()
        self.pismo_ok = False; self.pismo_name = "ArialCZ"
        variants = {"regular": ["arial.ttf", "ARIAL.TTF"], "bold": ["arialbd.ttf", "ARIALBD.TTF"]}
        found = {"regular": None, "bold": None}
        for style, names in variants.items():
            for name in names:
                if os.path.exists(name): found[style] = name; break
        if found["regular"] and found["bold"]:
            try:
                self.add_font(self.pismo_name, "", found["regular"])
                self.add_font(self.pismo_name, "B", found["bold"])
                self.pismo_ok = True
            except: pass

    def header(self):
        x_off = 10
        if os.path.exists("logo.png"): self.image("logo.png", x=10, y=8, w=22); x_off = 38
        self.set_xy(x_off, 10); self.set_font(self.pismo_name, "B", 14)
        self.cell(0, 7, FIRMA_VLASTNI["název"], ln=True)
        self.set_x(x_off); self.set_font(self.pismo_name, "", 9)
        self.cell(0, 5, f"Specialista na požární bezpečnost | Tradice od 1994 | {FIRMA_VLASTNI['sídlo']}", ln=True)
        self.line(10, 31, 200, 31); self.ln(12)

    def footer(self):
        self.set_y(-15); self.set_font(self.pismo_name, "", 8)
        self.cell(0, 10, f"Systém HASIČ-SERVIS | Odborná certifikace: {FIRMA_VLASTNI['certifikace']} | Strana {self.page_no()}", align="C")

def create_report_pdf(zakaznik, items_flat, total_zaklad, sazba, doc_title, note_text=""):
    pdf = UrbaneKPDF()
    if not pdf.pismo_ok: return None
    pdf.add_page(); pdf.set_font(pdf.pismo_name, "B", 15); pdf.cell(0, 10, doc_title, ln=True)
    
    # Odběratel
    pdf.set_font(pdf.pismo_name, "B", 11); pdf.cell(0, 8, f"Odběratel: {zakaznik['FIRMA']}", ln=True)
    pdf.set_font(pdf.pismo_name, "", 10); pdf.cell(0, 6, f"IČO: {zakaznik['ICO']} | DIČ: {zakaznik.get('DIC','')}", ln=True)
    
    ul, cp, co, ob, ps = zakaznik.get("ULICE",""), zakaznik.get("CP",""), zakaznik.get("CO",""), zakaznik.get("ADRESA3",""), zakaznik.get("PSC","")
    adr = f"{ul} {cp}" if ul else f"{ps} {ob}"
    if co and co not in ["None", "", "nan", "0"]: adr += f"/{co}"
    pdf.cell(0, 6, f"Adresa: {adr}", ln=True)
    if ul: pdf.cell(0, 6, f"        {ps} {ob}", ln=True)
    pdf.ln(4); pdf.set_line_width(0.2)

    # Hlavička tabulky
    pdf.set_font(pdf.pismo_name, "B", 8); pdf.set_fill_color(240, 240, 240)
    pdf.cell(100, 7, " Popis položky / úkonu (v souladu s vyhl. 246/2001 Sb.)", border=1, fill=True)
    pdf.cell(15, 7, "Ks", border=1, align="C", fill=True)
    pdf.cell(35, 7, "Cena/jedn.", border=1, align="R", fill=True)
    pdf.cell(40, 7, "Celkem", border=1, align="R", fill=True); pdf.ln()

    # Položky (Bez nadpisů kategorií - plochý seznam)
    pdf.set_font(pdf.pismo_name, "", 8)
    for name, qty, price in items_flat:
        pdf.cell(100, 6, f" {name}", border="LR")
        q_disp = f"{qty:,.2f}".rstrip("0").rstrip(".") if qty % 1 != 0 else f"{int(qty)}"
        pdf.cell(15, 6, q_disp, border="LR", align="C")
        pdf.cell(35, 6, f"{price:,.2f} Kč ", border="LR", align="R")
        pdf.cell(40, 6, f"{qty * price:,.2f} Kč ", border="LR", align="R"); pdf.ln()
    pdf.cell(190, 0, "", border="T", ln=True); pdf.ln(4)

    # Součty
    pdf.set_font(pdf.pismo_name, "B", 10)
    pdf.cell(150, 7, "Základ daně celkem:", align="R")
    pdf.cell(40, 7, f"{total_zaklad:,.2f} Kč ", align="R", border="T"); pdf.ln()
    pdf.set_text_color(200, 0, 0); pdf.set_font(pdf.pismo_name, "B", 12)
    pdf.cell(150, 9, f"CELKEM K ÚHRADĚ VČETNĚ DPH {int(sazba*100)}%:", align="R")
    pdf.cell(40, 9, f"{total_zaklad * (1+sazba):,.2f} Kč ", align="R"); pdf.set_text_color(0, 0, 0)
    if note_text:
        pdf.ln(10); pdf.set_font(pdf.pismo_name, "", 9)
        pdf.multi_cell(0, 5, note_text)
    return bytes(pdf.output())

# ==========================================
# 5. STREAMLIT UI
# ==========================================
st.set_page_config(page_title="Urbánek Master Pro v7.2", layout="wide", page_icon="🛡️")

def load_data():
    if not os.path.exists(DB_PATH): return None
    try:
        conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
        df = pd.read_sql("SELECT * FROM obchpartner", conn); conn.close(); return df
    except: return None

df_customers = load_data()

with st.sidebar:
    st.header("🏢 Hlavička dokladu")
    if df_customers is not None:
        sq = st.text_input("🔍 Vyhledat partnera (IČO/Název):")
        mask = (df_customers["ICO"].astype(str).str.contains(sq.lower(), na=False) | 
                df_customers["FIRMA"].str.lower().str.contains(sq.lower(), na=False))
        filt = df_customers[mask].sort_values(by="FIRMA", key=lambda s: s.str.lower())
        
        if not filt.empty:
            opts = filt["FIRMA"] + " (" + filt["ICO"].astype(str) + ")"
            sel = st.selectbox("Zvolte odběratele:", opts)
            curr = filt.iloc[opts.tolist().index(sel)].to_dict()
            if st.session_state.vybrany_zakaznik is None or st.session_state.vybrany_zakaznik["ICO"] != curr["ICO"]:
                if not curr.get("ARES_OK"):
                    with st.spinner("ARES synchronizace..."):
                        ares = get_company_from_ares(curr["ICO"])
                        if ares: curr.update(ares); update_customer_in_db(curr)
                st.session_state.vybrany_zakaznik = curr.copy()
    
    st.divider()
    source_dl = st.text_input("Zdrojový doklad", value=f"DL {datetime.date.today().year}/XXX")
    sazba = 0.12 if st.toggle("Uplatnit 12% DPH (SVJ)", value=True) else 0.21
    
    with st.expander("⚙️ Pokročilá správa"):
        if st.button("🚀 Synchronizovat ceníky (CSV)"):
            st.code(import_all_ceniky()); st.rerun()

st.title("🛡️ HASIČ-SERVIS URBÁNEK")
st.caption("Master Dashboard v7.2 | Boršov n. Vltavou | Robot-Verified Build")

tabs = st.tabs(["🔥 HP & Servis", "🚰 Vodovody", "🖼️ Tabulky & Značení", "🛠️ ND & Hilti", "🧾 Souhrn faktury"])

def item_row(cat_key: str, item_name: str, row_id: str, step_val: float = 1.0):
    p_val = get_price(cat_key, item_name)
    col1, col2, col3 = st.columns([3, 1, 1])
    with col1: st.write(f"**{item_name}**")
    with col2: q = st.number_input(f"Q_{row_id}", min_value=0.0, step=float(step_val), key=f"q_{row_id}", label_visibility="collapsed")
    with col3: p = st.number_input(f"P_{row_id}", min_value=0.0, step=0.1, value=float(p_val), key=f"p_{row_id}", label_visibility="collapsed")
    st.session_state.data_zakazky[item_name] = {"q": q, "p": p}

with tabs[0]:
    st.subheader("1. Kontrola provozuschopnosti a servis HP")
    item_row("HP", "Kontrola HP (shodný)", "h1")
    item_row("HP", "Kontrola HP (neshodný opravitelný)", "h2")
    item_row("HP", "Kontrola HP (neopravitelný) + odb. zneprovoznění", "h3")
    item_row("HP", "Hodinová sazba za provedení prací", "h5", step_val=0.05)
    st.divider()
    item_row("Nahrady", "Náhrada za 1km - osobní servisní vozidlo", "n4")
    item_row("Servisni_ukony", "Tlaková zkouška nádoby HP", "s1")

with tabs[1]:
    st.subheader("2. Zařízení pro zásobování požární vodou")
    st.info("Měření průtoku a tlaku speciálním zařízením dle vyhl. 246/2001 Sb.")
    item_row("Voda", "Prohlídka zařízení od 11 do 20 ks výtoků", "v1")
    item_row("Voda", "Měření průtoku á 1 ks vnitřní hydrant. systémů", "v3")
    item_row("ND_Voda", "Hydrantový box - zámek/okénko", "ndv1")

with tabs[2]:
    st.subheader("3. Bezpečnostní tabulky a značení")
    item_row("TAB", "Tabulka - Hasicí přístroj (plast)", "t1")
    item_row("TABFOTO", "Info.plast.fotolumin. 300x150mm", "tf1")
    item_row("reklama", "Polep firemním logem", "r1")

with tabs[3]:
    st.subheader("4. Náhradní díly a technologie")
    item_row("ND_HP", "Věšák Delta W+PG NEURUPPIN", "nd1")
    item_row("HILTI", "Protipožární ucpávka Hilti", "hi1")
    item_row("Ostatni", "Technicko organizační činnost v PO", "o1")

with tabs[4]:
    active_items = {k: v for k, v in st.session_state.data_zakazky.items() if v["q"] > 0}
    if not active_items:
        st.warning("Doklad je prázdný.")
    else:
        grand_total = sum(vals["q"] * vals["p"] for vals in active_items.values())
        st.write(f"### Náhled rekapitulace pro: {st.session_state.vybrany_zakaznik['FIRMA'] if st.session_state.vybrany_zakaznik else 'Neznámý'}")
        
        flat_list = [[k, v["q"], v["p"]] for k, v in active_items.items()]
        st.table([{"Položka": row[0], "Ks": f"{row[1]:.2f}".rstrip("0").rstrip("."), "Celkem": f"{row[1]*row[2]:,.2f} Kč"} for row in flat_list])
        
        st.divider()
        c_f1, c_f2 = st.columns(2)
        with c_f1:
            st.metric("ZÁKLAD DANĚ CELKEM", f"{grand_total:,.2f} Kč")
            st.metric("K ÚHRADĚ VČETNĚ DPH", f"{grand_total * (1+sazba):,.2f} Kč")
        
        with c_f2:
            if st.button("📄 GENEROVAT PDF ROZPIS"):
                note = "Poznámka: Kontrola provozuschopnosti dle vyhlášky 246/2001 Sb. U HP typu NV (neopravitelné) doklad neslouží pro státní evidenci odpadů. Zpracováno systémem HASIČ-SERVIS."
                pdf_doc = create_report_pdf(st.session_state.vybrany_zakaznik, flat_list, grand_total, sazba, f"Rozpis prací k {source_dl}", note)
                if pdf_doc: st.download_button("⬇️ STÁHNOUT PDF", data=pdf_doc, file_name=f"Rozpis_{source_dl.replace('/','-')}.pdf")

st.divider()
st.caption(f"© {datetime.date.today().year} {FIRMA_VLASTNI['název']} | Expert na požární ochranu od 1994 | Odborná certifikace TÜV NORD")
