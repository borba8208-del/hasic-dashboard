import streamlit as st
import datetime
from fpdf import FPDF
import os

# ==========================================
# 1. KONFIGURACE FIRMY A DATA
# ==========================================
FIRMA = {
    "název": "Ilja Urbánek - HASIČ-SERVIS",
    "sídlo": "Poříčská 186, 373 82 Boršov nad Vltavou",
    "ico": "60835265",
    "dic": "CZ5706281691",
    "certifikace": "TÜV NORD Czech",
    "založeno": 1994
}

# Výchozí ceny z vaší předlohy (Větrná 13 a další)
DEFAULTS = {
    "hp_shodny": 29.40,
    "hp_opravitelny": 19.70,
    "hp_likvidace": 23.50,
    "hp_pojezdny_s": 166.60,
    "pv_hydro_pausal": 352.00,
    "pv_mereni_ks": 95.00,
    "pv_hodinova_sazba": 450.00,
    "pv_vyhodnoceni": 153.00,
    "pv_zprava": 170.00,
    "pv_kolecko": 3.50,
    "p6": 1090.00,
    "v9": 1370.00,
    "vesak": 35.00,
    "pojistka": 21.00,
    "samolepka": 8.00,
    "toc": 4488.00
}

if 'data' not in st.session_state:
    st.session_state.data = {}

# ==========================================
# 2. PDF ENGINE (UNICODE A FIX ČEŠTINY)
# ==========================================
class UrbaneKPDF(FPDF):
    def __init__(self):
        super().__init__()
        self.pismo_ok = False
        # V Cloudu musíme použít přesné názvy souborů (Linux je citlivý na malá/velká písmena)
        try:
            # Zkusíme načíst z aktuální složky (GitHub)
            self.add_font("ArialCZ", "", "arial.ttf")
            self.add_font("ArialCZ", "B", "arialbd.ttf")
            self.add_font("ArialCZ", "I", "ariali.ttf")
            self.pismo = "ArialCZ"
            self.pismo_ok = True
        except:
            try:
                # Zkusíme Windows cestu pro lokální ladění
                path = "C:/Windows/Fonts/arial"
                self.add_font("ArialCZ", "", f"{path}.ttf")
                self.add_font("ArialCZ", "B", f"{path}bd.ttf")
                self.add_font("ArialCZ", "I", f"{path}i.ttf")
                self.pismo = "ArialCZ"
                self.pismo_ok = True
            except:
                # Pokud vše selže, fallback na helvetica (ale v cloudu to spadne na češtině)
                self.pismo = "helvetica"
                self.pismo_ok = False

    def header(self):
        if self.pismo_ok:
            self.set_font(self.pismo, 'B', 14)
        else:
            self.set_font('helvetica', 'B', 14)
        
        # Ošetření textu pro případ, že písmo chybí (odstranění diakritiky by byl nouzový plán)
        self.cell(0, 10, FIRMA["název"], ln=True)
        self.set_font(self.pismo, '', 9)
        self.cell(0, 5, f"Specialista na požární bezpečnost | {FIRMA['sídlo']}", ln=True)
        self.line(10, 28, 200, 28)
        self.ln(10)

    def footer(self):
        self.set_y(-15)
        self.set_font(self.pismo, 'I', 8)
        self.cell(0, 10, f"Zpracováno v systému W-SERVIS | Odborná certifikace: {FIRMA['certifikace']} | Strana {self.page_no()}", align='C')

def create_report_pdf(klient, items_dict, total_zaklad, sazba, doc_title, note_text=""):
    pdf = UrbaneKPDF()
    if not pdf.pismo_ok:
        raise Exception("Chyba písma: Soubory arial.ttf, arialbd.ttf nebo ariali.ttf nebyly nalezeny v repozitáři.")
        
    pdf.add_page()
    pdf.set_font(pdf.pismo, "B", 16)
    pdf.cell(0, 10, f"{doc_title}", ln=True)
    pdf.set_font(pdf.pismo, "", 12)
    pdf.cell(0, 10, f"Odběratel: {klient}", ln=True)
    pdf.ln(5)

    # Tabulka
    pdf.set_font(pdf.pismo, "B", 10)
    pdf.set_fill_color(240, 240, 240)
    pdf.cell(100, 8, "Položka / úkon (v souladu s vyhl. 246/2001 Sb.)", border=1, fill=True)
    pdf.cell(15, 8, "Ks", border=1, align='C', fill=True)
    pdf.cell(35, 8, "Cena/ks", border=1, align='R', fill=True)
    pdf.cell(40, 8, "Celkem", border=1, align='R', fill=True)
    pdf.ln()

    pdf.set_font(pdf.pismo, "", 9)
    for name, vals in items_dict.items():
        if vals['q'] > 0:
            pdf.cell(100, 8, name, border=1)
            pdf.cell(15, 8, str(vals['q']), border=1, align='C')
            pdf.cell(35, 8, f"{vals['p']:,.2f} Kč", border=1, align='R')
            pdf.cell(40, 8, f"{vals['q'] * vals['p']:,.2f} Kč", border=1, align='R')
            pdf.ln()

    pdf.ln(10)
    pdf.set_font(pdf.pismo, "B", 12)
    pdf.cell(150, 10, "ZÁKLAD DANĚ CELKEM:", align='R')
    pdf.cell(40, 10, f"{total_zaklad:,.2f} Kč", align='R')
    pdf.ln()
    pdf.set_text_color(200, 0, 0)
    pdf.cell(150, 10, f"CELKEM K ÚHRADĚ (včetně DPH {int(sazba*100)}%):", align='R')
    pdf.cell(40, 10, f"{total_zaklad * (1+sazba):,.2f} Kč", align='R')
    
    if note_text:
        pdf.set_text_color(0, 0, 0)
        pdf.ln(15)
        pdf.set_font(pdf.pismo, "I", 9)
        pdf.multi_cell(0, 5, note_text)
    
    return bytes(pdf.output())

# ==========================================
# 3. STREAMLIT UI
# ==========================================
st.set_page_config(page_title="Urbánek Pro v4.3", layout="wide", page_icon="🛡️")

with st.sidebar:
    st.header("🏢 Hlavička zakázky")
    klient_val = st.text_input("Zákazník", value="Domov pro seniory Máj")
    source_dl = st.text_input("Číslo DL", value="DL-2026-001")
    je_svj = st.toggle("Uplatnit sníženou sazbu DPH 12%", value=True)
    sazba = 0.12 if je_svj else 0.21
    st.divider()
    st.success("Režim: Cloudový asistent (v4.3)")

st.title("🛡️ HASIČ-SERVIS URBÁNEK")
st.caption("Stabilizovaná verze pro terénní provoz | Unicode PDF Fix")

tabs = st.tabs(["🔥 Hasicí přístroje", "🚰 Požární vodovody", "🛠️ Odborná činnost", "📦 Prodej zboží", "🧾 Souhrn & Export"])

def item_row(category, name, default_q, default_p, key):
    c1, c2, c3 = st.columns([3, 1, 1])
    with c1: st.write(f"**{name}**")
    with c2: q = st.number_input(f"Q_{key}", min_value=0.0, step=0.1, value=float(default_q), key=f"q_{key}", label_visibility="collapsed")
    with c3: p = st.number_input(f"P_{key}", min_value=0.0, step=0.1, value=float(default_p), key=f"p_{key}", label_visibility="collapsed")
    st.session_state.data[name] = {'q': q, 'p': p, 'cat': category}
    return q * p

# --- ZÁLOŽKY ---
with tabs[0]:
    st.subheader("1. Hasicí přístroje (Kontroly)")
    item_row("HP", "Kontrola HP (shodný)", 0, DEFAULTS["hp_shodny"], "h1")
    item_row("HP", "Kontrola HP (neshodný - opravitelný)", 0, DEFAULTS["hp_opravitelny"], "h2")
    item_row("HP", "Vyřazení a odborná likvidace HP (stav NV)", 0, DEFAULTS["hp_likvidace"], "h3")
    item_row("HP", "Kontrola pojízdného HP (shodný)", 0, DEFAULTS["hp_pojezdny_s"], "h4")

with tabs[1]:
    st.subheader("2. Zařízení pro zásobování požární vodou")
    item_row("PV", "Kontrola hydrodyn. tlaku a průtoku (paušál)", 0, DEFAULTS["pv_hydro_pausal"], "v1")
    item_row("PV", "Měření průtoku á 1 ks vnitřní hydrant.systémů typu D/C", 0, DEFAULTS["pv_mereni_ks"], "v2")
    item_row("PV", "Hod.sazba (pochůzky po objektu/ manipulace s HP/PV)", 0, DEFAULTS["pv_hodinova_sazba"], "v3")
    item_row("PV", "Vyhodnocení kontroly zařízení (paušál)", 0, DEFAULTS["pv_vyhodnoceni"], "v4")
    item_row("PV", "Vyhotovení zprávy o kontrole zařízení PBZ", 0, DEFAULTS["pv_zprava"], "v5")
    item_row("PV", "Označení - vylepení koleček o kontrole", 0, DEFAULTS["pv_kolecko"], "v6")

with tabs[2]:
    st.subheader("3. Technicko organizační činnost")
    item_row("TOC", "Technicko organizační činnost v PO (školení/dokumentace)", 0, DEFAULTS["toc"], "t1")

with tabs[3]:
    st.subheader("4. Prodej materiálu a zboží")
    item_row("Zboží", "Hasicí přístroj RAIMA P6 (34A, 233B, C)", 0, DEFAULTS["p6"], "p1")
    item_row("Zboží", "Hasicí přístroj V9Ti / V9LEc (voda)", 0, DEFAULTS["v9"], "p2")
    item_row("Zboží", "Věšák Delta W+PG NEURUPPIN", 0, DEFAULTS["vesak"], "p3")
    item_row("Zboží", "Pojistka Če/BETA PG/V/Pe/CO", 0, DEFAULTS["pojistka"], "p4")
    item_row("Zboží", "Informační samolepka", 0, DEFAULTS["samolepka"], "p5")

with tabs[4]:
    st.subheader("📊 Finální rekapitulace a PDF")
    final_items = {k: v for k, v in st.session_state.data.items() if v['q'] > 0}
    
    if not final_items:
        st.warning("Zatím nejsou zadány žádné položky. Vyplňte množství v předchozích záložkách.")
    else:
        grand_total = sum(v['q'] * v['p'] for v in final_items.values())
        st.write(f"### Rozpis pro: {klient_val}")
        st.table([{"Položka": k, "Množství": v['q'], "Celkem": f"{v['q']*v['p']:,.2f} Kč"} for k, v in final_items.items()])
        
        st.divider()
        st.metric("CELKEM BEZ DPH", f"{grand_total:,.2f} Kč")
        
        if st.button("📄 Vygenerovat a stáhnout PDF Rozpis"):
            # Kontrola, zda písmo existuje, dříve než začneme
            try:
                notes = []
                if any(v['cat'] == 'HP' for v in final_items.values()):
                    notes.append("Kontroly HP dle vyhl. 246/2001 Sb. Vyřazené HP byly odborně zneprovozněny.")
                if any(v['cat'] == 'PV' for v in final_items.values()):
                    notes.append("U požárních vodovodů bylo provedeno kapacitní měření certifikovaným zařízením s proměnnou clonou.")
                
                pdf_bytes = create_report_pdf(klient_val, final_items, grand_total, sazba, f"Rozpis prací k {source_dl}", "\n".join(notes))
                st.download_button(label="⬇️ Stáhnout PDF", data=pdf_bytes, file_name=f"Rozpis_{klient_val.replace(' ','_')}.pdf", mime="application/pdf")
            except Exception as e:
                st.error(f"⚠️ {str(e)}")

st.divider()
st.caption(f"© {datetime.date.today().year} {FIRMA['název']} | Future Firma v4.3 | RT: Ilja Urbánek")
