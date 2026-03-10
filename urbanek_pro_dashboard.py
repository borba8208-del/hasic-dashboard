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
# 2. PDF ENGINE (INTELIGENTNÍ DETEKCE PÍSEM)
# ==========================================
class UrbaneKPDF(FPDF):
    def __init__(self):
        super().__init__()
        self.pismo_ok = False
        self.pismo_name = "ArialCZ"
        
        # Seznam variant k prohledání (Cloud Linux je case-sensitive!)
        font_variants = {
            "regular": ["arial.ttf", "ARIAL.TTF", "Arial.ttf"],
            "bold": ["arialbd.ttf", "ARIALBD.TTF", "Arialbd.ttf"],
            "italic": ["ariali.ttf", "ARIALI.TTF", "Ariali.ttf"]
        }
        
        found = {"regular": None, "bold": None, "italic": None}
        
        # 1. Hledání v lokální složce (GitHub)
        for style, names in font_variants.items():
            for name in names:
                if os.path.exists(name):
                    found[style] = name
                    break
        
        # 2. Hledání ve Windows (Fallback pro lokální vývoj)
        if not found["regular"]:
            win_path = "C:/Windows/Fonts/"
            for style, names in font_variants.items():
                for name in names:
                    full_p = os.path.join(win_path, name)
                    if os.path.exists(full_p):
                        found[style] = full_p
                        break

        # Registrace, pokud jsme našli aspoň základ
        if found["regular"] and found["bold"]:
            try:
                self.add_font(self.pismo_name, "", found["regular"])
                self.add_font(self.pismo_name, "B", found["bold"])
                if found["italic"]:
                    self.add_font(self.pismo_name, "I", found["italic"])
                self.pismo_ok = True
            except:
                self.pismo_ok = False
        else:
            self.pismo_ok = False

    def header(self):
        f_style = self.pismo_name if self.pismo_ok else 'helvetica'
        self.set_font(f_style, 'B', 14)
        self.cell(0, 10, FIRMA["název"], ln=True)
        self.set_font(f_style, '', 9)
        self.cell(0, 5, f"Specialista na požární bezpečnost | {FIRMA['sídlo']}", ln=True)
        self.line(10, 28, 200, 28)
        self.ln(10)

    def footer(self):
        self.set_y(-15)
        f_style = self.pismo_name if self.pismo_ok else 'helvetica'
        self.set_font(f_style, 'I', 8)
        self.cell(0, 10, f"Zpracováno v systému W-SERVIS | Odborná certifikace: {FIRMA['certifikace']} | Strana {self.page_no()}", align='C')

def create_report_pdf(klient, items_dict, total_zaklad, sazba, doc_title, note_text=""):
    pdf = UrbaneKPDF()
    if not pdf.pismo_ok:
        st.error("❌ Kritická chyba: TTF soubory nebyly v repozitáři nalezeny. PDF nemůže být vygenerováno s češtinou.")
        return None
        
    pdf.add_page()
    pdf.set_font(pdf.pismo_name, "B", 16)
    pdf.cell(0, 10, f"{doc_title}", ln=True)
    pdf.set_font(pdf.pismo_name, "", 12)
    pdf.cell(0, 10, f"Odběratel: {klient}", ln=True)
    pdf.ln(5)

    # Tabulka
    pdf.set_font(pdf.pismo_name, "B", 10)
    pdf.set_fill_color(240, 240, 240)
    pdf.cell(100, 8, "Položka / úkon (v souladu s vyhl. 246/2001 Sb.)", border=1, fill=True)
    pdf.cell(15, 8, "Ks", border=1, align='C', fill=True)
    pdf.cell(35, 8, "Cena/ks", border=1, align='R', fill=True)
    pdf.cell(40, 8, "Celkem", border=1, align='R', fill=True)
    pdf.ln()

    pdf.set_font(pdf.pismo_name, "", 9)
    for name, vals in items_dict.items():
        if vals['q'] > 0:
            pdf.cell(100, 8, name, border=1)
            q_display = f"{vals['q']:.2f}".rstrip('0').rstrip('.') if vals['q'] % 1 != 0 else f"{int(vals['q'])}"
            pdf.cell(15, 8, q_display, border=1, align='C')
            pdf.cell(35, 8, f"{vals['p']:,.2f} Kč", border=1, align='R')
            pdf.cell(40, 8, f"{vals['q'] * vals['p']:,.2f} Kč", border=1, align='R')
            pdf.ln()

    pdf.ln(10)
    pdf.set_font(pdf.pismo_name, "B", 12)
    pdf.cell(150, 10, "ZÁKLAD DANĚ CELKEM:", align='R')
    pdf.cell(40, 10, f"{total_zaklad:,.2f} Kč", align='R')
    pdf.ln()
    pdf.set_text_color(200, 0, 0)
    pdf.cell(150, 10, f"CELKEM K ÚHRADĚ (vč. DPH {int(sazba*100)}%):", align='R')
    pdf.cell(40, 10, f"{total_zaklad * (1+sazba):,.2f} Kč", align='R')
    
    if note_text:
        pdf.set_text_color(0, 0, 0)
        pdf.ln(15)
        pdf.set_font(pdf.pismo_name, "I", 9)
        pdf.multi_cell(0, 5, note_text)
    
    return bytes(pdf.output())

# ==========================================
# 3. STREAMLIT UI
# ==========================================
st.set_page_config(page_title="Urbánek Pro v4.5", layout="wide", page_icon="🛡️")

# Kontrola písem při startu
pdf_tester = UrbaneKPDF()

with st.sidebar:
    st.header("🏢 Hlavička zakázky")
    klient_val = st.text_input("Zákazník", value="Domov pro seniory Máj")
    source_dl = st.text_input("Číslo DL", value="DL-2026-001")
    je_svj = st.toggle("Uplatnit 12% DPH (SVJ)", value=True)
    sazba = 0.12 if je_svj else 0.21
    st.divider()
    
    # Stav písem v Sidebaru
    if pdf_tester.pismo_ok:
        st.success("✅ Písmo Arial detekováno")
    else:
        st.error("⚠️ Písmo nenalezeno!")
        st.warning("Ujistěte se, že na GitHubu jsou soubory: arial.ttf, arialbd.ttf")

st.title("🛡️ HASIČ-SERVIS URBÁNEK")
st.caption("Verze 4.5 | Inteligentní PDF Font Detector")

tabs = st.tabs(["🔥 Hasicí přístroje", "🚰 Požární vodovody", "🛠️ Odborná činnost", "📦 Prodej zboží", "🧾 Souhrn & Export"])

def item_row(category, name, default_q, default_p, key, step_q=1.0):
    c1, c2, c3 = st.columns([3, 1, 1])
    with c1: st.write(f"**{name}**")
    with c2: 
        q = st.number_input(f"Q_{key}", min_value=0.0, step=float(step_q), value=float(default_q), key=f"q_{key}", label_visibility="collapsed")
    with c3: 
        p = st.number_input(f"P_{key}", min_value=0.0, step=0.1, value=float(default_p), key=f"p_{key}", label_visibility="collapsed")
    st.session_state.data[name] = {'q': q, 'p': p, 'cat': category}
    return q * p

# --- ZÁLOŽKY ---
with tabs[0]:
    st.subheader("1. Hasicí přístroje (Kontroly - krok 1 ks)")
    item_row("HP", "Kontrola HP (shodný)", 0, DEFAULTS["hp_shodny"], "h1", step_q=1.0)
    item_row("HP", "Kontrola HP (neshodný - opravitelný)", 0, DEFAULTS["hp_opravitelny"], "h2", step_q=1.0)
    item_row("HP", "Vyřazení a odborná likvidace HP (stav NV)", 0, DEFAULTS["hp_likvidace"], "h3", step_q=1.0)
    item_row("HP", "Kontrola pojízdného HP (shodný)", 0, DEFAULTS["hp_pojezdny_s"], "h4", step_q=1.0)

with tabs[1]:
    st.subheader("2. Požární vodovody (Větrná 13)")
    item_row("PV", "Kontrola hydrodyn. tlaku a průtoku (paušál)", 0, DEFAULTS["pv_hydro_pausal"], "v1", step_q=1.0)
    item_row("PV", "Měření průtoku á 1 ks vnitřní hydrant.systémů typu D/C", 0, DEFAULTS["pv_mereni_ks"], "v2", step_q=1.0)
    item_row("PV", "Hod.sazba (pochůzky po objektu/ manipulace s HP/PV)", 0, DEFAULTS["pv_hodinova_sazba"], "v3", step_q=0.05)
    item_row("PV", "Vyhodnocení kontroly zařízení (paušál)", 0, DEFAULTS["pv_vyhodnoceni"], "v4", step_q=1.0)
    item_row("PV", "Vyhotovení zprávy o kontrole zařízení PBZ", 0, DEFAULTS["pv_zprava"], "v5", step_q=1.0)
    item_row("PV", "Označení - vylepení koleček o kontrole", 0, DEFAULTS["pv_kolecko"], "v6", step_q=1.0)

with tabs[2]:
    st.subheader("3. Technicko organizační činnost")
    item_row("TOC", "Technicko organizační činnost v PO (školení/dokumentace)", 0, DEFAULTS["toc"], "t1", step_q=1.0)

with tabs[3]:
    st.subheader("4. Prodej materiálu a zboží")
    item_row("Zboží", "Hasicí přístroj RAIMA P6 (34A, 233B, C)", 0, DEFAULTS["p6"], "p1", step_q=1.0)
    item_row("Zboží", "Hasicí přístroj V9Ti / V9LEc (voda)", 0, DEFAULTS["v9"], "p2", step_q=1.0)
    item_row("Zboží", "Věšák Delta W+PG NEURUPPIN", 0, DEFAULTS["vesak"], "p3", step_q=1.0)
    item_row("Zboží", "Pojistka Če/BETA PG/V/Pe/CO", 0, DEFAULTS["pojistka"], "p4", step_q=1.0)
    item_row("Zboží", "Informační samolepka", 0, DEFAULTS["samolepka"], "p5", step_q=1.0)

with tabs[4]:
    st.subheader("📊 Finální rekapitulace")
    final_items = {k: v for k, v in st.session_state.data.items() if v['q'] > 0}
    
    if not final_items:
        st.warning("Zadejte množství v záložkách.")
    else:
        grand_total = sum(v['q'] * v['p'] for v in final_items.values())
        st.write(f"### Rozpis pro: {klient_val}")
        
        table_view = []
        for k, v in final_items.items():
            q_formated = f"{v['q']:.2f}".rstrip('0').rstrip('.') if v['q'] % 1 != 0 else f"{int(v['q'])}"
            table_view.append({
                "Položka": k,
                "Množství": q_formated,
                "Jednotková cena": f"{v['p']:,.2f} Kč",
                "Celkem bez DPH": f"{v['q']*v['p']:,.2f} Kč"
            })
        
        st.table(table_view)
        st.divider()
        st.metric("CELKEM BEZ DPH", f"{grand_total:,.2f} Kč")
        
        if st.button("📄 Vygenerovat a stáhnout PDF Rozpis"):
            if not pdf_tester.pismo_ok:
                st.error("Nelze generovat PDF: Chybí soubory písem v repozitáři.")
            else:
                try:
                    notes = []
                    if any(v['cat'] == 'HP' for v in final_items.values()):
                        notes.append("Kontroly HP dle vyhl. 246/2001 Sb. Vyřazené HP byly odborně zneprovozněny.")
                    if any(v['cat'] == 'PV' for v in final_items.values()):
                        notes.append("U požárních vodovodů bylo provedeno kapacitní měření certifikovaným zařízením s proměnnou clonou.")
                    
                    pdf_bytes = create_report_pdf(klient_val, final_items, grand_total, sazba, f"Rozpis prací k {source_dl}", "\n".join(notes))
                    if pdf_bytes:
                        st.download_button(label="⬇️ Stáhnout PDF", data=pdf_bytes, file_name=f"Rozpis_{klient_val.replace(' ','_')}.pdf", mime="application/pdf")
                except Exception as e:
                    st.error(f"⚠️ {str(e)}")

st.divider()
st.caption(f"© {datetime.date.today().year} {FIRMA['název']} | Future Firma v4.5 | RT: Ilja Urbánek")
