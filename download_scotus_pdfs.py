"""
download_scotus_pdfs.py  — download all 9 Supreme Court Justice 2024 annual disclosures.
Source: Fix the Court (fixthecourt.com) — direct PDF links, no login required.
Run:  python download_scotus_pdfs.py
"""
import urllib.request, os, time

DEST = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'scotus_pdfs_2024')
os.makedirs(DEST, exist_ok=True)

JUSTICES = [
    ("Roberts",    "John",     "Roberts-John-G-Jr-Annual-2024.pdf",   "2025/06"),
    ("Thomas",     "Clarence", "Thomas-Clarence-Annual-2024.pdf",     "2025/06"),
    ("Alito",      "Samuel",   "Alito-Samuel-A-Annual-2024.pdf",      "2025/08"),
    ("Sotomayor",  "Sonia",    "Sotomayor-Sonia-Annual-2024.pdf",     "2025/06"),
    ("Kagan",      "Elena",    "Kagan-Elena-Annual-2024.pdf",         "2025/06"),
    ("Gorsuch",    "Neil",     "Gorsuch-Neil-M-Annual-2024.pdf",      "2025/06"),
    ("Kavanaugh",  "Brett",    "Kavanaugh-Brett-M-Annual-2024.pdf",   "2025/06"),
    ("Barrett",    "Amy",      "Barrett-Amy-C-Annual-2024.pdf",       "2025/06"),
    ("Jackson",    "Ketanji",  "Jackson-Ketanji-B-Annual-2024.pdf",   "2025/06"),
]

for last, first, filename, date_path in JUSTICES:
    url  = f'https://fixthecourt.com/wp-content/uploads/{date_path}/{filename}'
    dest = os.path.join(DEST, f'{last}_{first}.pdf')

    if os.path.exists(dest) and os.path.getsize(dest) > 5000:
        print(f'skip  {last}, {first}')
        continue

    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=20) as r:
            data = r.read()
        with open(dest, 'wb') as f:
            f.write(data)
        print(f'ok    {last}, {first}  ({len(data)//1024}KB)')
    except Exception as e:
        print(f'ERROR {last}, {first}: {e}')

    time.sleep(0.3)

print(f'\nDone. PDFs in: {DEST}')
