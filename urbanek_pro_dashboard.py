import os
import re
import time
import unicodedata
import sqlite3
import urllib.request
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import streamlit as st
import pandas as pd
from fpdf import FPDF

# =====================================================================
# 🚀 LAYER 1: GLOBÁLNÍ IDENTITA & KONFIGURACE (Prevence NameError)
# =====================================================================
FIRMA_VLASTNI = {
    "název": "Ilja Urbánek HASIČ - SERVIS",
    "založeno": "1994",
    "sídlo": "Poříčská 186, 373 82 Boršov nad Vltavou",
    "ico": "60835265",
    "dic": "CZ5706281691",
    "zápis": "Zapsán v živnostenském rejstříku pod ID RŽP: 696191",
    "telefony": "608 409 036, 777 664 768",
    "email": "schranka@hasic-servis.com",
    "web": "www.hasic-servis.com",
    "certifikace": "TÜV NORD Czech",
    "podil": "50/50 Partnerství (Tomáš & Ilja)"
}

STAVY_HP = ["S", "NO", "NOPZ", "CH", "S-nový", "NV"]
DUVODY_VYRAZENI = {
    "": "", "A": "HP neodpovídá ČSN", "B": "Zákaz používání (ozón)", 
    "C": "Deformace nádoby", "D": "Poškozený lak vnější", "E": "Poškozený lak vnitřní", 
    "F": "Koroze", "G": "Životnost", "H": "Nečitelné číslo", 
    "I": "Nesplňuje tlak. zkoušky", "J": "Ukončení výroby ND", "K": "Neekonomické (na žádost)"
}

# Inteligentní identifikátory pro hloubkový sken souborů v podsložkách Gitu
FILE_IDENTIFIERS = {
    "HP": ["hp", "kontrol", "cenikhp"],
    "Nahrady": ["nahrady", "cestov", "km", "nahr"],
    "Voda": ["voda", "pv", "hydrant", "vodov"],
    "ND_HP": ["nd_hp", "dily", "ndhp"],
    "Opravy": ["opravy", "servis", "opr_hp"],
    "Zboží": ["zbozi", "prodej", "sklad"]
}

DB_PATH = "data/data.db"
BASE_DATA_DIR = "data/"

# =====================================================================
# 🧹 LAYER 2: NORMALIZAČNÍ LOGIKA (Safe Strings & Numbers)
# =====================================================================
def normalize_column_name(col: str) -> str:
    col = str(col)
    # Odstranění české diakritiky pro vnitřní názvy sloupců v DB
    col = unicodedata.normalize("NFKD", col).encode("ascii", "ignore").decode("ascii")
    col = col.lower().strip()
    col = re.sub(r"[\s/]+", "_", col)
    col = re.sub(r"[^a-z0-9_]", "", col)
    return col

def normalize_category_to_table(cat_key: str) -> str:
    return f"cenik_{normalize_column_name(cat_key)}"

def normalize_price(v: Any) -> float:
    if pd.isna(v): return 0.0
    s = str(v).replace(" ", "").replace(",", ".").replace("\xa0", "")
    try: return float(re.sub(r"[^-0-9.]", "", s))
    except: return 0.0

def clean_ico(ico_val: Any) -> str:
    s = str(ico_val).strip()
    if s.lower() in ['nan', 'none', 'null', '']: return ""
    return s.split('.')[0] # Odstraní případné .0 z Excelu

def format_cena(num):
    if num == 0: return "0,00"
    return f"{num:,.2f}".replace(",", "X").replace(".", ",").replace("X", " ")

# =====================================================================
# 🗄️ LAYER 3: REPOSITORY (Databázová pevnost)
# =====================================================================
def init_db():
    if not os.path.exists("data"): os.makedirs("data")
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    # Pevně definovaná schémata (prevence KeyError při vykreslování)
    cur.execute("CREATE TABLE IF NOT EXISTS objekty (id INTEGER PRIMARY KEY AUTOINCREMENT, ico TEXT NOT NULL, nazev_objektu TEXT NOT NULL, UNIQUE(ico, nazev_objektu))")
    cur.execute("CREATE TABLE IF NOT EXISTS evidence_hp (id INTEGER PRIMARY KEY AUTOINCREMENT, ico TEXT NOT NULL, druh TEXT, typ_hp TEXT, vyr_cislo TEXT, rok_vyr TEXT, mesic_vyr TEXT, tlak_rok TEXT, tlak_mesic TEXT, stav TEXT, duvod_nv TEXT, objekt TEXT, misto TEXT)")
    cur.execute("CREATE TABLE IF NOT EXISTS obchpartner (ico TEXT PRIMARY KEY, dic TEXT, firma TEXT, ulice TEXT, cp TEXT, mesto TEXT, psc TEXT)")
    conn.commit()
    conn.close()

def safe_db_query(query: str, params: tuple = ()) -> pd.DataFrame:
    """Robotická pojistka: Místo pádu vrátí prázdný DataFrame s výchozími sloupci."""
    try:
        conn = sqlite3.connect(DB_PATH)
        df = pd.read_sql(query, conn, params=params)
        conn.close()
        # Pokud tabulka existuje ale chybí sloupce, přidáme je pro stabilitu UI
        if "obchpartner" in query.lower():
            if "firma" not in df.columns: df["firma"] = None
            if "ico" not in df.columns: df["ico"] = None
        return df
    except:
        return pd.DataFrame()

def get_price(cat_key: str, item_name: str) -> float:
    table = normalize_category_to_table(cat_key)
    try:
        conn = sqlite3.connect(DB_PATH)
        res = conn.execute(f"SELECT cena FROM {table} WHERE nazev = ? LIMIT 1", (item_name.strip(),)).fetchone()
        conn.close()
        return float(res[0]) if res else 0.0
    except: return 0.0

# =====================================================================
# 🛡️ LAYER 4: AUDITNÍ ROBOT (Metodika TÜV NORD)
# =====================================================================
def run_expert_audit(df: pd.DataFrame, context_zakaznik: Dict = None) -> Tuple[pd.DataFrame, Dict]:
    df_audit = df.copy()
    if df_audit.empty: return df_audit, {"chyb": 0, "celkem": 0, "score": 100.0}
    
    def check_row(row):
        issues = []
        typ = str(row.get('typ_hp', '')).upper()
        # Audit Hydranty (Metodika TÜV NORD - měření tlaku speciálním zařízením)
        if any(x in typ for x in ["HYDRANT", "PV", "VODA"]):
            tlak = row.get('tlak_rok', 0)
            if not tlak or tlak == 0 or str(tlak) == "None":
                issues.append("Chybí měření tlaku spec. zařízením")
        # Audit Vyřazení (Legislativa - doložka o odpadech)
        if row.get('stav') == 'NV' and not row.get('duvod_nv'):
            issues.append("Chybí legislativní kód A-K")
        return "✅ OK" if not issues else "❌ " + ", ".join(issues)
    
    df_audit['robot_status'] = df_audit.apply(check_row, axis=1)
    errs = len(df_audit[df_audit['robot_status'].str.contains("❌")])
    total = len(df_audit)
    return df_audit, {"chyb": errs, "celkem": total, "score": ((total-errs)/total*100) if total > 0 else 100.0}

# =====================================================================
# ⚙️ LAYER 5: SERVICES (Vylepšený Rekurzivní Import)
# =====================================================================
def find_all_files_recursive(root_dir: str) -> List[str]:
    file_list = []
    if not os.path.exists(root_dir): return []
    for root, _, files in os.walk(root_dir):
        for file in files:
            if file.lower().endswith((".xlsx", ".csv")):
                file_list.append(os.path.join(root, file))
    return file_list

def safe_load_file(path: str) -> Optional[pd.DataFrame]:
    try:
        if path.lower().endswith(".xlsx"): return pd.read_excel(path)
        else:
            # Zkusíme nejčastější česká kódování z Windows Excelu
            for enc in ["utf-8-sig", "cp1250", "windows-1250", "utf-8"]:
                try: return pd.read_csv(path, sep=";", encoding=enc)
                except: continue
    except: return None
    return None

def service_import_data():
    """ROBUSTNÍ REKURZIVNÍ IMPORTÉR - Chrání integritu a validuje sloupce."""
    init_db()
    conn = sqlite3.connect(DB_PATH)
    logs = []
    all_files = find_all_files_recursive(BASE_DATA_DIR)
    
    if not all_files:
        return "⚠️ Robot nenašel v adresáři 'data/' žádné soubory (.xlsx, .csv). Zkontrolujte Git."

    for file_path in all_files:
        fname = os.path.basename(file_path).lower()
        df = safe_load_file(file_path)
        
        if df is None or df.empty:
            logs.append(f"⚠️ {fname}: Soubor je prázdný nebo nečitelný – přeskočeno.")
            continue
            
        df.columns = [normalize_column_name(c) for c in df.columns]

        # ------------------------------------------------------------
        # 1) OBCHPARTNER – ZÁKAZNÍCI (Priorita)
        # ------------------------------------------------------------
        if "obchpartner" in fname or "zakaznik" in fname or "partner" in fname:
            # Inteligentní hledání sloupců pro firmu a ico
            f_col = next((c for c in df.columns if any(x in c for x in ["firma", "nazev", "partner", "odberatel", "klient"])), None)
            i_col = next((c for c in df.columns if any(x in c for x in ["ico", "ic", "identifikacni"])), None)

            if not f_col or not i_col:
                logs.append(f"⚠️ {fname}: Nenalezeny sloupce firma/ico – přeskočeno.")
                continue

            df_cl = pd.DataFrame()
            df_cl["firma"] = df[f_col].astype(str).str.strip()
            df_cl["ico"] = df[i_col].apply(clean_ico)
            
            # Mapování doplňkových polí
            for target in ["dic", "ulice", "mesto", "psc"]:
                match = next((c for c in df.columns if target in c), None)
                if match: df_cl[target] = df[match]

            # Validace integrity (neukládáme prázdné řádky)
            df_cl = df_cl[df_cl["firma"] != ""]
            df_cl = df_cl[df_cl["ico"] != ""]

            if df_cl.empty:
                logs.append(f"⚠️ {fname}: Po validaci prázdné – přeskočeno.")
                continue

            df_cl.to_sql("obchpartner", conn, if_exists="replace", index=False)
            logs.append(f"🏢 Partneři načteni z: {fname} (Složka: {os.path.dirname(file_path)})")
            continue

        # ------------------------------------------------------------
        # 2) SKLAD / ZBOŽÍ (expimp)
        # ------------------------------------------------------------
        if any(x in fname for x in ["expimp", "sklad", "zbozi"]):
            n_col = next((c for c in df.columns if any(x in c for x in ["nazev", "polozka", "zkratka"])), None)
            p_col = next((c for c in df.columns if any(x in c for x in ["cena", "prodejni", "zaklad"])), None)

            if not n_col:
                logs.append(f"⚠️ {fname}: Nenalezen sloupec názvu položky – přeskočeno.")
                continue

            df_cl = pd.DataFrame()
            df_cl["nazev"] = df[n_col].astype(str).str.strip()
            df_cl["cena"] = df[p_col].apply(normalize_price) if p_col else 0.0

            df_cl = df_cl.dropna(subset=["nazev"]).drop_duplicates("nazev")

            if df_cl.empty:
                logs.append(f"⚠️ {fname}: Sklad po validaci prázdný – přeskočeno.")
                continue

            df_cl.to_sql("cenik_zbozi", conn, if_exists="replace", index=False)
            logs.append(f"📦 Sklad synchronizován z: {fname}")
            continue

        # ------------------------------------------------------------
        # 3) CENÍKY ÚKONŮ (HP, Voda, atd.)
        # ------------------------------------------------------------
        for cat, keywords in FILE_IDENTIFIERS.items():
            if any(kw in fname for kw in keywords):
                n_col = next((c for c in df.columns if any(x in c for x in ["nazev", "popis", "polozka"])), None)
                p_col = next((c for c in df.columns if any(x in c for x in ["cena", "castka"])), None)

                if not n_col or not p_col:
                    continue

                df_cl = pd.DataFrame()
                df_cl["nazev"] = df[n_col].astype(str).str.strip()
                df_cl["cena"] = df[p_col].apply(normalize_price)
                df_cl = df_cl[df_cl["nazev"] != ""]

                if not df_cl.empty:
                    df_cl.to_sql(normalize_category_to_table(cat), conn, if_exists="replace", index=False)
                    logs.append(f"✅ Ceník {cat} načten z: {fname}")
                break

    conn.close()
    return "\n".join(logs)

# =====================================================================
# 🌐 LAYER 6: SESSION & UI (SIDEBAR)
# =====================================================================
init_db()

def ensure_session():
    defaults = {
        "data_zakazky": {}, "dynamic_items": {}, "vybrany_zakaznik": None, 
        "evidence_df": pd.DataFrame(), "velin_data": pd.DataFrame(), "sync_report": ""
    }
    for k, v in defaults.items():
        if k not in st.session_state: st.session_state[k] = v

ensure_session()

# DESIGN: Sidebar je mozkem navigace
with st.sidebar:
    st.markdown(f"### 🚒 {FIRMA_VLASTNI['název']}")
    st.caption(f"Enterprise Terminal v59.0 | Společníci: 50/50")
    st.divider()
    
    # 🏢 VÝBĚR ZÁKAZNÍKA (Ochrana proti KeyError: firma)
    st.subheader("🏢 Aktivní zákazník")
    df_p = safe_db_query("SELECT firma, ico FROM obchpartner ORDER BY firma")
    
    if df_p.empty:
        st.warning("⚠️ Databáze partnerů je prázdná!")
        if st.button("🚀 SPUSTIT REKURZIVNÍ SYNCHRONIZACI", type="primary", use_container_width=True):
            st.session_state["sync_report"] = service_import_data()
            st.rerun()
    else:
        # Pevná tvorba seznamu možností (Failsafe)
        opts = ["-- Vyberte firmu --"]
        for _, r in df_p.iterrows():
            opts.append(f"{str(r.get('firma', 'Neznámý'))} | {str(r.get('ico', ''))}")
            
        vz = st.session_state.get("vybrany_zakaznik")
        curr_idx = 0
        if vz:
            try:
                search_str = f"{vz.get('firma','')} | {vz.get('ico','')}"
                if search_str in opts: curr_idx = opts.index(search_str)
            except: curr_idx = 0
            
        sel_p = st.selectbox("Pracovat na zakázce pro:", opts, index=curr_idx)
        if sel_p != "-- Vyberte firmu --":
            ico_sel = sel_p.split(" | ")[1].strip()
            if not vz or vz.get("ico") != ico_sel:
                partner_row = safe_db_query("SELECT * FROM obchpartner WHERE ico = ?", (ico_sel,))
                if not partner_row.empty:
                    st.session_state["vybrany_zakaznik"] = partner_row.iloc[0].to_dict()
                    st.session_state["evidence_df"] = pd.DataFrame() # Reset evidence pro novou firmu
                    st.rerun()

    st.divider()
    menu = st.radio("Sekce centrály:", ["📝 Evidence & Kontroly", "🗄️ Katalog & Sklad", "📊 Obchodní Velín"])
    st.divider()
    
    # Fakturační součet v Sidebaru
    tp = sum(v.get("q",0)*v.get("p",0) for v in st.session_state["data_zakazky"].values()) + sum(v.get("q",0)*v.get("p",0) for v in st.session_state["dynamic_items"].values())
    st.markdown(f"<div style='background:#f8f9fa;padding:10px;border-radius:8px;border-left:5px solid #ff4b4b'><b>🛒 Fakturace celkem:</b><br/>{format_cena(tp)} Kč</div>", unsafe_allow_html=True)
    if st.button("🗑️ Reset zakázky", use_container_width=True):
        st.session_state["data_zakazky"] = {}; st.session_state["dynamic_items"] = {}; st.session_state["evidence_df"] = pd.DataFrame(); st.rerun()

# =====================================================================
# 📝 PAGE: EVIDENCE & KONTROLY
# =====================================================================
if menu == "📝 Evidence & Kontroly":
    st.title("🛡️ Kontrola provozuschopnosti HP a PV")
    st.caption("Pravidla: Žádné slovo 'revize'. U hydrantů měření tlaku spec. zařízením.")
    
    tabs = st.tabs(["📋 1. Evidence kontrol", "💰 2. Fakturace", "🖨️ 3. Tisk"])
    
    with tabs[0]:
        if not st.session_state["vybrany_zakaznik"]:
            st.info("👈 Pro začátek práce vyberte zákazníka v levém panelu.")
        else:
            vz = st.session_state["vybrany_zakaznik"]
            st.subheader(f"Evidence pro: {vz.get('firma')}")
            
            if st.session_state["evidence_df"].empty:
                df_e = safe_db_query("SELECT * FROM evidence_hp WHERE ico = ?", (vz.get('ico'),))
                if df_e.empty:
                    df_e = pd.DataFrame(columns=["druh","typ_hp","vyr_cislo","rok_vyr","mesic_vyr","tlak_rok","tlak_mesic","stav","duvod_nv","objekt","misto"])
                    for i in range(5): df_e.loc[i] = ["přenosný", "", "", None, None, None, None, "S", "", "", ""]
                st.session_state["evidence_df"] = df_e

            df_aud, v_stats = run_expert_audit(st.session_state["evidence_df"], vz)
            if v_stats['chyb'] > 0: st.error(f"⚠️ Robot: Nalezeno {v_stats['chyb']} nesouladů s metodikou TÜV NORD.")
            else: st.success("✅ Robot: Data jsou legislativně v pořádku.")

            edited = st.data_editor(df_aud, num_rows="dynamic", use_container_width=1, key="main_editor",
                                   column_config={"robot_status": st.column_config.TextColumn("Robotická kontrola", disabled=True),
                                                 "stav": st.column_config.SelectboxColumn("Stav", options=STAVY_HP),
                                                 "duvod_nv": st.column_config.SelectboxColumn("Důvod NV", options=list(DUVODY_VYRAZENI.keys()))})
            
            if st.button("💾 Uložit a přepočítat do faktury", type="primary"):
                st.session_state["evidence_df"] = edited
                # Robotické napočítání faktury
                df_c = edited[edited['typ_hp'].fillna("") != ""]
                s_c = len(df_c[df_c['stav'].isin(['S', 'S-nový'])]); no_c = len(df_c[df_c['stav'].isin(['NO', 'NOPZ'])]); nv_c = len(df_c[df_c['stav'] == 'NV'])
                st.session_state["q1_h1"] = float(s_c); st.session_state["q1_h2"] = float(no_c); st.session_state["q1_h3"] = float(nv_c); st.session_state["q1_s1"] = float(s_c+no_c+nv_c)
                st.success("✅ Data uložena a faktura zaktualizována."); st.rerun()

    with tabs[1]:
        st.subheader("💰 Položky pro Dodací list")
        def draw_billing(name, price, row_id):
            cols = st.columns([4, 2, 1, 1, 1, 1, 1])
            cols[0].write(f"**{name}**")
            p = cols[1].number_input("Kč", 0.0, value=float(price), key=f"p_{row_id}")
            q1 = cols[2].number_input("O1", 0.0, value=float(st.session_state.get(f"q1_{row_id}", 0.0)), key=f"q1_{row_id}")
            st.session_state["data_zakazky"][name] = {"q": q1, "p": p}
        
        draw_billing("Kontrola HP (shodný)", 29.4, "h1")
        draw_billing("Kontrola HP (neshodný - opravitelný)", 19.7, "h2")
        draw_billing("Kontrola HP (neopravitelný) + zneprovoznění", 23.5, "h3")
        draw_billing("Vyhodnocení kontroly (á 1ks HP)", 5.8, "s1")

# =====================================================================
# 🗄️ PAGE: KATALOG & SKLAD
# =====================================================================
elif menu == "🗄️ Katalog & Sklad":
    st.title("🗄️ Správa databáze a skladu")
    t1, t2 = st.tabs(["📦 Pohled do DB", "⚙️ Synchronizace (Hloubkový sken Gitu)"])
    
    with t1:
        tbl = st.selectbox("Zobrazit tabulku:", ["obchpartner (Zákazníci)", "cenik_hp (Kontroly)", "cenik_zbozi (Sklad)"])
        actual_table = tbl.split(" ")[0]
        df_v = safe_db_query(f"SELECT * FROM {actual_table}")
        if df_v.empty: st.warning("⚠️ Tabulka je prázdná. Proveďte Synchronizaci v druhé záložce.")
        st.dataframe(df_v, use_container_width=1)
        
    with t2:
        st.info("💡 Synchronizace hloubkově prohledá celý adresář 'data' a spojí nalezené Excely/CSV.")
        if st.button("🚀 SPUSTIT KOMPLETNÍ REKURZIVNÍ SYNCHRONIZACI", type="primary"):
            st.session_state["sync_report"] = service_import_data()
            st.success("Synchronizace dokončena."); st.code(st.session_state["sync_report"])
            st.rerun()

# =====================================================================
# 📊 PAGE: OBCHODNÍ VELÍN
# =====================================================================
elif menu == "📊 Obchodní Velín":
    st.title("📊 Obchodní Velín (Audit & Vyrovnání)")
    st.markdown("### Model pro spravedlivé dělení 50:50")
    
    up_f = st.file_uploader("Nahrajte CSV pro audit (Migrace_Centraly_Navrh.csv):", type=['csv'])
    if up_f:
        try: st.session_state["velin_data"] = pd.read_csv(up_f, sep=';', encoding='utf-8-sig')
        except: st.error("Chyba čtení CSV.")
    
    if not st.session_state["velin_data"].empty:
        df_v, v_stats = run_expert_audit(st.session_state["velin_data"])
        
        c1, c2, c3 = st.columns(3)
        c1.metric("Integrita dat", f"{v_stats.get('score',0):.1f} %")
        c2.metric("Úkony celkem", v_stats.get("celkem", 0))
        c3.metric("Nalezené chyby", v_stats.get("chyb", 0))
        
        st.dataframe(df_v, use_container_width=1)
        
        st.divider()
        st.subheader("🤝 Navržené finanční vyrovnání (50:50)")
        total_tasks = v_stats.get("celkem", 0)
        split_df = pd.DataFrame({
            "Partner": ["Tomáš Urbánek (50 %)", "Ilja Urbánek (50 %)"],
            "Podíl na zakázkách (ks)": [total_tasks/2, total_tasks/2],
            "Status": ["Připraveno k fakturaci", "Připraveno k fakturaci"]
        })
        st.table(split_df)

# =====================================================================
# 🏁 LAYER 8: FOOTER (Safe against NameError)
# =====================================================================
st.sidebar.divider()
st.sidebar.caption(f"© {datetime.now().year} {FIRMA_VLASTNI.get('název')}")
