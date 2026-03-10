import streamlit as st

# --- 1. MASTER KONFIGURACE (Zákon firmy Urbánek) ---
FIRMA = "HASIČ-SERVIS URBÁNEK"
ZALOZENO = 1994
CERTIFIKACE = "TÜV NORD Czech"

CENIK = {
    "HP_shodny": 29.40,
    "HP_opravitelny": 19.70,
    "HP_neopravitelny": 23.50, # Včetně zneprovoznění
    "HP_novy_raima_p6": 1090.00
}

# Terminologický strážce
ZAKAZANE_SLOVO = "revize"
POVINNY_TERMIN = "kontrola provozuschopnosti"

# --- 2. LOGICKÉ FUNKCE (Mozek robota) ---
def analyzuj_terminologii(text):
    if ZAKAZANE_SLOVO in text.lower():
        return f"❌ Nalezen nepovolený termín '{ZAKAZANE_SLOVO}'. Opravuji na '{POVINNY_TERMIN}'."
    return "✅ Terminologie je v pořádku."

def vypocitej_cenu(pocty, je_svj):
    zaklad = (pocty['s'] * CENIK['HP_shodny'] +
              pocty['o'] * CENIK['HP_opravitelny'] +
              pocty['n'] * CENIK['HP_neopravitelny'] +
              pocty['novy'] * CENIK['HP_novy_raima_p6'])
    
    dph_sazba = 0.12 if je_svj else 0.21
    celkem_s_dph = zaklad * (1 + dph_sazba)
    return round(zaklad, 2), round(celkem_s_dph, 2), int(dph_sazba * 100)

# --- 3. UŽIVATELSKÉ ROZHRANÍ (Streamlit) ---
st.set_page_config(page_title=f"{FIRMA} | Asistent", page_icon="🛡️")
st.title(f"🛡️ {FIRMA}")
st.caption(f"Expertní systém pro požární ochranu (tradice od r. {ZALOZENO})")

st.divider()

# Sekce A: Audit dokumentu
st.subheader("🔍 Rychlý audit textu")
vstupni_text = st.text_area("Vložte text z protokolu nebo W-SERVISU k prověření:", 
                            placeholder="Zde vložte text...")

if vstupni_text:
    vysledek = analyzuj_terminologii(vstupni_text)
    st.write(vysledek)

# Sekce B: Inteligentní kalkulačka
st.subheader("💰 Kalkulace zakázky")
col1, col2 = st.columns(2)

with col1:
    s = st.number_input("Počet shodných HP (29,40 Kč)", min_value=0, step=1)
    o = st.number_input("Počet opravitelných HP (19,70 Kč)", min_value=0, step=1)
    typ_klienta = st.toggle("Jedná se o SVJ / Bytový dům (12% DPH)")

with col2:
    n = st.number_input("Počet neopravitelných HP (23,50 Kč)", min_value=0, step=1)
    novy = st.number_input("Prodej nových RAIMA P6 (1090 Kč)", min_value=0, step=1)

if st.button("Provést finanční audit"):
    pocty = {'s': s, 'o': o, 'n': n, 'novy': novy}
    bez_dph, s_dph, sazba = vypocitej_cenu(pocty, typ_klienta)
    
    st.success(f"**Základ daně:** {bez_dph} Kč")
    st.info(f"**DPH ({sazba}%):** {round(s_dph - bez_dph, 2)} Kč")
    st.metric("CELKEM K ÚHRADĚ", f"{round(s_dph, 1)} Kč")

    if n > 0:
        st.warning("⚠️ Nezapomeňte u NV přístrojů uvést kód A-K a doložku o odpadech.")

st.divider()
st.caption(f"Garance odbornosti: {CERTIFIKACE} | Software: W-SERVIS")