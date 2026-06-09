"""
download_house_ptrs.py
Download House Periodic Transaction Report (PTR) PDFs for the last 12 months.

Uses the same bulk FD ZIP used for annual disclosures — it contains ALL filing
types, including FilingType=='P' (PTR).  The search form is JS-rendered and
cannot be POSTed to directly.

ZIP URL pattern:  https://disclosures-clerk.house.gov/public_disc/financial-pdfs/{year}FD.zip
PTR PDF pattern:  https://disclosures-clerk.house.gov/public_disc/ptr-pdfs/{year}/{doc_id}.pdf

Usage:  python download_house_ptrs.py [--year 2025]
        python download_house_ptrs.py --years 2024 2025

PDFs land in:  house_ptrs_{year}/
"""
import urllib.request, urllib.parse, zipfile, io, csv, os, time, re, argparse

parser = argparse.ArgumentParser()
parser.add_argument('--year', type=int, default=2025)
parser.add_argument('--years', nargs='+', type=int)
args = parser.parse_args()

years = args.years if args.years else ([2024, 2025] if args.year == 2025 else [args.year])

BASE_DIR  = os.path.dirname(os.path.abspath(__file__))
ZIP_URL   = 'https://disclosures-clerk.house.gov/public_disc/financial-pdfs/{year}FD.zip'
PDF_BASE  = 'https://disclosures-clerk.house.gov/public_disc/ptr-pdfs/{year}/{doc_id}.pdf'

HEADERS = {'User-Agent': 'Mozilla/5.0 (public-interest research; congressional disclosures)'}


def fetch_ptr_records(year):
    """Download the FD bulk ZIP and extract PTR records (FilingType=='P')."""
    url = ZIP_URL.format(year=year)
    print(f'  Downloading {url} …')
    try:
        req  = urllib.request.Request(url, headers=HEADERS)
        data = urllib.request.urlopen(req, timeout=60).read()
    except Exception as e:
        print(f'  ERROR downloading ZIP for {year}: {e}')
        return []

    print(f'  Downloaded {len(data)//1024}KB')

    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except Exception as e:
        print(f'  ERROR opening ZIP: {e}')
        return []

    # Find the data file
    data_file = next((n for n in zf.namelist() if n.lower().endswith(('.txt', '.xml', '.csv'))), None)
    if not data_file:
        print(f'  No data file found in ZIP. Contents: {zf.namelist()}')
        return []

    raw = zf.read(data_file).decode('utf-8', errors='replace')

    # Detect delimiter
    first_line = raw.split('\n')[0]
    delim = '|' if '|' in first_line else ('\t' if '\t' in first_line else ',')

    reader = csv.DictReader(io.StringIO(raw), delimiter=delim)
    all_rows = list(reader)
    print(f'  Total rows in ZIP: {len(all_rows)}')

    # Filter for PTRs only (FilingType == 'P')
    ptr_rows = [r for r in all_rows
                if r.get('FilingType', r.get('Filing_Type', '')).strip() == 'P']
    print(f'  PTR filings for {year}: {len(ptr_rows)}')

    records = []
    for row in ptr_rows:
        doc_id = row.get('DocID', row.get('doc_id', '')).strip()
        last   = row.get('Last',  row.get('LastName',  '')).strip()
        first  = row.get('First', row.get('FirstName', '')).strip()
        office = row.get('StateDst', row.get('StateDist', row.get('Office', ''))).strip()
        date   = row.get('FilingDate', row.get('filing_date', '')).strip()
        if doc_id:
            records.append({
                'doc_id': doc_id,
                'year':   year,
                'name':   f'{last}, {first}',
                'office': office,
                'date':   date,
            })

    return records


def download_pdfs(records, year):
    dest_dir = os.path.join(BASE_DIR, f'house_ptrs_{year}')
    os.makedirs(dest_dir, exist_ok=True)

    opener = urllib.request.build_opener()
    opener.addheaders = list(HEADERS.items())

    total = len(records)
    ok = skip = err = 0

    for i, rec in enumerate(records, 1):
        doc_id = rec['doc_id']
        name   = rec.get('name', doc_id)
        dest   = os.path.join(dest_dir, f'{doc_id}.pdf')

        if os.path.exists(dest) and os.path.getsize(dest) > 500:
            skip += 1
            if skip <= 3 or skip % 100 == 0:
                print(f'[{i}/{total}] skip  {name}')
            continue

        url = PDF_BASE.format(year=year, doc_id=doc_id)
        try:
            resp = opener.open(url, timeout=20)
            with open(dest, 'wb') as f:
                f.write(resp.read())
            size = os.path.getsize(dest)
            ok += 1
            print(f'[{i}/{total}] ok    {name} ({doc_id})  {size//1024}KB')
        except Exception as e:
            err += 1
            print(f'[{i}/{total}] ERROR {name} ({doc_id}): {e}')
        time.sleep(0.25)

    print(f'\n  {year}: {ok} downloaded, {skip} skipped, {err} errors → {dest_dir}')
    return ok, skip, err


def save_manifest(records, year):
    """Save a manifest.tsv to house_ptrs_{year}/ so build_transactions.py can look up names."""
    dest_dir = os.path.join(BASE_DIR, f'house_ptrs_{year}')
    os.makedirs(dest_dir, exist_ok=True)
    manifest_path = os.path.join(dest_dir, 'manifest.tsv')
    import csv as _csv
    with open(manifest_path, 'w', newline='', encoding='utf-8') as f:
        w = _csv.DictWriter(f, fieldnames=['doc_id', 'last', 'first', 'office'], delimiter='\t')
        w.writeheader()
        for rec in records:
            name   = rec.get('name', '')
            parts  = name.split(',', 1) if ',' in name else [name, '']
            last_  = parts[0].strip()
            first_ = parts[1].strip() if len(parts) > 1 else ''
            w.writerow({'doc_id': rec['doc_id'], 'last': last_, 'first': first_, 'office': rec['office']})
    print(f'  Saved manifest: {manifest_path} ({len(records)} records)')


# ── Main ──────────────────────────────────────────────────────────────────────
if __name__ == '__main__':
    total_ok = total_skip = total_err = 0
    for year in years:
        print(f'\n── House PTRs for {year} ──')
        records = fetch_ptr_records(year)
        if records:
            save_manifest(records, year)
            ok, skip, err = download_pdfs(records, year)
            total_ok += ok; total_skip += skip; total_err += err
        else:
            print(f'  No PTR records found for {year}')

    print(f'\n━━ Done: {total_ok} new PDFs, {total_skip} already present, {total_err} errors ━━')
    print('Next step: python build_transactions.py')
