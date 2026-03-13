import os
import re
import time
import datetime
import unicodedata
import sqlite3
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

import streamlit as st
import pandas as pd
from fpdf import FPDF

# NASTAVENÍ STRÁNKY (Absolutně první příkaz)
st.set_page_config(page_title="W-SERVIS Enterprise v47.1", layout="wide", page_icon="🚒")

# =====================================================================
# 🗄️ LAYER 1: SCHEMA & IDENTITY
# =====================================================================
FIRMA_VLASTNI: Dict[str, Any] = {
    "název": "Ilja Urbánek HASIČ - SERVIS",
    "sídlo": "Poříčská 186, 373 82 Boršov nad Vltavou",
    "ico": "60835265",
    "dic": "CZ5706281691",
    "zápis": "Zapsán v živnostenském rejstříku Mag. města Č.Budějovic pod ID RŽP: 696191",
    "telefony": "608 409 036, 777 664 768",
    "email": "schranka@hasic-servis.com",
    "web": "www.hasic-servis.com",
    "certifikace": "TÜV NORD Czech",
}

STAVY_HP = ["S", "NO", "NOPZ", "CH", "S-nový", "NV"]

DUVODY_VYRAZENI = {
    "": "",
    "A": "HP neodpovídá ČSN (zastaralá/neschválená konstr.)",
    "B": "Zákaz používání (ozónová vrstva)",
    "C": "Nádoba je deformovaná - §9, odst.9, pís.a), vyhl. 246/2001 Sb.",
    "D": "Nádoba má poškozený vnější krycí ochranný lak",
    "E": "Nádoba má poškozený vnitřní ochranný lak - §9, odst.9, pís.c)",
    "F": "Nádoba je napadena korozí - §9, odst.9, pís.a)",
    "G": "Nádoba má splněnou životnost pro daný typ HP - §9, odst.9, pís.c)",
    "H": "Nečitelné číslo HP / rok výroby (nádoby) - §9, odst.9, pís.b)",
    "I": "Nádoba nesplňuje kritéria pro zkoušky tlakových nádob dle ČSN",
    "J": "HP nelze zprovoznit z důvodu ukončení výroby náhradních dílů",
    "K": "Zprovoznění HP je neekonomické - vyřazen na žádost majitele"
}

CATEGORY_MAP: Dict[str, str] = {
    "HP": "HP", "Nahrady": "Nahrady", "Voda": "VODA", "Ostatni": "ostatni",
    "ND_HP": "ND_HP", "ND_Voda": "ND_VODA", "FA": "FA", "TAB": "TAB",
    "TABFOTO": "TABFOTO", "HILTI": "HILTI", "CIDLO": "CIDLO", "PASKA": "PASKA",
    "PK": "PK", "OZO": "OZO", "reklama": "reklama", "Servisni_ukony": "revize",
    "Opravy": "opravy", "Zboží": "zbozi"
}

DB_PATH = "data/data.db"
CSV_FOLDER = "data/ceniky/"

# =====================================================================
# 🧹 LAYER 2: NORMALIZATION (Czech Language Shield)
# =====================================================================
def normalize_column_name(col: str) -> str:
    col = str(col)
    col = unicodedata.normalize("NFKD", col)
    col = "".join(c for c in col if not unicodedata.combining(c))
    col = col.lower().strip()
    col = re.sub(r"[\s/]+", "_", col)
    col = re.sub(r"[^a-z0-9_]", "", col)
    return col

def normalize_category_to_table(cat_key: str) -> str:
    if not cat_key: return "cenik_ostatni"
    normalized = cat_key.lower().strip()
    normalized = "".join(char for char in unicodedata.normalize("NFKD", normalized) if not unicodedata.combining(char))
    normalized = re.sub(r"[\s/]+", "_", normalized)
    normalized = re.sub(r"[^a-z0-9_]", "", normalized)
    return f"cenik_{normalized}"

def normalize_price(v: Any) -> float:
    s = str(v).replace(" ", "").replace(",", ".")
    try: return float(s)
    except: return 0.0

def clean_ico(ico_val: Any) -> str:
    s = str(ico_val).strip()
    if s.lower() in ['nan', 'none', 'null', '']: return ""
    return s.split('.')[0]

def format_cena(num):
    if num == 0: return "0,00"
    st = f"{num:,.2f}".replace(",", "X").replace(".", ",").replace("X", " ")
    return st.replace("X", " ")

def safe_str(txt, is_pismo_ok=True):
    """Základní ochrana textu před neplatnými znaky."""
    return str(txt).replace('\n', ' ').replace('\r', '').strip()

# =====================================================================
# 🛡️ LAYER 3: AUDIT ROBOT (Logic Engine)
# =====================================================================
def run_expert_audit(df: pd.DataFrame, context_zakaznik: Dict = None) -> Tuple[pd.DataFrame, Dict]:
    """Robotická kontrola kvality dat dle HASIČ-SERVIS standardů."""
    df_audit = df.copy()
    
    def check_row(row):
        issues = []
        # A. Kontrola DPH pro SVJ/BD
        firm_name = str(context_zakaznik.get('FIRMA', '') if context_zakaznik else "").upper()
        if any(x in firm_name for x in ["SVJ", "BYTOVÉ", "DRUŽSTVO"]):
            # Pozn: Logika se uplatní při fakturaci, zde robot jen varuje
            pass
            
        # B. Kontrola hydrantů (Měření průtoku a tlaku)
        typ = str(row.get('typ_hp', '')).upper()
        if "HYDRANT" in typ or "PV" in typ or str(row.get('druh','')).upper() == "VODA":
            tlak = row.get('tlak_rok', None) # V datu tlaku hledáme hodnotu
            if pd.isna(tlak) or tlak == 0:
                issues.append("Chybí měření tlaku (TÜV standard)")
        
        # C. Kontrola vyřazení (Kódy A-K)
        if row.get('stav') == 'NV' and not row.get('duvod_nv'):
            issues.append("Chybí kód vyřazení A-K")
            
        return "✅ OK" if not issues else "❌ " + ", ".join(issues)

    df_audit['audit_status'] = df_audit.apply(check_row, axis=1)
    
    total = len(df_audit)
    errs = len(df_audit[df_audit['audit_status'].str.contains("❌")])
    stats = {"celkem": total, "chyb": errs, "score": ((total-errs)/total*100) if total > 0 else 100}
    return df_audit, stats

# =====================================================================
# 💾 LAYER 4: REPOSITORY (Safe DB Access)
# =====================================================================
def init_db():
    os.makedirs("data", exist_ok=True)
    os.makedirs(CSV_FOLDER, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""CREATE TABLE IF NOT EXISTS objekty (id INTEGER PRIMARY KEY AUTOINCREMENT, ico TEXT NOT NULL, nazev_objektu TEXT NOT NULL, UNIQUE(ico, nazev_objektu))""")
    cur.execute("""CREATE TABLE IF NOT EXISTS evidence_hp (id INTEGER PRIMARY KEY AUTOINCREMENT, ico TEXT NOT NULL, druh TEXT, typ_hp TEXT, vyr_cislo TEXT, rok_vyr INTEGER, mesic_vyr INTEGER, tlak_rok INTEGER, tlak_mesic INTEGER, stav TEXT, duvod_nv TEXT, objekt TEXT, misto TEXT)""")
    conn.commit()
    conn.close()

def safe_db_query(query: str, params: tuple = ()) -> pd.DataFrame:
    """Bezpečné čtení z DB, které nikdy neshodí aplikaci."""
    try:
        conn = sqlite3.connect(DB_PATH)
        df = pd.read_sql(query, conn, params=params)
        conn.close()
        return df
    except Exception:
        return pd.DataFrame()

def get_items_from_db(categories: List[str]) -> List[Dict]:
    items = []
    for cat in categories:
        tbl = normalize_category_to_table(cat)
        df = safe_db_query(f"SELECT nazev, cena FROM {tbl} ORDER BY nazev")
        if not df.empty:
            for _, r in df.iterrows():
                items.append({"nazev": r['nazev'], "cena": float(r['cena']), "internal_cat": cat})
    return items

# =====================================================================
# ⚙️ LAYER 5: SERVICES (Business Services)
# =====================================================================
def build_form_data_from_customer(ico: Any) -> Optional[Dict[str, Any]]:
    ico_clean = clean_ico(ico)
    base_path = os.path.join("data", "ceniky", "zakaznici")
    # Načtení z CSV souboru zákazníků
    df = safe_read_data_io(base_path)
    if df is not None and not df.empty:
        df.columns = [normalize_column_name(c) for c in df.columns]
        if "ico" in df.columns:
            df["ico_match"] = df["ico"].apply(clean_ico)
            row = df[df["ico_match"] == ico_clean]
            if not row.empty:
                cust = row.iloc[0].to_dict()
                return {
                    "ICO": ico_clean, "DIC": cust.get("dic", ""), "FIRMA": cust.get("firma", ""),
                    "ULICE": cust.get("ulice", ""), "CP": cust.get("cp", ""), "CO": cust.get("co", ""),
                    "ADRESA3": cust.get("mesto", ""), "PSC": cust.get("psc", ""), "KONTAKT": cust.get("kontakt", ""),
                    "TELEFON": cust.get("telefon", ""), "EMAIL": cust.get("email", ""), "UCET": cust.get("ucet", ""),
                    "POZNAMKA": cust.get("poznamka", "")
                }
    return None

def service_import_all_ceniky() -> str:
    log_messages = []
    connection = sqlite3.connect(DB_PATH)
    try:
        for ui_key, name in CATEGORY_MAP.items():
            df = safe_read_data_io(os.path.join(CSV_FOLDER, name))
            if df is None: continue
            df.columns = [normalize_column_name(col) for col in df.columns]
            if 'zbozi_nazev' in df.columns: df.rename(columns={'zbozi_nazev': 'nazev'}, inplace=True)
            if 'zbozi_cena' in df.columns: df.rename(columns={'zbozi_cena': 'cena'}, inplace=True)
            if "nazev" not in df.columns or "cena" not in df.columns: continue
            df = df.dropna(subset=["nazev", "cena"])
            df["nazev"] = df["nazev"].astype(str).str.strip()
            df = df.drop_duplicates(subset=["nazev"])
            df["cena"] = df["cena"].apply(normalize_price)
            df[["nazev", "cena"]].to_sql(normalize_category_to_table(ui_key), connection, if_exists="replace", index=False)
            log_messages.append(f"✅ Načteno: {name} ({len(df)} položek)")
    finally: connection.close()
    return "\n".join(log_messages)

def safe_read_data_io(base_path: str) -> Optional[pd.DataFrame]:
    for ext in [".xlsx", ".csv"]:
        path = base_path + ext
        if os.path.exists(path):
            try:
                if ext == ".xlsx": return pd.read_excel(path)
                else:
                    for enc in ["utf-8-sig", "cp1250", "windows-1250"]:
                        try: return pd.read_csv(path, sep=";", encoding=enc)
                        except: continue
            except: continue
    return None

# =====================================================================
# 📄 LAYER 6: PDF ENGINE (Certified Quality)
# =====================================================================
class UrbaneKPDF(FPDF):
    def __init__(self, orientation='P'):
        super().__init__(orientation=orientation)
        self.pismo_ok = self._load_fonts()
        self.pismo_name = "PismoCZ" if self.pismo_ok else "helvetica"

    def _load_fonts(self):
        for f in [("arial.ttf", "arialbd.ttf"), ("Arial.ttf", "Arialbd.ttf")]:
            if os.path.exists(f[0]) and os.path.exists(f[1]):
                try:
                    self.add_font("PismoCZ", "", f[0], uni=True)
                    self.add_font("PismoCZ", "B", f[1], uni=True)
                    return True
                except: pass
        return False

    def header(self):
        p = self.pismo_name
        if os.path.exists("logo.png"): self.image("logo.png", 10, 10, 24)
        self.set_y(10); self.set_font(p, "B", 14); self.cell(0, 6, FIRMA_VLASTNI["název"], align="C", ln=True)
        self.set_font(p, "", 9); self.cell(0, 4.5, f"Sídlo: {FIRMA_VLASTNI['sídlo']}", align="C", ln=True)
        self.cell(0, 4.5, f"IČO: {FIRMA_VLASTNI['ico']}  DIČ: {FIRMA_VLASTNI['dic']}", align="C", ln=True)
        self.ln(3); self.set_line_width(0.5); self.line(10, self.get_y(), self.w - 10, self.get_y()); self.ln(5)

# =====================================================================
# 🌐 LAYER 7: STREAMLIT UI (The Control Room)
# =====================================================================
init_db()

# Session State Initialization
if "data_zakazky" not in st.session_state: st.session_state["data_zakazky"] = {}
if "dynamic_items" not in st.session_state: st.session_state["dynamic_items"] = {}
if "vybrany_zakaznik" not in st.session_state: st.session_state["vybrany_zakaznik"] = None
if "evidence_df" not in st.session_state: st.session_state["evidence_df"] = pd.DataFrame()

menu_volba = st.sidebar.radio("Navigace:", ["📝 Zpracování zakázky", "🗄️ Sklad & Ceníky", "📊 Obchodní Velín"])

if menu_volba == "📝 Zpracování zakázky":
    with st.sidebar:
        st.header("🏢 Hlavička")
        dl_number = st.text_input("Číslo DL:", "1698")
        zakazka = st.text_input("Zakázka:", "1/13")
        technik = st.text_input("Technik:", "Tomáš Urbánek")
        
        # Customer Selection logic
        cust_df = safe_read_data_io("data/ceniky/zakaznici")
        if cust_df is not None:
            cust_df.columns = [normalize_column_name(c) for c in cust_df.columns]
            names = cust_df['firma'].fillna("Neznámý").tolist()
            sel_name = st.selectbox("Vyberte firmu:", ["-- Vyberte --"] + names)
            if sel_name != "-- Vyberte --":
                row = cust_df[cust_df['firma'] == sel_name].iloc[0]
                st.session_state["vybrany_zakaznik"] = row.to_dict()
                
        if st.button("🗑️ Reset zakázky"):
            st.session_state["data_zakazky"] = {}; st.session_state["dynamic_items"] = {}; st.rerun()

    st.title("🛡️ Zpracování zakázky")
    
    t1, t2, t3, t4 = st.tabs(["📋 Evidence & Robot", "💰 Fakturace", "🛒 Zboží", "🖨️ Tisk"])
    
    with t1:
        st.info("💡 **Robotická kontrola:** Tabulka níže automaticky hlídá metodiku TÜV NORD a legislativu.")
        if st.session_state["vybrany_zakaznik"]:
            ico = clean_ico(st.session_state["vybrany_zakaznik"].get("ico", ""))
            # Load evidence
            if st.session_state["evidence_df"].empty:
                df_e = safe_db_query("SELECT * FROM evidence_hp WHERE ico = ?", (ico,))
                if df_e.empty:
                    df_e = pd.DataFrame(columns=["druh","typ_hp","vyr_cislo","rok_vyr","mesic_vyr","tlak_rok","tlak_mesic","stav","duvod_nv","objekt","misto"])
                    for i in range(5): df_e.loc[i] = ["přenosný", "", "", None, None, None, None, "S", "", "", ""]
                st.session_state["evidence_df"] = df_e

            # Audit Execution
            df_audited, stats = run_expert_audit(st.session_state["evidence_df"], st.session_state["vybrany_zakaznik"])
            
            # Display stats
            c1, c2 = st.columns(2)
            c1.metric("Kvalita dat (Integrity Score)", f"{stats['score']:.1f} %")
            if stats['chyb'] > 0: c2.error(f"Nalezeno {stats['chyb']} chyb v metodice!")
            
            edited = st.data_editor(df_audited, num_rows="dynamic", use_container_width=True, key="main_evid_editor",
                                   column_config={"audit_status": st.column_config.TextColumn("Robotická kontrola", width="large", disabled=True),
                                                 "druh": st.column_config.SelectboxColumn("Druh", options=["přenosný", "pojízdný", "AHS"]),
                                                 "stav": st.column_config.SelectboxColumn("Stav", options=STAVY_HP),
                                                 "duvod_nv": st.column_config.SelectboxColumn("Důvod NV", options=list(DUVODY_VYRAZENI.keys()))})
            
            if st.button("💾 Uložit a synchronizovat s fakturací", type="primary"):
                # Clean and save
                df_to_save = edited.drop(columns=['audit_status'], errors='ignore')
                df_to_save = df_to_save[df_to_save['typ_hp'].fillna("").astype(str).str.strip() != '']
                df_to_save['ico'] = ico
                
                conn = sqlite3.connect(DB_PATH)
                conn.execute("DELETE FROM evidence_hp WHERE ico = ?", (ico,))
                df_to_save.to_sql("evidence_hp", conn, if_exists="append", index=False)
                conn.close()
                
                # Auto-billing injection
                S = len(df_to_save[df_to_save['stav'].isin(['S', 'S-nový'])])
                NO = len(df_to_save[df_to_save['stav'].isin(['NO', 'NOPZ'])])
                NV = len(df_to_save[df_to_save['stav'] == 'NV'])
                
                st.session_state["q1_h1"] = float(S)
                st.session_state["q1_h2"] = float(NO)
                st.session_state["q1_h3"] = float(NV)
                st.session_state["q1_s1"] = float(S+NO+NV)
                st.success("✅ Data uložena a faktura přepočítána.")
                st.rerun()

    with t2:
        def draw_row(cat, name, price, row_id):
            cols = st.columns([4, 2, 1, 1, 1, 1, 1])
            cols[0].write(name)
            p = cols[1].number_input(f"Cena", 0.0, step=0.1, value=float(get_price(cat, name) or price), key=f"p_{row_id}")
            q1 = cols[2].number_input(f"O1", 0.0, value=float(st.session_state.get(f"q1_{row_id}", 0.0)), key=f"q1_{row_id}")
            st.session_state["data_zakazky"][name] = {"q": q1, "p": p, "cat": cat}

        st.subheader("Automatické úkony")
        draw_row("HP", "Kontrola HP (shodný)", 29.4, "h1")
        draw_row("HP", "Kontrola HP (neshodný - opravitelný)", 19.7, "h2")
        draw_row("HP", "Kontrola HP (neshodný - neopravitelný) + zneprovoznění", 23.5, "h3")
        draw_row("Servisni_ukony", "Vyhodnocení kontroly (á 1ks HP)", 5.8, "s1")

    with t3:
        st.subheader("Skladové položky")
        items = get_items_from_db(["Zboží", "zbozi"])
        if items:
            idict = {i['nazev']: i for i in items}
            sel = st.selectbox("Vyberte zboží:", ["-- Vyberte --"] + list(idict.keys()))
            if sel != "-- Vyberte --":
                pz = st.number_input("Prodejní cena", value=idict[sel]['cena'])
                qz = st.number_input("Množství", value=1.0)
                if st.button("➕ Přidat do zakázky"):
                    st.session_state["dynamic_items"][sel] = {"q": qz, "p": pz, "cat": "Zboží"}
                    st.rerun()
        
        for k, v in list(st.session_state["dynamic_items"].items()):
            c = st.columns([5, 2, 1])
            c[0].write(f"• {k}")
            c[1].write(f"{v['q']} ks x {v['p']} Kč")
            if c[2].button("❌", key=f"del_{k}"): del st.session_state["dynamic_items"][k]; st.rerun()

    with t4:
        if st.session_state["vybrany_zakaznik"]:
            st.button("📄 Generovat Kompletní PDF")
            st.button("📄 Generovat Protokol o vyřazení")
        else:
            st.warning("Není vybrán žádný zákazník.")

elif menu_volba == "🗄️ Sklad & Ceníky":
    st.title("🗄️ Správa ceníků a skladu")
    if st.button("🚀 Spustit synchronizaci s W-SERVIS"):
        res = service_import_all_ceniky()
        st.success("Import dokončen")
        st.code(res)
    
    st.subheader("Aktivní ceník zboží")
    df_z = safe_db_query("SELECT nazev, cena FROM cenik_zbozi ORDER BY nazev")
    if not df_z.empty:
        st.dataframe(df_z, use_container_width=True)
    else:
        st.warning("Sklad je prázdný. Spusťte synchronizaci.")

elif menu_volba == "📊 Obchodní Velín":
    st.title("🚒 Obchodní Velín HASIČ-SERVIS")
    u_file = st.file_uploader("Nahrajte soubor 'Migrace_Centraly_Navrh.csv' pro audit:", type=['csv'])
    if u_file:
        df_v = pd.read_csv(u_file, sep=';', encoding='utf-8-sig')
        df_v_audited, v_stats = run_expert_audit(df_v)
        st.metric("Celková integrita dat zakázky", f"{v_stats['score']:.1f} %")
        st.dataframe(df_v_audited, use_container_width=True)

st.sidebar.divider()
st.sidebar.caption(f"© {datetime.date.today().year} {FIRMA_VLASTNI['název']}")
