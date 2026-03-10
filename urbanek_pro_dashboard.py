import streamlit as st
import datetime
from fpdf import FPDF
import sqlite3
import pandas as pd
import os
import requests
import json

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

DEFAULTS = {
    "hp_shodny": 29.40, "hp_opravitelny": 19.70, "hp_likvidace": 23.50, "hp_pojezdny_s": 166.60,
    "hp_pojezdny_n": 111.60, "hp_hod_sazba": 450.00, "hp_obec_do_90": 44.40, "hp_obec_nad_90": 107.00,
    "hp_zapujcka": 155.00, "hp_montaz": 150.00, "hp_vyhodnoceni": 5.80, "nh_prevzeti": 88.00,
    "nh_kolecka": 3.50, "nh_stitky": 8.00, "nh_km_osobni": 13.80, "nh_km_prives": 16.00,
    "nh_km_nakladni": 15.90, "nh_komunikace": 48.00, "pv_prohlidka": 193.00, "pv_hydro_pausal": 352.00,
    "pv_mereni_ks": 95.00, "pv_hodinova_sazba": 450.00, "pv_vyhodnoceni": 153.00, "pv_zprava": 170.00,
    "pv_kolecko": 3.50, "p6": 1090.00, "v9": 1370.00, "vesak": 35.00, "toc": 4488.00
}

# Složka pro data
if not os.path.exists("data"):
    os.makedirs("data")

if 'data_zakazky' not in st.session_state:
    st.session_state.data_zakazky = {}
if 'vybrany_zakaznik' not in st.session_state:
    st.session_state.vybrany_zakaznik = None

# ==========================================
# 2. POMOCNÉ FUNKCE (ČÍTAČ, DB, ARES)
# ==========================================
def get_next_order_number():
    file_path = "data/counter.json"
    year = str(datetime.date.today().year)
    try:
        if os.path.exists(file_path):
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        else:
            data = {}
        current = data.get(year, 0) + 1
        return f"{year}/{current:03d}"
    except:
        return f"{year}/001"

def increment_order_counter():
    file_path = "data/counter.json"
    year = str(datetime.date.today().year)
    try:
        data = {}
        if os.path.exists(file_path):
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        data[year] = data.get(year, 0) + 1
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except: pass

def update_customer_in_db(z):
    db_path = "data/data.db"
    if not os.path.exists(db_path): return False
    try:
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        
        # Pojistka: Přidání chybějících sloupců z ARES do staré DB
        cols = [row[1] for row in cur.execute("PRAGMA table_info(obchpartner)")]
        if "ADRESA1" not in cols: cur.execute("ALTER TABLE obchpartner ADD COLUMN ADRESA1 TEXT")
        if "ADRESA2" not in cols: cur.execute("ALTER TABLE obchpartner ADD COLUMN ADRESA2 TEXT")
        
        cur.execute("""
            UPDATE obchpartner 
            SET FIRMA = ?, ADRESA1 = ?, ADRESA2 = ?, ADRESA3 = ?, PSC = ?, DIC = ?
            WHERE ICO = ?
        """, (z.get("FIRMA"), z.get("ULICE"), z.get("CP"), z.get("ADRESA3"), z.get("PSC"), z.get("DIC"), z.get("ICO")))
        conn.commit()
        conn.close()
        return True
    except: return False

def get_company_from_ares(ico):
    ico = str(ico).strip().zfill(8)
    url = f"https://ares.gov.cz/ekonomicke-subjekty-v-be/rest/ekonomicke-subjekty/{ico}"
    try:
        r = requests.get(url, timeout=5)
        if r.status_code == 200:
            d = r.json()
            s = d.get("sidlo", {})
            return {
                "FIRMA": d.get("obchodniJmeno", ""),
                "DIC": d.get("dic", ""),
                "ULICE": s.get("nazevUlice", ""),
                "CP": s.get("cisloDomovni", ""),
                "CO": s.get("cisloOrientacni", ""),
                "ADRESA3": s.get("nazevObce", ""),
                "PSC": s.get("psc", ""),
                "ARES_OK": True
            }
    except: pass
    return None

def load_customers():
    db_path = "data/data.db"
    if not os.path.exists(db_path): return None
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        df = pd.read_sql("SELECT * FROM obchpartner", conn)
        conn.close()
        return df
    except: return None

# ==========================================
# 3. PDF ENGINE (PROFESIONÁLNÍ LAYOUT)
# ==========================================
class UrbaneKPDF(FPDF):
    def __init__(self):
        super().__init__()
        self.pismo_ok = False
        self.italic_ok = False
        self.pismo_name = "ArialCZ"
        variants = {"regular": ["arial.ttf", "ARIAL.TTF"], "bold": ["arialbd.ttf", "ARIALBD.TTF"], "italic": ["ariali.ttf", "ARIALI.TTF"]}
        found = {"regular": None, "bold": None, "italic": None}
        for style, names in variants.items():
            for name in names:
                if os.path.exists(name): found[style] = name; break
        if not found["regular"]:
            win_path = "C:/Windows/Fonts/"
            for style, names in variants.items():
                for name in names:
                    f = os.path.join(win_path, name)
                    if os.path.exists(f): found[style] = f; break
        if found["regular"] and found["bold"]:
            try:
                self.add_font(self.pismo_name, "", found["regular"])
                self.add_font(self.pismo_name, "B", found["bold"])
                if found["italic"]: self.add_font(self.pismo_name, "I", found["italic"]); self.italic_ok = True
                self.pismo_ok = True
            except: pass

    def header(self):
        x_start = 10
        if os.path.exists("logo.png"):
            self.image("logo.png", x=10, y=8, w=22)
            x_start = 38
        self.set_xy(x_start, 10)
        self.set_font(self.pismo_name, 'B', 14)
        self.cell(0, 7, FIRMA_VLASTNI["název"], ln=True)
        self.set_x(x_start)
        self.set_font(self.pismo_name, '', 9)
        self.cell(0, 5, f"Specialista na požární bezpečnost | {FIRMA_VLASTNI['sídlo']}", ln=True)
        self.line(10, 31, 200, 31)
        self.ln(12)

    def footer(self):
        self.set_y(-15)
        style = 'I' if self.italic_ok else ''
        self.set_font(self.pismo_name, style, 8)
        self.cell(0, 10, f"Odborná certifikace: {FIRMA_VLASTNI['certifikace']} | Strana {self.page_no()}", align='C')

def create_report_pdf(zakaznik, categories, total_zaklad, sazba, doc_title, note_text=""):
    pdf = UrbaneKPDF()
    if not pdf.pismo_ok: return None
    pdf.add_page()
    pdf.set_font(pdf.pismo_name, "B", 15)
    pdf.cell(0, 10, doc_title, ln=True)
    
    # Odběratel
    pdf.set_font(pdf.pismo_name, "B", 11)
    pdf.cell(0, 8, f"Odběratel: {zakaznik['FIRMA']}", ln=True)
    pdf.set_font(pdf.pismo_name, "", 10)
    pdf.cell(0, 6, f"IČO: {zakaznik['ICO']} | DIČ: {zakaznik.get('DIC','')}", ln=True)
    
    ulice = zakaznik.get('ULICE', '')
    cp = str(zakaznik.get('CP', ''))
    co = str(zakaznik.get('CO', ''))
    obec = zakaznik.get('ADRESA3', '')
    psc = str(zakaznik.get('PSC', ''))
    
    adr = f"{ulice} {cp}" if ulice else f"{psc} {obec}"
    if co and co != 'None' and co != '': adr += f"/{co}"
    pdf.cell(0, 6, f"Adresa: {adr}", ln=True)
    if ulice: pdf.cell(0, 6, f"        {psc} {obec}", ln=True)
    pdf.ln(4)

    # Tabulka
    pdf.set_line_width(0.2)
    for cat_name, items in categories.items():
        active = [i for i in items if i[1] > 0]
        if not active: continue
        
        pdf.set_font(pdf.pismo_name, "B", 9)
        pdf.set_fill_color(235, 235, 235)
        pdf.cell(190, 7, f"  {cat_name.upper()}", border=1, ln=True, fill=True)
        
        pdf.set_font(pdf.pismo_name, "B", 8)
        pdf.cell(100, 6, " Popis položky / úkonu", border=1)
        pdf.cell(15, 6, "Ks", border=1, align='C')
        pdf.cell(35, 6, "Cena/jedn.", border=1, align='R')
        pdf.cell(40, 6, "Celkem", border=1, align='R')
        pdf.ln()

        pdf.set_font(pdf.pismo_name, "", 8)
        for name, qty, price in active:
            h = 6
            pdf.cell(100, h, f" {name}", border="LR")
            q_disp = f"{qty:,.2f}".rstrip('0').rstrip('.') if qty % 1 != 0 else f"{int(qty)}"
            pdf.cell(15, h, q_disp, border="LR", align='C')
            pdf.cell(35, h, f"{price:,.2f} Kč ", border="LR", align='R')
            pdf.cell(40, h, f"{qty * price:,.2f} Kč ", border="LR", align='R')
            pdf.ln()
        pdf.cell(190, 0, "", border="T", ln=True)
        pdf.ln(2)

    # Součty
    pdf.ln(2)
    pdf.set_font(pdf.pismo_name, "B", 10)
    pdf.cell(150, 7, "Základ daně celkem:", align='R')
    pdf.cell(40, 7, f"{total_zaklad:,.2f} Kč ", align='R', border="T")
    pdf.ln()
    pdf.set_text_color(200, 0, 0)
    pdf.set_font(pdf.pismo_name, "B", 12)
    pdf.cell(150, 9, f"CELKEM K ÚHRADĚ VČETNĚ DPH {int(sazba*100)}%:", align='R')
    pdf.cell(40, 9, f"{total_zaklad * (1+sazba):,.2f} Kč ", align='R')
    pdf.set_text_color(0, 0, 0)
    
    if note_text:
        pdf.ln(10); pdf.set_font(pdf.pismo_name, "I" if pdf.italic_ok else "", 9)
        pdf.multi_cell(0, 5, note_text)
    return bytes(pdf.output())

# ==========================================
# 4. STREAMLIT UI
# ==========================================
st.set_page_config(page_title="Urbánek Pro v5.5", layout="wide", page_icon="🛡️")

df_customers = load_customers()

with st.sidebar:
    st.header("🏢 Správa partnerů")
    if df_customers is not None:
        sq = st.text_input("🔍 Rychlé hledání (Název, IČO...):")
        mask = (df_customers["ICO"].astype(str).str.contains(sq.lower(), na=False) | 
                df_customers["FIRMA"].str.lower().str.contains(sq.lower(), na=False))
        filt = df_customers[mask]
        if not filt.empty:
            opts = filt["FIRMA"] + " (" + filt["ICO"].astype(str) + ")"
            sel = st.selectbox("Vyberte zákazníka:", opts)
            idx = filt.index[opts == sel].tolist()[0]
            curr = filt.loc[idx].to_dict()
            
            if st.button("🪄 Opravit diakritiku a uložit (ARES)"):
                with st.spinner("Čistím data..."):
                    ares = get_company_from_ares(curr["ICO"])
                    if ares:
                        curr.update(ares)
                        st.session_state.vybrany_zakaznik = curr
                        update_customer_in_db(curr)
                        st.success("Data v DB trvale opravena!")
                    else: st.error("ARES neodpovídá.")
            
            if st.session_state.vybrany_zakaznik is None or st.session_state.vybrany_zakaznik['ICO'] != curr['ICO']:
                st.session_state.vybrany_zakaznik = curr
        else: st.warning("Nenalezeno.")
    else: st.error("Chybí data/data.db")

    st.divider()
    st.header("📝 Detaily zakázky")
    klient_pdf = st.text_input("Odběratel na PDF:", value=st.session_state.vybrany_zakaznik['FIRMA'] if st.session_state.vybrany_zakaznik else "")
    source_dl = st.text_input("Číslo zakázky:", value=get_next_order_number())
    je_svj = st.toggle("Sazba DPH 12% (SVJ)", value=True)
    sazba = 0.12 if je_svj else 0.21

st.title("🛡️ HASIČ-SERVIS URBÁNEK")
st.caption("v5.5 | Profesionální layout | Automatické učení databáze | Smart Číslování")

tabs = st.tabs(["🔥 HP & Náhrady", "🚰 Vodovody", "🛠️ Odborná činnost", "📦 Prodej zboží", "🧾 Export"])

def item_row(cat, name, d_q, d_p, key, step=1.0):
    c1, c2, c3 = st.columns([3, 1, 1])
    with c1: st.write(f"**{name}**")
    with c2: q = st.number_input(f"Q_{key}", min_value=0.0, step=float(step), value=float(d_q), key=f"q_{key}", label_visibility="collapsed")
    with c3: p = st.number_input(f"P_{key}", min_value=0.0, step=0.1, value=float(d_p), key=f"p_{key}", label_visibility="collapsed")
    st.session_state.data_zakazky[name] = {'q': q, 'p': p, 'cat': cat}

with tabs[0]:
    st.subheader("Hasicí přístroje")
    item_row("HP", "Kontrola HP (shodný)", 0, DEFAULTS["hp_shodny"], "h1")
    item_row("HP", "Kontrola HP (neshodný opravitelný)", 0, DEFAULTS["hp_opravitelny"], "h2")
    item_row("HP", "Kontrola HP (neopravitelný) + zneprovoznění", 0, DEFAULTS["hp_likvidace"], "h3")
    item_row("HP", "Hodinová sazba za provedení prací", 0, DEFAULTS["hp_hod_sazba"], "h5", step=0.05)
    st.divider(); st.subheader("Náhrady")
    item_row("Náhrady", "Označení - vylepení koleček (á 2ks / HP)", 0, DEFAULTS["nh_kolecka"], "n2")
    item_row("Náhrady", "Náhrada za 1km - osobní servisní vozidlo", 0, DEFAULTS["nh_km_osobni"], "n4")

with tabs[1]:
    item_row("PV", "Měření průtoku á 1 ks vnitřní hydrant. systémů", 0, DEFAULTS["pv_mereni_ks"], "v3")
    item_row("PV", "Hod. sazba (pochůzky / manipulace s PV)", 0, DEFAULTS["pv_hodinova_sazba"], "v4", step=0.05)
    item_row("PV", "Vyhodnocení kontroly zařízení PV", 0, DEFAULTS["pv_vyhodnoceni"], "v5")
    item_row("PV", "Vyhotovení zprávy o kontrole PBZ", 0, DEFAULTS["pv_zprava"], "v6")

with tabs[2]:
    item_row("TOC", "Technicko organizační činnost v PO", 0, DEFAULTS["toc"], "t1")

with tabs[3]:
    item_row("Zboží", "Hasicí přístroj RAIMA P6 (34A, 233B, C)", 0, DEFAULTS["p6"], "p1")
    item_row("Zboží", "Hasicí přístroj V9Ti (voda)", 0, DEFAULTS["v9"], "p2")

with tabs[4]:
    active = {k: v for k, v in st.session_state.data_zakazky.items() if v['q'] > 0}
    if not active: st.warning("Zadejte položky.")
    else:
        grand_total = sum(v['q'] * v['p'] for v in active.values())
        st.write(f"### Náhled rozpočtu: {klient_pdf}")
        cats = sorted(list(set([v['cat'] for v in active.values()])))
        structured = {c: [(k, v['q'], v['p']) for k, v in active.items() if v['cat'] == c] for c in cats}
        for c, itms in structured.items():
            st.markdown(f"**{c.upper()}**")
            st.table([{"Položka": i[0], "Ks": f"{i[1]:.2f}".rstrip('0').rstrip('.'), "Celkem": f"{i[1]*i[2]:,.2f} Kč"} for i in itms])
        
        st.divider(); st.metric("CELKEM BEZ DPH", f"{grand_total:,.2f} Kč")
        if st.button("📄 VYGENEROVAT FINÁLNÍ PDF"):
            if not st.session_state.vybrany_zakaznik: st.error("Vyberte zákazníka.")
            else:
                pdf = create_report_pdf(st.session_state.vybrany_zakaznik, structured, grand_total, sazba, f"Rozpis prací k č. {source_dl}", "Poznámka: Kontroly dle vyhl. 246/2001 Sb. Zpracováno systémem HASIČ-SERVIS.")
                if pdf:
                    increment_order_counter()
                    st.download_button("⬇️ STÁHNOUT PDF", data=pdf, file_name=f"Rozpis_{source_dl.replace('/','-')}.pdf")

st.divider(); st.caption(f"© {datetime.date.today().year} HASIČ-SERVIS | Moderní správa požární bezpečnosti")

