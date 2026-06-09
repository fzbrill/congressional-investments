"""
fix_wrong_docids.py
Four TSV entries have wrong DocIDs — the downloaded PDF belongs to a different member.
This script fetches the correct DocIDs from the House Clerk search and re-downloads.

Affected members:
  Blumenauer  (OR03) — file had Lauren Boebert
  Gimenez     (FL28) — file had Max Miller
  McHenry     (NC10) — file had Gregory Meeks
  Phillips    (MN03) — file had Jennifer Kiggans

Usage:  python fix_wrong_docids.py
"""
import urllib.request, urllib.parse, re, os, time, csv
from html.parser import HTMLParser

BASE     = os.path.dirname(os.path.abspath(__file__))
DEST     = os.path.join(BASE, 'house_pdfs_2024')
SEARCH   = 'https://disclosures-clerk.house.gov/FinancialDisclosure/search'
PDF_BASE = 'https://disclosures-clerk.house.gov/public_disc/financial-pdfs/2024/{doc_id}.pdf'

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (public-interest research)',
    'Accept': 'text/html,application/xhtml+xml',
}

# Members with wrong DocIDs — (last_name, first_name, state, district, expected_office)
WRONG = [
    ('Blumenauer', 'Earl',   'OR', 'OR03'),
    ('Gimenez',    'Carlos', 'FL', 'FL28'),
    ('McHenry',    'Patrick','NC', 'NC10'),
    ('Phillips',   'Dean',   'MN', 'MN03'),
]


def search_member(last, first, year=2024):
    """Search House Clerk for a specific member's annual disclosure DocID."""
    opener = urllib.request.build_opener()
    opener.addheaders = list(HEADERS.items())

    data = urllib.parse.urlencode({
        'LastName':   last,
        'FirstName':  first,
        'FilingYear': str(year),
        'State':      '',
        'District':   '',
        'ReportType': 'Annual',
        'Submit':     'Search',
    }).encode()

    try:
        resp = opener.open(SEARCH, data=data, timeout=20)
        html = resp.read().decode('utf-8', errors='replace')
    except Exception as e:
        print(f'  ERROR searching {last}: {e}')
        return None

    # Extract DocIDs from PDF links
    doc_ids = re.findall(
        r'/public_disc/financial-pdfs/\d+/(\d+)\.pdf', html)
    if not doc_ids:
        print(f'  No results for {last}, {first} ({year})')
        return None

    # Also try to confirm name in the results
    # Look for the name near the docid link
    for doc_id in doc_ids:
        # Simple check: see if member's last name appears near this link
        pattern = rf'{doc_id}.*?{last}|{last}.*?{doc_id}'
        if re.search(pattern, html, re.I | re.S):
            return doc_id

    # If can't confirm, return first result with a warning
    print(f'  WARNING: returning first result {doc_ids[0]} for {last} (unconfirmed)')
    return doc_ids[0]


def download_pdf(doc_id, name):
    dest = os.path.join(DEST, f'{doc_id}.pdf')
    if os.path.exists(dest) and os.path.getsize(dest) > 5000:
        print(f'  Already downloaded: {doc_id}.pdf ({os.path.getsize(dest)//1024}KB)')
        return True

    url    = PDF_BASE.format(doc_id=doc_id)
    opener = urllib.request.build_opener()
    opener.addheaders = list(HEADERS.items())
    try:
        resp = opener.open(url, timeout=20)
        with open(dest, 'wb') as f:
            f.write(resp.read())
        print(f'  Downloaded {doc_id}.pdf ({os.path.getsize(dest)//1024}KB) for {name}')
        return True
    except Exception as e:
        print(f'  ERROR downloading {doc_id}: {e}')
        return False


def update_tsv(old_docid, new_docid, member_name):
    """Update house_members_2024.tsv with the corrected DocID."""
    tsv = os.path.join(BASE, 'house_members_2024.tsv')
    with open(tsv, encoding='utf-8') as f:
        content = f.read()

    if old_docid in content:
        new_content = content.replace(old_docid, new_docid)
        with open(tsv, 'w', encoding='utf-8') as f:
            f.write(new_content)
        print(f'  TSV updated: {old_docid} → {new_docid} for {member_name}')
    else:
        print(f'  TSV: {old_docid} not found (already fixed?)')


# Current wrong DocIDs from the TSV
CURRENT_WRONG_DOCIDS = {
    'Blumenauer': '10070343',
    'Gimenez':    '10066804',
    'McHenry':    '10068784',
    'Phillips':   '10067550',
}

for last, first, state, office in WRONG:
    print(f'\n── {last}, {first} ({office}) ──')
    new_docid = search_member(last, first, year=2024)
    if not new_docid:
        print(f'  Could not find correct DocID — check manually at disclosures-clerk.house.gov')
        continue

    old_docid = CURRENT_WRONG_DOCIDS.get(last)
    print(f'  Old DocID: {old_docid}  →  New DocID: {new_docid}')

    if new_docid == old_docid:
        print(f'  Same DocID — search may have returned same result, check manually')
        continue

    ok = download_pdf(new_docid, last)
    if ok and old_docid:
        update_tsv(old_docid, new_docid, last)

    time.sleep(0.5)

print('\n━━ Done. Run python build_db.py to reparse with corrected files. ━━')
