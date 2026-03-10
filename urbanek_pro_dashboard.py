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

# Zajištění existence složky pro data
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
    """Spravuje roční čítač zakázek v JSON souboru."""
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
    """Inkrementuje čítač po úspěšném vygenerování PDF."""
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
    except:
        pass

def update_customer_in_db(zakaznik):
    """Trvale uloží opravená data z ARES do SQLite databáze."""
    db_path = "data/data.db"
    if not os.path.exists(db_path): return
    try:
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        cur.execute("""
            UPDATE obchpartner 
            SET FIRMA = ?, ADRESA3 = ?, PSC = ?, DIC = ?
            WHERE ICO = ?
        """, (
            zakaznik.get("FIRMA"), 
            zakaznik.get("ADRESA3"), 
            zakaznik.get("PSC"), 
            zakaznik.get("DIC"), 
            zakaznik.get("ICO")
        ))
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        st.warning(f"Nepodařilo se trvale uložit data do DB: {e}")
        return False

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
# 3. PDF ENGINE (LOGO + MODERNÍ LAYOUT)
# ==========================================
class UrbaneKPDF(FPDF):
    def __init__(self):
        super().__init__()
        self.pismo_ok = False
        self.italic_ok = False
        self.pismo_name = "ArialCZ"
        
        variants = {
            "regular": ["arial.ttf", "ARIAL.TTF", "Arial.ttf"],
            "bold": ["arialbd.ttf", "ARIALBD.TTF", "Arialbd.ttf"],
            "italic": ["ariali.ttf", "ARIALI.TTF", "Ariali.ttf"]
        }
        found = {"regular": None, "bold": None, "italic": None}
        for style, names in variants.items():
            for name in names:
                if os.path.exists(name): found[style] = name; break
        
        if found["regular"] and found["bold"]:
            try:
                self.add_font(self.pismo_name, "", found["regular"])
                self.add_font(self.pismo_name, "B", found["bold"])
                if found["italic"]:
                    self.add_font(self.pismo_name, "I", found["italic"])
                    self.italic_ok = True
                self.pismo_ok = True
            except: self.pismo_ok = False
        else: self.pismo_ok = False

    def header(self):
        x_text_start = 10
        if os.path.exists("logo.png"):
            self.image("logo.png", x=10, y=8, w=25)
            x_text_start = 40

        self.set_xy(x_text_start, 10)
        self.set_font(self.pismo_name, 'B', 14)
        self.cell(0, 8, FIRMA_VLASTNI["název"], ln=True)
        self.set_x(x_text_start)
        self.set_font(self.pismo_name, '', 9)
        self.cell(0, 5, f"Specialista na požární bezpečnost | {FIRMA_VLASTNI['sídlo']}", ln=True)
        self.line(10, 28, 200, 28)
        self.ln(10)

    def footer(self):
        self.set_y(-15)
        style = 'I' if self.italic_ok else ''
        self.set_font(self.pismo_name, style, 8)
        self.cell(0, 10, f"Zpracováno v systému W-SERVIS | Odborná certifikace: {FIRMA_VLASTNI['certifikace']} | Strana {self.page_no()}", align='C')

def create_report_pdf(zakaznik, categories, total_zaklad, sazba, doc_title, note_text=""):
    pdf = UrbaneKPDF()
    if not pdf.pismo_ok: return None
    pdf.add_page()
    pdf.set_font(pdf.pismo_name, "B", 16)
    pdf.cell(0, 10, doc_title, ln=True)
    pdf.set_font(pdf.pismo_name, "", 12)
    
    pdf.cell(0, 8, f"Odběratel: {zakaznik['FIRMA']} (IČO: {zakaznik['ICO']})", ln=True)
    ulice = zakaznik.get('ULICE', '')
    cp = str(zakaznik.get('CP', ''))
    co = str(zakaznik.get('CO', ''))
    obec = zakaznik.get('ADRESA3', '')
    psc = str(zakaznik.get('PSC', ''))
    
    if ulice:
        adr = f"{ulice} {cp}"
        if co and co != 'None' and co != '': adr += f"/{co}"
        pdf.cell(0, 7, f"Adresa: {adr}", ln=True)
        pdf.cell(0, 7, f"        {psc} {obec}", ln=True)
    else:
        pdf.cell(0, 7, f"Adresa: {psc} {obec}", ln=True)
    pdf.ln(5)

    pdf.set_line_width(0.1)
    for cat_name, items in categories.items():
        active_items = [i for i in items if i[1] > 0]
        if not active_items: continue
        
        pdf.set_font(pdf.pismo_name, "B", 10)
        pdf.set_fill_color(245, 245, 245)
        pdf.cell(0, 8, cat_name.upper(), ln=True, fill=True, border="TB")
        
        pdf.set_font(pdf.pismo_name, "B", 8)
        pdf.cell(100, 7, "Položka / úkon (v souladu s vyhl. 246/2001 Sb.)", border=1, align='L')
        pdf.cell(15, 7, "Ks", border=1, align='C')
        pdf.cell(35, 7, "Cena/ks", border=1, align='R')
        pdf.cell(40, 7, "Celkem", border=1, align='R')
        pdf.ln()

        pdf.set_font(pdf.pismo_name, "", 8)
        for name, qty, price in active_items:
            pdf.cell(100, 6, name, border="LR")
            q_disp = f"{qty:,.2f}".rstrip('0').rstrip('.') if qty % 1 != 0 else f"{int(qty)}"
            pdf.cell(15, 6, q_disp, border="LR", align='C')
            pdf.cell(35, 6, f"{price:,.2f} Kč", border="LR", align='R')
            pdf.cell(40, 6, f"{qty * price:,.2f} Kč", border="LR", align='R')
            pdf.ln()
        pdf.cell(190, 0, "", border="T", ln=True)
        pdf.ln(2)

    pdf.ln(5)
    pdf.set_font(pdf.pismo_name, "B", 11)
    pdf.cell(150, 8, "ZÁKLAD DANĚ CELKEM:", align='R')
    pdf.cell(40, 8, f"{total_zaklad:,.2f} Kč", align='R', border="T")
    pdf.ln()
    pdf.set_text_color(200, 0, 0)
    pdf.cell(150, 8, f"CELKEM K ÚHRADĚ VČETNĚ DPH {int(sazba*100)}%:", align='R')
    pdf.cell(40, 8, f"{total_zaklad * (1+sazba):,.2f} Kč", align='R')
    pdf.set_text_color(0, 0, 0)
    
    if note_text:
        pdf.ln(10)
        style = 'I' if pdf.italic_ok else ''
        pdf.set_font(pdf.pismo_name, style, 9)
        pdf.multi_cell(0, 5, note_text)
    
    return bytes(pdf.output())

# ==========================================
# 4. STREAMLIT UI
# ==========================================
st.set_page_config(page_title="Urbánek Pro v5.4", layout="wide", page_icon="🛡️")

df_customers = load_customers()

with st.sidebar:
    st.header("🏢 Výběr zákazníka")
    if df_customers is not None:
        search_q = st.text_input("🔍 Hledat (IČO, Název...):", placeholder="např. Domov Máj")
        q_norm = search_q.lower()
        mask = (df_customers["ICO"].astype(str).str.contains(q_norm, na=False) | 
                df_customers["FIRMA"].str.lower().str.contains(q_norm, na=False) |
                df_customers["ADRESA3"].str.lower().str.contains(q_norm, na=False))
        filtered = df_customers[mask]
        
        if not filtered.empty:
            opts = filtered["FIRMA"] + " (" + filtered["ICO"].astype(str) + ")"
            sel = st.selectbox("Potvrďte partnera:", opts, index=0)
            act_idx = filtered.index[opts == sel].tolist()[0]
            local_data = filtered.loc[act_idx].to_dict()
            
            if st.button("🪄 Opravit a uložit data (ARES)"):
                with st.spinner("Aktualizuji registry..."):
                    ares_res = get_company_from_ares(local_data["ICO"])
                    if ares_res:
                        local_data.update(ares_res)
                        st.session_state.vybrany_zakaznik = local_data
                        if update_customer_in_db(local_data):
                            st.success("Data opravena v PDF i v databázi!")
                        else:
                            st.info("Data opravena pro PDF (DB je zamčená).")
                    else:
                        st.error("ARES neodpovídá.")
            
            if st.session_state.vybrany_zakaznik is None or st.session_state.vybrany_zakaznik['ICO'] != local_data['ICO']:
                st.session_state.vybrany_zakaznik = local_data
        else: st.warning("Nenalezeno.")
    else: st.error("⚠️ Databáze data/data.db chybí.")

    st.divider()
    st.header("📝 Detaily zakázky")
    def_klient = st.session_state.vybrany_zakaznik['FIRMA'] if st.session_state.vybrany_zakaznik else "Ruční zadání..."
    klient_pdf = st.text_input("Název na dokumentu:", value=def_klient)
    
    next_num = get_next_order_number()
    source_dl = st.text_input("Číslo zakázky:", value=next_num)
    
    je_svj = st.toggle("Snížená sazba DPH 12% (SVJ)", value=True)
    sazba = 0.12 if je_svj else 0.21

st.title("🛡️ HASIČ-SERVIS URBÁNEK")
st.caption("v5.4 | Trvalá oprava dat (ARES) | Automatické číslování | Moderní PDF")

tabs = st.tabs(["🔥 Hasicí přístroje", "🚰 Požární vodovody", "🛠️ Odborná činnost", "📦 Prodej zboží", "🧾 Souhrn & Export"])

def item_row(category, name, default_q, default_p, key, step_q=1.0):
    c1, c2, c3 = st.columns([3, 1, 1])
    with c1: st.write(f"**{name}**")
    with c2: q = st.number_input(f"Q_{key}", min_value=0.0, step=float(step_q), value=float(default_q), key=f"q_{key}", label_visibility="collapsed")
    with c3: p = st.number_input(f"P_{key}", min_value=0.0, step=0.1, value=float(default_p), key=f"p_{key}", label_visibility="collapsed")
    st.session_state.data_zakazky[name] = {'q': q, 'p': p, 'cat': category}
    return q * p

with tabs[0]:
    st.subheader("1. KONTROLY HASÍCÍCH PŘÍSTROJŮ")
    item_row("HP", "Kontrola HP (shodný)", 0, DEFAULTS["hp_shodny"], "h1")
    item_row("HP", "Kontrola HP (neshodný opravitelný)", 0, DEFAULTS["hp_opravitelny"], "h2")
    item_row("HP", "Kontrola HP (neopravitelný) + odb. zneprovoznění", 0, DEFAULTS["hp_likvidace"], "h3")
    item_row("HP", "Hodinová sazba za provedení prací", 0, DEFAULTS["hp_hod_sazba"], "h5", step_q=0.05)
    item_row("Náhrady", "Označení - vylepení koleček o kontrole (á 2ks / HP)", 0, DEFAULTS["nh_kolecka"], "n2")

with tabs[1]:
    st.subheader("ZAŘÍZENÍ PRO ZÁSOBOVÁNÍ POŽÁRNÍ VODOU")
    item_row("PV", "Měření průtoku á 1 ks vnitřní hydrant. systémů typu D/C", 0, DEFAULTS["pv_mereni_ks"], "v3")
    item_row("PV", "Hod. sazba (pochůzky / manipulace s PV)", 0, DEFAULTS["pv_hodinova_sazba"], "v4", step_q=0.05)
    item_row("PV", "Vyhodnocení kontroly zařízení PV", 0, DEFAULTS["pv_vyhodnoceni"], "v5")
    item_row("PV", "Vyhotovení zprávy o kontrole PBZ", 0, DEFAULTS["pv_zprava"], "v6")

with tabs[2]:
    st.subheader("TECHNICKO-ORGANIZAČNÍ ČINNOST")
    item_row("TOC", "Technicko organizační činnost v PO", 0, DEFAULTS["toc"], "t1")

with tabs[3]:
    st.subheader("PRODEJ MATERIÁLU A ZBOŽÍ")
    item_row("Zboží", "Hasicí přístroj RAIMA P6 (34A, 233B, C)", 0, DEFAULTS["p6"], "p1")
    item_row("Zboží", "Hasicí přístroj V9Ti (voda)", 0, DEFAULTS["v9"], "p2")

with tabs[4]:
    st.subheader("📊 Rekapitulace a Export")
    active_data = {k: v for k, v in st.session_state.data_zakazky.items() if v['q'] > 0}
    if not active_data: st.warning("Zadejte množství v záložkách.")
    else:
        grand_total = sum(v['q'] * v['p'] for v in active_data.values())
        st.write(f"### Rozpis pro: {klient_pdf}")
        cats = sorted(list(set([v['cat'] for v in active_data.values()])))
        structured = {c: [(k, v['q'], v['p']) for k, v in active_data.items() if v['cat'] == c] for c in cats}
        for cat, items in structured.items():
            st.markdown(f"**{cat.upper()}**")
            st.table([{"Položka": i[0], "Ks": f"{i[1]:.2f}".rstrip('0').rstrip('.'), "Celkem": f"{i[1]*i[2]:,.2f} Kč"} for i in items])
        st.divider()
        st.metric("CELKEM BEZ DPH", f"{grand_total:,.2f} Kč")
        if st.button("📄 Vygenerovat a potvrdit PDF"):
            if not st.session_state.vybrany_zakaznik:
                st.error("Vyberte zákazníka.")
            else:
                note = "Poznámka: Kontroly jsou prováděny dle vyhlášky 246/2001 Sb. Zpracováno v systému W-SERVIS."
                pdf_bytes = create_report_pdf(st.session_state.vybrany_zakaznik, structured, grand_total, sazba, f"Rozpis prací k č. {source_dl}", note)
                if pdf_bytes:
                    increment_order_counter()
                    st.download_button(label="⬇️ Stáhnout PDF", data=pdf_bytes, file_name=f"Rozpis_{source_dl.replace('/', '_')}.pdf", mime="application/pdf")

st.divider()
st.caption(f"© {datetime.date.today().year} {FIRMA_VLASTNI['název']} | Tradice od 1994 | Odborný garant: Tomáš Urbánek")
