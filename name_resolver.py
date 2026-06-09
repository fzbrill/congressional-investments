"""
name_resolver.py
Resolve (last_name, first_name, filing_year) -> bioguide from congress-legislators YAML.
Uses term dates to disambiguate when multiple legislators share a last name.

Used by build_db.py and build_transactions.py at import time so holdings/transactions
rows carry the bioguide directly, enabling a simple JOIN instead of complex runtime
name-matching SQL.
"""
import unicodedata, re, urllib.request

try:
    import yaml
except ImportError:
    import subprocess, sys
    subprocess.check_call([sys.executable, '-m', 'pip', 'install', 'pyyaml',
                           '--break-system-packages', '-q'])
    import yaml

# ── Nickname / formal-name pairs (bidirectional) ──────────────────────────────
NICKNAMES = {
    'mike': 'michael', 'michael': 'mike',
    'ted':  'theodore', 'theodore': 'ted',
    'chuck': 'charles', 'charles': 'chuck',
    'bill': 'william',  'william': 'bill',
    'bob':  'robert',   'rob': 'robert',
    'jim':  'james',    'james': 'jim',
    'dick': 'richard',  'rick': 'richard', 'rich': 'richard',
    'tom':  'thomas',   'thomas': 'tom',
    'joe':  'joseph',   'joseph': 'joe',
    'jack': 'john',
    'tim':  'timothy',  'timothy': 'tim',
    'dan':  'daniel',   'daniel': 'dan',
    'ben':  'benjamin', 'benjamin': 'ben',
    'dave': 'david',    'david': 'dave',
    'pete': 'peter',    'peter': 'pete',
    'tony': 'anthony',  'anthony': 'tony',
    'frank': 'francis', 'francis': 'frank',
    'andy': 'andrew',   'andrew': 'andy',
    'pat':  'patrick',  'patrick': 'pat',
    'liz':  'elizabeth', 'beth': 'elizabeth',
    'lizzie': 'elizabeth', 'elizabeth': 'lizzie',
    'sue':  'susan',    'susan': 'sue',
    'al':   'albert',
    'fred': 'frederick', 'frederick': 'fred',
    'ed':   'edward',   'edward': 'ed',
    'ron':  'ronald',   'ronald': 'ron',
    'don':  'donald',   'donald': 'don',
    'sam':  'samuel',   'samuel': 'sam',
    'ken':  'kenneth',  'kenneth': 'ken',
    'gary': 'garland',
    'greg': 'gregory',  'gregory': 'greg',
    'mitch': 'addison',
    'zach': 'zachary',  'zachary': 'zach',
    'jake': 'jacob',    'jacob': 'jake',
}

# holdings_last (normalized) -> yaml_last (normalized)
MANUAL_LAST_ALIASES = {
    'arenholz':    'hinson',     # Ashley Hinson's birth name on some old filings
    'paulina luna': 'luna',      # Anna Paulina Luna (compound last name)
    'amata':       'radewagen',  # Aumua Amata Coleman Radewagen (AS delegate)
    'justice ii':  'justice',        # Jim Justice (R-WV) — Senate PTR heading includes "II" in last
    'coleman':     'watson coleman', # Bonnie Watson Coleman (D-NJ12) — FD ZIP stores last="Coleman"
}

# Non-legislator executive branch members (no bioguide in congress-legislators)
EXEC_PRINCIPALS = {
    'trump': ('EXEC_TRUMP', {'donald', 'donald j', 'donald john', 'donald j.'}, 2017, 2099),
    # April McClain Delaney (D-MD06) — new 119th Congress member; not yet in congress-legislators YAML.
    # Use same synthetic bioguide as download_committees.py so party/state JOIN works.
    'delaney': ('D000671_SYN', {'april mcclain', 'april'}, 2025, 2099),
}

# Professional credentials that sometimes appear in FD ZIP name fields (e.g. "MD, FACS, Neal Patrick").
_CREDENTIAL_TOKENS = frozenset({
    'md', 'do', 'phd', 'dds', 'dmd', 'jd', 'mba', 'esq', 'facs', 'facc',
    'rn', 'ms', 'dnp', 'np', 'dvm', 'lcsw', 'lpc', 'mph',
})

def _strip_creds(s):
    """Strip professional credential abbreviations from a name string.

    Handles FD ZIP quirks where credentials land in the first-name field:
      'MD, FACS, Neal Patrick' → 'Neal Patrick'
    """
    if not s:
        return s
    tokens = [t.strip().rstrip('.') for t in re.split(r'[,\s]+', s) if t.strip()]
    while tokens and tokens[0].lower() in _CREDENTIAL_TOKENS:
        tokens.pop(0)
    while tokens and tokens[-1].lower() in _CREDENTIAL_TOKENS:
        tokens.pop()
    return ' '.join(tokens) if tokens else s

RAW = 'https://raw.githubusercontent.com/unitedstates/congress-legislators/main/{}'
HEADERS = {'User-Agent': 'Mozilla/5.0 (public-interest research; congressional disclosures)'}


def _nrm(s):
    """Normalize: strip accents, lowercase, strip whitespace."""
    if not s:
        return ''
    return (unicodedata.normalize('NFKD', str(s))
            .encode('ascii', 'ignore').decode('ascii')
            .lower().strip())


def _strip_hon(s):
    """Strip 'Hon.' / 'Hon..' prefix from first_name field."""
    return re.sub(r'^hon\.\.?\s+', '', s.strip(), flags=re.IGNORECASE).strip()


def _first_variants(name_dict):
    """Return set of normalized first-name variants from a YAML name dict."""
    variants = set()
    for key in ('first', 'nickname'):
        v = name_dict.get(key, '').strip()
        if v:
            nv = _nrm(v)
            variants.add(nv)
            exp = NICKNAMES.get(nv)
            if exp:
                variants.add(exp)
    off_full = name_dict.get('official_full', '').strip()
    if off_full:
        off_first = _nrm(off_full.split()[0])
        variants.add(off_first)
        exp = NICKNAMES.get(off_first)
        if exp:
            variants.add(exp)
    return variants


class NameResolver:
    """
    Resolves (last, first, year) -> bioguide.

    Index structure:
        _index[normalized_last] = [(bioguide, frozenset_of_norm_firsts, min_year, max_year), ...]
    """

    def __init__(self):
        print('NameResolver: loading congress-legislators YAML...')
        current    = self._fetch('legislators-current.yaml')
        historical = self._fetch('legislators-historical.yaml')
        self._index = {}

        for leg in historical:
            self._index_leg(leg, is_historical=True)
        for leg in current:
            self._index_leg(leg, is_historical=False)

        # Manual last-name aliases (e.g. 'amata' -> same entries as 'radewagen')
        for hold_last, yaml_last in MANUAL_LAST_ALIASES.items():
            for entry in list(self._index.get(yaml_last, [])):
                self._index.setdefault(hold_last, []).append(entry)

        # Executive principals
        for nrm_last, (bg, firsts, min_yr, max_yr) in EXEC_PRINCIPALS.items():
            self._index.setdefault(nrm_last, []).append(
                (bg, frozenset(firsts), min_yr, max_yr))

        total = sum(len(v) for v in self._index.values())
        print(f'NameResolver: {len(self._index)} last-name keys, {total} entries')

    def _fetch(self, filename):
        url = RAW.format(filename)
        req  = urllib.request.Request(url, headers=HEADERS)
        data = urllib.request.urlopen(req, timeout=30).read().decode('utf-8')
        result = yaml.safe_load(data)
        print(f'  {filename}: {len(result)} records')
        return result

    def _index_leg(self, leg, is_historical=False):
        bg = leg.get('id', {}).get('bioguide')
        if not bg:
            return
        name     = leg.get('name', {})
        last_nrm = _nrm(name.get('last', ''))
        if not last_nrm:
            return

        terms = leg.get('terms', [])
        years = []
        for t in terms:
            for key in ('start', 'end'):
                val = t.get(key, '')
                if val:
                    try:
                        years.append(int(str(val)[:4]))
                    except ValueError:
                        pass
        min_year = min(years) if years else 1789
        # Historical legislators with missing term dates get a conservative ceiling
        # so they don't shadow modern legislators with the same last name (e.g.
        # John Kennedy of the Whig party, d.1870, vs. John Kennedy, R-LA, 2017–).
        max_year = max(years) if years else (1900 if is_historical else 2099)

        variants = _first_variants(name)
        self._index.setdefault(last_nrm, []).append(
            (bg, frozenset(variants), min_year, max_year))

    def resolve(self, last, first, year=2024):
        """
        Return bioguide string, or None if unresolvable.

        Matching order:
          1. Exact normalized first-name match among year-filtered candidates
          2. First word of multi-word first (e.g. 'John Kevin' -> try 'john')
          3. Nickname expansion of the holdings first name
          4. Unique candidate after year filter (unambiguous last name)
        """
        first    = _strip_creds(_strip_hon(first))
        # Some FD ZIP entries append credentials to the last-name field
        # (e.g. "Dunn, MD, FACS" → use only the part before the first comma).
        if ',' in last:
            last = last.split(',')[0].strip()
        last_nrm = _nrm(last)
        first_nrm = _nrm(first)
        first_word = first_nrm.split()[0] if ' ' in first_nrm else first_nrm

        candidates = self._index.get(last_nrm, [])
        if not candidates:
            return None

        # Year filter: keep only members whose term overlaps the filing year
        if year:
            yr_ok = [c for c in candidates if c[2] <= year + 1 and c[3] >= year - 1]
            if yr_ok:
                candidates = yr_ok

        def _pick(matches):
            """Return single bioguide, preferring most-recently-serving."""
            if len(matches) == 1:
                return matches[0][0]
            if len(matches) > 1:
                return max(matches, key=lambda c: c[3])[0]
            return None

        # 1. Exact match on full normalized first name
        exact = [c for c in candidates if first_nrm in c[1]]
        if exact:
            return _pick(exact)

        # 2. Match on first word of a multi-word first name
        if first_word != first_nrm:
            fw = [c for c in candidates if first_word in c[1]]
            if fw:
                return _pick(fw)

        # 2b. Match on last word of a multi-word first name.
        # Handles members who go by their middle name (e.g. "Felix Barry" → try "Barry").
        last_word = first_nrm.split()[-1] if ' ' in first_nrm else ''
        if last_word and last_word != first_word:
            lw = [c for c in candidates if last_word in c[1]]
            if lw:
                return _pick(lw)

        # 3. Nickname expansion of the holdings first name
        exp = NICKNAMES.get(first_nrm)
        if exp:
            nm = [c for c in candidates if exp in c[1]]
            if nm:
                return _pick(nm)

        # 4. Unique candidate (unambiguous last name in this year range)
        if len(candidates) == 1:
            return candidates[0][0]

        return None


# ── Module-level singleton — lazy-loaded on first use ─────────────────────────
_resolver = None

def get_resolver():
    global _resolver
    if _resolver is None:
        _resolver = NameResolver()
    return _resolver


def resolve(last, first, year=2024):
    """Convenience wrapper around the module-level singleton."""
    return get_resolver().resolve(last, first, year)
