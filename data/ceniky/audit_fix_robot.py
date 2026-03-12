import os
import unicodedata
import pandas as pd

# ---------------------------------------------
# KONFIGURACE
# ---------------------------------------------
CSV_FOLDER = "./"   # složka se všemi CSV
EXPECTED_COLUMNS = ["nazev", "cena", "jednotka", "kategorie"]

# mapování kategorií po normalizaci (bez diakritiky, velká písmena)
CATEGORY_MAP = {
    "CIDLO": "CIDLO",
    "ČIDLO": "CIDLO",
    "FA": "FA",
    "HILTI": "HILTI",
    "HP": "HP",
    "REVIZE": "REVIZE",
    "KONTROLY": "REVIZE",
    "NAHRADY": "NAHRADY",
    "NÁHRADY": "NAHRADY",
    "ND HP": "ND_HP",
    "ND_HP": "ND_HP",
    "ND VODA": "ND_VODA",
    "ND_VODA": "ND_VODA",
    "OSTATNI": "OSTATNI",
    "OSTATNÍ": "OSTATNI",
    "OZO": "OZO",
    "PASKA": "PASKA",
    "PÁSKA": "PASKA",
    "PK": "PK",
    "REKLAMA": "REKLAMA",
    "TAB": "TAB",
    "TABFOTO": "TABFOTO",
    "VODA": "VODA",
}

# kategorie, kde je cena 0 logická a necháváme ji být
ZERO_OK_CATEGORIES = {"FA", "NAHRADY"}

# ---------------------------------------------
# POMOCNÉ FUNKCE
# ---------------------------------------------

def strip_diacritics(s: str) -> str:
    """Odstraní diakritiku z řetězce."""
    return "".join(
        c for c in unicodedata.normalize("NFD", s)
        if unicodedata.category(c) != "Mn"
    )

def load_csv_auto(path):
    """Načte CSV i když je rozbité, s fallbackem na různé encodings a delimitery."""
    encodings = ["utf-8", "cp1250", "iso-8859-2"]
    delimiters = [";", ",", "\t"]

    for enc in encodings:
        for sep in delimiters:
            try:
                return pd.read_csv(
                    path,
                    sep=sep,
                    encoding=enc,
                    engine="python",
                    on_bad_lines="skip"
                )
            except Exception:
                pass

    print(f"❌ Soubor {path} nelze načíst – neznámé kódování nebo rozbitá struktura.")
    return pd.DataFrame()

def normalize_category(cat):
    """Normalizuje kategorii: trim, velká písmena, bez diakritiky."""
    if pd.isna(cat):
        return None
    c = str(cat).strip()
    c_no_diac = strip_diacritics(c).upper()
    return c_no_diac

def fix_categories(df, filename):
    """Opraví kategorie podle CATEGORY_MAP, vrátí df + log."""
    logs = []
    if "kategorie" not in df.columns:
        logs.append(f"{filename}: chybí sloupec 'kategorie' – neopravuji kategorie.")
        return df, logs

    df["kategorie_orig"] = df["kategorie"]
    df["kategorie_norm"] = df["kategorie"].apply(normalize_category)

    def map_cat(row):
        norm = row["kategorie_norm"]
        if norm in CATEGORY_MAP:
            return CATEGORY_MAP[norm]
        return row["kategorie"]  # necháme původní, pokud neznáme

    df["kategorie"] = df.apply(map_cat, axis=1)

    changed = df[df["kategorie"] != df["kategorie_orig"]]
    if not changed.empty:
        logs.append(f"{filename}: opravené kategorie u {len(changed)} řádků.")
    else:
        logs.append(f"{filename}: kategorie beze změny.")

    df = df.drop(columns=["kategorie_norm"])
    return df, logs

def fix_duplicates(df, filename):
    """Opraví duplicity podle názvu – preferuje nenulovou cenu."""
    logs = []
    if "nazev" not in df.columns:
        logs.append(f"{filename}: chybí sloupec 'nazev' – neřeším duplicity.")
        return df, logs

    before = len(df)

    # seřadíme tak, aby nenulové ceny byly první
    if "cena" in df.columns:
        df = df.sort_values(by=["nazev", "cena"], ascending=[True, False])
    else:
        df = df.sort_values(by=["nazev"])

    # ponecháme první výskyt každého názvu
    df = df.drop_duplicates(subset=["nazev"], keep="first")

    after = len(df)
    removed = before - after
    if removed > 0:
        logs.append(f"{filename}: odstraněno {removed} duplicitních řádků podle názvu.")
    else:
        logs.append(f"{filename}: žádné duplicity podle názvu nenalezeny.")

    return df, logs

def mark_zero_prices(df, filename):
    """Označí řádky s cenou 0, ale neopravuje je – jen loguje."""
    logs = []
    if "cena" not in df.columns:
        logs.append(f"{filename}: chybí sloupec 'cena' – neřeším nuly.")
        return df, logs

    zeros = df[df["cena"] == 0]
    if zeros.empty:
        logs.append(f"{filename}: žádné položky s cenou 0.")
        return df, logs

    # pokud máme kategorie, můžeme filtrovat podle nich
    if "kategorie" in df.columns:
        zeros_problem = zeros[~zeros["kategorie"].isin(ZERO_OK_CATEGORIES)]
        zeros_ok = zeros[zeros["kategorie"].isin(ZERO_OK_CATEGORIES)]

        if not zeros_ok.empty:
            logs.append(
                f"{filename}: {len(zeros_ok)} položek s cenou 0 v kategoriích, kde je to OK: "
                f"{sorted(zeros_ok['kategorie'].unique())}"
            )
        if not zeros_problem.empty:
            logs.append(
                f"{filename}: ⚠ {len(zeros_problem)} položek s cenou 0 v kategoriích, kde to asi není v pořádku."
            )
    else:
        logs.append(f"{filename}: {len(zeros)} položek s cenou 0 (bez znalosti kategorie).")

    return df, logs

def fix_and_save_file(path):
    filename = os.path.basename(path)
    print(f"\n--- Zpracovávám {filename} ---")

    df = load_csv_auto(path)

    logs = []

    # doplnění chybějících sloupců, pokud je to potřeba
    for col in EXPECTED_COLUMNS:
        if col not in df.columns:
            if col == "kategorie":
                # některé soubory (např. Nahrady) kategorii nemají – necháme být
                logs.append(f"{filename}: sloupec '{col}' chybí – u tohoto typu souboru to může být v pořádku.")
            else:
                df[col] = None
                logs.append(f"{filename}: doplněn chybějící sloupec '{col}' jako None.")

    # oprava kategorií
    df, log_cat = fix_categories(df, filename)
    logs.extend(log_cat)

    # oprava duplicit
    df, log_dup = fix_duplicates(df, filename)
    logs.extend(log_dup)

    # označení nulových cen
    df, log_zero = mark_zero_prices(df, filename)
    logs.extend(log_zero)

    # uložení opraveného CSV
    clean_name = f"clean_{filename}"
    clean_path = os.path.join(os.path.dirname(path), clean_name)
    df.to_csv(clean_path, sep=";", index=False)
    logs.append(f"{filename}: uloženo opravené CSV jako {clean_name}")

    # výpis logů
    for l in logs:
        print(l)

# ---------------------------------------------
# HLAVNÍ LOGIKA
# ---------------------------------------------

def run_fix_and_audit():
    print("\n==============================")
    print(" 🔥 AUDIT + OPRAVY CSV – START")
    print("==============================\n")

    for file in os.listdir(CSV_FOLDER):
        if file.lower().endswith(".csv"):
            path = os.path.join(CSV_FOLDER, file)
            fix_and_save_file(path)

    print("\n==============================")
    print(" ✅ HOTOVO – OPRAVENÁ CSV VYGENEROVÁNA")
    print("==============================\n")

# ---------------------------------------------
# SPUŠTĚNÍ
# ---------------------------------------------
if __name__ == "__main__":
    run_fix_and_audit()
