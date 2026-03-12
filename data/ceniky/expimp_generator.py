import os
import unicodedata
import pandas as pd

CSV_FOLDER = "./"   # složka s ceníky

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

def strip_diacritics(s):
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")

def load_csv_auto(path):
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

    print(f"❌ Nelze načíst soubor: {path}")
    return pd.DataFrame()

def normalize_category(cat):
    if pd.isna(cat):
        return None
    c = strip_diacritics(str(cat).strip().upper())
    return CATEGORY_MAP.get(c, c)

def process_file(path):
    df = load_csv_auto(path)
    if df.empty:
        return df

    # doplnění sloupců
    if "kategorie" not in df.columns:
        # odvodíme kategorii z názvu souboru
        base = os.path.basename(path).split(".")[0].upper()
        base = strip_diacritics(base)
        df["kategorie"] = CATEGORY_MAP.get(base, base)

    # normalizace kategorií
    df["kategorie"] = df["kategorie"].apply(normalize_category)

    # odstranění duplicit podle názvu
    if "nazev" in df.columns:
        df = df.sort_values(by=["nazev", "cena"], ascending=[True, False])
        df = df.drop_duplicates(subset=["nazev"], keep="first")

    return df[["nazev", "cena", "jednotka", "kategorie"]]

def generate_expimp():
    all_rows = []

    for file in os.listdir(CSV_FOLDER):
        if file.lower().endswith(".csv") and not file.startswith("expimp"):
            path = os.path.join(CSV_FOLDER, file)
            print(f"Načítám: {file}")
            df = process_file(path)
            if not df.empty:
                all_rows.append(df)

    if not all_rows:
        print("❌ Nebyly nalezeny žádné platné CSV soubory.")
        return

    final_df = pd.concat(all_rows, ignore_index=True)

    # uložení expimp.csv
    output_path = os.path.join(CSV_FOLDER, "expimp.csv")
    final_df.to_csv(output_path, sep=";", index=False, encoding="utf-8-sig")

    print("\n====================================")
    print("✅ expimp.csv byl úspěšně vygenerován!")
    print(f"📄 Počet položek: {len(final_df)}")
    print("====================================\n")

if __name__ == "__main__":
    generate_expimp()
