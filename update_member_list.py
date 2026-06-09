"""
update_member_list.py
Download the official 2024 House FD bulk data ZIP from the Clerk's office.
This contains the complete, correct member list with DocIDs for ALL filers.

Fixes two problems at once:
  1. Four wrong DocIDs in house_members_2024.tsv (Blumenauer, Gimenez, McHenry, Phillips)
  2. ~96 members who were never in the TSV at all

Then downloads any newly discovered PDFs.

Usage:  python update_member_list.py
"""
import urllib.request, zipfile, io, csv, os, time, re

BASE     = os.path.dirname(os.path.abspath(__file__))
ZIP_URL  = 'https://disclosures-clerk.house.gov/public_disc/financial-pdfs/2024FD.zip'
TSV_PATH = os.path.join(BASE, 'house_members_2024.tsv')
PDF_DIR  = os.path.join(BASE, 'house_pdfs_2024')
PDF_BASE = 'https://disclosures-clerk.house.gov/public_disc/financial-pdfs/2024/{doc_id}.pdf'

HEADERS = {'User-Agent': 'Mozilla/5.0 (public-interest research; congressional disclosures)'}

os.makedirs(PDF_DIR, exist_ok=True)

# ── Download the ZIP ──────────────────────────────────────────────────────────
print(f'Downloading {ZIP_URL} …')
req  = urllib.request.Request(ZIP_URL, headers=HEADERS)
resp = urllib.request.urlopen(req, timeout=60)
data = resp.read()
print(f'  Downloaded {len(data)//1024}KB')

# ── Extract and parse the member list ────────────────────────────────────────
zf = zipfile.ZipFile(io.BytesIO(data))
print(f'ZIP contents: {zf.namelist()}')

# Find the data file (usually a .txt or .xml with all filings)
data_file = None
for name in zf.namelist():
    if name.lower().endswith(('.txt', '.xml', '.csv')):
        data_file = name
        break

if not data_file:
    print('ERROR: No data file found in ZIP')
    print('Files:', zf.namelist())
    exit(1)

raw = zf.read(data_file).decode('utf-8', errors='replace')
print(f'Data file: {data_file} ({len(raw)//1024}KB)')
print(f'First 300 chars: {raw[:300]}')
print()

# ── Parse the data file ───────────────────────────────────────────────────────
# The file is typically pipe-delimited or tab-delimited with fields:
# LastName | FirstName | Office | FilingType | StateDist | Year | FilingDate | DocID
# Exact format may vary; we detect the delimiter and column positions.

lines = raw.strip().split('\n')
header_line = lines[0] if lines else ''
print(f'Header: {header_line[:200]}')

# Detect delimiter
if '|' in header_line:
    delim = '|'
elif '\t' in header_line:
    delim = '\t'
else:
    delim = ','

print(f'Delimiter: {repr(delim)}')

reader = csv.DictReader(io.StringIO(raw), delimiter=delim)
all_rows = list(reader)
print(f'Total rows: {len(all_rows)}')
print(f'Columns: {list(all_rows[0].keys()) if all_rows else "none"}')

# Filter to annual reports for Members (not candidates, not PTRs)
# Filing type is usually 'Annual', 'Annual (Amendment)', etc.
# We want the most recent annual report per member.

def is_annual(row):
    ft = row.get('FilingType', row.get('Filing_Type', row.get('filing_type', ''))).strip()
    # FilingType codes: O=Original annual, A=Amendment, P=PTR, C=Candidate, etc.
    # Accept 'O' (original member annual) or any value containing 'Annual'
    return ft in ('O',) or 'annual' in ft.lower()

def get_docid(row):
    for key in ['DocID', 'doc_id', 'DocumentID', 'ID', 'DOCID']:
        if key in row and row[key].strip():
            return row[key].strip()
    return ''

def get_name(row):
    last  = row.get('Last', row.get('LastName', row.get('last_name', ''))).strip()
    first = row.get('First', row.get('FirstName', row.get('first_name', ''))).strip()
    return last, first

def get_office(row):
    # Column is 'StateDst' in this file (e.g. 'NC12', 'TX05')
    return row.get('StateDst', row.get('Office', row.get('StateDist', row.get('office', '')))).strip()

annual_rows = [r for r in all_rows if is_annual(r)]
print(f'Annual reports: {len(annual_rows)}')

# De-duplicate: one per member (keep first/only annual)
seen = {}
for row in annual_rows:
    last, first = get_name(row)
    key = (last.lower(), first.lower()[:3])
    if key not in seen:
        seen[key] = row

print(f'Unique members with annual reports: {len(seen)}')

# ── Load existing TSV ────────────────────────────────────────────────────────
existing = {}
if os.path.exists(TSV_PATH):
    with open(TSV_PATH, newline='', encoding='utf-8') as f:
        for row in csv.DictReader(f, delimiter='\t'):
            docid = row.get('DOCID', '').strip()
            name  = row.get('NAME', '').strip()
            existing[docid] = {'NAME': name, 'OFFICE': row.get('OFFICE',''), 'DOCID': docid}
    print(f'Existing TSV: {len(existing)} entries')

# ── Build updated TSV ────────────────────────────────────────────────────────
new_tsv_rows = []
new_members  = []
fixed        = []

for key, row in seen.items():
    last, first = get_name(row)
    docid  = get_docid(row)
    office = get_office(row)

    if not docid:
        continue

    # Format name like existing TSV
    name_formatted = f'{last}, Hon.. {first}'
    new_tsv_rows.append({'NAME': name_formatted, 'OFFICE': office, 'DOCID': docid})

    if docid not in existing:
        new_members.append((name_formatted, office, docid))

print(f'\nNew TSV will have {len(new_tsv_rows)} members')
print(f'  New members not in old TSV: {len(new_members)}')

# Write updated TSV
with open(TSV_PATH, 'w', newline='', encoding='utf-8') as f:
    w = csv.DictWriter(f, fieldnames=['NAME','OFFICE','DOCID'], delimiter='\t')
    w.writeheader()
    w.writerows(sorted(new_tsv_rows, key=lambda r: r['NAME']))

print(f'TSV updated: {TSV_PATH}')

# ── Download PDFs for new/missing members ───────────────────────────────────
if new_members:
    print(f'\nDownloading {len(new_members)} new PDFs…')
    opener = urllib.request.build_opener()
    opener.addheaders = list(HEADERS.items())
    ok = err = 0
    for i, (name, office, docid) in enumerate(new_members, 1):
        dest = os.path.join(PDF_DIR, f'{docid}.pdf')
        if os.path.exists(dest) and os.path.getsize(dest) > 500:
            print(f'[{i}/{len(new_members)}] skip {name}')
            continue
        url = PDF_BASE.format(doc_id=docid)
        try:
            r = opener.open(url, timeout=20)
            with open(dest, 'wb') as f:
                f.write(r.read())
            ok += 1
            print(f'[{i}/{len(new_members)}] ok   {name} ({docid}) {os.path.getsize(dest)//1024}KB')
        except Exception as e:
            err += 1
            print(f'[{i}/{len(new_members)}] ERR  {name}: {e}')
        time.sleep(0.3)
    print(f'Downloaded {ok} new PDFs, {err} errors')

print(f"""
━━ Done ━━
  Run python build_db.py   to reparse all PDFs into holdings.db
  Run python ocr_scanned_pdfs.py   to OCR the scanned ones (needs ANTHROPIC_API_KEY)
""")
