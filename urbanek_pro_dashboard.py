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
# 1. KONFIGURACE A DATOVÝ MODEL
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

if 'data_zakazky' not in st.session_state: st.session_state.data_zakazky = {}
if 'vybrany_zakaznik' not in st.session_state: st.session_state.vybrany_zakaznik = None

# ==========================================
# 2. CENÍKOVÝ MODUL (MULTI-TABLE SQLITE)
# ==========================================
def normalize_category(cat: str) -> str:
    """Převede název kategorie na bezpečný název SQL tabulky."""
    if not cat: return "cenik_ostatni"
    s = cat.lower().strip()
    s = "".join(c for c in unicodedata.normalize('NFKD', s) if not unicodedata.combining(c))
    s = re.sub(r'[\s/]+', '_', s)
    s = re.sub(r'[^a-z0-9_]', '', s)
    return f"cenik_{s}"

def import_all_ceniky():
    """Načte všechny CSV ve složce a vytvoří tabulky v SQLite."""
    if not os.path.exists(CSV_FOLDER):
        return "Složka data/ceniky/ neexistuje."
    
    files = [f for f in os.listdir(CSV_FOLDER) if f.endswith('.csv')]
    if not files:
        return "Ve složce nejsou žádné CSV soubory pro import."
    
    conn = sqlite3.connect(DB_PATH)
    log = []
    
    for filename in files:
        try:
            path = os.path.join(CSV_FOLDER, filename)
            # Čtení s oddělovačem ; (typické pro český Excel/Access export)
            df = pd.read_csv(path, sep=';', encoding='utf-8')
            df.columns = [c.strip().lower() for c in df.columns]
            
            # Název tabulky podle jména souboru (např. VODA.csv -> cenik_voda)
            cat_name = filename.rsplit('.', 1)[0]
            table_name = normalize_category(cat_name)
            
            cols_to_save = [c for c in df.columns if c in ['nazev', 'cena', 'jednotka']]
            if 'nazev' in df.columns and 'cena' in df.columns:
                df[cols_to_save].to_sql(table_name, conn, if_exists="replace", index=False)
                log.append(f"✅ {table_name}: {len(df)} položek")
            else:
                log.append(f"⚠️ {filename}: Chybí sloupce 'nazev' nebo 'cena'")
        except Exception as e:
            log.append(f"❌ {filename}: {str(e)}")
            
    conn.close()
    return "\n".join(log)

def get_price(category: str, item_name: str) -> float:
    """Získá cenu položky z konkrétní tabulky ceníku."""
    table = normalize_category(category)
    try:
        conn = sqlite3.connect(DB_PATH)
        query = f"SELECT cena FROM {table} WHERE nazev = ? LIMIT 1"
        res = conn.execute(query, (item_name,)).fetchone()
        conn.close()
        return float(res[0]) if res else 0.0
    except:
        return 0.0

# ==========================================
# 3. POMOCNÉ FUNKCE (ARES, DB, ČÍTAČ)
# ==========================================
def get_company_from_ares(ico):
    """Opravený a hluboký parser ARES JSON (ekonomickySubjekt -> sidlo)."""
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
            "ARES_OK": True
        }
    except: return None

def update_customer_in_db(z):
    try:
        conn = sqlite3.connect(DB_PATH); cur = conn.cursor()
        cols = [row[1] for row in cur.execute("PRAGMA table_info(obchpartner)")]
        for c in ["ADRESA1", "ADRESA2"]:
            if c not in cols: cur.execute(f"ALTER TABLE obchpartner ADD COLUMN {c} TEXT")
        cur.execute("""
            UPDATE obchpartner SET FIRMA=?, ADRESA1=?, ADRESA2=?, ADRESA3=?, PSC=?, DIC=? WHERE ICO=?
        """, (z.get("FIRMA"), z.get("ULICE"), z.get("CP"), z.get("ADRESA3"), z.get("PSC"), z.get("DIC"), z.get("ICO")))
        conn.commit(); conn.close(); return True
    except: return False

def load_customers():
    if not os.path.exists(DB_PATH): return None
    try:
        conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
        df = pd.read_sql("SELECT * FROM obchpartner", conn)
        conn.close(); return df
    except: return None

def get_next_order_number():
    file_path = "data/counter.json"
    year = str(datetime.date.today().year)
    try:
        if os.path.exists(file_path):
            with open(file_path, "r", encoding="utf-8") as f: data = json.load(f)
        else: data = {}
        return f"{year}/{(data.get(year, 0) + 1):03d}"
    except: return f"{year}/001"

def increment_order_counter():
    file_path = "data/counter.json"
    year = str(datetime.date.today().year)
    try:
        data = {}
        if os.path.exists(file_path):
            with open(file_path, "r", encoding="utf-8") as f: data = json.load(f)
        data[year] = data.get(year, 0) + 1
        with open(file_path, "w", encoding="utf-8") as f: json.dump(data, f, indent=2)
    except: pass

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
        x_off = 10
        if os.path.exists("logo.png"): self.image("logo.png", x=10, y=8, w=22); x_off = 38
        self.set_xy(x_off, 10); self.set_font(self.pismo_name, 'B', 14)
        self.cell(0, 7, FIRMA_VLASTNI["název"], ln=True)
        self.set_x(x_off); self.set_font(self.pismo_name, '', 9)
        self.cell(0, 5, f"Specialista na požární bezpečnost | Tradice od 1994 | {FIRMA_VLASTNI['sídlo']}", ln=True)
        self.line(10, 31, 200, 31); self.ln(12)

    def footer(self):
        self.set_y(-15); s = 'I' if self.italic_ok else ''
        self.set_font(self.pismo_name, s, 8)
        self.cell(0, 10, f"Zpracováno v systému HASIČ-SERVIS | Odborná certifikace: {FIRMA_VLASTNI['certifikace']} | Strana {self.page_no()}", align='C')

def create_report_pdf(zakaznik, categories, total_zaklad, sazba, doc_title, note_text=""):
    pdf = UrbaneKPDF()
    if not pdf.pismo_ok: return None
    pdf.add_page(); pdf.set_font(pdf.pismo_name, "B", 15); pdf.cell(0, 10, doc_title, ln=True)
    pdf.set_font(pdf.pismo_name, "B", 11); pdf.cell(0, 8, f"Odběratel: {zakaznik['FIRMA']}", ln=True)
    
    ul, cp, co, ob, ps = zakaznik.get('ULICE',''), zakaznik.get('CP',''), zakaznik.get('CO',''), zakaznik.get('ADRESA3',''), zakaznik.get('PSC','')
    adr = f"{ul} {cp}" if ul else f"{ps} {ob}"
    if co and co not in ['None','','nan','0']: adr += f"/{co}"
    pdf.set_font(pdf.pismo_name, "", 10); pdf.cell(0, 6, f"Adresa: {adr}", ln=True)
    pdf.cell(0, 6, f"IČO: {zakaznik['ICO']} | DIČ: {zakaznik.get('DIC','')}", ln=True); pdf.ln(4)
    pdf.set_line_width(0.2)

    for cat_name, items in categories.items():
        active = [i for i in items if i[1] > 0]
        if not active: continue
        pdf.set_font(pdf.pismo_name, "B", 9); pdf.set_fill_color(235, 235, 235)
        pdf.cell(190, 7, f"  {cat_name.upper()}", border=1, ln=True, fill=True)
        pdf.set_font(pdf.pismo_name, "B", 8)
        pdf.cell(100, 6, " Popis položky / úkonu (v souladu s vyhl. 246/2001 Sb.)", border=1)
        pdf.cell(15, 6, "Ks", border=1, align='C'); pdf.cell(35, 6, "Cena/jedn.", border=1, align='R')
        pdf.cell(40, 6, "Celkem", border=1, align='R'); pdf.ln(); pdf.set_font(pdf.pismo_name, "", 8)
        for name, qty, price in active:
            pdf.cell(100, 6, f" {name}", border="LR")
            q_disp = f"{qty:,.2f}".rstrip('0').rstrip('.') if qty % 1 != 0 else f"{int(qty)}"
            pdf.cell(15, 6, q_disp, border="LR", align='C')
            pdf.cell(35, 6, f"{price:,.2f} Kč ", border="LR", align='R')
            pdf.cell(40, 6, f"{qty * price:,.2f} Kč ", border="LR", align='R'); pdf.ln()
        pdf.cell(190, 0, "", border="T", ln=True); pdf.ln(2)

    pdf.ln(2); pdf.set_font(pdf.pismo_name, "B", 10)
    pdf.cell(150, 7, "Základ daně celkem:", align='R')
    pdf.cell(40, 7, f"{total_zaklad:,.2f} Kč ", align='R', border="T"); pdf.ln()
    pdf.set_text_color(200, 0, 0); pdf.set_font(pdf.pismo_name, "B", 12)
    pdf.cell(150, 9, f"CELKEM K ÚHRADĚ VČETNĚ DPH {int(sazba*100)}%:", align='R')
    pdf.cell(40, 9, f"{total_zaklad * (1+sazba):,.2f} Kč ", align='R'); pdf.set_text_color(0, 0, 0)
    if note_text: pdf.ln(10); pdf.set_font(pdf.pismo_name, "I" if pdf.italic_ok else "", 9); pdf.multi_cell(0, 5, note_text)
    return bytes(pdf.output())

# ==========================================
# 5. STREAMLIT UI
# ==========================================
st.set_page_config(page_title="Urbánek Master Pro v6.2", layout="wide", page_icon="🛡️")

df_customers = load_customers()

with st.sidebar:
    st.header("🏢 Správa systému")
    
    with st.expander("📦 Ceníky → Synchronizace"):
        st.write("Nahrajte CSV do `data/ceniky/` a synchronizujte.")
        if st.button("🚀 Synchronizovat ceníky"):
            log_msg = import_all_ceniky()
            st.code(log_msg)
            st.success("Synchronizace dokončena.")

    st.divider()
    st.header("👤 Odběratel")
    if df_customers is not None:
        sq = st.text_input("🔍 Hledat firmu (IČO/Název):")
        mask = (df_customers["ICO"].astype(str).str.contains(sq.lower(), na=False) | 
                df_customers["FIRMA"].str.lower().str.contains(sq.lower(), na=False))
        filt = df_customers[mask].sort_values(by="FIRMA", key=lambda s: s.str.lower())
        
        if not filt.empty:
            opts = filt["FIRMA"] + " (" + filt["ICO"].astype(str) + ")"
            sel = st.selectbox("Zvolte partnera:", opts)
            idx = filt.index[opts == sel].tolist()[0]
            curr = filt.loc[idx].to_dict()
            
            # Logika zachování ARES dat
            if st.session_state.vybrany_zakaznik is None or st.session_state.vybrany_zakaznik["ICO"] != curr["ICO"]:
                st.session_state.vybrany_zakaznik = curr.copy()
            
            if not st.session_state.vybrany_zakaznik.get("ARES_OK"):
                with st.spinner("Dolaďuji diakritiku přes ARES..."):
                    ares = get_company_from_ares(st.session_state.vybrany_zakaznik["ICO"])
                    if ares:
                        st.session_state.vybrany_zakaznik.update(ares)
                        update_customer_in_db(st.session_state.vybrany_zakaznik)
                        st.toast(f"Data firmy {ares['FIRMA']} opravena.", icon="🪄")
    
    st.divider(); st.header("📝 Zakázka")
    kl_pdf = st.text_input("Odběratel na dokumentu:", value=st.session_state.vybrany_zakaznik['FIRMA'] if st.session_state.vybrany_zakaznik else "")
    src_dl = st.text_input("Číslo dokladu:", value=get_next_order_number())
    je_svj = st.toggle("Snížená sazba DPH 12% (SVJ)", value=True)
    sazba = 0.12 if je_svj else 0.21

st.title("🛡️ HASIČ-SERVIS URBÁNEK")
st.caption("v6.2 | Fix kategorií | Automatický ARES | Boršov n. Vltavou")

tabs = st.tabs(["🔥 Hasicí přístroje", "📦 Náhrady", "🚰 Požární vodovody", "🛠️ Ostatní", "🧾 Export"])

def item_row(cat, name, key, step=1.0):
    """Hledá cenu v tabulce, která odpovídá normalizované kategorii."""
    c_p = get_price(cat, name)
    c1, c2, c3 = st.columns([3, 1, 1])
    with c1: st.write(f"**{name}**")
    with c2: q = st.number_input(f"Q_{key}", min_value=0.0, step=float(step), value=0.0, key=f"q_{key}", label_visibility="collapsed")
    with c3: p = st.number_input(f"P_{key}", min_value=0.0, step=0.1, value=float(c_p), key=f"p_{key}", label_visibility="collapsed")
    st.session_state.data_zakazky[name] = {'q': q, 'p': p, 'cat': cat}

with tabs[0]:
    st.subheader("1. KONTROLY HASÍCÍCH PŘÍSTROJŮ")
    # Soubor HP.csv -> tabulka cenik_hp
    item_row("HP", "Kontrola HP (shodný)", "h1")
    item_row("HP", "Kontrola HP (neshodný opravitelný)", "h2")
    item_row("HP", "Kontrola HP (neopravitelný) + odb. zneprovoznění", "h3")
    item_row("HP", "Manipulace a odvoz HP k údržbě/demontáži", "h4")
    item_row("HP", "Hodinová sazba za provedení prací", "h5", step=0.05)
    item_row("HP", "Náklady v obci do 90 tisích obyvatel", "h6")
    item_row("HP", "Zápůjčka za HP v údržbě (ČSN ISO11602-2)", "h8")
    item_row("HP", "Vyhodnocení kontroly + vystavení dokladu (á 1ks)", "h10")

with tabs[1]:
    st.subheader("2. NÁHRADY (Hasicí přístroje)")
    # Soubor Náhrady.csv -> tabulka cenik_nahrady
    item_row("Náhrady", "Převzetí HP vyřazeného z užívání dodavatelem", "n1")
    item_row("Náhrady", "Označení - vylepení koleček o kontrole (á 2ks)", "n2")
    item_row("Náhrady", "Náhrada za 1km - osobní servisní vozidlo", "n4")

with tabs[2]:
    st.subheader("ZAŘÍZENÍ PRO ZÁSOBOVÁNÍ POŽÁRNÍ VODOU")
    # Soubor VODA.csv -> tabulka cenik_voda
    item_row("Voda", "Prohlídka zařízení od 11 do 20 ks výtoků", "v1")
    item_row("Voda", "Měření průtoku á 1 ks vnitřní hydrant. systémů", "v3")
    item_row("Voda", "Vyhodnocení kontroly zařízení PV", "v5")
    item_row("Voda", "Vyhotovení zprávy o kontrole PBZ", "v6")

with tabs[3]:
    st.subheader("OSTATNÍ ČINNOST A PRODEJ")
    # Soubor ostatni.csv -> tabulka cenik_ostatni
    item_row("ostatni", "Hasicí přístroj RAIMA P6 (34A, 233B, C)", "p1")
    item_row("ostatni", "Technicko organizační činnost v PO", "t1")

with tabs[4]:
    active = {k: v for k, v in st.session_state.data_zakazky.items() if v['q'] > 0}
    if not active: st.warning("Nebyly vybrány žádné položky.")
    else:
        grand_total = sum(v['q'] * v['p'] for v in active.values())
        st.write(f"### Náhled rozpisu: {kl_pdf}")
        cats = sorted(list(set([v['cat'] for v in active.values()])))
        structured = {c: [(k, v['q'], v['p']) for k, v in active.items() if v['cat'] == c] for c in cats}
        for c, itms in structured.items():
            st.markdown(f"**{c.upper()}**")
            st.table([{"Položka": i[0], "Ks": f"{i[1]:.2f}".rstrip('0').rstrip('.'), "Celkem": f"{i[1]*i[2]:,.2f} Kč"} for i in itms])
        
        st.divider(); st.metric("CELKEM BEZ DPH", f"{grand_total:,.2f} Kč")
        if st.button("📄 VYGENEROVAT DOKUMENT PDF"):
            if not st.session_state.vybrany_zakaznik: st.error("Vyberte partnera v bočním panelu.")
            else:
                note = "Poznámka: Kontrola provozuschopnosti dle vyhl. 246/2001 Sb. U PV provedeno měření certifikovaným zařízením. Zpracováno systémem HASIČ-SERVIS."
                pdf = create_report_pdf(st.session_state.vybrany_zakaznik, structured, grand_total, sazba, f"Rozpis prací k č. {src_dl}", note)
                if pdf:
                    increment_order_counter(); st.download_button("⬇️ STÁHNOUT PDF", data=pdf, file_name=f"Rozpis_{src_dl.replace('/','-')}.pdf")

st.divider(); st.caption(f"© {datetime.date.today().year} HASIČ-SERVIS URBÁNEK | Profesionální systém správy PO")
