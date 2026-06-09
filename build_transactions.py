"""
build_transactions.py
Parse House and Senate PTR files and populate the `transactions` table in holdings.db.

Run after download_house_ptrs.py and download_senate_ptrs.py:
    python build_transactions.py

Schema added to holdings.db:
    transactions(id, chamber, last_name, first_name, state, district,
                 transaction_date, disclosure_date, asset, asset_type,
                 transaction_type, amount, ticker, comment, source_file)
"""
import os, re, sqlite3, glob, csv, urllib.request, zipfile, io, argparse
import pdfplumber
from bs4 import BeautifulSoup
import name_resolver

BASE    = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE, 'holdings.db')

# ── Args ──────────────────────────────────────────────────────────────────────
_ap = argparse.ArgumentParser(description='Build transactions table')
_ap.add_argument('--years', nargs='+', type=int, metavar='YEAR',
                 help='Only process these years (append mode — skips full rebuild)')
args = _ap.parse_args()
APPEND_YEARS = set(args.years) if args.years else None

# ── DB setup ──────────────────────────────────────────────────────────────────
conn = sqlite3.connect(DB_PATH)
_SCHEMA = """
CREATE TABLE IF NOT EXISTS transactions (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    chamber          TEXT,
    last_name        TEXT,
    first_name       TEXT,
    state            TEXT,
    district         TEXT,
    transaction_date TEXT,
    disclosure_date  TEXT,
    asset            TEXT,
    asset_type       TEXT,
    transaction_type TEXT,
    amount           TEXT,
    ticker           TEXT,
    comment          TEXT,
    source_file      TEXT,
    bioguide         TEXT
)"""
if APPEND_YEARS:
    conn.execute(_SCHEMA)
    # Remove any existing rows for the years being re-processed (idempotent re-run)
    for _yr in APPEND_YEARS:
        conn.execute("DELETE FROM transactions WHERE source_file IN "
                     "(SELECT DISTINCT source_file FROM transactions "
                     " WHERE source_file LIKE ? OR source_file LIKE ?)",
                     (f'%_{_yr}_%', f'%{_yr}%'))
    conn.commit()
    print(f"Append mode: processing years {sorted(APPEND_YEARS)}", flush=True)
else:
    conn.execute("DROP TABLE IF EXISTS transactions")
    conn.execute(_SCHEMA.replace("IF NOT EXISTS ", ""))
    conn.commit()
    print("transactions table created (full rebuild)", flush=True)

def clean(s):
    if s is None: return ''
    return re.sub(r'[\x00-\x08\x0b-\x1f\x7f]', '', str(s)).strip()

TICKER_RE = re.compile(r'\b([A-Z]{1,5})\b')
DATE_RE   = re.compile(r'(\d{1,2})[/\-](\d{1,2})[/\-](\d{2,4})')

def normalize_date(s):
    """Convert MM/DD/YYYY or MM-DD-YYYY to YYYY-MM-DD."""
    s = clean(s)
    m = DATE_RE.search(s)
    if not m: return s
    mo, dy, yr = m.group(1), m.group(2), m.group(3)
    if len(yr) == 2:
        yr = ('20' + yr) if int(yr) < 50 else ('19' + yr)
    return f'{yr}-{mo.zfill(2)}-{dy.zfill(2)}'

def extract_ticker(asset_name):
    """Best-effort ticker extraction from asset name like 'Apple Inc (AAPL)'."""
    m = re.search(r'\(([A-Z]{1,5})\)', asset_name)
    if m: return m.group(1)
    # Some use brackets
    m = re.search(r'\[([A-Z]{1,5})\]', asset_name)
    if m: return m.group(1)
    return ''


# Regex for parsing smashed House PTR rows (all data merged into column 0)
_SMASH_AMOUNT_RE  = re.compile(r'(\\$[\d,]+(?:\.\d+)?\s*[-–]\s*\\$[\d,]+(?:\.\d+)?)', re.I)
_SMASH_DATE_RE    = re.compile(r'(\d{2}/\d{2}/\d{4})')
_SMASH_OWNER_RE   = re.compile(r'^(DC|SP|JT|dep\.?\s*child)\s+', re.I)
_SMASH_TXTYPE_RE  = re.compile(
    r'(S\s*\(partial\)|S\s*\(full\)|Sale\s*\(partial\)|Sale\s*\(full\)|'
    r'Exchange\s+\(New\)|Received\s*\(New\)|P)', re.I)
_FS_NEW_RE        = re.compile(r'^F\s+S\s*:', re.I)

def _parse_smashed_row(text):
    """Parse a House PTR row where pdfplumber merged all columns into one cell.
    Format: '{owner} {asset_part1} {tx_type} {dates} {amount}\n{asset_part2}\nF S: New'
    Returns dict or None.
    """
    # Strip "F S: New" section label lines
    lines = [l for l in text.split('\n') if not _FS_NEW_RE.match(l.strip())]

    # Fix split amounts: "$X,XXX -\n...text...$Y,YYY" -> "$X,XXX - $Y,YYY" on one line
    fixed = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if re.search(r'\$[\d,]+\s*-\s*$', line) and i + 1 < len(lines):
            m = re.search(r'\$[\d,]+(?:\.\d+)?', lines[i + 1])
            if m:
                line = line.rstrip() + ' ' + m.group(0)
                rest = (lines[i + 1][:m.start()] + lines[i + 1][m.end():]).strip()
                fixed.append(line)
                if rest:
                    fixed.append(rest)
                i += 2
                continue
        fixed.append(line)
        i += 1
    text = ' '.join(fixed).strip()

    # Extract amount ($X - $Y)
    m = re.search(r'(\$[\d,]+\s*[-\u2013]\s*\$[\d,]+)', text)
    amount = m.group(1).strip() if m else ''
    if amount:
        idx = text.rfind(amount)
        text = (text[:idx] + text[idx + len(amount):]).strip()

    # Extract two dates
    dates = _SMASH_DATE_RE.findall(text)
    tx_date   = normalize_date(dates[0]) if dates else ''
    disc_date = normalize_date(dates[1]) if len(dates) > 1 else ''
    for d in dates[:2]:
        text = text.replace(d, '', 1)
    text = re.sub(r'\s+', ' ', text).strip()

    # Find transaction type
    m = _SMASH_TXTYPE_RE.search(text)
    if not m:
        return None
    raw_tx = m.group(1)
    t = raw_tx.lower().replace(' ', '')
    if 'partial' in t:      tx_type = 'Sale (Partial)'
    elif 'full' in t:       tx_type = 'Sale (Full)'
    elif 'exchange' in t:   tx_type = 'Exchange'
    elif 'received' in t:   tx_type = 'Received'
    elif t.startswith('p'): tx_type = 'Purchase'
    else:                   tx_type = raw_tx.strip().title()

    # Asset = before tx_type + after tx_type (dates/amount already removed)
    before = text[:m.start()].strip()
    after  = text[m.end():].strip()
    om = _SMASH_OWNER_RE.match(before)
    before = before[om.end():].strip() if om else before
    asset = (before + ' ' + after).strip() if after else before
    asset = re.sub(r'\s+', ' ', asset).strip()
    if not asset:
        return None

    return {
        'asset': asset, 'tx_type': tx_type,
        'tx_date': tx_date, 'disc_date': disc_date,
        'amount': amount, 'owner': (om.group(1).upper() if om else 'DC'),
        'atype': '', 'comment': '',
    }

def insert_rows(rows):
    conn.executemany("""
        INSERT INTO transactions
          (chamber, last_name, first_name, state, district,
           transaction_date, disclosure_date, asset, asset_type,
           transaction_type, amount, ticker, comment, source_file, bioguide)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, rows)
    conn.commit()

# ── House PTR member lookup ──────────────────────────────────────────────────
# Preferred source: house_ptrs_{year}/manifest.tsv (written by download_house_ptrs.py).
# Fallback: re-download the bulk FD ZIP and cache the manifest for future runs.

ZIP_URL     = 'https://disclosures-clerk.house.gov/public_disc/financial-pdfs/{year}FD.zip'
ZIP_HEADERS = {'User-Agent': 'Mozilla/5.0 (public-interest research; congressional disclosures)'}

def load_house_ptr_lookup_for_year(year):
    """Return doc_id → {last, first, state, district} for one year's PTR filings."""
    dest_dir = os.path.join(BASE, f'house_ptrs_{year}')
    manifest = os.path.join(dest_dir, 'manifest.tsv')

    if os.path.exists(manifest):
        lookup = {}
        with open(manifest, newline='', encoding='utf-8') as f:
            for row in csv.DictReader(f, delimiter='\t'):
                doc_id = row.get('doc_id', '').strip()
                if not doc_id: continue
                office = row.get('office', '').strip()
                last   = row.get('last',   '').strip()
                first  = row.get('first',  '').strip()
                state  = re.match(r'([A-Z]{2})', office).group(1) if re.match(r'([A-Z]{2})', office) else ''
                dist   = re.search(r'\d+', office).group(0)        if re.search(r'\d+', office)       else ''
                lookup[doc_id] = {'last': last, 'first': first, 'state': state, 'district': dist}
        print(f'  Loaded {len(lookup)} House PTR names for {year} from manifest.tsv')
        return lookup

    # Manifest missing — download bulk ZIP and build it
    url = ZIP_URL.format(year=year)
    print(f'  manifest.tsv not found; fetching {url} …')
    try:
        req  = urllib.request.Request(url, headers=ZIP_HEADERS)
        data = urllib.request.urlopen(req, timeout=60).read()
        zf   = zipfile.ZipFile(io.BytesIO(data))
    except Exception as e:
        print(f'  WARN: Could not fetch House bulk ZIP for {year}: {e}')
        return {}

    data_file = next((n for n in zf.namelist() if n.lower().endswith(('.txt', '.csv', '.xml'))), None)
    if not data_file:
        print(f'  WARN: No data file found in ZIP for {year}')
        return {}

    raw   = zf.read(data_file).decode('utf-8', errors='replace')
    first_line = raw.split('\n')[0]
    delim = '|' if '|' in first_line else ('\t' if '\t' in first_line else ',')

    lookup        = {}
    manifest_rows = []
    for row in csv.DictReader(io.StringIO(raw), delimiter=delim):
        if row.get('FilingType', row.get('Filing_Type', '')).strip() != 'P':
            continue
        doc_id = row.get('DocID', row.get('doc_id', '')).strip()
        if not doc_id: continue
        last_  = row.get('Last',    row.get('LastName',  '')).strip()
        first_ = row.get('First',   row.get('FirstName', '')).strip()
        office = row.get('StateDst', row.get('StateDist', row.get('Office', ''))).strip()
        state  = re.match(r'([A-Z]{2})', office).group(1) if re.match(r'([A-Z]{2})', office) else ''
        dist   = re.search(r'\d+', office).group(0)        if re.search(r'\d+', office)       else ''
        lookup[doc_id] = {'last': last_, 'first': first_, 'state': state, 'district': dist}
        manifest_rows.append({'doc_id': doc_id, 'last': last_, 'first': first_, 'office': office})

    print(f'  Downloaded {len(lookup)} House PTR records for {year}')

    # Cache manifest so future runs skip the download
    if os.path.isdir(dest_dir) and manifest_rows:
        try:
            with open(manifest, 'w', newline='', encoding='utf-8') as f:
                w = csv.DictWriter(f, fieldnames=['doc_id', 'last', 'first', 'office'], delimiter='\t')
                w.writeheader()
                w.writerows(manifest_rows)
            print(f'  Cached to {manifest}')
        except Exception as e:
            print(f'  WARN: Could not save manifest: {e}')

    return lookup


# Build combined lookup from all house_ptrs_{year} directories
house_members = {}  # doc_id → {last, first, state, district}
for _yr_dir in sorted(glob.glob(os.path.join(BASE, 'house_ptrs_*'))):
    _m = re.search(r'(\d{4})$', _yr_dir)
    if _m:
        house_members.update(load_house_ptr_lookup_for_year(int(_m.group(1))))
print(f'House PTR lookup: {len(house_members)} total records')

# ── Senate member lookup ──────────────────────────────────────────────────────
senate_members = {}
tsv_path2 = os.path.join(BASE, 'senate_members_2024.tsv')
if os.path.exists(tsv_path2):
    with open(tsv_path2, newline='', encoding='utf-8') as f:
        for row in csv.DictReader(f, delimiter='\t'):
            name  = row.get('NAME', '').strip()
            state = row.get('STATE', '').strip()
            url   = row.get('URL', '').strip()
            uuid  = url.rstrip('/').split('/')[-1]
            senate_members[uuid] = {'name': name, 'state': state}

_HON_RE_TX = re.compile(r'(?i)\b(?:the\s+honorable|hon\.\.?)\s*')

def split_name(full_name):
    """Split 'Last, First' or 'First Last' into (last, first), stripping Hon. prefix."""
    full_name = _HON_RE_TX.sub('', full_name).strip()
    if ',' in full_name:
        parts = full_name.split(',', 1)
        last  = parts[0].strip()
        first = parts[1].strip()
    else:
        parts = full_name.split()
        last  = parts[-1] if parts else full_name
        first = ' '.join(parts[:-1])
    return clean(last), clean(first)

# ── Parse House PTR PDFs ──────────────────────────────────────────────────────
# House PTR table columns (approximate, varies slightly by form version):
#   Asset Name | Asset Type | Owner | Transaction Type | Date | Notification Date | Amount | Comment
#
# Some forms order as:
#   Asset Name | Owner | Transaction Type | Transaction Date | Notification Date | Amount | Comment

HOUSE_TX_TYPE_RE = re.compile(
    r'\b(purchase|sale\s*\(full\)|sale\s*\(partial\)|exchange|received)\b', re.I)
AMOUNT_RE = re.compile(r'\$[\d,]+\s*[-–]\s*\$[\d,]+')

def parse_house_ptr_pdf(path, bioguide=None):
    """Extract transaction rows from a House PTR PDF."""
    rows = []
    doc_id   = os.path.splitext(os.path.basename(path))[0]
    info     = house_members.get(doc_id, {})
    last     = info.get('last',     '')
    first    = info.get('first',    '')
    state    = info.get('state',    '')
    district = info.get('district', '')

    try:
        with pdfplumber.open(path) as pdf:
            for page in pdf.pages:
                tables = page.extract_tables()
                for table in tables:
                    if not table: continue
                    # Detect header row
                    header = [clean(c).lower() for c in (table[0] or [])]
                    is_ptr_table = any('transaction' in h or 'amount' in h for h in header)
                    if not is_ptr_table:
                        continue

                    # Map columns
                    col = {}
                    for i, h in enumerate(header):
                        if 'asset' in h and 'type' not in h:  col.setdefault('asset', i)
                        if 'type' in h and 'transaction' not in h: col.setdefault('asset_type', i)
                        if 'owner' in h:                       col.setdefault('owner', i)
                        if 'transaction type' in h or 'trans type' in h: col.setdefault('tx_type', i)
                        if 'transaction date' in h or ('date' in h and 'notif' not in h): col.setdefault('tx_date', i)
                        if 'notif' in h:                       col.setdefault('disc_date', i)
                        if 'amount' in h:                      col.setdefault('amount', i)
                        if 'comment' in h or 'description' in h: col.setdefault('comment', i)

                    for data_row in table[1:]:
                        if not data_row or all(not c for c in data_row):
                            continue
                        def g(key, default=''):
                            idx = col.get(key)
                            if idx is None or idx >= len(data_row): return default
                            return clean(data_row[idx])

                        # Handle smashed rows (pdfplumber merged all cols into col 0)
                        col0 = clean(data_row[0]) if data_row else ''
                        is_smashed = col0 and all(
                            not clean(c) for c in (data_row[1:] if len(data_row) > 1 else [])
                        )

                        if is_smashed:
                            parsed = _parse_smashed_row(col0)
                            if not parsed:
                                continue
                            asset     = parsed['asset']
                            tx_type   = parsed['tx_type']
                            tx_date   = parsed['tx_date']
                            disc_date = parsed['disc_date']
                            amount    = parsed['amount']
                            atype     = parsed['atype']
                            comment   = parsed['comment']
                        else:
                            asset   = g('asset')
                            if not asset: continue   # skip empty rows
                            # Skip PDF section-label rows ("F S: New" in asset col)
                            if _FS_NEW_RE.match(asset):
                                continue

                            tx_type  = g('tx_type')
                            tx_date  = normalize_date(g('tx_date'))
                            disc_date = normalize_date(g('disc_date'))
                            amount   = g('amount')
                            comment  = g('comment')
                            atype    = g('asset_type')

                            # Normalize transaction type
                            m = HOUSE_TX_TYPE_RE.search(tx_type)
                            if m:
                                t = m.group(1).lower()
                                if 'purchase' in t: tx_type = 'Purchase'
                                elif 'partial' in t: tx_type = 'Sale (Partial)'
                                elif 'full' in t: tx_type = 'Sale (Full)'
                                elif 'exchange' in t: tx_type = 'Exchange'
                                elif 'received' in t: tx_type = 'Received'
                                else: tx_type = t.title()

                        # Strip PDF section-label artifacts embedded in asset name:
                        #   "F S: [section]"  Filing Section label (e.g. "F S: New")
                        #   "S O:" prefix     Schedule O label (e.g. "S O: JP Morgan ...")
                        asset = re.sub(r'\s*F\s+S\s*:.*$', '', asset).strip()
                        asset = re.sub(r'^S\s+O\s*:\s*', '', asset).strip()
                        ticker   = extract_ticker(asset)

                        rows.append((
                            'House', last, first, state, district,
                            tx_date, disc_date, asset, atype,
                            tx_type, amount, ticker, comment,
                            os.path.basename(path), bioguide
                        ))
    except Exception as e:
        print(f'  WARN: {os.path.basename(path)}: {e}')

    return rows


# ── Parse Senate PTR HTML ──────────────────────────────────────────────────────
# Senate PTR HTML tables have columns similar to:
#   Asset Name | Asset Type | Owner | Transaction Type | Amount | Transaction Date | Notification Date | Comment

def parse_senate_ptr_html(path, bioguide=None):
    rows = []
    uuid   = os.path.splitext(os.path.basename(path))[0]
    info   = senate_members.get(uuid, {})
    name   = info.get('name', '')
    state  = info.get('state', '')
    last, first = split_name(name) if name else ('', '')

    try:
        with open(path, encoding='utf-8', errors='replace') as f:
            soup = BeautifulSoup(f.read(), 'html.parser')

        # Try to get name/state from page if not in TSV.
        # Senate PTR page structure:
        #   H1 = "Periodic Transaction Report for MM/DD/YYYY"  ← skip this
        #   H2 = "The Honorable First [M] Last [Jr.] (Last, First)"
        # The parenthetical "(Last, First)" is the most reliable anchor.
        if not name:
            for h in soup.find_all(['h2', 'h3']):
                text = clean(h.get_text())
                # Prefer parenthetical "(Last, First)" — reliable canonical form
                m = re.search(r'\(([A-Za-z][^,)]{1,30},\s*[A-Za-z][^)]{1,30})\)', text)
                if m:
                    name = m.group(1)
                    last, first = split_name(name)
                    break
                # Fallback: "The Honorable ..." heading (no parenthetical)
                if re.search(r'(?i)the\s+honorable', text):
                    # Strip title prefix and any trailing suffix like "Jr."
                    name = re.sub(r'(?i)the\s+honorable\s*', '', text).strip()
                    name = re.sub(r'\s+(jr|sr|ii|iii|iv)\.?$', '', name, flags=re.I).strip()
                    # Strip office qualifiers: "(Former Senator)", "(Senator)", etc.
                    # These appear for resigned/retired members and corrupt split_name.
                    name = re.sub(
                        r'\s*\((?:Former\s+)?(?:Senator|Representative|Congressman|Member)\s*\).*$',
                        '', name, flags=re.I).strip()
                    last, first = split_name(name)
                    break

        # Find all tables — look for a transaction table
        for table in soup.find_all('table'):
            headers = [clean(th.get_text()).lower() for th in table.find_all('th')]
            if not any('asset' in h or 'transaction' in h for h in headers):
                continue

            # Map column indices
            col = {}
            for i, h in enumerate(headers):
                if 'asset name' in h or ('asset' in h and 'type' not in h): col.setdefault('asset', i)
                if 'asset type' in h:          col.setdefault('asset_type', i)
                if 'owner' in h:               col.setdefault('owner', i)
                if 'transaction type' in h:    col.setdefault('tx_type', i)
                if 'amount' in h:              col.setdefault('amount', i)
                if 'transaction date' in h or ('date' in h and 'notif' not in h): col.setdefault('tx_date', i)
                if 'notif' in h or 'disclosure' in h: col.setdefault('disc_date', i)
                if 'comment' in h:             col.setdefault('comment', i)

            for tr in table.find_all('tr')[1:]:
                cells = [clean(td.get_text()) for td in tr.find_all(['td','th'])]
                if not cells: continue

                def g(key, default=''):
                    idx = col.get(key)
                    if idx is None or idx >= len(cells): return default
                    return cells[idx]

                asset = g('asset')
                if not asset or asset.lower() in ('asset name', 'n/a', ''): continue

                tx_type  = g('tx_type')
                tx_date  = normalize_date(g('tx_date'))
                disc_date = normalize_date(g('disc_date'))
                amount   = g('amount')
                comment  = g('comment')
                atype    = g('asset_type')
                ticker   = extract_ticker(asset)

                m = HOUSE_TX_TYPE_RE.search(tx_type)
                if m:
                    t = m.group(1).lower()
                    if 'purchase' in t: tx_type = 'Purchase'
                    elif 'partial' in t: tx_type = 'Sale (Partial)'
                    elif 'full' in t: tx_type = 'Sale (Full)'
                    elif 'exchange' in t: tx_type = 'Exchange'
                    else: tx_type = t.title()

                rows.append((
                    'Senate', last, first, state, '',
                    tx_date, disc_date, asset, atype,
                    tx_type, amount, ticker, comment,
                    os.path.basename(path), bioguide
                ))

    except Exception as e:
        print(f'  WARN: {os.path.basename(path)}: {e}')

    return rows


# ── Load name resolver (downloads YAMLs once) ─────────────────────────────────
resolver = name_resolver.NameResolver()

# ── Main ──────────────────────────────────────────────────────────────────────
total = 0

# House PDFs
for year_dir in sorted(glob.glob(os.path.join(BASE, 'house_ptrs_*'))):
    _yr_m = re.search(r'(\d{4})$', year_dir)
    filing_year = int(_yr_m.group(1)) if _yr_m else 2024
    if APPEND_YEARS and filing_year not in APPEND_YEARS:
        continue
    pdfs = sorted(glob.glob(os.path.join(year_dir, '*.pdf')))
    print(f"\n── Parsing {len(pdfs)} House PTR PDFs from {os.path.basename(year_dir)} ──", flush=True)
    for i, pdf in enumerate(pdfs, 1):
        doc_id = os.path.splitext(os.path.basename(pdf))[0]
        info = house_members.get(doc_id, {})
        last, first = info.get('last', ''), info.get('first', '')
        bioguide = resolver.resolve(last, first, filing_year) if last else None
        if last and bioguide is None:
            print(f'  WARN unmatched: {last}, {first} (House, {os.path.basename(pdf)})')
        rows = parse_house_ptr_pdf(pdf, bioguide=bioguide)
        if rows:
            insert_rows(rows)
            total += len(rows)
        if i % 20 == 0 or i == len(pdfs):
            print(f'  {i}/{len(pdfs)} files processed, {total} transactions so far…', flush=True)

# Senate HTML
for year_dir in sorted(glob.glob(os.path.join(BASE, 'senate_ptrs_*'))):
    _yr_m = re.search(r'(\d{4})$', year_dir)
    filing_year = int(_yr_m.group(1)) if _yr_m else 2024
    if APPEND_YEARS and filing_year not in APPEND_YEARS:
        continue
    htmls = sorted(glob.glob(os.path.join(year_dir, '*.html')))
    print(f"\n── Parsing {len(htmls)} Senate PTR HTML files from {os.path.basename(year_dir)} ──", flush=True)
    for i, html in enumerate(htmls, 1):
        uuid = os.path.splitext(os.path.basename(html))[0]
        info = senate_members.get(uuid, {})
        name = info.get('name', '')
        if name:
            last_s, first_s = split_name(name)
        else:
            last_s, first_s = '', ''
        bioguide = resolver.resolve(last_s, first_s, filing_year) if last_s else None
        rows = parse_senate_ptr_html(html, bioguide=bioguide)
        # Senate PTR UUIDs differ from annual-report UUIDs in senate_members, so the
        # pre-parse lookup almost always returns bioguide=None.  Re-resolve using the
        # name actually parsed from the HTML, and warn if still unmatched.
        if rows and not bioguide:
            actual_last  = rows[0][1]   # last_name position in insert tuple
            actual_first = rows[0][2]   # first_name position
            if actual_last:
                bioguide = resolver.resolve(actual_last, actual_first, filing_year)
                if bioguide:
                    rows = [r[:14] + (bioguide,) for r in rows]
                else:
                    print(f'  WARN unmatched: {actual_last}, {actual_first} '
                          f'(Senate, {os.path.basename(html)})')
        if rows:
            insert_rows(rows)
            total += len(rows)
        if i % 20 == 0 or i == len(htmls):
            print(f'  {i}/{len(htmls)} files processed, {total} transactions so far…', flush=True)

# ── SCOTUS transactions (from Markdown cache) ─────────────────────────────────────────────
# OGE-278 Section VII has transactions inline with holdings (column D).
# We reuse the same code-parsing logic from build_db.py.

from build_db import (
    _INCOME_CODES, _VALUE_CODES, _METHOD_CODES, _TX_TYPES,
    _LEGEND_RE, _NUMBERED_RE, _PAGE_HDR_RE, _DATE_RE, _SKIP_KW,
    _skip278, _parse_codes
)

def parse_scotus_278_transactions_md(md_path, last_name, first_name):
    rows = []
    try:
        with open(md_path, encoding='utf-8', errors='replace') as f:
            text = f.read()
    except Exception as e:
        print(f"  ERROR reading {os.path.basename(md_path)}: {e}")
        return rows

    _PART_ADDL_RE_TX = re.compile(r"^\((?:part\.?|add'?l\.?)\)\s*", re.I)

    in_section = False
    current_num = None
    current_asset = ''
    current_codes = {}
    prev_asset = ''   # remembered so (part)/(add'l) rows can inherit it

    def flush():
        nonlocal current_num, current_asset, current_codes, prev_asset
        if current_num and current_asset and current_codes.get('tx_date'):
            asset = re.sub(r'\s*\([A-Z]\)\s*$', '', current_asset.strip()).strip()
            prev_asset = asset
            tx_date = normalize_date(current_codes['tx_date'])
            tx_type_raw = current_codes.get('tx_type', '').lower()
            if 'buy' in tx_type_raw or 'purchase' in tx_type_raw:
                tx_type = 'Purchase'
            elif 'sell' in tx_type_raw or 'sold' in tx_type_raw:
                tx_type = 'Sale (Full)'
            elif 'redeem' in tx_type_raw:
                tx_type = 'Sale (Full)'
            elif 'exchange' in tx_type_raw:
                tx_type = 'Exchange'
            else:
                tx_type = current_codes.get('tx_type', '').title()
            val_map = {
                'J':  '$1,001 - $15,000',
                'K':  '$15,001 - $50,000',
                'L':  '$50,001 - $100,000',
                'M':  '$100,001 - $250,000',
                'N':  '$250,001 - $500,000',
                'O':  '$500,001 - $1,000,000',
                'P1': '$1,000,001 - $5,000,000',
                'P2': '$5,000,001 - $25,000,000',
                'P3': '$25,000,001 - $50,000,000',
                'P4': 'Over $50,000,000',
            }
            amount = val_map.get(current_codes.get('tx_value', ''), current_codes.get('tx_value', ''))
            rows.append((
                'SCOTUS', last_name, first_name, '', '',
                tx_date, '',
                asset, '', tx_type, amount, extract_ticker(asset),
                '', os.path.basename(md_path), None
            ))
        current_num = None
        current_asset = ''
        current_codes = {}

    for line in text.split('\n'):
        line = line.strip()
        if not in_section:
            if 'VII.' in line or 'INVESTMENTS and TRUSTS' in line.upper():
                in_section = True
            continue
        if not line or _skip278(line): continue

        m = _NUMBERED_RE.match(line)
        if m:
            flush()
            current_num = int(m.group(1))
            rest = m.group(2).strip()
            is_continuation_entry = bool(_PART_ADDL_RE_TX.match(rest))
            rest = _PART_ADDL_RE_TX.sub('', rest).strip()
            tokens = rest.split()
            remaining, ic, it, vc, mc, tt, td, tv, tg = _parse_codes(tokens)
            candidate_asset = ' '.join(remaining).strip()
            if is_continuation_entry or not candidate_asset:
                current_asset = prev_asset
            else:
                current_asset = candidate_asset
            current_codes = {'income_code':ic,'income_type':it,'value_code':vc,'method':mc,
                             'tx_type':tt,'tx_date':td,'tx_value':tv,'tx_gain':tg}
        elif current_num is not None:
            tokens = line.split()
            remaining, ic, it, vc, mc, tt, td, tv, tg = _parse_codes(tokens)
            if vc or td:
                current_asset += ' ' + ' '.join(remaining)
                if vc: current_codes['value_code'] = vc
                if mc: current_codes['method'] = mc
                if ic: current_codes['income_code'] = ic
                if it: current_codes['income_type'] = it
                if td: current_codes.update({'tx_type':tt,'tx_date':td,'tx_value':tv,'tx_gain':tg})
            else:
                current_asset += ' ' + line
    flush()
    return rows

scotus_md_dir   = os.path.join(BASE, 'scotus_md_2024')
scotus_md_files = sorted(f for f in os.listdir(scotus_md_dir) if f.endswith('.md')) \
    if os.path.isdir(scotus_md_dir) else []
if scotus_md_files:
    print(f"\n\u2500\u2500 Parsing SCOTUS transactions from {len(scotus_md_files)} Markdown files \u2500\u2500", flush=True)
    scotus_tx = 0
    for fname in scotus_md_files:
        parts = fname.replace('.md','').split('_')
        last  = parts[0] if parts else fname
        first = parts[1] if len(parts) > 1 else ''
        rows  = parse_scotus_278_transactions_md(os.path.join(scotus_md_dir, fname), last, first)
        if rows:
            insert_rows(rows)
            scotus_tx += len(rows)
            print(f"  {last}: {len(rows)} transactions")
    print(f"  SCOTUS total: {scotus_tx} transactions")
    total += scotus_tx
else:
    print("\nscotus_md_2024/ not found \u2014 run convert_pdfs_to_md.py first.")

conn.close()
print(f"\n\u2501\u2501 Done: {total:,} transactions written to holdings.db \u2501\u2501")
print("Next step: python query_server.py")
