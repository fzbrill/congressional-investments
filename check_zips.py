"""
Diagnostic: check what FilingTypes are in 2025FD.zip and 2026FD.zip.
Run from Windows: python check_zips.py
This tells us whether 2025 annual reports are in 2025FD.zip (reporting-year conv)
or 2026FD.zip (filing-year conv).
"""
import urllib.request, zipfile, io, csv
from collections import Counter

HEADERS = {'User-Agent': 'Mozilla/5.0 (public-interest research; congressional disclosures)'}

for year in [2025, 2026]:
    url = f'https://disclosures-clerk.house.gov/public_disc/financial-pdfs/{year}FD.zip'
    print(f'\n{"="*60}')
    print(f'{year}FD.zip')
    try:
        req  = urllib.request.Request(url, headers=HEADERS)
        data = urllib.request.urlopen(req, timeout=60).read()
        print(f'  Size: {len(data)//1024}KB')
    except Exception as e:
        print(f'  ERROR: {e}'); continue

    zf = zipfile.ZipFile(io.BytesIO(data))
    data_file = next(n for n in zf.namelist() if n.lower().endswith(('.txt','.xml','.csv')))
    raw = zf.read(data_file).decode('utf-8', errors='replace')
    delim = '|' if '|' in raw.split('\n')[0] else '\t'
    rows = list(csv.DictReader(io.StringIO(raw), delimiter=delim))
    print(f'  Rows: {len(rows)}  Columns: {list(rows[0].keys()) if rows else []}')

    types = Counter(r.get('FilingType','').strip() for r in rows)
    print(f'  FilingType breakdown:')
    for k,v in sorted(types.items(), key=lambda x:-x[1]):
        print(f'    {repr(k):20s} {v}')

    annuals = [r for r in rows if r.get('FilingType','').strip() in ('O','A','OA')]
    print(f'\n  Annual rows (O/A): {len(annuals)}')
    for r in annuals[:5]:
        last  = r.get('Last', r.get('LastName',''))
        first = r.get('First', r.get('FirstName',''))
        docid = r.get('DocID', r.get('doc_id',''))
        date  = r.get('FilingDate','')
        print(f'    {last}, {first}  DocID={docid}  Date={date}')

print('\nDone.')
