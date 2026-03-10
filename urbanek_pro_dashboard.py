import streamlit as st
import datetime
from fpdf import FPDF
import sqlite3
import pandas as pd
import os

# ==========================================
# 1. KONFIGURACE FIRMY A DATABÁZE
# ==========================================
FIRMA = {
    "název": "Ilja Urbánek - HASIČ-SERVIS",
    "sídlo": "Poříčská 186, 373 82 Boršov nad Vltavou",
    "ico": "60835265",
    "dic": "CZ5706281691",
    "certifikace": "TÜV NORD Czech",
    "založeno": 1994
}

# Výchozí ceny (DNA firmy Urbánek)
DEFAULTS = {
    "hp_shodny": 29.40, "hp_opravitelny": 19.70, "hp_likvidace": 23.50, "hp_pojezdny_s": 166.60,
    "hp_pojezdny_n": 111.60, "hp_hod_sazba": 450.00, "hp_obec_do_90": 44.40, "hp_obec_nad_90": 107.00,
    "hp_zapujcka": 155.00, "hp_montaz": 150.00, "hp_vyhodnoceni": 5.80, "nh_prevzeti": 88.00,
    "nh_kolecka": 3.50, "nh_stitky": 8.00, "nh_km_osobni": 13.80, "nh_km_prives": 16.00,
    "nh_km_nakladni": 15.90, "nh_komunikace": 48.00, "pv_prohlidka": 193.00, "pv_hydro_pausal": 352.00,
    "pv_mereni_ks": 95.00, "pv_hodinova_sazba": 450.00, "pv_vyhodnoceni": 153.00, "pv_zprava": 170.00,
    "pv_kolecko": 3.50, "p6": 1090.00, "v9": 1370.00, "vesak": 35.00, "toc": 4488.00
}

if 'data' not in st.session_state:
    st.session_state.data = {}

# ==========================================
# 2. DATABÁZOVÝ MODUL (SQLITE READ-ONLY)
# ==========================================
def load_customers():
    db_path = "data/data.db"
    if not os.path.exists(db_path):
        return None
    try:
        # Připojení v režimu jen pro čtení přes URI
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        df = pd.read_sql("SELECT * FROM obchpartner", conn)
        conn.close()
        return df
    except Exception as e:
        st.error(f"Chyba při načítání databáze: {e}")
        return None

def search_customers(df, query):
    if not query or df is None:
        return df
    q = query.lower()
    # Vyhledávání napříč sloupci IČO, Název, Adresa, Město
    mask = (
        df["ICO"].astype(str).str.contains(q, case=False, na=False) |
        df["NAZEV"].str.lower().str.contains(q, na=False) |
        df["ADRESA"].str.lower().str.contains(q, na=False) |
        df["MESTO"].str.lower().str.contains(q, na=False)
    )
    return df[mask]

# ==========================================
# 3. PDF ENGINE (UNICODE SUPPORT)
# ==========================================
class UrbaneKPDF(FPDF):
    def __init__(self):
        super().__init__()
        self.pismo_ok = False
        self.pismo_name = "ArialCZ"
        font_variants = {
            "regular": ["arial.ttf", "ARIAL.TTF", "Arial.ttf"],
            "bold": ["arialbd.ttf", "ARIALBD.TTF", "Arialbd.ttf"]
        }
        found = {"regular": None, "bold": None}
        for style, names in font_variants.items():
            for name in names:
                if os.path.exists(name):
                    found[style] = name
                    break
        if not found["regular"]:
            win_path = "C:/Windows/Fonts/"
            for style, names in font_variants.items():
                for name in names:
                    full_p = os.path.join(win_path, name)
                    if os.path.exists(full_p):
                        found[style] = full_p
                        break
        if found["regular"] and found["bold"]:
            try:
                self.add_font(self.pismo_name, "", found["regular"])
                self.add_font(self.pismo_name, "B", found["bold"])
                self.pismo_ok = True
            except: self.pismo_ok = False
        else: self.pismo_ok = False

    def header(self):
        f_style = self.pismo_name if self.pismo_ok else 'helvetica'
        self.set_font(f_style, 'B', 14)
        self.cell(0, 10, FIRMA["název"], ln=True)
        self.set_font(f_style, '', 9)
        self.cell(0, 5, f"Specialista na požární bezpečnost | Tradice od 1994 | {FIRMA['sídlo']}", ln=True)
        self.line(10, 28, 200, 28)
        self.ln(10)

    def footer(self):
        self.set_y(-15)
        f_style = self.pismo_name if self.pismo_ok else 'helvetica'
        self.set_font(f_style, 'I', 8)
        self.cell(0, 10, f"Systém W-SERVIS | Odborná certifikace: {FIRMA['certifikace']} | Strana {self.page_no()}", align='C')

def create_report_pdf(klient, categories, total_zaklad, sazba, doc_title, note_text=""):
    pdf = UrbaneKPDF()
    if not pdf.pismo_ok: return None
    pdf.add_page()
    pdf.set_font(pdf.pismo_name, "B", 16)
    pdf.cell(0, 10, doc_title, ln=True)
    pdf.set_font(pdf.pismo_name, "", 12)
    pdf.cell(0, 10, f"Odběratel: {klient}", ln=True)
    pdf.ln(5)

    for cat_name, items in categories.items():
        active_items = [i for i in items if i[1] > 0]
        if not active_items: continue
        pdf.ln(2)
        pdf.set_font(pdf.pismo_name, "B", 10)
        pdf.set_fill_color(240, 240, 240)
        pdf.cell(0, 8, cat_name.upper(), ln=True, fill=True, border="TB")
        pdf.set_font(pdf.pismo_name, "B", 8)
        pdf.cell(100, 7, "Položka / úkon (dle vyhl. 246/2001 Sb.)", border=1)
        pdf.cell(15, 7, "Ks", border=1, align='C')
        pdf.cell(35, 7, "Cena/ks", border=1, align='R')
        pdf.cell(40, 7, "Celkem", border=1, align='R')
        pdf.ln()
        pdf.set_font(pdf.pismo_name, "", 8)
        for name, qty, price in active_items:
            pdf.cell(100, 7, name, border=1)
            q_disp = f"{qty:,.2f}".rstrip('0').rstrip('.') if qty % 1 != 0 else f"{int(qty)}"
            pdf.cell(15, 7, q_disp, border=1, align='C')
            pdf.cell(35, 7, f"{price:,.2f} Kč", border=1, align='R')
            pdf.cell(40, 7, f"{qty * price:,.2f} Kč", border=1, align='R')
            pdf.ln()

    pdf.ln(10)
    pdf.set_font(pdf.pismo_name, "B", 12)
    pdf.cell(150, 8, "ZÁKLAD DANĚ CELKEM:", align='R')
    pdf.cell(40, 8, f"{total_zaklad:,.2f} Kč", align='R')
    pdf.ln()
    pdf.set_text_color(200, 0, 0)
    pdf.cell(150, 10, f"CELKEM K ÚHRADĚ VČ. DPH {int(sazba*100)}%:", align='R')
    pdf.cell(40, 10, f"{total_zaklad * (1+sazba):,.2f} Kč", align='R')
    pdf.set_text_color(0, 0, 0)
    if note_text:
        pdf.ln(10)
        pdf.set_font(pdf.pismo_name, "I", 9)
        pdf.multi_cell(0, 5, note_text)
    return bytes(pdf.output())

# ==========================================
# 4. STREAMLIT UI
# ==========================================
st.set_page_config(page_title="Urbánek Pro v4.8", layout="wide", page_icon="🛡️")

# Načtení databáze
df_customers = load_customers()

with st.sidebar:
    st.header("🏢 Výběr zákazníka")
    default_klient_name = "Domov pro seniory Máj"
    
    if df_customers is not None:
        search_query = st.text_input("🔍 Hledat (IČO, Název, Adresa):", placeholder="Začněte psát...")
        filtered = search_customers(df_customers, search_query)
        
        if search_query and not filtered.empty:
            options = filtered["NAZEV"] + " (" + filtered["ICO"].astype(str) + ")"
            selected_option = st.selectbox("Vyberte ze seznamu:", options)
            selected_name = selected_option.split(" (")[0]
            selected_data = filtered[filtered["NAZEV"] == selected_name].iloc[0]
            
            st.success(f"Vybráno: {selected_name}")
            st.caption(f"📍 {selected_data['ADRESA']}, {selected_data['MESTO']}")
            default_klient_name = selected_name
        elif search_query:
            st.warning("Zákazník nenalezen.")
    else:
        st.error("⚠️ Databáze data/data.db nebyla nalezena.")
        st.info("Nahrajte data.db do složky data na GitHubu.")

    st.divider()
    st.header("📝 Detaily dokladu")
    klient_val = st.text_input("Odběratel na PDF:", value=default_klient_name)
    source_dl = st.text_input("Číslo DL (Zdroj):", value="1064")
    je_svj = st.toggle("Snížená sazba DPH 12% (SVJ)", value=True)
    sazba = 0.12 if je_svj else 0.21

st.title("🛡️ HASIČ-SERVIS URBÁNEK")
st.caption("Verze 4.8 | Databáze obchpartner.csv | Mobilní asistent")

tabs = st.tabs(["🔥 Hasicí přístroje & Náhrady", "🚰 Požární vodovody", "🛠️ Odborná činnost", "📦 Prodej zboží", "🧾 Souhrn & Export"])

def item_row(category, name, default_q, default_p, key, step_q=1.0):
    c1, c2, c3 = st.columns([3, 1, 1])
    with c1: st.write(f"**{name}**")
    with c2: q = st.number_input(f"Q_{key}", min_value=0.0, step=float(step_q), value=float(default_q), key=f"q_{key}", label_visibility="collapsed")
    with c3: p = st.number_input(f"P_{key}", min_value=0.0, step=0.1, value=float(default_p), key=f"p_{key}", label_visibility="collapsed")
    st.session_state.data[name] = {'q': q, 'p': p, 'cat': category}
    return q * p

# --- ZÁLOŽKY ---
with tabs[0]:
    st.subheader("1. KONTROLY HASÍCÍCH PŘÍSTROJŮ")
    item_row("HP", "Kontrola HP (shodný)", 0, DEFAULTS["hp_shodny"], "h1")
    item_row("HP", "Kontrola HP (neshodný opravitelný)", 0, DEFAULTS["hp_opravitelny"], "h2")
    item_row("HP", "Kontrola HP (neshodný - neopravitelný) + odb. zneprovoznění", 0, DEFAULTS["hp_likvidace"], "h3")
    item_row("HP", "Hodinová sazba za provedení prací", 0, DEFAULTS["hp_hod_sazba"], "h5", step_q=0.05)
    item_row("HP", "Vyhodnocení kontroly + vystavení dokladu (á 1ks HP)", 0, DEFAULTS["hp_vyhodnoceni"], "h10")
    st.divider()
    st.subheader("2. NÁHRADY")
    item_row("Náhrady", "Označení - vylepení koleček o kontrole (á 2ks / HP)", 0, DEFAULTS["nh_kolecka"], "n2")
    item_row("Náhrady", "Náhrada za 1km - osobní servisní vozidlo", 0, DEFAULTS["nh_km_osobni"], "n4", step_q=1.0)

with tabs[1]:
    st.subheader("ZAŘÍZENÍ PRO ZÁSOBOVÁNÍ POŽÁRNÍ VODOU")
    item_row("PV", "Měření průtoku á 1 ks vnitřní hydrant.systémů typu D/C", 0, DEFAULTS["pv_mereni_ks"], "v3")
    item_row("PV", "Hod.sazba (pochůzky po objektu/ manipulace s HP/PV)", 0, DEFAULTS["pv_hodinova_sazba"], "v4", step_q=0.05)
    item_row("PV", "Vyhodnocení kontroly zařízení PV", 0, DEFAULTS["pv_vyhodnoceni"], "v5")
    item_row("PV", "Vyhotovení zprávy o kontrole zařízení PBZ", 0, DEFAULTS["pv_zprava"], "v6")

with tabs[2]:
    st.subheader("TECHNICKO-ORGANIZAČNÍ ČINNOST")
    item_row("TOC", "Technicko organizační činnost v PO (jedn.)", 0, DEFAULTS["toc"], "t1")

with tabs[3]:
    st.subheader("PRODEJ MATERIÁLU A ZBOŽÍ")
    item_row("Zboží", "Hasicí přístroj RAIMA P6 (34A, 233B, C)", 0, DEFAULTS["p6"], "p1")
    item_row("Zboží", "Hasicí přístroj V9Ti / V9LEc (voda)", 0, DEFAULTS["v9"], "p2")

with tabs[4]:
    st.subheader("📊 Rekapitulace a Export")
    active_data = {k: v for k, v in st.session_state.data.items() if v['q'] > 0}
    if not active_data: st.warning("Zadejte množství v záložkách.")
    else:
        grand_total = sum(v['q'] * v['p'] for v in active_data.values())
        st.write(f"### Rozpis pro: {klient_val}")
        cats = sorted(list(set([v['cat'] for v in active_data.values()])))
        structured_data = {c: [(k, v['q'], v['p']) for k, v in active_data.items() if v['cat'] == c] for c in cats}
        for cat, items in structured_data.items():
            st.markdown(f"**{cat.upper()}**")
            st.table([{"Položka": i[0], "Ks": f"{i[1]:.2f}".rstrip('0').rstrip('.'), "Celkem": f"{i[1]*i[2]:,.2f} Kč"} for i in items])
        st.divider()
        st.metric("CELKEM BEZ DPH", f"{grand_total:,.2f} Kč")
        if st.button("📄 Vygenerovat PDF Rozpis"):
            note = "Poznámka: Kontroly jsou prováděny dle vyhlášky 246/2001 Sb. Zpracováno v systému W-SERVIS."
            pdf_bytes = create_report_pdf(klient_val, structured_data, grand_total, sazba, f"Rozpis k dodacímu listu č. {source_dl}", note)
            if pdf_bytes:
                st.download_button(label="⬇️ Stáhnout PDF", data=pdf_bytes, file_name=f"DL_{source_dl}_{klient_val.replace(' ','_')}.pdf", mime="application/pdf")

st.divider()
st.caption(f"© {datetime.date.today().year} {FIRMA['název']} | Future Firma v4.8 | RT: Ilja Urbánek")
