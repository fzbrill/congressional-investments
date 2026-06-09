"""
download_committees.py
Build the committees and committee_members tables in holdings.db.
"""
import urllib.request, io, os, sqlite3, json, re, argparse, unicodedata

def normalize(s):
    """Strip accent marks so Sanchez == Sanchez, Garcia == Garcia, etc."""
    return unicodedata.normalize('NFKD', s).encode('ascii', 'ignore').decode('ascii')

try:
    import yaml
except ImportError:
    print("Installing pyyaml...")
    import subprocess, sys
    subprocess.check_call([sys.executable, '-m', 'pip', 'install', 'pyyaml', '--break-system-packages', '-q'])
    import yaml

parser = argparse.ArgumentParser()
parser.add_argument('--dry-run', action='store_true')
args = parser.parse_args()

BASE    = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE, 'holdings.db')

RAW = 'https://raw.githubusercontent.com/unitedstates/congress-legislators/main/{}'
URLS = {
    'committees':             RAW.format('committees-current.yaml'),
    'membership':             RAW.format('committee-membership-current.yaml'),
    'legislators':            RAW.format('legislators-current.yaml'),
    'legislators_historical': RAW.format('legislators-historical.yaml'),
}

HEADERS = {'User-Agent': 'Mozilla/5.0 (public-interest research; congressional disclosures)'}


def fetch_yaml(key):
    url = URLS[key]
    print(f'  Fetching {url} ...')
    req  = urllib.request.Request(url, headers=HEADERS)
    data = urllib.request.urlopen(req, timeout=30).read().decode('utf-8')
    result = yaml.safe_load(data)
    print(f'  -> {len(result)} records')
    return result


print('Downloading committee data from congress-legislators...')
committees_raw   = fetch_yaml('committees')
membership_raw   = fetch_yaml('membership')
legislators_hist = fetch_yaml('legislators_historical')
legislators_raw  = fetch_yaml('legislators')
current_bioguides = {leg.get('id', {}).get('bioguide') for leg in legislators_raw if leg.get('id', {}).get('bioguide')}
print(f'  {len(current_bioguides)} current legislators')

print('\nBuilding legislator lookup...')
bio_lookup = {}
for leg in legislators_hist + legislators_raw:
    bg = leg.get('id', {}).get('bioguide')
    if not bg:
        continue
    name  = leg.get('name', {})
    terms = leg.get('terms', [])
    last_term = terms[-1] if terms else {}
    first      = name.get('first', '')
    nickname   = name.get('nickname', '')
    off_full   = name.get('official_full', '')
    off_first  = off_full.split()[0] if off_full else ''
    display    = nickname or (off_first if off_first.lower() != first.lower() else first) or first
    bio_lookup[bg] = {
        'last_name':      name.get('last', ''),
        'first_name':     first,
        'display_first':  display,
        'nickname':       nickname,
        'off_full_first': off_first,
        'state':          last_term.get('state', ''),
        'district':       str(last_term.get('district', '')) if last_term.get('district') else '',
        'party':          last_term.get('party', ''),
        'is_current':     1 if bg in current_bioguides else 0,
    }
print(f'  {len(bio_lookup)} legislators indexed')

print('\nParsing committees...')
committee_rows = []
for comm in committees_raw:
    tid     = comm.get('thomas_id', '')
    name    = comm.get('name', '')
    chamber = comm.get('type', '')
    if not tid:
        continue
    committee_rows.append((tid, name, chamber, None))
    for sub in comm.get('subcommittees', []):
        sub_suffix = sub.get('thomas_id', '')
        sub_tid    = tid + sub_suffix
        sub_name   = sub.get('name', '')
        committee_rows.append((sub_tid, sub_name, chamber, tid))
print(f'  {len(committee_rows)} committees + subcommittees')

print('Parsing membership...')
member_rows = []
for thomas_id, members in membership_raw.items():
    if not isinstance(members, list):
        continue
    for m in members:
        bg    = m.get('bioguide', '')
        leg   = bio_lookup.get(bg, {})
        last  = leg.get('last_name',  m.get('name', '').rsplit(' ', 1)[-1])
        first = leg.get('first_name', m.get('name', '').rsplit(' ', 1)[0] if ' ' in m.get('name','') else '')
        state = leg.get('state', '')
        dist  = leg.get('district', '')
        party = m.get('party', '')
        rank  = m.get('rank', 0)
        title = m.get('title', '')
        member_rows.append((thomas_id, bg, last, first, state, dist, party, rank, title))
print(f'  {len(member_rows)} committee-member assignments')

print('Building member_info...')
member_info_rows = []
name_alias_rows  = []

NICKNAME_EXPANSIONS = {
    'mike': 'michael', 'michael': 'mike',
    'ted':  'theodore', 'theodore': 'ted',
    'chuck': 'charles', 'charles': 'chuck',
    'bill': 'william', 'william': 'bill',
    'bob': 'robert', 'rob': 'robert',
    'jim': 'james', 'james': 'jim',
    'dick': 'richard', 'rick': 'richard', 'rich': 'richard',
    'tom': 'thomas', 'thomas': 'tom',
    'joe': 'joseph', 'joseph': 'joe',
    'jack': 'john',
    'tim': 'timothy', 'timothy': 'tim',
    'dan': 'daniel', 'daniel': 'dan',
    'ben': 'benjamin', 'benjamin': 'ben',
    'dave': 'david', 'david': 'dave',
    'pete': 'peter', 'peter': 'pete',
    'tony': 'anthony', 'anthony': 'tony',
    'frank': 'francis', 'francis': 'frank',
    'andy': 'andrew', 'andrew': 'andy',
    'pat': 'patrick', 'patrick': 'pat',
    'liz': 'elizabeth', 'beth': 'elizabeth',
    'sue': 'susan', 'susan': 'sue',
    'al': 'albert',
    'fred': 'frederick', 'frederick': 'fred',
    'ed': 'edward', 'edward': 'ed',
    'ron': 'ronald', 'ronald': 'ron',
    'don': 'donald', 'donald': 'don',
    'sam': 'samuel', 'samuel': 'sam',
    'ken': 'kenneth', 'kenneth': 'ken',
    'gary': 'garland',
    'greg': 'gregory', 'gregory': 'greg',
    'mitch': 'addison',
}

for bg, info in bio_lookup.items():
    member_info_rows.append((
        bg,
        info['last_name'],
        info['first_name'],
        info['display_first'],
        info['state'],
        info['party'],
        info.get('is_current', 0),
    ))
    aliases = set()
    for fn in [info['first_name'], info['display_first'], info['nickname'], info['off_full_first']]:
        fn = fn.strip()
        if fn:
            nfn = normalize(fn).lower()
            aliases.add(nfn)
            exp = NICKNAME_EXPANSIONS.get(nfn)
            if exp:
                aliases.add(exp)
    for alias_fn in aliases:
        stored = None
        for fn in [info['first_name'], info['display_first'], info['nickname'], info['off_full_first']]:
            if normalize(fn.strip()).lower() == alias_fn:
                stored = normalize(fn.strip())
                break
        if stored is None:
            stored = alias_fn.capitalize()
        name_alias_rows.append((bg, normalize(info['last_name']), stored))

MANUAL_LAST_ALIASES = [
    ('Arenholz',       'Hinson',          'Ashley'),
    ('Paulina Luna',   'Luna',            'Anna'),
    ('Amata',          'Radewagen',       'Aumua Amata'),  # Aumua Amata Coleman Radewagen (AS delegate)
]
for holdings_last, yaml_last, yaml_first in MANUAL_LAST_ALIASES:
    for bg, info in bio_lookup.items():
        if (normalize(info['last_name']).lower() == normalize(yaml_last).lower()
                and normalize(info['first_name']).lower() == normalize(yaml_first).lower()):
            existing_fns = set(r[2] for r in name_alias_rows if r[0] == bg)
            for fn in existing_fns:
                name_alias_rows.append((bg, holdings_last, fn))
            print(f'  Manual override: {holdings_last!r} -> {yaml_last}, {yaml_first} ({bg}), {len(existing_fns)} FN aliases')
            break

# Executive principals not covered by congress-legislators (e.g. Trump was never a legislator)
EXEC_PRINCIPALS = [
    # (synth_bioguide, last, first_variants, display, party, is_current)
    ('EXEC_TRUMP', 'Trump', ['Donald', 'Donald J.', 'Donald John', 'Donald J'], 'Donald', 'Republican', 1),
    # April McClain Delaney (D-MD06) — not yet in congress-legislators YAML (new member 119th Congress)
    # PTR first name is "April McClain"; also accept "April"
    ('D000671_SYN', 'Delaney', ['April McClain', 'April'], 'April', 'Democrat', 1),
]
for synth_bg, last, firsts, display, party, is_cur in EXEC_PRINCIPALS:
    if synth_bg not in bio_lookup:
        member_info_rows.append((synth_bg, last, firsts[0], display, '', party, is_cur))
        for fn in firsts:
            name_alias_rows.append((synth_bg, last, fn))
        print(f'  Added executive principal: {last}, {display} ({synth_bg})')

print(f'  {len(member_info_rows)} members with party data')

if args.dry_run:
    print('\nDRY RUN -- not writing to DB')
    print('Sample committees:', committee_rows[:3])
    print('Sample members:', member_rows[:3])
    print('Sample member_info:', member_info_rows[:3])
    exit(0)

print(f'\nWriting to {DB_PATH} ...')
conn = sqlite3.connect(DB_PATH)
c    = conn.cursor()

c.executescript(
    "DROP TABLE IF EXISTS committees;"
    "CREATE TABLE committees (thomas_id TEXT PRIMARY KEY, name TEXT, chamber TEXT, parent_id TEXT);"
    "DROP TABLE IF EXISTS committee_members;"
    "CREATE TABLE committee_members ("
    "  thomas_id TEXT, bioguide TEXT, last_name TEXT, first_name TEXT,"
    "  state TEXT, district TEXT, party TEXT, rank INTEGER, title TEXT,"
    "  PRIMARY KEY (thomas_id, bioguide));"
    "DROP TABLE IF EXISTS member_info;"
    "CREATE TABLE member_info ("
    "  bioguide TEXT PRIMARY KEY, last_name TEXT, first_name TEXT,"
    "  display_first TEXT, state TEXT, party TEXT, is_current INTEGER DEFAULT 0);"
    "DROP TABLE IF EXISTS name_aliases;"
    "CREATE TABLE name_aliases (bioguide TEXT, last_name TEXT, first_name TEXT);"
    "CREATE INDEX IF NOT EXISTS idx_aliases ON name_aliases (last_name, first_name);"
)

c.executemany('INSERT OR REPLACE INTO committees VALUES (?,?,?,?)', committee_rows)
c.executemany('INSERT OR REPLACE INTO committee_members VALUES (?,?,?,?,?,?,?,?,?)', member_rows)
c.executemany('INSERT OR REPLACE INTO member_info VALUES (?,?,?,?,?,?,?)', member_info_rows)
c.executemany('INSERT INTO name_aliases VALUES (?,?,?)', name_alias_rows)
conn.commit()

n_comm    = c.execute('SELECT COUNT(*) FROM committees').fetchone()[0]
n_memb    = c.execute('SELECT COUNT(*) FROM committee_members').fetchone()[0]
n_info    = c.execute('SELECT COUNT(*) FROM member_info').fetchone()[0]
n_aliases = c.execute('SELECT COUNT(*) FROM name_aliases').fetchone()[0]
conn.close()

print(f'\n== Done ==')
print(f'  committees:        {n_comm} rows')
print(f'  committee_members: {n_memb} rows')
print(f'  member_info:       {n_info} rows (with party affiliation)')
print(f'  name_aliases:      {n_aliases} rows (nickname + legal name variants)')
print('Refresh complete.')
