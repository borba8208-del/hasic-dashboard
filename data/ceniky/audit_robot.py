import os
import pandas as pd

# ---------------------------------------------
# KONFIGURACE
# ---------------------------------------------
CSV_FOLDER = "./"   # složka se všemi CSV
EXPECTED_COLUMNS = ["nazev", "cena", "jednotka", "kategorie"]

# Kategorie, které mají být bez diakritiky a velkými písmeny
NORMALIZED_CATEGORIES = {
    "CIDLO": "CIDLO",
    "FA": "FA",
    "HILTI": "HILTI",
    "HP": "HP",
    "REVIZE": "REVIZE",
    "NAHRADY": "NAHRADY",
    "ND HP": "ND_HP",
    "ND VODA": "ND_VODA",
    "OSTATNI": "OSTATNI",
    "OZO": "OZO",
    "PASKA": "PASKA",
    "PK": "PK",
    "REKLAMA": "REKLAMA",
    "TAB": "TAB",
    "TABFOTO": "TABFOTO",
    "VODA": "VODA"
}

# ---------------------------------------------
# FUNKCE
# ---------------------------------------------

def load_csv_auto(path):
    """Načte CSV se správným oddělovačem."""
    try:
        return pd.read_csv(path, sep=";")
    except:
        return pd.read_csv(path, sep=",")

def normalize_category(cat):
    """Odstraní diakritiku a sjednotí formát kategorií."""
    if pd.isna(cat):
        return None
    c = str(cat).strip().upper()
    return c

def audit_file(filename, df):
    report = []
    base = os.path.basename(filename)

    # 1) Kontrola sloupců
    missing_cols = [c for c in EXPECTED_COLUMNS if c not in df.columns]
    if missing_cols:
        report.append(f"❗ {base}: Chybí sloupce: {missing_cols}")

    # 2) Duplicity názvů
    dups = df[df.duplicated("nazev", keep=False)]
    if not dups.empty:
        report.append(f"❗ {base}: DUPLICITY názvů:\n{dups[['nazev','cena']]}")

    # 3) Ceny 0
    zeros = df[df["cena"] == 0]
    if not zeros.empty:
        report.append(f"⚠️ {base}: Položky s cenou 0:\n{zeros[['nazev','cena']]}")

    # 4) Chybné kategorie
    if "kategorie" in df.columns:
        df["cat_norm"] = df["kategorie"].apply(normalize_category)
        wrong = df[df["cat_norm"].isin(NORMALIZED_CATEGORIES.values()) == False]
        if not wrong.empty:
            report.append(f"⚠️ {base}: Nesjednocené kategorie:\n{wrong[['nazev','kategorie']]}")

    # 5) Chybějící hodnoty
    missing = df[df.isna().any(axis=1)]
    if not missing.empty:
        report.append(f"⚠️ {base}: Chybějící hodnoty:\n{missing}")

    return report

# ---------------------------------------------
# HLAVNÍ LOGIKA
# ---------------------------------------------

def run_audit():
    print("\n==============================")
    print(" 🔥 AUDIT CSV – ZAČÍNÁME")
    print("==============================\n")

    all_reports = []

    for file in os.listdir(CSV_FOLDER):
        if file.lower().endswith(".csv"):
            path = os.path.join(CSV_FOLDER, file)
            df = load_csv_auto(path)
            report = audit_file(file, df)
            if report:
                all_reports.extend(report)
            else:
                print(f"✔ {file}: Bez chyb")

    print("\n==============================")
    print(" 🔍 VÝSLEDKY AUDITU")
    print("==============================\n")

    if not all_reports:
        print("🎉 Všechny soubory jsou v pořádku!")
    else:
