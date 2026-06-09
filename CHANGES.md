# Change History

## Session 14 (2026-06-08)

### Data quality fixes in `build_db.py`
- Strip `⇒` prefix from House asset names — account/schedule header rows merged by pdfplumber (e.g. `LIVTR ⇒ Johnson & Johnson`) now cleaned via `_clean_asset()`
- Strip leading hyphens from SCOTUS asset names — markdown conversion produces `- Asset Name` list markers; `_clean_asset()` now strips leading `-`, `–`, `—`
- Expand OGE letter codes to dollar ranges — SCOTUS OGE-278 value codes (J/K/L/M/N/O/P1–P4) now stored as human-readable ranges via `_OGE_VALUE_MAP`
- 6-column vs 5-column PDF auto-detection — House annual PDFs have either 5 or 6 columns; parser now detects layout by checking whether the dollar range falls at index 2 or 3
- Batched validator — `_val_warnings` accumulates data quality issues and prints a deduplicated summary at end of build instead of per-row WARNs
- SCOTUS `(part)`/`(add'l)` fix — numbered OGE entries like `2. (add'l) P1 E` now inherit the preceding asset's name via `prev_asset` tracking
- `Hon.` stripped universally — `_strip_hon()` applied in `load_tsv()` and Senate HTML title parsing
- Bonnie Watson Coleman alias added — `'coleman' → 'watson coleman'` in `MANUAL_LAST_ALIASES`
- Schedule B leak fix — 3,802 House annual PDF rows from Part II (Transactions) filtered out by date-in-value guard

### Data quality fixes in `build_transactions.py`
- `S O:` prefix (Schedule O label) stripped from asset names
- Smashed rows starting with `F S:` now parsed correctly — removed early-skip that was discarding real transaction data
- Senate former-senator names fixed — `(Former Senator)` qualifier stripped before `split_name()`
- Senate PTR bioguide now resolved from HTML-parsed name — fixes NULL bioguide for most senators
- `Hon.` stripped via `_HON_RE_TX` in `split_name()`
- SCOTUS `(part)`/`(add'l)` fix applied in `parse_scotus_278_transactions_md()` flush()

### `name_resolver.py`
- Kennedy/Whig fix — historical legislators now get `max_year=1900` instead of 2099
- Jim Justice alias: `'justice ii' → 'justice'`
- Barry Moore fix: step 2b tries last word of multi-word first name
- Credential stripping: `_strip_creds()` strips MD/FACS/PhD etc. from first name
- Delaney added to `EXEC_PRINCIPALS`

### Repository
- Created `.gitignore`
- Initial commit pushed to https://github.com/fzbrill/congressional-investments

---

## Session 13 (2026-06-08)

### `download_annual_filings.py`
- Fixed Senate 403 error: wrong date format (`2026-01-01` → `01/01/2026 00:00:00`) and missing `csrfmiddlewaretoken` in POST payload
- Added FilingType breakdown diagnostic
- Added low-count warning explaining bulk ZIP lag

### `check_zips.py` (new)
- Diagnosed root cause: 2025 House bulk ZIP not yet populated. `2025FD.zip` has only 1 FilingType='O' row (Hampton Redmond). ~430 member originals not yet available. Re-run `download_annual_filings.py --year 2025` in a few months.

---

## Session 12 (2026-06-08)

### `build_transactions.py`
- House PTR smashed-row parser: `_parse_smashed_row()` recovers transactions where pdfplumber merges all columns into column 0
- "F S: New" (Filing Section label) spurious rows eliminated
- April McClain Delaney party fixed (new 119th Congress member not yet in congress-legislators YAML; added manual entry in `EXEC_PRINCIPALS`)
- 46/46 tests pass

---

## Session 11 (2026-06-08)

### `query_server.py`
- SCOTUS party filter fixed — CASE expression on `last_name` keyed to appointing president added to `/api/search` and `/api/transactions`
- Sidebar asset type checkboxes removed — long asset type list better served by top search bar dropdown only

### `test_server.py`
- File reconstructed after CIFS truncation at line 759; 46/46 tests pass

---

## Session 10 (2026-06-08)

### Architecture
- `query_server.py` split into `query_server.py` (661 lines) + `template.html` — eliminates CIFS-mount Write-tool truncation errors on the HTML template
- `build_db.py` `__main__` guard added — `from build_db import ...` in `build_transactions.py` no longer wipes the holdings table

### Features
- SCOTUS party dots — justices show R/D dot based on appointing president (static JS client-side dict)
- Member picker in sidebar — filterable dropdown below State picker; filters by current chamber/party/state; selecting a member sets both holdings and transactions member fields
- Radio button defaults — "All chambers" and "All parties" now checked by default
- Browse Members click behavior — clicking a member card selects them in the sidebar picker without switching panel
- `build_transactions.py` `--years` flag — append only specified years; avoids full rebuild when new year data arrives
- `flush=True` on all progress prints — live build progress on Windows
- 2026 PTR data downloaded (234 House + 69 Senate)

---

## Session 9 (2026-06-08)

### `build_db.py`
- File restored after CIFS truncation at line 264
- Trump 278e parser: `parse_trump_278e_md()` handles Part 6 (EIF-first) and Schedule 1 for Part 2 (description-first, EIF inline); normalises spelled-out dollar ranges
- SCOTUS 278 parser: `parse_scotus_278_md()` reads from `.md` cache
- `convert_pdfs_to_md.py` (new): converts SCOTUS PDFs and Trump 278e to `.md` text caches with `<!-- page N -->` markers; skips already-cached files on re-run

---

## Session 8 (2026-06-08)

### `query_server.py`
- `_ensure_member_info()` now fixes NULL bioguides in `transactions` table as well as `holdings`
- "Viewing X–Y" counter fixed: DOM-based `offsetTop` measurement replaces fixed `ROW_H=35` estimate
- SCOTUS legend rows filtered at query time (WHERE clause filters rows where `asset` starts with digit + ". ") and at parse time (`LEGEND_ROW_RE` in `build_db.py`)
- Sidebar filter routing fixed: `applyQfFilters()` dispatches to active panel; party/state params added to `/api/transactions`

### `test_server.py`
- 3 new regression tests for `TestFixNullBioguideTransactions`

---

## Session 7 (2026-06-08)

### `query_server.py`
- `_ensure_member_info()` last-word first-name retry for NULL bioguides — fixes Brown ("M Shontel" → "shontel") and Kelly ("John Trent" → "trent")

### UI
- Member count shown in Browse Members panel topbar (updates live with search/filter)

---

## Session 6 (2026-06-08)

### Architecture change: bioguide-based JOIN
- New `name_resolver.py` module — resolves (last, first, year) → bioguide at import time from congress-legislators YAMLs; year-based filtering disambiguates historical vs. current members
- `holdings` and `transactions` tables now have `bioguide TEXT` column populated at parse time
- All API endpoints use `LEFT JOIN member_info mi ON h.bioguide = mi.bioguide` — replaces complex correlated subqueries

### Fixes
- Multiple CIFS file truncations repaired in `test_server.py`, `query_server.py`, `build_transactions.py`, `download_committees.py`
- 47/47 tests pass

---

## Session 5 (2026-06-07)

### UI
- Party affiliation color dots (blue=Democrat, red=Republican, lavender=Independent) in Search Holdings, Transactions, Browse Members
- Sidebar quick filters redesigned: Chamber=radio group (mutually exclusive), Asset Type=checkboxes (multi-select, OR within group); groups AND together
- Infinite scroll: Search Holdings + Transactions load 200 rows at a time via IntersectionObserver; replaces prev/next pagination
- "Viewing rows X–Y of Z" visible-range indicator

### Backend
- `member_info` table added to `download_committees.py` (536 rows from legislators-current.yaml)
- `_ensure_member_info()` added to `query_server.py` — graceful degradation if table not yet populated
- `build_transactions.py` fetches bulk ZIP per year to build `manifest.tsv`; `download_house_ptrs.py` also saves manifest on download
- Senate PTR name extraction fixed (H2 parenthetical "(Last, First)" instead of H1 title)
- `/api/search` and `/api/export` accept pipe-separated `asset_type` values (e.g. `Stock|Mutual Fund`)

---

## Session 4 (2026-06-07)

### UI
- State filter for holdings
- Date range filter for transactions
- Ctrl+Enter for SQL
- State column in transactions table
- Owner code decoding (SP/JT/DC)
- SQL templates for committees + transactions
- 8 NL example chips including committee queries

### Fixes
- Auto-load transactions bug fixed
- `exportTxCSV` fixed
- Asset quick-filters fixed
- `filterByMember` fixed (full name matching)
- High-value SQL template fixed
- Sort indicators fixed
- Partisan label fixed

---

## Session 3 (2026-06-07)

### UI
- Members panel party data fixed: `/api/members` uses correlated subquery + wrap for party filter
- Members panel scrollbar fixed: `min-height:0` added to `.members-grid`
- Sidebar state picker: custom dropdown widget (all 50 states + DC); replaces inline member-state select

---

## Sessions 1–2 (2026-06-06 / 2026-06-07)

Initial build:
- Flask web app with Holdings search, Transactions browser, Browse Members, SQL Explorer, Ask (NL via Anthropic API)
- House annual PDFs parsed via pdfplumber
- Senate annual HTML parsed via BeautifulSoup
- House + Senate PTR transactions downloaded and parsed
- Committee memberships from unitedstates/congress-legislators
- SQLite database with 6 tables: holdings, transactions, committees, committee_members, member_info, name_aliases
