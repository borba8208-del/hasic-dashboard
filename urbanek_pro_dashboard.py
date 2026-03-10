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
    "revize": "revize"
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
# 2. CENÍKY – NORMALIZACE A IMPORT
# ==========================================
def normalize_category(cat_key: str) -> str:
    """Převede UI klíč na deterministický název SQL tabulky."""
    if not cat_key:
        return "cenik_ostatni"
    s = cat_key.lower().strip()
    # Odstranění diakritiky
    s = "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))
    # Náhrada mezer a lomítek podtržítkem
    s = re.sub(r"[\s/]+", "_", s)
    # Odstranění speciálních znaků
    s = re.sub(r"[^a-z0-9_]", "", s)
    return f"cenik_{s}"

def import_all_ceniky() -> str:
    """Hromadný import CSV souborů do SQL tabulek dle CATEGORY_MAP."""
    log = []
    conn = sqlite3.connect(DB_PATH)
    for ui_key, csv_name in CATEGORY_MAP.items():
        file_path = os.path.join(CSV_FOLDER, f"{csv_name}.csv")
        table_name = normalize_category(ui_key)
        
        if not os.path.exists(file_path):
            log.append(f"⚠️ {csv_name}.csv: Nenalezen (UI: {ui_key})")
            continue
            
        try:
            # Načtení s českým oddělovačem ;
            df = pd.read_csv(file_path, sep=";", encoding="utf-8")
            df.columns = [c.strip().lower() for c in df.columns]
            
            if "nazev" not in df.columns or "cena" not in df.columns:
                log.append(f"❌ {csv_name}.csv: Chybí sloupce 'nazev' nebo 'cena'")
                continue
                
            cols = [c for c in df.columns if c in ["nazev", "cena", "jednotka"]]
            if "jednotka" not in cols:
                df["jednotka"] = "ks"
                cols = ["nazev", "cena", "jednotka"]
                
            df[cols].to_sql(table_name, conn, if_exists="replace", index=False)
            log.append(f"✅ {table_name}: {len(df)} položek (zdroj {csv_name}.csv)")
        except Exception as e:
            log.append(f"❌ {csv_name}.csv: Chyba {str(e)}")
            
    conn.close()
    return "\n".join(log)

def get_price(cat_key: str, item_name: str) -> float:
    """Získá cenu položky z konkrétní SQL tabulky ceníku."""
    table = normalize_category(cat_key)
    try:
        conn = sqlite3.connect(DB_PATH)
        query = f"SELECT cena FROM {table} WHERE nazev = ? LIMIT 1"
        res = conn.execute(query, (item_name,)).fetchone()
        conn.close()
        return float(res[0]) if res else 0.0
    except Exception:
        return 0.0

# ==========================================
# 3. ARES A PARTNEŘI
# ==========================================
def get_company_from_ares(ico: str | int):
    """Hluboký parser ARES API pro 100% správnou češtinu a adresu."""
    ico = str(ico).strip().zfill(8)
    url = f"https://ares.gov.cz/ekonomicke-subjekty-v-be/rest/ekonomicke-subjekty/{ico}"
    try:
        r = requests.get(url, timeout=5)
        if r.status_code != 200: return None
        data = r.json()
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

def update_customer_in_db(z: dict) -> bool:
    """Uloží data z ARES trvale do SQLite, včetně ulice a č.p."""
    try:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cols = [row[1] for row in cur.execute("PRAGMA table_info(obchpartner)")]
        for c in ["ADRESA1", "ADRESA2"]:
            if c not in cols: cur.execute(f"ALTER TABLE obchpartner ADD COLUMN {c} TEXT")
        cur.execute("""
            UPDATE obchpartner SET FIRMA=?, ADRESA1=?, ADRESA2=?, ADRESA3=?, PSC=?, DIC=? WHERE ICO=?
        """, (z.get("FIRMA"), z.get("ULICE"), z.get("CP"), z.get("ADRESA3"), z.get("PSC"), z.get("DIC"), z.get("ICO")))
        conn.commit()
        conn.close()
        return True
    except Exception: return False

def repair_all_customers_with_ares() -> str:
    """Hromadná oprava celé databáze partnerů podle IČO."""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    rows = cur.execute("SELECT ICO FROM obchpartner").fetchall()
    fixed = 0; skipped = 0
    prog = st.progress(0)
    for i, (ico,) in enumerate(rows):
        ico_str = str(ico).strip()
        if len(ico_str) < 6: skipped += 1; continue
        ares = get_company_from_ares(ico_str)
        if ares:
            ares["ICO"] = ico_str
            update_customer_in_db(ares)
            fixed += 1
        else: skipped += 1
        prog.progress((i + 1) / len(rows))
        if i % 10 == 0: time.sleep(0.05)
    conn.close()
    return f"Hotovo. Opraveno {fixed} firem přes ARES."

# ==========================================
# 4. PDF ENGINE
# ==========================================
class UrbaneKPDF(FPDF):
    def __init__(self):
        super().__init__()
        self.pismo_ok = False; self.italic_ok = False; self.pismo_name = "ArialCZ"
        variants = {"regular": ["arial.ttf", "ARIAL.TTF"], "bold": ["arialbd.ttf", "ARIALBD.TTF"], "italic": ["ariali.ttf", "ARIALI.TTF"]}
        found = {"regular": None, "bold": None, "italic": None}
        for style, names in variants.items():
            for name in names:
                if os.path.exists(name): found[style] = name; break
        if found["regular"] and found["bold"]:
            try:
                self.add_font(self.pismo_name, "", found["regular"])
                self.add_font(self.pismo_name, "B", found["bold"])
                if found["italic"]: self.add_font(self.pismo_name, "I", found["italic"]); self.italic_ok = True
                self.pismo_ok = True
            except: pass

    def header(self):
        x_start = 10
        if os.path.exists("logo.png"): self.image("logo.png", x=10, y=8, w=22); x_start = 38
        self.set_xy(x_start, 10); self.set_font(self.pismo_name, "B", 14)
        self.cell(0, 7, FIRMA_VLASTNI["název"], ln=True)
        self.set_x(x_start); self.set_font(self.pismo_name, "", 9)
        self.cell(0, 5, f"Specialista na požární bezpečnost | Tradice od 1994 | {FIRMA_VLASTNI['sídlo']}", ln=True)
        self.line(10, 31, 200, 31); self.ln(12)

    def footer(self):
        self.set_y(-15); style = "I" if self.italic_ok else ""
        self.set_font(self.pismo_name, style, 8)
        self.cell(0, 10, f"Systém HASIČ-SERVIS | Odborná certifikace: {FIRMA_VLASTNI['certifikace']} | Strana {self.page_no()}", align="C")

def create_report_pdf(zakaznik, categories, total_zaklad, sazba, doc_title, note_text=""):
    pdf = UrbaneKPDF()
    if not pdf.pismo_ok: return None
    pdf.add_page(); pdf.set_font(pdf.pismo_name, "B", 15); pdf.cell(0, 10, doc_title, ln=True)
    
    # Odběratel
    pdf.set_font(pdf.pismo_name, "B", 11); pdf.cell(0, 8, f"Odběratel: {zakaznik['FIRMA']}", ln=True)
    pdf.set_font(pdf.pismo_name, "", 10); pdf.cell(0, 6, f"IČO: {zakaznik['ICO']} | DIČ: {zakaznik.get('DIC','')}", ln=True)

    ul, cp, co, ob, ps = zakaznik.get("ULICE", ""), zakaznik.get("CP", ""), zakaznik.get("CO", ""), zakaznik.get("ADRESA3", ""), zakaznik.get("PSC", "")
    adr = f"{ul} {cp}" if ul else f"{ps} {ob}"
    if co and co not in ["None", "", "nan", "0"]: adr += f"/{co}"
    pdf.cell(0, 6, f"Adresa: {adr}", ln=True)
    if ul: pdf.cell(0, 6, f"        {ps} {ob}", ln=True)
    pdf.ln(4); pdf.set_line_width(0.2)

    for cat_key, items in categories.items():
        active = [i for i in items if i[1] > 0]
        if not active: continue
        pdf.set_font(pdf.pismo_name, "B", 9); pdf.set_fill_color(235, 235, 235)
        pdf.cell(190, 7, f"  {cat_key.upper()}", border=1, ln=True, fill=True)
        pdf.set_font(pdf.pismo_name, "B", 8)
        pdf.cell(100, 6, " Popis položky / úkonu (v souladu s vyhl. 246/2001 Sb.)", border=1)
        pdf.cell(15, 6, "Ks", border=1, align="C"); pdf.cell(35, 6, "Cena/jedn.", border=1, align="R")
        pdf.cell(40, 6, "Celkem", border=1, align="R"); pdf.ln(); pdf.set_font(pdf.pismo_name, "", 8)
        for name, qty, price in active:
            pdf.cell(100, 6, f" {name}", border="LR")
            q_disp = f"{qty:,.2f}".rstrip("0").rstrip(".") if qty % 1 != 0 else f"{int(qty)}"
            pdf.cell(15, 6, q_disp, border="LR", align="C")
            pdf.cell(35, 6, f"{price:,.2f} Kč ", border="LR", align="R")
            pdf.cell(40, 6, f"{qty * price:,.2f} Kč ", border="LR", align="R"); pdf.ln()
        pdf.cell(190, 0, "", border="T", ln=True); pdf.ln(2)

    pdf.ln(2); pdf.set_font(pdf.pismo_name, "B", 10)
    pdf.cell(150, 7, "Základ daně celkem:", align="R")
    pdf.cell(40, 7, f"{total_zaklad:,.2f} Kč ", align="R", border="T"); pdf.ln()
    pdf.set_text_color(200, 0, 0); pdf.set_font(pdf.pismo_name, "B", 12)
    pdf.cell(150, 9, f"CELKEM K ÚHRADĚ VČETNĚ DPH {int(sazba*100)}%:", align="R")
    pdf.cell(40, 9, f"{total_zaklad * (1+sazba):,.2f} Kč ", align="R"); pdf.set_text_color(0, 0, 0)
    if note_text:
        pdf.ln(10); pdf.set_font(pdf.pismo_name, "I" if pdf.italic_ok else "", 9)
        pdf.multi_cell(0, 5, note_text)
    return bytes(pdf.output())

# ==========================================
# 5. STREAMLIT UI
# ==========================================
st.set_page_config(page_title="Urbánek Pro v6.5", layout="wide", page_icon="🛡️")

def load_all_customers():
    if not os.path.exists(DB_PATH): return None
    try:
        conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
        df = pd.read_sql("SELECT * FROM obchpartner", conn); conn.close(); return df
    except: return None

df_customers = load_all_customers()

with st.sidebar:
    st.header("⚙️ Správa systému")
    with st.expander("📦 Ceníky"):
        st.caption("Synchronizuje ceníky ze složky data/ceniky/ dle mapy.")
        if st.button("🚀 Synchronizovat ceníky (CSV)"):
            st.code(import_all_ceniky()); st.success("Hotovo."); st.rerun()

    st.divider(); st.header("👤 Partner")
    if df_customers is not None:
        sq = st.text_input("🔍 Hledat firmu (IČO/Název):")
        mask = (df_customers["ICO"].astype(str).str.contains(sq.lower(), na=False) | 
                df_customers["FIRMA"].str.lower().str.contains(sq.lower(), na=False))
        filt = df_customers[mask].sort_values(by="FIRMA", key=lambda s: s.str.lower())
        
        if not filt.empty:
            opts = filt["FIRMA"] + " (" + filt["ICO"].astype(str) + ")"
            sel = st.selectbox("Vyberte partnera:", opts)
            idx = filt.index[opts == sel].tolist()[0]
            curr = filt.loc[idx].to_dict()
            
            # SESSION LOGIKA: Ochrana ARES dat
            if st.session_state.vybrany_zakaznik is None or st.session_state.vybrany_zakaznik["ICO"] != curr["ICO"]:
                if not curr.get("ARES_OK"):
                    with st.spinner("Ladění adresy přes ARES..."):
                        ares = get_company_from_ares(curr["ICO"])
                        if ares: 
                            curr.update(ares)
                            update_customer_in_db(curr)
                st.session_state.vybrany_zakaznik = curr.copy()
        else: st.warning("Nenalezeno.")
        
        if st.button("🛠️ Opravit CELOU DB přes ARES"):
            st.success(repair_all_customers_with_ares()); st.rerun()
    
    st.divider(); st.header("📝 Detaily dokladu")
    kl_pdf = st.text_input("Odběratel na dokumentu:", value=st.session_state.vybrany_zakaznik["FIRMA"] if st.session_state.vybrany_zakaznik else "")
    src_dl = st.text_input("Číslo zakázky:", value=f"{datetime.date.today().year}/XXX")
    je_svj = st.toggle("Uplatnit sníženou sazbu 12% (SVJ)", value=True)
    sazba = 0.12 if je_svj else 0.21

st.title("🛡️ HASIČ-SERVIS URBÁNEK")
st.caption("v6.5 | Deterministické ceníky | Jediný zdroj pravdy | 100% čeština")

tabs = st.tabs(["🔥 Hasicí přístroje", "📦 Náhrady", "🚰 Požární vodovody", "🛠️ Ostatní", "🧾 Export"])

def item_row(cat_key: str, item_name: str, key: str, step: float = 1.0):
    price = get_price(cat_key, item_name)
    c1, c2, c3 = st.columns([3, 1, 1])
    with c1: st.write(f"**{item_name}**")
    with c2: q = st.number_input(f"Q_{key}", min_value=0.0, step=float(step), value=0.0, key=f"q_{key}", label_visibility="collapsed")
    with c3: p = st.number_input(f"P_{key}", min_value=0.0, step=0.1, value=float(price), key=f"p_{key}", label_visibility="collapsed")
    st.session_state.data_zakazky[item_name] = {"q": q, "p": p, "cat": cat_key}

with tabs[0]:
    st.subheader("1. KONTROLY HASÍCÍCH PŘÍSTROJŮ (HP)")
    item_row("HP", "Kontrola HP (shodný)", "h1")
    item_row("HP", "Kontrola HP (neshodný opravitelný)", "h2")
    item_row("HP", "Kontrola HP (neopravitelný) + odb. zneprovoznění", "h3")
    item_row("HP", "Hodinová sazba za provedení prací", "h5", step=0.05)
    item_row("HP", "Náklady v obci do 90 tisích obyvatel", "h6")

with tabs[1]:
    st.subheader("2. NÁHRADY (Hasicí přístroje)")
    item_row("Nahrady", "Převzetí HP vyřazeného z užívání dodavatelem", "n1")
    item_row("Nahrady", "Označení - vylepení koleček o kontrole (á 2ks)", "n2")
    item_row("Nahrady", "Náhrada za 1km - osobní servisní vozidlo", "n4")

with tabs[2]:
    st.subheader("3. ZAŘÍZENÍ PRO ZÁSOBOVÁNÍ POŽÁRNÍ VODOU")
    item_row("Voda", "Prohlídka zařízení od 11 do 20 ks výtoků", "v1")
    item_row("Voda", "Měření průtoku á 1 ks vnitřní hydrant. systémů", "v3")
    item_row("Voda", "Hod. sazba (pochůzky / manipulace s PV)", "v4", step=0.05)
    item_row("Voda", "Vyhotovení zprávy o kontrole PBZ", "v6")

with tabs[3]:
    st.subheader("4. OSTATNÍ ČINNOST A PRODEJ")
    item_row("Ostatni", "Hasicí přístroj RAIMA P6 (34A, 233B, C)", "p1")
    item_row("Ostatni", "Technicko organizační činnost v PO", "t1")

with tabs[4]:
    active = {k: v for k, v in st.session_state.data_zakazky.items() if v["q"] > 0}
    if not active: st.warning("Zadejte položky pro export.")
    else:
        grand_total = sum(v["q"] * v["p"] for v in active.values())
        st.write(f"### Náhled rozpisu: {kl_pdf}")
        cats = sorted(list(set([v["cat"] for v in active.values()])))
        structured = {c: [(k, v["q"], v["p"]) for k, v in active.items() if v["cat"] == c] for c in cats}
        for c, itms in structured.items():
            st.markdown(f"**{c.upper()}**")
            st.table([{"Položka": i[0], "Ks": f"{i[1]:.2f}".rstrip("0").rstrip("."), "Celkem": f"{i[1]*i[2]:,.2f} Kč"} for i in itms])
        
        st.divider(); st.metric("CELKEM BEZ DPH", f"{grand_total:,.2f} Kč")
        if st.button("📄 VYGENEROVAT DOKUMENT PDF"):
            if not st.session_state.vybrany_zakaznik: st.error("Vyberte partnera v bočním panelu.")
            else:
                note = "Poznámka: Kontrola provozuschopnosti dle vyhlšky 246/2001 Sb. U PV provedeno měření certifikovaným zařízením. Zpracováno systémem HASIČ-SERVIS."
                pdf = create_report_pdf(st.session_state.vybrany_zakaznik, structured, grand_total, sazba, f"Rozpis prací k č. {src_dl}", note)
                if pdf: st.download_button("⬇️ STÁHNOUT PDF", data=pdf, file_name=f"Rozpis_{src_dl.replace('/','-')}.pdf")

st.divider(); st.caption(f"© {datetime.date.today().year} HASIČ-SERVIS URBÁNEK | Boršov n. Vltavou")
