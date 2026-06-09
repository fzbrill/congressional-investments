"""
download_house_pdfs.py
Run once from your Downloads folder to fetch all 323 remaining House member
annual disclosure PDFs. They'll land in Downloads/house_pdfs_2024/.

Usage:  python download_house_pdfs.py
"""
import urllib.request, os, time, csv

TSV   = os.path.join(os.path.dirname(__file__), 'house_members_2024.tsv')
DEST  = os.path.join(os.path.dirname(__file__), 'house_pdfs_2024')
BASE  = 'https://disclosures-clerk.house.gov/public_disc/financial-pdfs/2024/{doc_id}.pdf'

os.makedirs(DEST, exist_ok=True)

with open(TSV, newline='', encoding='utf-8') as f:
    rows = list(csv.DictReader(f, delimiter='\t'))

total = len(rows)
for i, row in enumerate(rows, 1):
    doc_id = row['DOCID'].strip()
    name   = row['NAME'].strip()
    dest   = os.path.join(DEST, f'{doc_id}.pdf')
    if os.path.exists(dest) and os.path.getsize(dest) > 500:
        print(f'[{i}/{total}] skip  {name} ({doc_id})')
        continue
    url = BASE.format(doc_id=doc_id)
    try:
        urllib.request.urlretrieve(url, dest)
        size = os.path.getsize(dest)
        print(f'[{i}/{total}] ok    {name} ({doc_id})  {size//1024}KB')
    except Exception as e:
        print(f'[{i}/{total}] ERROR {name} ({doc_id}): {e}')
    time.sleep(0.3)   # be polite to the server

print(f'\nDone. PDFs in: {DEST}')
