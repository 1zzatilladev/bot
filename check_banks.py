import json

bankuz_banks = [
    'Asia Alliance Bank',
    'Octobank', 
    'Kapitalbank',
    "O'zbekiston Milliy banki",
    'BRB',
    "O'zsanoatqurilishbank",
    'Ipak Yuli Bank',
    'Infinbank',
    'Tenge Bank',
    'Hayot Bank',
    'Asakabank',
    'Orient Finans Bank',
    'Trastbank',
    'Garant bank',
    'Ipoteka bank',
    'Turon bank',
    'Poytaxt bank',
    'Aloqabank',
    'Anorbank'
]

given = [
    'UZUM Bank', 'Agrobank', 'Hamkorbank', 'Unired', 'TBC Bank Uzbekistan', 
    'Ziraat Bank Uzbekiston', 'MPay', 'SalamPay', 'Yubor', 'Avosend', 
    'Multitransfer', 'Koronapay', 'Unistream', 'Western Union', 'Dengi.ru', 
    'Bank Solidarnost', 'MTS Bank', 'Sberbank'
]

print('📊 TAQQOSLASH:\n')
print(f'Bank.uz olib keladigan: {len(bankuz_banks)} ta')
print(f'Berilgan ro\'yxat: {len(given)} ta\n')

print('✅ Qaysilari bank.uz\'da mavjud:')
for g in given:
    found = False
    for b in bankuz_banks:
        if g.lower() in b.lower() or b.lower() in g.lower():
            print(f'  ✅ {g} → {b}')
            found = True
            break
    if not found:
        print(f'  ❌ {g} — bank.uz\'da YO\'Q')

print(f'\n📌 Bank.uz\' dagi hamma banklar: {bankuz_banks}')
