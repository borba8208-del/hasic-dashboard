⭐ 1) Logo firmy v PDF (vlevo nahoře)
Do třídy UrbaneKPDF přidej do header() něco jako:

python
def header(self):
    # Logo (pokud existuje soubor logo.png v aktuální složce)
    if os.path.exists("logo.png"):
        self.image("logo.png", x=10, y=8, w=20)  # šířka cca 20 mm
        x_start = 35
    else:
        x_start = 10

    self.set_xy(x_start, 10)
    self.set_font(self.pismo_name, 'B', 14)
    self.cell(0, 7, FIRMA_VLASTNI["název"], ln=True)
    self.set_font(self.pismo_name, '', 9)
    self.cell(0, 5, f"Specialista na požární bezpečnost | Tradice od 1994 | {FIRMA_VLASTNI['sídlo']}", ln=True)
    self.line(10, 28, 200, 28)
    self.ln(10)
Stačí uložit logo jako logo.png vedle skriptu.

⭐ 3) Automatické číslování zakázek
Vytvoř soubor counter.py:

python
import json
import os
from datetime import date

COUNTER_FILE = "counter.json"

def load_counter():
    if not os.path.exists(COUNTER_FILE):
        return {}
    with open(COUNTER_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def save_counter(data):
    with open(COUNTER_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def next_order_number():
    data = load_counter()
    year = str(date.today().year)
    current = data.get(year, 0) + 1
    data[year] = current
    save_counter(data)
    return f"{year}/{current:03d}"
V main:

python
from counter import next_order_number

# v sidebaru místo pevného "2026/001":
default_zakazka = next_order_number()
source_dl = st.text_input("Číslo DL / Zakázky:", value=default_zakazka)
⭐ 4) Uložení opravených ARES dat zpět do SQLite
Po úspěšném ARES dotazu:

python
if ares_res:
    local_data.update(ares_res)
    st.session_state.vybrany_zakaznik = local_data

    # Uložit zpět do SQLite
    try:
        conn = sqlite3.connect("data/data.db")
        cur = conn.cursor()
        cur.execute("""
            UPDATE obchpartner
            SET FIRMA = ?, ADRESA1 = ?, ADRESA2 = ?, ADRESA3 = ?, PSC = ?, DIC = ?
            WHERE ICO = ?
        """, (
            local_data.get("FIRMA", ""),
            local_data.get("ULICE", ""),
            local_data.get("CP", ""),
            local_data.get("ADRESA3", ""),
            local_data.get("PSC", ""),
            local_data.get("DIC", ""),
            local_data.get("ICO", "")
        ))
        conn.commit()
        conn.close()
    except Exception as e:
        st.warning(f"Data opravena, ale nepodařilo se je uložit do DB: {e}")
Tím se ARES oprava stane trvalou.

⭐ 5) Profesionální tabulkový layout v PDF
V create_report_pdf můžeš tabulky trochu „odlehčit“:

tenčí rámečky

menší vertikální mezery

lepší zarovnání

Například:

python
pdf.set_line_width(0.2)

pdf.set_font(pdf.pismo_name, "B", 8)
pdf.cell(100, 6, "Položka / úkon (v souladu s vyhl. 246/2001 Sb.)", border=1)
pdf.cell(15, 6, "Ks", border=1, align='C')
pdf.cell(35, 6, "Cena/ks", border=1, align='R')
pdf.cell(40, 6, "Celkem", border=1, align='R')
pdf.ln()

pdf.set_font(pdf.pismo_name, "", 8)
for name, qty, price in active_items:
    pdf.cell(100, 6, name, border="LR")
    q_disp = f"{qty:,.2f}".rstrip('0').rstrip('.') if qty % 1 != 0 else f"{int(qty)}"
    pdf.cell(15, 6, q_disp, border="LR", align='C')
    pdf.cell(35, 6, f"{price:,.2f} Kč", border="LR", align='R')
    pdf.cell(40, 6, f"{qty * price:,.2f} Kč", border="LR", align='R')
    pdf.ln()
# spodní linka tabulky
pdf.cell(190, 0, "", border="T")
pdf.ln(2)
Tím dostaneš jemnější, „účtárenský“ vzhled.

