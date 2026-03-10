import streamlit as st
import datetime
from fpdf import FPDF
import socket
import os

# ==========================================
# 1. KONFIGURACE FIRMY A VÝCHOZÍCH CEN
# ==========================================
FIRMA = {
    "název": "Ilja Urbánek - HASIČ-SERVIS",
    "sídlo": "Poříčská 186, 373 82 Boršov nad Vltavou",
    "ico": "60835265",
    "dic": "CZ5706281691",
    "certifikace": "TÜV NORD Czech",
    "založeno": 1994
}

# Pomocná funkce pro získání IP adresy pro mobilní připojení
def get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except:
        return "127.0.0.1"

# Inicializace session state
if 'data' not in st.session_state:
    st.session_state.data = {}

# ==========================================
# 2. PDF ENGINE (PODPORA ČEŠTINY)
# ==========================================
class UrbaneKPDF(FPDF):
    def __init__(self):
        super().__init__()
        f_path = "C:/Windows/Fonts/arial"
        self.pismo = "ArialCZ"
        try:
            self.add_font("ArialCZ", "", f"{f_path}.ttf")
            self.add_font("ArialCZ", "B", f"{f_path}bd.ttf")
            self.add_font("ArialCZ", "I", f"{f_path}i.ttf")
            self.pismo = "ArialCZ"
        except:
            self.pismo = "helvetica"

    def header(self):
        self.set_font(self.pismo, 'B', 14)
        self.cell(0, 10, FIRMA["název"], ln=True)
        self.set_font(self.pismo, '', 9)
        self.cell(0, 5, f"Specialista na požární bezpečnost | {FIRMA['sídlo']}", ln=True)
        self.line(10, 28, 200, 28)
        self.ln(10)

    def footer(self):
        self.set_y(-15)
        self.set_font(self.pismo, 'I', 8)
        self.cell(0, 10, f"Zpracováno v systému W-SERVIS | Certifikace: {FIRMA['certifikace']} | Strana {self.page_no()}", align='C')

def create_report_pdf(klient, items_dict, total_zaklad, sazba, doc_title):
    pdf = UrbaneKPDF()
    pdf.add_page()
    pdf.set_font(pdf.pismo, "B", 16)
    pdf.cell(0, 10, f"{doc_title}", ln=True)
    pdf.set_font(pdf.pismo, "", 12)
    pdf.cell(0, 10, f"Odběratel: {klient}", ln=True)
    pdf.ln(5)

    pdf.set_font(pdf.pismo, "B", 10)
    pdf.set_fill_color(240, 240, 240)
    pdf.cell(100, 8, "Položka / úkon (dle vyhl. 246/2001 Sb.)", border=1, fill=True)
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
    pdf.cell(150, 10, f"CELKEM K ÚHRADĚ VČ. DPH {int(sazba*100)}%:", align='R')
    pdf.cell(40, 10, f"{total_zaklad * (1+sazba):,.2f} Kč", align='R')
    
    return bytes(pdf.output())

# ==========================================
# 3. STREAMLIT UI
# ==========================================
st.set_page_config(page_title="Urbánek Pro v4.1", layout="wide", page_icon="🛡️")

with st.sidebar:
    st.header("🏢 Správa zakázky")
    klient = st.text_input("Zákazník (přesný název)", value="Domov pro seniory Máj")
    source_dl = st.text_input("Zdrojový doklad", value="DL 2026/001")
    je_svj = st.toggle("Uplatnit 12% DPH (SVJ)", value=True)
    sazba = 0.12 if je_svj else 0.21
    
    st.divider()
    st.subheader("📱 Mobilní přístup")
    local_ip = get_local_ip()
    st.write("Pokud jste na stejné Wi-Fi, zadejte v mobilu:")
    st.code(f"http://{local_ip}:8501")
    st.caption("Při práci v terénu doporučuji nasazení na Streamlit Cloud.")
    st.divider()
    st.success("Režim: Plná editace")

st.title("🛡️ HASIČ-SERVIS URBÁNEK")
st.caption("Verze 4.1 - Mobilní asistent | Datový model W-SERVIS")

tabs = st.tabs(["🔥 Hasicí přístroje", "🚰 Vodovody (Větrná)", "📦 Materiál & Zboží", "🛠️ Odborná činnost", "🧾 Souhrn faktury"])

def item_row(category, name, default_q, default_p, key):
    c1, c2, c3 = st.columns([3, 1, 1])
    with c1: st.write(f"**{name}**")
    with c2: q = st.number_input(f"Ks_{key}", min_value=0.0, step=1.0, value=float(default_q), key=f"q_{key}", label_visibility="collapsed")
    with c3: p = st.number_input(f"P_{key}", min_value=0.0, step=0.1, value=float(default_p), key=f"p_{key}", label_visibility="collapsed")
    st.session_state.data[name] = {'q': q, 'p': p, 'cat': category}
    return q * p

# --- ZÁLOŽKY ---
with tabs[0]:
    st.subheader("1. Hasicí přístroje")
    hp_total = 0
    hp_total += item_row("HP", "Kontrola HP (shodný)", 0, 29.40, "hp1")
    hp_total += item_row("HP", "Kontrola HP (neshodný - opravitelný)", 0, 19.70, "hp2")
    hp_total += item_row("HP", "Vyřazení a odborná likvidace HP (stav NV)", 0, 23.50, "hp3")
    hp_total += item_row("HP", "Kontrola pojízdného HP (shodný)", 0, 166.60, "hp4")
    st.metric("Mezisoučet HP", f"{hp_total:,.2f} Kč")

with tabs[1]:
    st.subheader("2. Zařízení pro zásobování požární vodou")
    pv_total = 0
    pv_total += item_row("PV", "Kontrola hydrodyn. tlaku a průtoku (paušál)", 0, 352.00, "pv1")
    pv_total += item_row("PV", "Měření průtoku á 1 ks vnitřní hydrant.systémů typu D/C", 0, 95.00, "pv2")
    pv_total += item_row("PV", "Hod.sazba (pochůzky po objektu/ manipulace s HP/PV)", 0, 450.00, "pv3")
    pv_total += item_row("PV", "Vyhodnocení kontroly zařízení od 11 do 20 ks výtoků", 0, 153.00, "pv4")
    pv_total += item_row("PV", "Vyhotovení zprávy o kontrole zařízení pro zásob.pož.vodou", 0, 170.00, "pv5")
    pv_total += item_row("PV", "Označení - vylepení koleček o kontrole", 0, 3.50, "pv6")
    st.metric("Mezisoučet PV", f"{pv_total:,.2f} Kč")

with tabs[2]:
    st.subheader("3. Prodej zboží a materiálu")
    pr_total = 0
    pr_total += item_row("Zboží", "Hasicí přístroj RAIMA P6 (34A, 233B, C)", 0, 1090.00, "pr1")
    pr_total += item_row("Zboží", "Hasicí přístroj V9Ti / V9LEc (voda)", 0, 1370.00, "pr2")
    pr_total += item_row("Zboží", "Věšák Delta W+PG NEURUPPIN", 0, 35.00, "pr3")
    pr_total += item_row("Zboží", "Pojistka Če/BETA PG/V/Pe/CO", 0, 21.00, "pr4")
    pr_total += item_row("Zboží", "Informační samolepka", 0, 8.00, "pr5")
    st.metric("Mezisoučet Zboží", f"{pr_total:,.2f} Kč")

with tabs[3]:
    st.subheader("4. Technicko organizační činnost")
    toc_total = item_row("TOC", "Technicko organizační činnost v PO (školení/dokumentace)", 0, 4488.00, "toc1")
    st.metric("Mezisoučet TOC", f"{toc_total:,.2f} Kč")

with tabs[4]:
    st.subheader("📊 Rekapitulace dokladu")
    final_items = {k: v for k, v in st.session_state.data.items() if v['q'] > 0}
    
    if not final_items:
        st.warning("Doklad neobsahuje žádné položky.")
    else:
        table_data = []
        grand_total_zaklad = 0
        for name, vals in final_items.items():
            line_total = vals['q'] * vals['p']
            grand_total_zaklad += line_total
            table_data.append({"Položka": name, "Množství": vals['q'], "Cena/ks": f"{vals['p']:,.2f} Kč", "Celkem": f"{line_total:,.2f} Kč"})
        
        st.table(table_data)
        st.divider()
        col_f1, col_f2 = st.columns(2)
        with col_f1:
            st.metric("ZÁKLAD DANĚ", f"{grand_total_zaklad:,.2f} Kč")
            st.metric(f"DPH ({int(sazba*100)}%)", f"{grand_total_zaklad * sazba:,.2f} Kč")
            st.metric("CELKEM K ÚHRADĚ", f"{grand_total_zaklad * (1+sazba):,.2f} Kč")
            
        with col_f2:
            st.write("### 📤 Exporty")
            if st.button("📄 Vygenerovat PDF Rozpis"):
                pdf_bytes = create_report_pdf(klient, final_items, grand_total_zaklad, sazba, f"Rozpis prací k {source_dl}")
                st.download_button(label="⬇️ Stáhnout PDF", data=pdf_bytes, file_name=f"Rozpis_{klient.replace(' ','_')}.pdf", mime="application/pdf")

st.divider()
st.caption(f"© {datetime.date.today().year} {FIRMA['název']} | Future Firma v4.1 |RT: Ilja Urbánek")