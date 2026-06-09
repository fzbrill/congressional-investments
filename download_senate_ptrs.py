"""
download_senate_ptrs.py
Download Senate Periodic Transaction Report (PTR) HTML files from eFD.

The Senate eFD uses a DataTables-style JSON API at /search/report/data/.
Must first agree to terms at /search/home/ to get a session cookie.

Usage:  python download_senate_ptrs.py [--year 2025]
        python download_senate_ptrs.py --years 2024 2025

Files land in:  senate_ptrs_{year}/
"""
import urllib.request, urllib.parse, http.cookiejar, os, time, re, json, argparse

parser = argparse.ArgumentParser()
parser.add_argument('--year', type=int, default=2025)
parser.add_argument('--years', nargs='+', type=int)
args = parser.parse_args()

years = args.years if args.years else ([2024, 2025] if args.year == 2025 else [args.year])

BASE_DIR  = os.path.dirname(os.path.abspath(__file__))
HOME      = 'https://efdsearch.senate.gov/search/home/'
SEARCH    = 'https://efdsearch.senate.gov/search/report/data/'
VIEW_BASE = 'https://efdsearch.senate.gov/search/view/ptr/{uuid}/'

# ── Session setup ─────────────────────────────────────────────────────────────
jar    = http.cookiejar.CookieJar()
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
opener.addheaders = [
    ('User-Agent', 'Mozilla/5.0 (public interest research; congressional disclosures)'),
    ('Accept', 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8'),
]


def get_csrf():
    """Load home page and return CSRF token."""
    html = opener.open(HOME, timeout=20).read().decode('utf-8')
    m = re.search(r'csrfmiddlewaretoken[^>]+value="([^"]+)"', html)
    token = m.group(1) if m else ''
    if not token:
        # Try cookie
        for cookie in jar:
            if cookie.name == 'csrftoken':
                return cookie.value
    return token


def agree_to_terms(token):
    """POST the prohibition agreement to activate the session."""
    body = urllib.parse.urlencode({
        'csrfmiddlewaretoken': token,
        'prohibition_agreement': '1',
    }).encode()
    req = urllib.request.Request(HOME, body, {
        'Referer': HOME,
        'Content-Type': 'application/x-www-form-urlencoded',
    })
    opener.open(req, timeout=20)
    # Return fresh CSRF from cookie (updated after POST)
    for cookie in jar:
        if cookie.name == 'csrftoken':
            return cookie.value
    return token


def establish_session():
    token = get_csrf()
    token = agree_to_terms(token)
    return token


print('Establishing eFD session…')
csrf = establish_session()
print(f'  Session ready (csrf={csrf[:8]}…)')


def search_ptrs(year, start=0, length=100):
    """
    Query the eFD DataTables API for PTRs filed in a given year.
    Report type 11 = Periodic Transaction Report.
    """
    date_from = f'01/01/{year} 00:00:00'
    date_to   = f'12/31/{year} 23:59:59'

    data = urllib.parse.urlencode({
        'csrfmiddlewaretoken':       csrf,
        'draw':                      '1',
        'columns[0][data]':          'first_name',
        'columns[0][name]':          '',
        'columns[0][searchable]':    'true',
        'columns[0][orderable]':     'true',
        'columns[0][search][value]': '',
        'columns[0][search][regex]': 'false',
        'columns[1][data]':          'last_name',
        'columns[1][name]':          '',
        'columns[1][searchable]':    'true',
        'columns[1][orderable]':     'true',
        'columns[1][search][value]': '',
        'columns[1][search][regex]': 'false',
        'columns[2][data]':          'office',
        'columns[2][name]':          '',
        'columns[2][searchable]':    'true',
        'columns[2][orderable]':     'true',
        'columns[2][search][value]': '',
        'columns[2][search][regex]': 'false',
        'columns[3][data]':          'report_type',
        'columns[3][name]':          '',
        'columns[3][searchable]':    'true',
        'columns[3][orderable]':     'true',
        'columns[3][search][value]': '',
        'columns[3][search][regex]': 'false',
        'columns[4][data]':          'date_received',
        'columns[4][name]':          '',
        'columns[4][searchable]':    'true',
        'columns[4][orderable]':     'true',
        'columns[4][search][value]': '',
        'columns[4][search][regex]': 'false',
        'columns[5][data]':          'link',
        'columns[5][name]':          '',
        'columns[5][searchable]':    'false',
        'columns[5][orderable]':     'false',
        'columns[5][search][value]': '',
        'columns[5][search][regex]': 'false',
        'order[0][column]':          '4',
        'order[0][dir]':             'desc',
        'start':                     str(start),
        'length':                    str(length),
        'search[value]':             '',
        'search[regex]':             'false',
        'report_types':              '[11]',
        'submitted_start_date':      date_from,
        'submitted_end_date':        date_to,
    }).encode()

    req = urllib.request.Request(SEARCH, data, {
        'Referer':            HOME,
        'X-Requested-With':   'XMLHttpRequest',
        'Content-Type':       'application/x-www-form-urlencoded',
        'Accept':             'application/json, text/javascript, */*; q=0.01',
    })
    try:
        resp = opener.open(req, timeout=30)
        return json.loads(resp.read().decode('utf-8'))
    except Exception as e:
        print(f'  Search error (start={start}): {e}')
        return {}


def get_all_ptrs(year):
    """Page through all PTR results for a year."""
    all_data = []
    start    = 0
    length   = 100
    total    = None

    while True:
        result = search_ptrs(year, start=start, length=length)

        if not result or 'data' not in result:
            # Re-auth and retry once
            global csrf
            csrf = establish_session()
            result = search_ptrs(year, start=start, length=length)
            if not result or 'data' not in result:
                print(f'  Could not fetch results for {year} at offset {start}')
                break

        if total is None:
            total = result.get('recordsTotal', result.get('recordsFiltered', 0))
            print(f'  Found {total} PTRs for {year}')
            if total == 0:
                break

        rows = result.get('data', [])
        if not rows:
            break

        all_data.extend(rows)
        start += length

        if start >= total:
            break

        time.sleep(0.3)

    return all_data


def parse_row(row):
    """Extract uuid and name from a result row (list or dict)."""
    row_str = json.dumps(row)
    uuid_m  = re.search(r'/search/view/ptr/([a-f0-9\-]{36})/', row_str)
    uuid    = uuid_m.group(1) if uuid_m else None

    if isinstance(row, dict):
        first = row.get('first_name', '')
        last  = row.get('last_name', '')
        name  = f'{last}, {first}'.strip(', ')
    elif isinstance(row, list):
        texts = [re.sub(r'<[^>]+>', '', str(c)).strip() for c in row[:3]]
        name  = ' '.join(t for t in texts if t)
    else:
        name = 'Unknown'

    return uuid, name


def download_ptrs(year):
    dest_dir = os.path.join(BASE_DIR, f'senate_ptrs_{year}')
    os.makedirs(dest_dir, exist_ok=True)

    rows = get_all_ptrs(year)
    if not rows:
        print(f'  No PTRs found for {year}')
        return

    total = len(rows)
    ok = skip = err = 0

    for i, row in enumerate(rows, 1):
        uuid, name = parse_row(row)
        if not uuid:
            err += 1
            print(f'[{i}/{total}] skip  (no uuid): {str(row)[:80]}')
            continue

        dest = os.path.join(dest_dir, f'{uuid}.html')
        if os.path.exists(dest) and os.path.getsize(dest) > 2000:
            skip += 1
            if skip <= 3 or skip % 100 == 0:
                print(f'[{i}/{total}] skip  {name}')
            continue

        url = VIEW_BASE.format(uuid=uuid)
        try:
            html = opener.open(url, timeout=20).read().decode('utf-8')
            if 'prohibition_agreement' in html:
                # Session expired — re-establish
                csrf = establish_session()
                html = opener.open(url, timeout=20).read().decode('utf-8')
            with open(dest, 'w', encoding='utf-8') as f:
                f.write(html)
            ok += 1
            print(f'[{i}/{total}] ok    {name} ({uuid[:8]}…)  {len(html)//1024}KB')
        except Exception as e:
            err += 1
            print(f'[{i}/{total}] ERROR {name}: {e}')

        time.sleep(0.4)

    print(f'\n  {year}: {ok} downloaded, {skip} skipped, {err} errors → {dest_dir}')


# ── Main ──────────────────────────────────────────────────────────────────────
for year in years:
    print(f'\n── Senate PTRs for {year} ──')
    download_ptrs(year)

print('\n━━ Done. Next step: python build_transactions.py ━━')
