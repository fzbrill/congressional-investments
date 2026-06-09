"""
ocr_scanned_pdfs.py
Recover financial holdings from scanned (image-based) House disclosure PDFs
by sending each page to the Claude vision API for structured extraction.

Usage:  python ocr_scanned_pdfs.py [--dry-run] [--member Bilirakis]

Requirements:
    pip install pymupdf pytesseract pillow anthropic
    ANTHROPIC_API_KEY must be set

Cost estimate: ~$0.50-1.50 total for all 21 PDFs (claude-haiku-4-5 vision)
"""
import os, re, json, sqlite3, base64, argparse, time
import pymupdf
from PIL import Image
import io

parser = argparse.ArgumentParser()
parser.add_argument('--dry-run', action='store_true', help='Show what would be done, no API calls')
parser.add_argument('--member', help='Process only this member (partial last name match)')
parser.add_argument('--start-page', type=int, default=0, help='Start from this page index (0-based)')
args = parser.parse_args()

BASE    = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE, 'holdings.db')

# The 21 members with scanned PDFs, plus their state/district from the TSV
SCANNED_MEMBERS = [
    ('8221132',  'Bilirakis',     'Gus M.',        'FL', 'FL12'),
    ('8221133',  'Carey',         'Mike',           'OH', 'OH15'),
    ('9115587',  'Chavez-DeRemer','Lori',           'OR', 'OR05'),
    ('9115612',  'Ciscomani',     'Juan',           'AZ', 'AZ06'),
    ('8221125',  'Courtney',      'Joe',            'CT', 'CT02'),
    ('8221134',  "D'Esposito",    'Anthony',        'NY', 'NY04'),
    ('8221130',  'Fleischmann',   'Charles J.',     'TN', 'TN03'),
    ('9115581',  'Guest',         'Michael',        'MS', 'MS03'),
    ('8221136',  'Guthrie',       'Brett',          'KY', 'KY02'),
    ('9115594',  'Harshbarger',   'Diana',          'TN', 'TN01'),
    ('9115596',  'Kilmer',        'Derek',          'WA', 'WA06'),
    ('8221137',  'Matsui',        'Doris O.',       'CA', 'CA07'),
    ('9115586',  'McCaul',        'Michael T.',     'TX', 'TX10'),
    ('8221127',  'Neguse',        'Joe',            'CO', 'CO02'),
    ('8221138',  "O'Halleran",    'Tom',            'AZ', 'AZ01'),
    ('9115593',  'Rogers',        'Harold',         'KY', 'KY05'),
    ('8221131',  'Rogers',        'Mike',           'AL', 'AL03'),
    ('8221149',  'Self',          'Keith',          'TX', 'TX03'),
    ('8221129',  'Sherrill',      'Mikie',          'NJ', 'NJ11'),
    ('9115582',  'Steil',         'Bryan',          'WI', 'WI01'),
    ('8221141',  'Tokuda',        'Jill',           'HI', 'HI02'),
]

# For very long PDFs, cap at this many pages to keep costs reasonable
MAX_PAGES_PER_PDF = 60  # covers ~98% of holdings pages for even large portfolios

EXTRACTION_PROMPT = """This is a page from a US House of Representatives annual financial disclosure form (Form FD).

Extract ALL financial holdings listed on this page. These appear in Part III "Assets and Unearned Income"
or similar asset schedule sections as a table with columns like:
- Asset name / description
- Owner (SP=spouse, JT=joint, DC=dependent child, blank=self)
- Value (a dollar range like "$15,001 - $50,000")
- Income type (Dividends, Capital Gains, Rent, Interest, etc.)
- Income amount (a dollar range)

Return a JSON object:
{
  "has_assets": true/false,
  "holdings": [
    {
      "asset": "full asset name",
      "asset_type": "Stock/Mutual Fund/Real Estate/etc (if visible)",
      "owner": "SP/JT/DC or blank",
      "value": "$X - $Y range or exact text",
      "income_type": "type if shown",
      "income": "$X - $Y range or blank"
    }
  ]
}

If this page has NO financial holdings table (e.g. it's a cover page, instructions, signature page,
liabilities section, positions section, or blank), return: {"has_assets": false, "holdings": []}

Be thorough — extract every row, even if the handwriting is messy. Use "?" for illegible values.
Return ONLY the JSON object, no other text."""


def pdf_page_to_base64(pdf_path, page_num, dpi=150):
    """Convert a PDF page to a base64-encoded JPEG for the vision API."""
    doc = pymupdf.open(pdf_path)
    page = doc[page_num]
    mat  = pymupdf.Matrix(dpi/72, dpi/72)
    pix  = page.get_pixmap(matrix=mat, colorspace=pymupdf.csRGB)
    img  = Image.frombytes('RGB', [pix.width, pix.height], pix.samples)

    # Convert to JPEG (smaller than PNG, fine for text)
    buf = io.BytesIO()
    img.save(buf, format='JPEG', quality=85)
    return base64.standard_b64encode(buf.getvalue()).decode('utf-8')


def extract_page_holdings(client, pdf_path, page_num):
    """Send one page to Claude vision and return list of holding dicts."""
    img_b64 = pdf_page_to_base64(pdf_path, page_num)

    msg = client.messages.create(
        model='claude-haiku-4-5-20251001',
        max_tokens=2048,
        messages=[{
            'role': 'user',
            'content': [
                {
                    'type': 'image',
                    'source': {
                        'type': 'base64',
                        'media_type': 'image/jpeg',
                        'data': img_b64,
                    }
                },
                {'type': 'text', 'text': EXTRACTION_PROMPT}
            ]
        }]
    )

    raw = msg.content[0].text.strip()
    # Strip markdown fences if present
    raw = re.sub(r'^```(?:json)?\s*', '', raw, flags=re.M)
    raw = re.sub(r'\s*```$', '', raw, flags=re.M).strip()

    try:
        data = json.loads(raw)
        return data.get('holdings', []) if data.get('has_assets') else []
    except json.JSONDecodeError:
        # Try to extract JSON from response
        m = re.search(r'\{.*\}', raw, re.DOTALL)
        if m:
            try:
                data = json.loads(m.group(0))
                return data.get('holdings', []) if data.get('has_assets') else []
            except:
                pass
        return []


def process_member(client, docid, last_name, first_name, state, office, conn):
    pdf_path = os.path.join(BASE, 'house_pdfs_2024', f'{docid}.pdf')
    if not os.path.exists(pdf_path):
        print(f'  SKIP: {pdf_path} not found')
        return 0

    doc      = pymupdf.open(pdf_path)
    n_pages  = doc.page_count
    doc.close()

    print(f'\n── {last_name}, {first_name} ({office}) — {n_pages} pages ──')

    if args.dry_run:
        print(f'  DRY RUN: would process up to {min(n_pages, MAX_PAGES_PER_PDF)} pages')
        return 0

    all_holdings = []
    consecutive_empty = 0

    for pg in range(min(n_pages, MAX_PAGES_PER_PDF)):
        try:
            holdings = extract_page_holdings(client, pdf_path, pg)
            if holdings:
                all_holdings.extend(holdings)
                consecutive_empty = 0
                print(f'  p{pg+1}: {len(holdings)} holdings extracted')
            else:
                consecutive_empty += 1
                # After 5 consecutive empty pages past page 5, we're likely done with assets
                if pg > 5 and consecutive_empty >= 5:
                    print(f'  p{pg+1}: 5 consecutive empty pages, stopping')
                    break
                else:
                    print(f'  p{pg+1}: no assets')
        except Exception as e:
            print(f'  p{pg+1}: ERROR {e}')

        time.sleep(0.3)  # rate limiting

    # Insert into DB
    if all_holdings:
        rows = []
        for h in all_holdings:
            rows.append((
                'House', last_name, first_name, state,
                h.get('asset', ''), h.get('asset_type', ''),
                h.get('owner', ''), h.get('value', ''),
                h.get('income_type', ''), h.get('income', ''),
                f'{docid}.pdf'
            ))
        conn.executemany("""
            INSERT INTO holdings
              (chamber,last_name,first_name,state,asset,asset_type,owner,value,income_type,income,source_file)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)
        """, rows)
        conn.commit()
        print(f'  → Inserted {len(rows)} holdings for {last_name}')
    else:
        print(f'  → No holdings found (may be a non-disclosure or parse issue)')

    return len(all_holdings)


# ── Main ──────────────────────────────────────────────────────────────────────
if __name__ == '__main__':
    import anthropic

    api_key = os.environ.get('ANTHROPIC_API_KEY', '')
    if not api_key and not args.dry_run:
        print('ERROR: ANTHROPIC_API_KEY not set')
        exit(1)

    client = anthropic.Anthropic(api_key=api_key) if not args.dry_run else None
    conn   = sqlite3.connect(DB_PATH)

    # Filter if --member specified
    members = SCANNED_MEMBERS
    if args.member:
        members = [m for m in members if args.member.lower() in m[1].lower()]
        if not members:
            print(f'No member matching "{args.member}"')
            exit(1)

    print(f'Processing {len(members)} scanned PDFs')
    if args.dry_run:
        print('DRY RUN — no API calls will be made\n')

    total = 0
    for docid, last, first, state, office in members:
        n = process_member(client, docid, last, first, state, office, conn)
        total += n

    conn.close()
    print(f'\n━━ Done: {total} total holdings extracted and inserted ━━')
    print('Run python query_server.py to explore the updated data.')
