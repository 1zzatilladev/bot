import json

berilgan_banklar = [
    "UZUM Bank",
    "Agrobank", 
    "Hamkorbank",
    "Unired",
    "TBC Bank Uzbekistan",
    "Ziraat Bank Uzbekistan",
    "MPay",
    "SalamPay",
    "Yubor",
    "Avosend",
    "Multitransfer",
    "Koronapay",
    "Unistream",
    "Western Union",
    "Dengi.ru",
    "Bank Solidarnost",
    "MTS Bank",
    "Sberbank"
]

bankuz_data = {
    "aab": "Asia Alliance Bank",
    "octobank": "Octobank",
    "kapitalbank": "Kapitalbank",
    "nbu": "O'zbekiston Milliy banki",
    "brb": "BRB",
    "sqb": "O'zsanoatqurilishbank",
    "ipakyuli": "Ipak Yuli Bank",
    "infinbank": "Infinbank",
    "tengebank": "Tenge Bank",
    "hayotbank": "Hayot Bank",
    "asakabank": "Asakabank",
    "ofb": "Orient Finans Bank",
    "trustbank": "Trastbank",
    "garantbank": "Garant bank",
    "ipotekabank": "Ipoteka bank",
    "turonbank": "Turon bank",
    "poytaxtbank": "Poytaxt bank",
    "aloqabank": "Aloqabank",
    "anorbank": "Anorbank"
}

print("🔍 Berilgan banklar bank.uz'da mavjudmi?\n")
print("Berilgan banklarda bank.uz'da mavjud:")
for berilgan in berilgan_banklar:
    for key, name in bankuz_data.items():
        if berilgan.lower() in name.lower() or name.lower() in berilgan.lower():
            print(f"  ✅ {berilgan:30} → {name} (bank.uz)")
            break

print("\n" + "="*60)
print(f"\nBank.uz'dagi 19 ta bank: {len(bankuz_data)} ta")
for key, name in bankuz_data.items():
    print(f"  • {name}")

print("\n\n⚠️ MASALALA:")
print("• UZUM Bank, Agrobank, Hamkorbank, TBC, Ziraat - bank.uz'da YO'Q")
print("• Bu banklarning saytlari JavaScript orqali dynamic content yuklaydi (scrape qilib bo'lmadi)")
print("\n✅ YECHIM: Bank.uz agregator orqali 19 ta bankning kurslarini olish")
