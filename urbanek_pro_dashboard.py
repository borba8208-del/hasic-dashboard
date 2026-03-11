
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
    "Servisni_ukony": "revize", # CSV revize se propisuje do bezpečné kategorie
}

# ==========================================
# 1. KONFIGURACE FIRMY
# ==========================================
FIRMA_VLASTNI: Dict[str, Any] = {
    "název": "Ilja Urbánek - HASIČ-SERVIS",
    "sídlo": "Poříčská 186, 373 82 Boršov nad Vltavou",
    "ico": "60835265",
    "dic": "CZ5706281691",
    "certifikace": "TÜV NORD Czech",
    "založeno": 1994,
}

DB_PATH = "data/data.db"
CSV_FOLDER = "data/ceniky/"

os.makedirs("data", exist_ok=True)
os.makedirs(CSV_FOLDER, exist_ok=True)

# ==========================================
# SESSION STATE
# ==========================================
if "data_zakazky" not in st.session_state:
    st.session_state.data_zakazky = {}

if "vybrany_zakaznik" not in st.session_state:
    st.session_state.vybrany_zakaznik = None

# ==========================================
# 2. CENÍKY – ROBUSTNÍ IMPORT A SQL LOGIKA
# ==========================================
def normalize_category_to_table(cat_key: str) -> str:
    """Převede klíč kategorie na bezpečný název SQL tabulky."""
    if not cat_key:
        return "cenik_ostatni"
    normalized = cat_key.lower().strip()
    normalized = "".join(char for char in unicodedata.normalize("NFKD", normalized) if not unicodedata.combining(char))
    normalized = re.sub(r"[\s/]+", "_", normalized)
    normalized = re.sub(r"[^a-z0-9_]", "", normalized)
    return f"cenik_{normalized}"

def safe_read_csv(path: str) -> Optional[pd.DataFrame]:
    """Bezpečné načtení CSV s fallbackem na český Windows (cp1250)."""
    if not os.path.exists(path):
        return None
    for enc in ("utf-8", "cp1250"):
        try:
            return pd.read_csv(path, sep=";", encoding=enc)
        except Exception:
            continue
    return None

def import_all_ceniky() -> str:
    """Synchronizuje CSV s DB. Řeší diakritiku, kódování a čistí duplicity."""
    log_messages: List[str] = []
    
    # Pojistka pro vytvoření DB souboru, pokud neexistuje
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
                log_messages.append(f"❌ {csv_name}.csv: Nelze načíst (chyba kódování nebo struktury).")
                continue

            df.columns = [str(col).strip().lower() for col in df.columns]

            if "nazev" not in df.columns or "cena" not in df.columns:
                log_messages.append(f"❌ {csv_name}.csv: Chybí sloupce 'nazev' nebo 'cena'.")
                continue

            # FIX DUPLICIT: Automatické odstranění duplicit nahlášených Robotem
            df["nazev"] = df["nazev"].astype(str).str.strip()
            count_before = len(df)
            df = df.drop_duplicates(subset=["nazev"], keep="first")
            count_after = len(df)

            # Bezpečný převod ceny (řeší čárky z Excelu)
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
                log_messages.append(f"❌ {table_name}: Chyba při zápisu do DB – {e}")
    finally:
        connection.close()

    return "\n".join(log_messages)

def get_price(cat_key: str, item_name: str) -> float:
    """Získá cenu položky ze správné SQL tabulky. Vrací 0.0 pokud nenajde."""
    if not os.path.exists(DB_PATH):
        return 0.0
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
# 3. ARES API – ZÁCHRANA DIAKRITIKY
# ==========================================
def get_company_from_ares(ico: str | int) -> Optional[Dict[str, Any]]:
    """Hluboký parser ARES JSON pro 100% správnou adresu a diakritiku."""
    ico_clean = str(ico).strip().zfill(8)
    url = f"https://ares.gov.cz/ekonomicke-subjekty-v-be/rest/ekonomicke-subjekty/{ico_clean}"
    try:
        response = requests.get(url, timeout=5)
        if response.status_code != 200: 
            return None
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
    except Exception: 
        return None

def update_customer_in_db(zakaznik: Dict[str, Any]) -> bool:
    if not os.path.exists(DB_PATH): 
        return False
    try:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cols = [row[1] for row in cur.execute("PRAGMA table_info(obchpartner)")]
        for c in ["ADRESA1", "ADRESA2"]:
            if c not in cols: 
                cur.execute(f"ALTER TABLE obchpartner ADD COLUMN {c} TEXT")
        cur.execute("""
            UPDATE obchpartner SET FIRMA=?, ADRESA1=?, ADRESA2=?, ADRESA3=?, PSC=?, DIC=? WHERE ICO=?
        """, (zakaznik.get("FIRMA"), zakaznik.get("ULICE"), zakaznik.get("CP"), zakaznik.get("ADRESA3"), zakaznik.get("PSC"), zakaznik.get("DIC"), zakaznik.get("ICO")))
        conn.commit()
        conn.close()
        return True
    except Exception: 
        return False

def repair_all_customers_with_ares() -> str:
    if not os.path.exists(DB_PATH): 
        return "Databáze neexistuje."
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    try:
        rows = cur.execute("SELECT ICO FROM obchpartner").fetchall()
    except Exception:
        conn.close()
        return "Tabulka obchpartner neexistuje."
    
    fixed = 0
    skipped = 0
    total = len(rows)
    prog = st.progress(0.0)

    for i, (ico,) in enumerate(rows):
        ico_str = str(ico).strip()
        if len(ico_str) < 6:
            skipped += 1
            continue
        ares = get_company_from_ares(ico_str)
        if ares:
            ares["ICO"] = ico_str
            if update_customer_in_db(ares): 
                fixed += 1
        else: 
            skipped += 1
            
        prog.progress((i + 1) / max(total, 1))
        if i % 10 == 0: 
            time.sleep(0.05)

    conn.close()
    return f"Vyčištěno {fixed} záznamů přes ARES, přeskočeno {skipped}."

# ==========================================
# 4. PDF ENGINE (STABILNÍ FONDY)
# ==========================================
class UrbaneKPDF(FPDF):
    def __init__(self) -> None:
        super().__init__()
        self.pismo_ok = False
        self.pismo_name = "ArialCZ"
        # Agresivní vyhledávání fontů (lokálně i ve Windows)
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
        x_off = 10
        if os.path.exists("logo.png"):
            self.image("logo.png", x=10, y=8, w=22)
            x_off = 38
            
        self.set_xy(x_off, 10)
        self.set_font(self.pismo_name if self.pismo_ok else "helvetica", "B", 14)
        self.cell(0, 7, FIRMA_VLASTNI["název"], ln=True)
        self.set_x(x_off)
        self.set_font(self.pismo_name if self.pismo_ok else "helvetica", "", 9)
        self.cell(0, 5, f"Specialista na požární bezpečnost | Tradice od 1994 | {FIRMA_VLASTNI['sídlo']}", ln=True)
        self.line(10, 31, 200, 31)
        self.ln(12)

    def footer(self) -> None:
        self.set_y(-15)
        self.set_font(self.pismo_name if self.pismo_ok else "helvetica", "", 8)
        self.cell(0, 10, f"Systém HASIČ-SERVIS | Odborná certifikace: {FIRMA_VLASTNI['certifikace']} | Strana {self.page_no()}", align="C")

def create_report_pdf(zakaznik: Dict[str, Any], items_flat: List[List[Any]], total_zaklad: float, sazba: float, doc_title: str, note_text: str = "") -> Optional[bytes]:
    pdf = UrbaneKPDF()
    pismo = pdf.pismo_name if pdf.pismo_ok else "helvetica"
    
    pdf.add_page()
    pdf.set_font(pismo, "B", 15)
    pdf.cell(0, 10, doc_title, ln=True)

    # Odběratel
    pdf.set_font(pismo, "B", 11)
    pdf.cell(0, 8, f"Odběratel: {zakaznik.get('FIRMA','')}", ln=True)
    pdf.set_font(pismo, "", 10)
    pdf.cell(0, 6, f"IČO: {zakaznik.get('ICO','')} | DIČ: {zakaznik.get('DIC','')}", ln=True)

    ul = zakaznik.get("ULICE", "") or ""
    cp = zakaznik.get("CP", "") or ""
    co = zakaznik.get("CO", "") or ""
    ob = zakaznik.get("ADRESA3", "") or ""
    ps = zakaznik.get("PSC", "") or ""

    if ul:
        adr = f"{ul} {cp}".strip()
        if co and co not in ["None", "", "nan", "0"]: 
            adr += f"/{co}"
        pdf.cell(0, 6, f"Adresa: {adr}", ln=True)
        pdf.cell(0, 6, f"        {ps} {ob}".strip(), ln=True)
    else:
        adr = f"{ps} {ob}".strip()
        pdf.cell(0, 6, f"Adresa: {adr}", ln=True)

    pdf.ln(4)
    pdf.set_line_width(0.2)

    # Hlavička tabulky
    pdf.set_font(pismo, "B", 8)
    pdf.set_fill_color(240, 240, 240)
    pdf.cell(100, 7, " Popis položky / úkonu (v souladu s vyhl. 246/2001 Sb.)", border=1, fill=True)
    pdf.cell(15, 7, "Ks", border=1, align="C", fill=True)
    pdf.cell(35, 7, "Cena/jedn.", border=1, align="R", fill=True)
    pdf.cell(40, 7, "Celkem", border=1, align="R", fill=True)
    pdf.ln()

    # Položky (Čistý list bez mezer)
    pdf.set_font(pismo, "", 8)
    for name, qty, price in items_flat:
        qty = float(qty)
        price = float(price)
        pdf.cell(100, 6, f" {name}", border="LR")
        
        if qty % 1 != 0:
            q_disp = f"{qty:,.2f}".rstrip("0").rstrip(".")
        else:
            q_disp = f"{int(qty)}"
            
        pdf.cell(15, 6, q_disp, border="LR", align="C")
        pdf.cell(35, 6, f"{price:,.2f} Kč ", border="LR", align="R")
        pdf.cell(40, 6, f"{qty * price:,.2f} Kč ", border="LR", align="R")
        pdf.ln()
    
    pdf.cell(190, 0, "", border="T", ln=True)
    pdf.ln(4)

    # Součty
    pdf.set_font(pismo, "B", 10)
    pdf.cell(150, 7, "ZÁKLAD DANĚ CELKEM:", align="R")
    pdf.cell(40, 7, f"{total_zaklad:,.2f} Kč ", align="R", border="T")
    pdf.ln()
    
    pdf.set_text_color(200, 0, 0)
    pdf.set_font(pismo, "B", 12)
    pdf.cell(150, 9, f"CELKEM K ÚHRADĚ VČETNĚ DPH {int(sazba*100)}%:", align="R")
    pdf.cell(40, 9, f"{total_zaklad * (1 + sazba):,.2f} Kč ", align="R")
    pdf.set_text_color(0, 0, 0)

    if note_text:
        pdf.ln(10)
        pdf.set_font(pismo, "", 9)
        pdf.multi_cell(0, 5, note_text)

    try: 
        return bytes(pdf.output())
    except Exception: 
        return None

# ==========================================
# 5. STREAMLIT UI 
# ==========================================
st.set_page_config(page_title="Urbánek Master Pro v9.1", layout="wide", page_icon="🛡️")

def load_all_customers() -> Optional[pd.DataFrame]:
    if not os.path.exists(DB_PATH): 
        return None
    try:
        conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
        df = pd.read_sql("SELECT * FROM obchpartner", conn)
        conn.close()
        return df
    except Exception: 
        return None

df_customers = load_all_customers()

with st.sidebar:
    st.header("🏢 Hlavička dokladu")
    if df_customers is not None:
        sq = st.text_input("🔍 Vyhledat partnera (IČO/Název):")
        sq_lower = sq.lower().strip()
        
        if sq_lower:
            mask = (df_customers["ICO"].astype(str).str.contains(sq_lower, na=False) | 
                    df_customers["FIRMA"].str.lower().str.contains(sq_lower, na=False))
        else: 
            mask = pd.Series([True] * len(df_customers))
        
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
                            curr.update(ares)
                            update_customer_in_db(curr)
                st.session_state.vybrany_zakaznik = curr.copy()
        else: 
            st.warning("Nenalezeno.")

        st.divider()
        if st.button("🛠️ Opravit celou DB přes ARES"):
            msg = repair_all_customers_with_ares()
            st.success(msg)
            st.rerun()
    else: 
        st.warning("Nelze načíst databázi partnerů.")

    st.divider()
    source_dl = st.text_input("Číslo dokladu", value=f"DL {datetime.date.today().year}/XXX")
    je_svj = st.toggle("Uplatnit sníženou sazbu 12% (SVJ)", value=True)
    sazba = 0.12 if je_svj else 0.21

    with st.expander("⚙️ Pokročilá správa"):
        if st.button("🚀 Synchronizovat ceníky (CSV)"):
            log = import_all_ceniky()
            st.code(log)
            st.rerun()

st.title("🛡️ HASIČ-SERVIS URBÁNEK")
st.caption("Master Dashboard v9.1 | Ultimátní Stabilita | Boršov n. Vltavou")

tabs = st.tabs(["🔥 HP & Servis", "🚰 Požární vodovody", "📦 Náhrady & Servisní úkony", "🖼️ Tabulky & Značení", "🛠️ ND & Ostatní", "🧾 Export"])

def item_row(cat_key: str, item_name: str, fallback_price: float, row_id: str, step_val: float = 1.0) -> None:
    """Získá cenu z DB. Pokud ji nenajde, použije fallback (záložní cenu). Tím pádem aplikace nikdy nespadne."""
    p_val = get_price(cat_key, item_name)
    if p_val == 0.0:
        p_val = fallback_price  # Záchranná brzda

    col1, col2, col3 = st.columns([3, 1, 1])
    with col1: 
        st.write(f"**{item_name}**")
    with col2: 
        q = st.number_input(f"Q_{row_id}", min_value=0.0, step=float(step_val), key=f"q_{row_id}", label_visibility="collapsed")
    with col3: 
        p = st.number_input(f"P_{row_id}", min_value=0.0, step=0.1, value=float(p_val), key=f"p_{row_id}", label_visibility="collapsed")
    
    st.session_state.data_zakazky[item_name] = {"q": float(q), "p": float(p)}

# TAB 0 – HP
with tabs[0]:
    st.subheader("1. Kontrola provozuschopnosti HP")
    item_row("HP", "Kontrola HP (shodný)", 29.40, "h1")
    item_row("HP", "Kontrola HP (neshodný opravitelný)", 19.70, "h2")
    item_row("HP", "Kontrola HP (neopravitelný) + odb. zneprovoznění", 23.50, "h3")
    item_row("HP", "Hodinová sazba za provedení prací", 450.00, "h5", step_val=0.05)
    st.divider()
    st.subheader("Prodej a příslušenství")
    item_row("HP", "Skříň na HP 9kg KOM 9 AZ/O", 820.00, "h11")

# TAB 1 – Vodovody
with tabs[1]:
    st.subheader("2. Zařízení pro zásobování požární vodou")
    st.info("Prováděno měření průtoku a tlaku certifikovaným zařízením dle vyhl. 246/2001 Sb.")
    item_row("Voda", "Prohlídka zařízení od 11 do 20 ks výtoků", 193.00, "v1")
    item_row("Voda", "Měření průtoku á 1 ks vnitřní hydrant. systémů", 95.00, "v3")

# TAB 2 – Náhrady
with tabs[2]:
    st.subheader("3. Servisní úkony a náhrady")
    item_row("Nahrady", "Převzetí HP vyřazeného z užívání dodavatelem", 88.00, "n1")
    item_row("Nahrady", "Označení - vylepení koleček o kontrole (á 2ks)", 3.50, "n2")
    item_row("Nahrady", "Náhrada za 1km - osobní servisní vozidlo", 13.80, "n4")
    item_row("Servisni_ukony", "Tlaková zkouška nádoby HP", 352.00, "s1")

# TAB 3 – Tabulky
with tabs[3]:
    st.subheader("4. Bezpečnostní tabulky a značení")
    item_row("TAB", "Tabulka - Hasicí přístroj (plast)", 25.00, "t1")
    item_row("TABFOTO", "Info.plast.fotolumin. 300x150mm", 65.00, "tf1")
    item_row("reklama", "Polep firemním logem", 15.00, "r1")

# TAB 4 – ND
with tabs[4]:
    st.subheader("5. Náhradní díly a ostatní")
    item_row("ND_HP", "Věšák Delta W+PG NEURUPPIN", 35.00, "nd1")
    item_row("HILTI", "Protipožární ucpávka Hilti", 500.00, "hi1")
    item_row("Ostatni", "Technicko organizační činnost v PO", 4488.00, "o1")

# TAB 5 – Export
with tabs[5]:
    # Zjednodušený slovník bez složitých zápisů, aby Python nehlásil nesmyslné chyby
    active_items = {}
    for k, v in st.session_state.data_zakazky.items():
        if v["q"] > 0:
            active_items[k] = v

    if not active_items:
        st.warning("Doklad neobsahuje žádné položky.")
    else:
        grand_total = 0.0
        for vals in active_items.values():
            grand_total += vals["q"] * vals["p"]
            
        firma = "Neznámý"
        if st.session_state.vybrany_zakaznik:
            firma = st.session_state.vybrany_zakaznik.get("FIRMA", "Neznámý")
        
        st.write(f"### Rozpis pro: {firma}")
        
        flat_list = []
        table_rows = []
        for k, v in active_items.items():
            flat_list.append([k, v["q"], v["p"]])
            
            # Formátování tabulky
            mnozstvi_str = f"{v['q']:.2f}".rstrip("0").rstrip(".")
            celkem_str = f"{v['q'] * v['p']:.2f} Kč"
            
            table_rows.append({
                "Položka": k, 
                "Ks": mnozstvi_str, 
                "Celkem": celkem_str
            })
            
        st.table(table_rows)

        st.divider()
        c_f1, c_f2 = st.columns(2)
        with c_f1:
            st.metric("ZÁKLAD DANĚ CELKEM", f"{grand_total:,.2f} Kč")
            st.metric("K ÚHRADĚ VČETNĚ DPH", f"{grand_total * (1 + sazba):,.2f} Kč")

        with c_f2:
            if st.button("📄 VYGENEROVAT PDF ROZPIS"):
                if not st.session_state.vybrany_zakaznik:
                    st.error("Vyberte partnera v bočním panelu.")
                else:
                    note = (
                        "Poznámka: Kontrola provozuschopnosti dle vyhlášky 246/2001 Sb. "
                        "U HP typu NV (neopravitelné) doklad neslouží pro státní evidenci odpadů. "
                        "Zpracováno systémem HASIČ-SERVIS."
                    )
                    pdf_doc = create_report_pdf(
                        st.session_state.vybrany_zakaznik, flat_list, grand_total, sazba, f"Rozpis prací k {source_dl}", note
                    )
                    if pdf_doc:
                        st.download_button("⬇️ STÁHNOUT PDF", data=pdf_doc, file_name=f"Rozpis_{source_dl.replace('/','-')}.pdf")
                    else:
                        st.error("Nepodařilo se vytvořit PDF.")

st.divider()
st.caption(f"© {datetime.date.today().year} {FIRMA_VLASTNI['název']} | Expert na požární ochranu od 1994 | Odborná certifikace TÜV NORD")




