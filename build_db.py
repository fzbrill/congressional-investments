"""
build_db.py  — Parse all downloaded disclosures and build holdings.db
Handles: House PDFs, Senate HTML, Trump 278e (from MD cache), SCOTUS 278 (from MD cache)
Run: python build_db.py
Prerequisite: python convert_pdfs_to_md.py  (creates scotus_md_2024/ and trump_md/)
"""
import os, re, sqlite3
import pdfplumber
from bs4 import BeautifulSoup
import name_resolver

BASE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE, 'holdings.db')



def clean(s):
    if s is None: return ''
    return re.sub(r'[\x00-\x08\x0b-\x1f]', '', str(s)).strip()

VALUE_RE = re.compile(r'\$[\d,]+')

# OGE Form 278 value-range codes → human-readable dollar ranges
_OGE_VALUE_MAP = {
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

_DATE_CELL_RE  = re.compile(r'^\d{1,2}/\d{1,2}/\d{2,4}$')
_OWNER_CELL_RE = re.compile(r'^(?:SP|DC|JT|Self|SELF|Joint|JOINT)$', re.I)
_TX_TYPE_RE    = re.compile(r'^(?:P|S|S\s*\(partial\)|Buy|Sell|Exchange)$', re.I)

# Batched validator: accumulates {category: {'count': n, 'sample': str}}
_val_warnings: dict = {}

def _val_warn(category: str, sample: str) -> None:
    if category not in _val_warnings:
        _val_warnings[category] = {'count': 0, 'sample': sample}
    _val_warnings[category]['count'] += 1

def _print_val_summary() -> None:
    if not _val_warnings:
        return
    print("\n── Validator summary ──────────────────────────────────")
    for cat, info in sorted(_val_warnings.items()):
        print(f"  {cat}: {info['count']:,} rows  (e.g. {info['sample']!r})")
    print("───────────────────────────────────────────────────────")

LEGEND_ROW_RE = re.compile(r'^\d\. ')
_HON_RE = re.compile(r'(?i)\b(?:the\s+honorable|hon\.\.?)\s*')

def _strip_hon(s: str) -> str:
    """Strip 'Hon.', 'Hon..', 'The Honorable' anywhere in a name string."""
    return _HON_RE.sub('', s).strip() if s else s

def is_asset_header(row):
    if not row: return False
    text = ' '.join(clean(c) for c in row if c).lower()
    return 'asset' in text and ('value' in text or 'income' in text)

def is_tx_header(row):
    """Detect Part II transaction table headers (Asset|Date|Type|Amount|Value).
    These should end the holdings section, not start a new one."""
    if not row: return False
    text = ' '.join(clean(c) for c in row if c).lower()
    return 'asset' in text and 'date' in text and ('type' in text or 'buy' in text or 'sell' in text)

def is_continuation(row):
    if not row: return True
    nonempty = [c for c in row if clean(c)]
    return len(nonempty) <= 1 and not VALUE_RE.search(' '.join(clean(c) for c in row))

def _clean_asset(s: str) -> str:
    """Strip ⇒-prefix artifacts and leading hyphens from asset names."""
    # "LIVTR ⇒ Johnson & Johnson" → "Johnson & Johnson"
    s = re.sub(r'^.*⇒\s*', '', s).strip()
    # Strip leading hyphens / dashes (SCOTUS markdown list markers)
    s = re.sub(r'^[-–—]+\s*', '', s).strip()
    return s

def parse_house_pdf(pdf_path, last_name, first_name, state, bioguide=None):
    rows_out = []
    try:
        with pdfplumber.open(pdf_path) as pdf:
            in_assets = False
            current_asset = None
            for page in pdf.pages:
                for table in page.extract_tables():
                    for row in table:
                        row = [clean(c) for c in row]
                        if is_asset_header(row):
                            in_assets = True; current_asset = None; continue
                        if not in_assets or not any(row): continue
                        asset_cell = _clean_asset(row[0]) if row else ''
                        if asset_cell and LEGEND_ROW_RE.match(asset_cell): continue

                        # Detect column layout:
                        #   6-col: Asset | EIF | Owner | Value | IncType | Income
                        #   5-col: Asset | Owner | Value | IncType | Income
                        # Use dollar-range presence as the primary discriminator.
                        if len(row) > 3 and VALUE_RE.search(row[3]):
                            # 6-column: dollar range is at index 3
                            owner_cell       = row[2] if len(row) > 2 else ''
                            value_cell       = row[3] if len(row) > 3 else ''
                            income_type_cell = row[4] if len(row) > 4 else ''
                            income_cell      = row[5] if len(row) > 5 else ''
                        else:
                            # 5-column (or no dollar range found): original mapping
                            owner_cell       = row[1] if len(row) > 1 else ''
                            value_cell       = row[2] if len(row) > 2 else ''
                            income_type_cell = row[3] if len(row) > 3 else ''
                            income_cell      = row[4] if len(row) > 4 else ''

                        # Skip Schedule B (transaction) rows that leaked past the
                        # holdings-section guard — their date lands in value_cell.
                        if value_cell and _DATE_CELL_RE.match(value_cell):
                            _val_warn('date-in-value-skipped', value_cell)
                            continue
                        if income_cell and _TX_TYPE_RE.match(income_cell):
                            _val_warn('tx-type-in-income', income_cell)
                        if not asset_cell and VALUE_RE.search(value_cell):
                            _val_warn('empty-asset', value_cell)

                        if asset_cell and VALUE_RE.search(value_cell + income_cell + asset_cell):
                            current_asset = asset_cell
                            rows_out.append(('House', last_name, first_name, state,
                                             current_asset, '', owner_cell, value_cell,
                                             income_type_cell, income_cell,
                                             os.path.basename(pdf_path), bioguide))
                        elif current_asset and asset_cell and not VALUE_RE.search(value_cell):
                            current_asset += ' ' + asset_cell
                            if rows_out:
                                r = list(rows_out[-1]); r[4] = current_asset; rows_out[-1] = tuple(r)
    except Exception as e:
        print(f"  ERROR reading {os.path.basename(pdf_path)}: {e}")
    return rows_out

def parse_senate_html(html_path, last_name, first_name, state, bioguide=None):
    rows_out = []
    try:
        with open(html_path, encoding='utf-8', errors='replace') as f:
            soup = BeautifulSoup(f, 'lxml')
        asset_table = None
        for t in soup.find_all('table'):
            headers = [th.get_text(' ', strip=True).lower() for th in t.find_all('th')]
            if 'asset' in ' '.join(headers) and 'value' in ' '.join(headers):
                asset_table = t; break
        if not asset_table: return rows_out
        for tr in asset_table.find_all('tr'):
            cells = [td.get_text(' ', strip=True) for td in tr.find_all(['td','th'])]
            if not cells or 'asset' in cells[1].lower() if len(cells) > 1 else False: continue
            if len(cells) >= 7:
                _, asset, asset_type, owner, value, income_type, income = cells[:7]
                if asset and asset.lower() not in ('asset', ''):
                    rows_out.append(('Senate', last_name, first_name, state,
                                     asset, asset_type, owner, value,
                                     income_type, income,
                                     os.path.basename(html_path), bioguide))
    except Exception as e:
        print(f"  ERROR reading {os.path.basename(html_path)}: {e}")
    return rows_out

# OGE Form 278 (SCOTUS judiciary) — text-based, reads from .md cache
_INCOME_CODES = frozenset(['A','B','C','D','E','F','G','H1','H2'])
_VALUE_CODES  = frozenset(['J','K','L','M','N','O','P1','P2','P3','P4'])
_METHOD_CODES = frozenset(['Q','R','S','T','U','V','W'])
_TX_TYPES     = frozenset(['buy','sold','sell','redeemed','exchange','purchase'])
_LEGEND_RE    = re.compile(r'^\d+\.\s+(Income Gain Codes|Value Codes|Value Method)', re.I)
_NUMBERED_RE  = re.compile(r'^(\d+)\.\s+(.*)')
_PAGE_HDR_RE  = re.compile(r'^(Page\s+\d+\s+of\s+\d+|<!--\s*page\s+\d+)', re.I)
_DATE_RE      = re.compile(r'\b(\d{2}/\d{2}/\d{2,4})\b')
# NOTE: 'VII. INVESTMENTS' must NOT be in _SKIP_KW — it is the section trigger.
_SKIP_KW      = ['FINANCIAL DISCLOSURE REPORT','Name of Person Reporting','Date of Report',
                 'Description of Assets','Income during','Gross value','Transactions during',
                 'Place "(X)"','exempt from prior','(A-H)','(J-P)','(Q-W)',
                 'NONE (No reportable','A. B. C. D.',
                 '-- income, value','reporting period','of reporting period',
                 'Code 1 div.,','buy, sell,','Code 3 redemption','mm/dd/yy']

def _skip278(line):
    if _LEGEND_RE.match(line): return True
    if _PAGE_HDR_RE.match(line): return True
    return any(kw in line for kw in _SKIP_KW)

def _parse_codes(tokens):
    t = list(tokens)
    tx_gain = tx_value = tx_date = tx_type = ''
    value_code = method_code = income_code = income_type = ''
    for i, tok in enumerate(t):
        if tok.lower() in _TX_TYPES and i+1 < len(t) and _DATE_RE.match(t[i+1]):
            tx_type = tok; tx_date = t[i+1]
            after = t[i+2:]
            if after and after[0].upper() in _VALUE_CODES:
                tx_value = after[0].upper(); after = after[1:]
            if after and after[0].upper() in _INCOME_CODES:
                tx_gain = after[0].upper()
            t = t[:i]; break
    if t and t[-1].upper() in _METHOD_CODES:
        method_code = t[-1].upper(); t = t[:-1]
    if t and t[-1].upper() in _VALUE_CODES:
        value_code = t[-1].upper(); t = t[:-1]
    if t and t[-1].lower() in ('dividend','interest','rent','capital','none','royalties','other'):
        income_type = t[-1]; t = t[:-1]
    if t and t[-1].upper() in _INCOME_CODES:
        income_code = t[-1].upper(); t = t[:-1]
    return t, income_code, income_type, value_code, method_code, tx_type, tx_date, tx_value, tx_gain

def parse_scotus_278_md(md_path, last_name, first_name, bioguide=None):
    rows_out = []
    try:
        with open(md_path, encoding='utf-8', errors='replace') as f:
            text = f.read()
    except Exception as e:
        print(f"  ERROR reading {os.path.basename(md_path)}: {e}"); return rows_out
    # Regex for (part) / (add'l) / (part.) continuation annotations on numbered lines
    _PART_ADDL_RE = re.compile(r"^\((?:part\.?|add'?l\.?)\)\s*", re.I)

    in_section = False; current_num = None; current_asset = ''; current_codes = {}
    prev_asset = ''   # remembered so (part)/(add'l) rows can inherit it
    def flush():
        nonlocal current_num, current_asset, current_codes, prev_asset
        if current_num and current_asset and (current_codes.get('value_code') or current_codes.get('income_code')):
            asset = re.sub(r'\s*\([A-Z]\)\s*$', '', current_asset.strip()).strip()
            asset = _clean_asset(asset)
            prev_asset = asset   # remember for next (part)/(add'l) entry
            raw_vc = current_codes.get('value_code', '')
            value  = _OGE_VALUE_MAP.get(raw_vc.upper(), raw_vc)
            rows_out.append(('SCOTUS', last_name, first_name, '', asset, '', '',
                             value, current_codes.get('income_type',''),
                             current_codes.get('income_code',''), os.path.basename(md_path), bioguide))
        current_num = None; current_asset = ''; current_codes = {}
    for line in text.split('\n'):
        line = line.strip()
        if not in_section:
            if 'VII.' in line or 'INVESTMENTS and TRUSTS' in line.upper(): in_section = True
            continue
        if not line or _skip278(line): continue
        m = _NUMBERED_RE.match(line)
        if m:
            flush(); current_num = int(m.group(1))
            rest = m.group(2).strip()
            # If the content starts with (part) or (add'l), this entry is a continuation
            # of the previous asset — strip the annotation and inherit the asset name.
            is_continuation_entry = bool(_PART_ADDL_RE.match(rest))
            rest = _PART_ADDL_RE.sub('', rest).strip()
            tokens = rest.split()
            remaining, ic, it, vc, mc, tt, td, tv, tg = _parse_codes(tokens)
            candidate_asset = ' '.join(remaining).strip()
            if is_continuation_entry or not candidate_asset:
                current_asset = prev_asset   # inherit preceding asset name
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
    return rows_out

# OGE Form 278e (Executive/Trump) — Part 6 + Schedule 1 for Part 2
# Layout: numbered line has EIF + Value + Income columns; asset name is on the
# FOLLOWING continuation line (when line starts with Yes/No/N/A), or embedded
# in the same line (when description precedes the EIF marker).
_278E_PART_RE   = re.compile(r'^Part\s+(\d+)[:\s]', re.I)
_278E_SCHED_RE  = re.compile(r'^Schedule\s+1\s+for\s+Part\s+(\d+)', re.I)
_278E_ROW_RE    = re.compile(r'^(\d+(?:\.\d+)?)\s+(.*)')
_278E_EIF_START = re.compile(r'^(Yes|No|N/A)\b', re.I)
_278E_VAL_RE    = re.compile(
    r'(?:None\s*\(or less than[^)]+\)|\$[\d,]+(?:\s*(?:to|-)\s*\$?[\d,]+(?:\.\d+)?)?)', re.I)
_278E_INC_TYPE  = re.compile(r'\b(DIVIDEND|INTEREST|RENT|ROYALT\w+|OTHER)\b', re.I)
_278E_SKIP_KW   = ['Instructions for Part','Instructions for Schedule',
                   'Note: This is a public form','If you need more pages',
                   'Note: You must add pages',"Filer's Name",'Page Number',
                   '<!-- page','# Description','Underlying Assets and Location',
                   'EIF Value Income','Description EIF Value']
_278E_CONT_SKIP = ['location:','ownership','inactive','see line',
                   'underlying asset','intentionally left blank','additional underlying']

def _278e_parse_values(text):
    vals = _278E_VAL_RE.findall(text)
    def norm(v):
        if v.lower().startswith('none'): return 'None / <$1,001'
        return re.sub(r'\s+', ' ', v.strip())
    value    = norm(vals[0]) if vals else ''
    income   = norm(vals[1]) if len(vals) > 1 else ''
    inc_m    = _278E_INC_TYPE.search(text)
    inc_type = inc_m.group(1).title() if inc_m else ''
    return value, inc_type, income

def parse_trump_278e_md(md_path, last_name='Trump', first_name='Donald',
                         state='DC', bioguide='EXEC_TRUMP'):
    rows_out = []
    try:
        with open(md_path, encoding='utf-8', errors='replace') as f:
            text = f.read()
    except Exception as e:
        print(f"  ERROR reading {os.path.basename(md_path)}: {e}"); return rows_out
    in_target = False
    pending   = None   # dict with value/inc_type/income when desc is on next line
    source    = os.path.basename(md_path)

    def emit(desc, val, inc_type, inc):
        desc = re.sub(r'\s{2,}', ' ', desc).strip()
        if desc and len(desc) > 2:
            rows_out.append(('Executive', last_name, first_name, state,
                             desc, '', '', val, inc_type, inc, source, bioguide))

    for line in text.split('\n'):
        ls = line.strip()
        if not ls: continue
        if any(kw in ls for kw in _278E_SKIP_KW): continue
        sm = _278E_SCHED_RE.match(ls)
        if sm:
            in_target = int(sm.group(1)) == 2; pending = None; continue
        pm = _278E_PART_RE.match(ls)
        if pm:
            in_target = int(pm.group(1)) in (2, 6); pending = None; continue
        if not in_target: continue
        rm = _278E_ROW_RE.match(ls)
        if rm:
            pending = None
            rest = rm.group(2).strip()
            if _278E_EIF_START.match(rest):
                val, inc_type, inc = _278e_parse_values(rest)
                pending = {'val': val, 'inc_type': inc_type, 'inc': inc}
            else:
                eif_m = re.search(r'\s+(Yes|No|N/A)\s+', rest, re.I)
                if eif_m:
                    desc        = rest[:eif_m.start()].strip()
                    values_part = rest[eif_m.start():]
                    val, inc_type, inc = _278e_parse_values(values_part)
                else:
                    desc = rest; val = inc_type = inc = ''
                if desc:
                    emit(desc, val, inc_type, inc)
        else:
            if pending is not None:
                skip = any(kw in ls.lower() for kw in _278E_CONT_SKIP)
                if not skip and ls and not _278E_EIF_START.match(ls) and not _278E_VAL_RE.search(ls[:25]):
                    emit(ls, pending['val'], pending['inc_type'], pending['inc'])
                    pending = None
    return rows_out

import csv

def load_tsv(path):
    members = {}
    try:
        with open(path, newline='', encoding='utf-8') as f:
            reader = csv.DictReader(f, delimiter='\t')
            for row in reader:
                doc_id = row.get('DOCID','').strip()
                name   = row.get('NAME','').strip()
                state  = row.get('STATE','').strip()
                parts  = name.split(',', 1)
                last   = _strip_hon(parts[0].strip()) if parts else _strip_hon(name)
                first  = _strip_hon(parts[1].strip()) if len(parts) > 1 else ''
                members[doc_id] = (last, first, state)
    except Exception as e:
        print(f"Could not load TSV {path}: {e}")
    return members


if __name__ == "__main__":
    # Back up existing DB before wiping, so an accidental run is recoverable
    import shutil as _shutil
    bak = DB_PATH + '.bak'
    if os.path.exists(DB_PATH) and os.path.getsize(DB_PATH) > 0:
        _shutil.copy2(DB_PATH, bak)
        print(f"Backed up existing DB → {bak}", flush=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("DROP TABLE IF EXISTS holdings")
    conn.execute("""
CREATE TABLE holdings (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    chamber     TEXT,
    last_name   TEXT,
    first_name  TEXT,
    state       TEXT,
    asset       TEXT,
    asset_type  TEXT,
    owner       TEXT,
    value       TEXT,
    income_type TEXT,
    income      TEXT,
    source_file TEXT,
    bioguide    TEXT
)
""")
    conn.commit()

    def insert(rows):
        conn.executemany("""
            INSERT INTO holdings
              (chamber,last_name,first_name,state,asset,asset_type,owner,value,income_type,income,source_file,bioguide)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
        """, rows)
        conn.commit()


    house_members = load_tsv(os.path.join(BASE, 'house_members_2024.tsv'))
    resolver = name_resolver.NameResolver()

    # House PDFs
    house_dir   = os.path.join(BASE, 'house_pdfs_2024')
    house_files = [f for f in os.listdir(house_dir) if f.endswith('.pdf')]
    print(f"Processing {len(house_files)} House PDFs...", flush=True)
    total_house = 0; unresolved_house = []
    for i, fname in enumerate(sorted(house_files), 1):
        doc_id = fname.replace('.pdf','')
        last, first, state = house_members.get(doc_id, ('Unknown','',''))
        bioguide = resolver.resolve(last, first, 2024)
        if not bioguide: unresolved_house.append(f"{last}, {first}")
        rows = parse_house_pdf(os.path.join(house_dir, fname), last, first, state, bioguide=bioguide)
        insert(rows); total_house += len(rows)
        if i % 20 == 0 or i == len(house_files):
            print(f"  {i}/{len(house_files)} PDFs, {total_house} holdings so far...", flush=True)
    print(f"  House: {total_house} holdings from {len(house_files)} members")
    if unresolved_house:
        print(f"  Unresolved ({len(unresolved_house)}): {', '.join(unresolved_house[:10])}"
              + (f" (+{len(unresolved_house)-10} more)" if len(unresolved_house) > 10 else ""))

    # Senate HTML
    senate_dir   = os.path.join(BASE, 'senate_html_2024')
    senate_files = [f for f in os.listdir(senate_dir) if f.endswith('.html')]
    print(f"Processing {len(senate_files)} Senate HTML files...", flush=True)
    total_senate = 0; unresolved_senate = []
    for i, fname in enumerate(sorted(senate_files), 1):
        html_path = os.path.join(senate_dir, fname)
        try:
            with open(html_path, encoding='utf-8', errors='replace') as f:
                soup = BeautifulSoup(f, 'lxml')
            title = soup.title.get_text(' ', strip=True) if soup.title else ''
            m = re.search(r'Annual Report for \d{4}\s*[-–]\s*([^,]+),\s*(.+)', title)
            if m:
                last, first = _strip_hon(m.group(1).strip()), _strip_hon(m.group(2).strip())
            else:
                header = soup.find(['h2','h3','h4'])
                text2  = header.get_text(' ', strip=True) if header else fname
                parts  = text2.split()
                last, first = (parts[-1], parts[0]) if len(parts) >= 2 else (text2, '')
                last, first = _strip_hon(last), _strip_hon(first)
            state  = ''
            state_m = re.search(r'State:\s*([A-Z]{2})', soup.get_text())
            if state_m: state = state_m.group(1)
        except:
            last, first, state = fname, '', ''
        bioguide = resolver.resolve(last, first, 2024)
        if not bioguide: unresolved_senate.append(f"{last}, {first}")
        rows = parse_senate_html(html_path, last, first, state, bioguide=bioguide)
        insert(rows); total_senate += len(rows)
        if i % 20 == 0 or i == len(senate_files):
            print(f"  {i}/{len(senate_files)} files, {total_senate} holdings so far...", flush=True)
    print(f"  Senate: {total_senate} holdings from {len(senate_files)} members")
    if unresolved_senate:
        print(f"  Unresolved ({len(unresolved_senate)}): {', '.join(unresolved_senate)}")

    # Trump 278e (Markdown cache)
    trump_md = os.path.join(BASE, 'trump_md', 'Trump_Donald.md')
    if os.path.exists(trump_md):
        print("Processing Trump 278e (from Markdown cache)...")
        rows = parse_trump_278e_md(trump_md)
        insert(rows)
        print(f"  Trump: {len(rows)} holdings")
    else:
        print("trump_md/Trump_Donald.md not found — run convert_pdfs_to_md.py first.")

    # SCOTUS (Markdown cache)
    scotus_md_dir   = os.path.join(BASE, 'scotus_md_2024')
    scotus_md_files = sorted(f for f in os.listdir(scotus_md_dir) if f.endswith('.md')) \
        if os.path.isdir(scotus_md_dir) else []
    if scotus_md_files:
        print(f"Processing {len(scotus_md_files)} SCOTUS disclosures (from Markdown cache)...")
        total_scotus = 0
        for fname in scotus_md_files:
            parts = fname.replace('.md','').split('_')
            last  = parts[0] if parts else fname
            first = parts[1] if len(parts) > 1 else ''
            rows  = parse_scotus_278_md(os.path.join(scotus_md_dir, fname), last, first)
            insert(rows); total_scotus += len(rows)
            print(f"  {last}: {len(rows)} holdings")
        print(f"  SCOTUS total: {total_scotus} from {len(scotus_md_files)} justices")
    else:
        print("scotus_md_2024/ not found — run convert_pdfs_to_md.py first.")

    _print_val_summary()

    total = conn.execute("SELECT COUNT(*) FROM holdings").fetchone()[0]
    by_chamber = conn.execute("SELECT chamber, COUNT(*) FROM holdings GROUP BY chamber ORDER BY chamber").fetchall()
    print(f"\n{'='*50}")
    print(f"Total holdings in DB: {total:,}")
    for chamber, count in by_chamber:
        print(f"  {chamber}: {count:,}")
    print(f"DB saved to: {DB_PATH}")
    conn.close()
