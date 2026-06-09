"""
download_annual_filings.py
Download House and Senate annual financial disclosure filings for a given year.

"Year" refers to the *calendar year covered* by the disclosure:
  --year 2025  →  House {YEAR}FD.zip (reporting-year convention) + Senate eFD Jan–Dec 2026

House bulk ZIP naming: {YEAR}FD.zip contains filings with Year=YEAR.
Annual reports for year N are due May 15 of N+1, but the House Clerk updates
the bulk ZIP with a significant lag (potentially months after the deadline).
If you run this soon after the deadline and get very few annuals, the ZIP
hasn't been fully populated yet — check back in a few months.

Usage:
    python download_annual_filings.py --year 2025
    python download_annual_filings.py --year 2024   # re-download / update 2024 data
"""
import argparse, csv, io, json, os, re, time, urllib.parse, urllib.request
import http.cookiejar, zipfile

parser = argparse.ArgumentParser()
parser.add_argument('--year', type=int, required=True, help='Reporting year (e.g. 2025)')
parser.add_argument('--house-only',  action='store_true')
parser.add_argument('--senate-only', action='store_true')
args = parser.parse_args()

YEAR     = args.year
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
HEADERS  = {'User-Agent': 'Mozilla/5.0 (public-interest research; congressional disclosures)'}

do_house  = not args.senate_only
do_senate = not args.house_only


# ══════════════════════════════════════════════════════════════════════════════
#  HOUSE
# ══════════════════════════════════════════════════════════════════════════════
def run_house():
    # The bulk ZIP is named by reporting year: {YEAR}FD.zip contains rows with Year=YEAR.
    # NOTE: The Clerk updates the ZIP with a lag after the May 15 filing deadline.
    # If you see very few 'O' annuals, the ZIP isn't fully populated yet.
    zip_url  = f'https://disclosures-clerk.house.gov/public_disc/financial-pdfs/{YEAR}FD.zip'
    tsv_path = os.path.join(BASE_DIR, f'house_members_{YEAR}.tsv')
    pdf_dir  = os.path.join(BASE_DIR, f'house_pdfs_{YEAR}')
    pdf_base = f'https://disclosures-clerk.house.gov/public_disc/financial-pdfs/{YEAR}/{{doc_id}}.pdf'
    os.makedirs(pdf_dir, exist_ok=True)

    print(f'\n── House {YEAR} ──────────────────────────────────────────────')
    print(f'Downloading {zip_url} …')
    req  = urllib.request.Request(zip_url, headers=HEADERS)
    try:
        resp = urllib.request.urlopen(req, timeout=60)
    except urllib.error.HTTPError as e:
        print(f'ERROR: {e.code} {e.reason}')
        if e.code == 404:
            print(f'  {YEAR}FD.zip not yet available on the House server.')
        return
    data = resp.read()
    print(f'  Downloaded {len(data)//1024} KB')

    zf = zipfile.ZipFile(io.BytesIO(data))
    data_file = next((n for n in zf.namelist()
                      if n.lower().endswith(('.txt', '.xml', '.csv'))), None)
    if not data_file:
        print('ERROR: No data file in ZIP:', zf.namelist()); return
    raw = zf.read(data_file).decode('utf-8', errors='replace')
    print(f'  Data file: {data_file} ({len(raw)//1024} KB)')

    # Parse — pipe or tab delimited
    header_line = raw.split('\n', 1)[0]
    delim = '|' if '|' in header_line else ('\t' if '\t' in header_line else ',')
    all_rows = list(csv.DictReader(io.StringIO(raw), delimiter=delim))
    print(f'  Total rows: {len(all_rows)}  columns: {list(all_rows[0].keys()) if all_rows else []}')

    # Show FilingType breakdown so we can diagnose unexpected codes
    from collections import Counter as _Counter
    ft_counts = _Counter(r.get('FilingType','').strip() for r in all_rows)
    print(f'  FilingType counts: ' + ', '.join(f'{k}={v}' for k,v in ft_counts.most_common()))

    def is_annual(row):
        ft = row.get('FilingType', row.get('Filing_Type', '')).strip()
        # O = Original Annual, A = Amendment to Annual, OA = Original Annual (some years)
        return ft in ('O', 'A', 'OA') or 'annual' in ft.lower()

    def get_docid(row):
        for k in ['DocID', 'doc_id', 'DocumentID', 'ID', 'DOCID']:
            v = row.get(k, '').strip()
            if v: return v
        return ''

    def get_name(row):
        last  = row.get('Last',  row.get('LastName',  '')).strip()
        first = row.get('First', row.get('FirstName', '')).strip()
        return last, first

    def get_office(row):
        return row.get('StateDst', row.get('Office', row.get('StateDist', ''))).strip()

    annual_rows = [r for r in all_rows if is_annual(r)]
    print(f'  Annual reports: {len(annual_rows)}')
    if len(annual_rows) < 50:
        print(f'  ⚠ WARNING: Expected ~430 annual reports for sitting members.')
        print(f'    The House Clerk updates the bulk ZIP with a significant lag after')
        print(f'    the May 15 filing deadline. Only {len(annual_rows)} found — the ZIP')
        print(f'    for year {YEAR} may not be fully populated yet. Check back in a few months.')
        print(f'    Individual PDFs are available via the House Clerk search UI in the meantime.')

    # Deduplicate — keep one per member
    seen = {}
    for row in annual_rows:
        last, first = get_name(row)
        key = (last.lower(), first.lower()[:3])
        if key not in seen:
            seen[key] = row

    # Load existing TSV to detect additions
    existing_docids = set()
    if os.path.exists(tsv_path):
        with open(tsv_path, newline='', encoding='utf-8') as f:
            for r in csv.DictReader(f, delimiter='\t'):
                existing_docids.add(r.get('DOCID','').strip())
        print(f'  Existing TSV: {len(existing_docids)} entries')

    # Write updated TSV
    tsv_rows = []
    new_members = []
    for key, row in seen.items():
        last, first = get_name(row)
        docid  = get_docid(row)
        office = get_office(row)
        if not docid: continue
        name = f'{last}, Hon.. {first}'
        tsv_rows.append({'NAME': name, 'OFFICE': office, 'DOCID': docid})
        if docid not in existing_docids:
            new_members.append((name, office, docid))

    tsv_rows.sort(key=lambda r: r['NAME'])
    with open(tsv_path, 'w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=['NAME','OFFICE','DOCID'], delimiter='\t')
        w.writeheader()
        w.writerows(tsv_rows)
    print(f'  TSV: {tsv_path} ({len(tsv_rows)} members, {len(new_members)} new)')

    # Download PDFs
    to_download = [(n,o,d) for n,o,d in new_members
                   if not (os.path.exists(os.path.join(pdf_dir,f'{d}.pdf'))
                           and os.path.getsize(os.path.join(pdf_dir,f'{d}.pdf')) > 500)]
    if not to_download:
        print('  All PDFs already present.')
        return
    print(f'  Downloading {len(to_download)} PDFs …')
    opener = urllib.request.build_opener()
    opener.addheaders = list(HEADERS.items())
    ok = err = 0
    for i, (name, office, docid) in enumerate(to_download, 1):
        dest = os.path.join(pdf_dir, f'{docid}.pdf')
        url  = pdf_base.format(doc_id=docid)
        try:
            r = opener.open(url, timeout=20)
            with open(dest, 'wb') as f:
                f.write(r.read())
            ok += 1
            print(f'  [{i}/{len(to_download)}] ok   {name} {os.path.getsize(dest)//1024}KB')
        except Exception as e:
            err += 1
            print(f'  [{i}/{len(to_download)}] ERR  {name}: {e}')
        time.sleep(0.3)
    print(f'  House done: {ok} downloaded, {err} errors')


# ══════════════════════════════════════════════════════════════════════════════
#  SENATE
# ══════════════════════════════════════════════════════════════════════════════
def run_senate():
    # Annual reports for calendar year YEAR are submitted the following year.
    # Senate eFD date format is MM/DD/YYYY HH:MM:SS.
    submit_year = YEAR + 1
    date_from   = f'01/01/{submit_year} 00:00:00'
    date_to     = f'12/31/{submit_year} 23:59:59'

    tsv_path = os.path.join(BASE_DIR, f'senate_members_{YEAR}.tsv')
    html_dir = os.path.join(BASE_DIR, f'senate_html_{YEAR}')
    os.makedirs(html_dir, exist_ok=True)

    HOME   = 'https://efdsearch.senate.gov/search/home/'
    SEARCH = 'https://efdsearch.senate.gov/search/report/data/'

    print(f'\n── Senate {YEAR} ─────────────────────────────────────────────')
    print(f'  Querying eFD for annual reports submitted {date_from} – {date_to} …')

    jar    = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    opener.addheaders = [
        ('User-Agent', 'Mozilla/5.0 (public-interest research; congressional disclosures)'),
        ('Accept', 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8'),
    ]

    # Step 1: GET home page to obtain CSRF token
    html  = opener.open(HOME, timeout=20).read().decode('utf-8')
    csrf_m = re.search(r'csrfmiddlewaretoken[^>]+value="([^"]+)"', html)
    token  = csrf_m.group(1) if csrf_m else ''
    if not token:
        for c in jar:
            if c.name == 'csrftoken':
                token = c.value

    # Step 2: POST prohibition agreement to activate session
    req = urllib.request.Request(
        HOME,
        urllib.parse.urlencode({'csrfmiddlewaretoken': token, 'prohibition_agreement': '1'}).encode(),
        headers={'Referer': HOME, 'Content-Type': 'application/x-www-form-urlencoded'},
    )
    opener.open(req, timeout=20)
    # Refresh CSRF from cookie (updated after POST)
    for c in jar:
        if c.name == 'csrftoken':
            token = c.value
    print(f'  Session established (csrf={token[:8]}…)')

    def search_page(start, length=100):
        payload = urllib.parse.urlencode({
            'csrfmiddlewaretoken':       token,   # required — missing was the 403 cause
            'draw': '1',
            'columns[0][data]': 'first_name', 'columns[0][name]': '',
            'columns[0][searchable]': 'true', 'columns[0][orderable]': 'true',
            'columns[0][search][value]': '', 'columns[0][search][regex]': 'false',
            'columns[1][data]': 'last_name',  'columns[1][name]': '',
            'columns[1][searchable]': 'true', 'columns[1][orderable]': 'true',
            'columns[1][search][value]': '', 'columns[1][search][regex]': 'false',
            'columns[2][data]': 'office',     'columns[2][name]': '',
            'columns[2][searchable]': 'true', 'columns[2][orderable]': 'true',
            'columns[2][search][value]': '', 'columns[2][search][regex]': 'false',
            'columns[3][data]': 'report_type','columns[3][name]': '',
            'columns[3][searchable]': 'true', 'columns[3][orderable]': 'true',
            'columns[3][search][value]': '', 'columns[3][search][regex]': 'false',
            'columns[4][data]': 'date_received','columns[4][name]': '',
            'columns[4][searchable]': 'true', 'columns[4][orderable]': 'true',
            'columns[4][search][value]': '', 'columns[4][search][regex]': 'false',
            'columns[5][data]': 'link',       'columns[5][name]': '',
            'columns[5][searchable]': 'false','columns[5][orderable]': 'false',
            'columns[5][search][value]': '', 'columns[5][search][regex]': 'false',
            'order[0][column]': '4', 'order[0][dir]': 'desc',
            'start': str(start), 'length': str(length),
            'search[value]': '', 'search[regex]': 'false',
            'report_types': '[7,10]',    # 7=Annual, 10=Annual (Amendment)
            'submitted_start_date': date_from,
            'submitted_end_date':   date_to,
        }).encode()
        req = urllib.request.Request(SEARCH, payload, {
            'Referer':           HOME,
            'X-Requested-With':  'XMLHttpRequest',
            'Content-Type':      'application/x-www-form-urlencoded',
            'Accept':            'application/json, text/javascript, */*; q=0.01',
        })
        try:
            return json.loads(opener.open(req, timeout=30).read().decode('utf-8'))
        except Exception as e:
            print(f'  Search error at start={start}: {e}')
            return {}

    # Page through all results
    all_records = []
    start = 0
    length = 100
    total = None
    while True:
        result = search_page(start, length)
        if not result or 'data' not in result:
            break
        if total is None:
            total = result.get('recordsTotal', '?')
            print(f'  Total records: {total}')
        batch = result['data']
        all_records.extend(batch)
        print(f'  Fetched {len(all_records)} / {total}', end='\r')
        if len(batch) < length:
            break
        start += length
        time.sleep(0.5)
    print(f'\n  Found {len(all_records)} annual report records')

    if not all_records:
        print('  No records found — check report_types codes or date range')
        print(f'  Sample report_type values (from a manual eFD search) would help diagnose this.')
        return

    # Print sample to help diagnose report_type codes
    print('  Sample records:')
    for r in all_records[:3]:
        print(f'    {r}')

    # Extract UUID from link HTML: <a href="/search/view/annual/{uuid}/">
    def extract_uuid(link_html):
        m = re.search(r'/search/view/(?:annual|ptr)/([0-9a-f-]+)/', link_html or '')
        return m.group(1) if m else ''

    # The Senate eFD returns each record as a list:
    #   [first_name, last_name, office, link_html, date_received]
    # (report_type is embedded in the link anchor text, not a separate field)
    def rec_field(rec, dict_key, list_idx):
        if isinstance(rec, list):
            return rec[list_idx] if list_idx < len(rec) else ''
        return rec.get(dict_key, '')

    def is_senator(record):
        office = rec_field(record, 'office', 2)
        return 'Senator' in office or 'Sen.' in office

    # Build TSV rows — senators only, deduplicate (keep original over amendment)
    seen = {}  # last_name.lower() → record
    for rec in all_records:
        if not is_senator(rec):
            continue
        link_html = rec_field(rec, 'link', 3)
        uuid = extract_uuid(link_html)
        if not uuid:
            continue
        first = rec_field(rec, 'first_name', 0).strip()
        last  = rec_field(rec, 'last_name',  1).strip()
        date  = rec_field(rec, 'date_received', 4).strip()
        filer = rec_field(rec, 'office', 2).strip()
        # Prefer original annual over amendment (check link text)
        rtype = link_html  # UUID is the same; just use link text presence
        key   = last.lower()
        is_amendment = 'Amendment' in rtype
        if key not in seen or not is_amendment:
            seen[key] = {
                'NAME': f'{first} {last}',
                'FILER': filer,
                'DATE':  date,
                'URL':   f'https://efdsearch.senate.gov/search/view/annual/{uuid}/',
            }

    tsv_rows = sorted(seen.values(), key=lambda r: r['NAME'])
    with open(tsv_path, 'w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=['NAME','FILER','DATE','URL'], delimiter='\t')
        w.writeheader()
        w.writerows(tsv_rows)
    print(f'  TSV: {tsv_path} ({len(tsv_rows)} senators)')

    # Load existing HTML dir to skip already-downloaded
    already = {os.path.splitext(fn)[0] for fn in os.listdir(html_dir) if fn.endswith('.html')}

    # Download HTML
    to_fetch = [(r['NAME'], r['URL']) for r in tsv_rows
                if r['URL'].rstrip('/').split('/')[-1] not in already]
    if not to_fetch:
        print('  All HTML files already present.')
        return
    print(f'  Downloading {len(to_fetch)} HTML files …')
    ok = err = 0
    for i, (name, url) in enumerate(to_fetch, 1):
        uuid = url.rstrip('/').split('/')[-1]
        dest = os.path.join(html_dir, f'{uuid}.html')
        try:
            html = opener.open(url, timeout=20).read().decode('utf-8')
            with open(dest, 'w', encoding='utf-8') as f:
                f.write(html)
            ok += 1
            print(f'  [{i}/{len(to_fetch)}] ok   {name} ({len(html)//1024}KB)')
        except Exception as e:
            err += 1
            print(f'  [{i}/{len(to_fetch)}] ERR  {name}: {e}')
        time.sleep(0.4)
    print(f'  Senate done: {ok} downloaded, {err} errors')


# ══════════════════════════════════════════════════════════════════════════════
if do_house:
    run_house()
if do_senate:
    run_senate()

print(f"""
━━ Done ━━
Next steps:
  python build_db.py              # rebuild holdings (parses new PDFs + HTML)
  python ocr_scanned_pdfs.py     # OCR any newly-scanned PDFs (needs ANTHROPIC_API_KEY)
""")
