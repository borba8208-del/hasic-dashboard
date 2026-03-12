import os
import unicodedata

CENIK_DIR = "data/ceniky/"

def remove_diacritics(text):
    return ''.join(
        c for c in unicodedata.normalize('NFKD', text)
        if not unicodedata.combining(c)
    )

def normalize_filename(name):
    # odstranění prefixu
    if name.lower().startswith("cenik_"):
        name = name[6:]

    # odstranění přípony
    base = name.replace(".csv", "")

    # sjednocení ostatní
    if base.lower() in ["ostatni", "ostatní", "ostatni2"]:
        base = "ostatni"

    # odstranění diakritiky
    base = remove_diacritics(base)

    # odstranění mezer na podtržítka
    base = base.replace(" ", "_")

    return f"{base}.csv"

def rename_files():
    print("=== Přejmenování ceníků ===")
    files = os.listdir(CENIK_DIR)

    for f in files:
        if not f.lower().endswith(".csv"):
            continue

        old_path = os.path.join(CENIK_DIR, f)
        new_name = normalize_filename(f)
        new_path = os.path.join(CENIK_DIR, new_name)

        if old_path == new_path:
            print(f"OK: {f} (bez změny)")
            continue

        # pokud existuje kolize, smažeme starý duplicitní soubor
        if os.path.exists(new_path):
            print(f"⚠ Kolize: {new_name} už existuje → mažu duplicitní {f}")
            os.remove(old_path)
            continue

        os.rename(old_path, new_path)
        print(f"✔ {f} → {new_name}")

    print("=== Hotovo ===")

if __name__ == "__main__":
    rename_files()
