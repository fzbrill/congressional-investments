# Congressional Investments Project

## Quick restart prompt
> "Continue congressional investments work. Connect Documents\Claude\Projects\congressional-investments. See README for current status and next steps."

## Contents
| File/Folder | Description |
|---|---|
| `holdings.db` | SQLite DB — holdings + transactions tables |
| `congressional_investments.xlsx` | Main spreadsheet |
| `query_server.py` | Flask web UI (localhost:5050) — Search, Transactions, Ask (NL), SQL, Members |
| `refresh.py` | One-command full refresh orchestrator |
| `build_db.py` | Parse annual disclosures → `holdings` table |
| `build_transactions.py` | Parse PTRs → `transactions` table |
| `update_member_list.py` | Download 2024FD.zip → rebuild TSV → download new PDFs |
| `download_house_pdfs.py` | Download House annual PDFs |
| `download_senate_html.py` | Download Senate annual HTML |
| `download_scotus_pdfs.py` | Download SCOTUS PDFs |
| `download_house_ptrs.py` | Download House PTR transaction PDFs |
| `download_senate_ptrs.py` | Download Senate PTR HTML |
| `ocr_scanned_pdfs.py` | OCR 21 scanned House PDFs via Claude vision API |
| `download_committees.py` | Download committee membership → committees + committee_members tables |
| `fix_wrong_docids.py` | (superseded by update_member_list.py) |
| `house_members_2024.tsv` | House member list — 372 members (rebuilt 2026-06-06) |
| `senate_members_2024.tsv` | Senate member list (intact) |
| `house_pdfs_2024/` | Downloaded House annual PDFs |
| `senate_html_2024/` | Downloaded Senate annual HTML |

## Current status (as of 2026-06-08, session 14)

### Completed this session (session 14)

**Data quality fixes in `build_transactions.py` — full rebuild required:**

- ✅ **`S O:` prefix (Schedule O label) now stripped** — non-smashed rows where the asset column contained `S O: JP Morgan Investment Account` had the prefix stored literally. Now stripped at line 393 (`^S\s+O\s*:\s*` regex) for both smashed and non-smashed paths.
- ✅ **Smashed rows starting with `F S:` now parsed correctly** — previously `if _FS_NEW_RE.match(col0): continue` at line 354 skipped the entire smashed cell, including the real transaction data on subsequent lines (e.g. `F S: New\nS O: JP Morgan P 01/01/2024 $1,000 - $15,000`). Removed the early-skip; `_parse_smashed_row()` already filters `F S:` lines internally and returns None for pure section-label cells.
- ✅ **Senate former-senator names fixed** — `(Former Senator)` qualifier in HTML heading `The Honorable Marco Rubio (Former Senator)` was passed to `split_name`, producing `last="Senator)"`, `first="Marco Rubio (Former"`. Added a regex to strip `(Former Senator)` / `(Senator)` / `(Representative)` etc. before calling `split_name`.
- ✅ **Senate PTR bioguide now resolved from HTML-parsed name** — PTR file UUIDs never match the annual-report UUIDs in `senate_members_2024.tsv`, so all Senate PTR transactions had `bioguide=NULL`. After calling `parse_senate_ptr_html()`, the main loop now re-resolves using the name actually extracted from the HTML and patches all rows. This means most senators will now have correct bioguide/party stored in the DB (not just resolved at query time).
- ✅ **Kennedy/Whig fix in `name_resolver.py`** — historical legislators with no term dates in the YAML previously got `max_year=2099`, letting them beat modern legislators in year-filtered matching. Now: `max_year = 1900 if is_historical else 2099`.
- ✅ **Unmatched member warnings** — both House and Senate loops now print `WARN unmatched: Last, First (file)` when `resolver.resolve()` returns None for a non-empty name, satisfying the "report an error for unrecognized filers" requirement.
- **Final result: zero WARN unmatched lines across all 1200 House + 330 Senate PTR files (9,725 transactions)**

**Data quality fixes in `build_db.py` — full holdings rebuild required:**

- ✅ **Strip ⇒ prefix from House asset names** — House annual PDFs contain account/schedule header rows that pdfplumber merged into asset names (e.g. `LIVTR ⇒ Johnson & Johnson`). Now stripped via `_clean_asset()` using `r'^.*⇒\s*'` regex.
- ✅ **Strip leading hyphens from SCOTUS asset names** — markdown conversion of SCOTUS PDFs produces lines starting with `- Asset Name` (list markers). `_clean_asset()` now also strips leading `-`, `–`, `—` characters.
- ✅ **Expand OGE letter codes to dollar ranges** — SCOTUS OGE-278 value_code field was stored as raw codes (J/K/L/M/N/O/P1-P4). Now expanded to human-readable ranges (J→`$1,001 - $15,000`, …, P4→`Over $50,000,000`) via `_OGE_VALUE_MAP` in `parse_scotus_278_md()`.
- ✅ **6-column vs 5-column PDF auto-detection** — House annual PDFs have either 5 columns (Asset|Owner|Value|IncType|Income) or 6 columns (Asset|EIF|Owner|Value|IncType|Income). Parser now detects which layout is in use by checking whether the dollar range falls at index 2 or index 3, and reads the correct columns accordingly. This fixes dates appearing in the Value field and P/S codes appearing in Income for some members.
- ✅ **Batched validator** — accumulates `{category: {count, sample}}` for data quality issues (date-in-value, tx-type-in-income, empty-asset) and prints a deduplicated summary at the end of the build run instead of per-row WARNs.

### Resolver improvements in `name_resolver.py` (also session 14)
- ✅ **Delaney** — added `D000671_SYN` to `EXEC_PRINCIPALS` (same synthetic bioguide as `download_committees.py`)
- ✅ **Jim Justice** — added `'justice ii' → 'justice'` to `MANUAL_LAST_ALIASES` (Senate PTR heading includes "II" in last name)
- ✅ **Barry Moore** — added step 2b: try last word of multi-word first name ("Felix Barry" → try "barry")
- ✅ **Credential stripping** — `_strip_creds()` helper strips MD/FACS/PhD etc. from first name; `last.split(',')[0]` strips credentials from last name (FD ZIP stores "Dunn, MD, FACS" in Last field)

### ACTION REQUIRED — full rebuild from Windows terminal
```
# 1. Rebuild holdings DB (⇒ prefix, leading hyphens, OGE codes, column mapping all fixed)
python build_db.py

# 2. Rebuild transactions (F S:/S O: fixes, senator name fixes, zero WARNs)
python build_transactions.py

# 3. Restart server
python query_server.py
```
After build_db.py: check the validator summary at the end for any remaining date-in-value or tx-type-in-income issues.

### Still open
- [ ] **McConnell no party** — he resolves via `unique candidate` fallback (step 4) but `_ensure_member_info()` in `query_server.py` may be failing to find him at query time. Investigate after rebuild.
- [ ] **Delaney in Browse Members** — requires task #13 (show members with zero holdings/transactions).
- [ ] #12: Deduplicate transactions table
- [ ] #13: Show complete member roster (545 members, including those with zero holdings)
- [ ] #14: Add Executive branch PTR transactions
- [ ] #15: Sync left sidebar filters with top search bar
- ✅ **#16: SCOTUS (part)/(add'l) asset names fixed** — numbered OGE entries like `2. (add'l) P1 E` now inherit the preceding asset's name. Same fix applied to both `build_db.py` and `build_transactions.py`.
- ✅ **Bonnie Watson Coleman alias** — added `'coleman' → 'watson coleman'` to `MANUAL_LAST_ALIASES` (FD ZIP stores last="Coleman", YAML has "Watson Coleman")
- ✅ **Schedule B rows no longer leak into holdings** — House annual PDFs have a Part I (holdings) and Part II (transactions) section. Part II table headers now trigger `is_tx_header()` which sets `in_assets = False`, stopping the holdings parser. Previously these rows leaked through with dates in the value column; also added a skip-on-date guard as a safety net. Final holdings count: 22,642.
- ✅ **"Hon." stripped universally** — `_strip_hon()` now applied in `load_tsv()` (House names), Senate HTML title parsing, and `split_name()` in build_transactions.py. No more "Hon.. Richard W." stored in the DB.
- [ ] **Diehl, "Abigdail" (MD03)** — unresolvable name in house_members_2024.tsv; no matching legislator found. Likely a FD data quality error. Holdings are stored without a bioguide.
- [ ] **SCOTUS transaction asset names still have leading hyphens** — `_clean_asset()` was added to `parse_scotus_278_md()` (holdings) but the same stripping is not applied in `parse_scotus_278_transactions_md()` (build_transactions.py). Apply `_clean_asset()` (or an equivalent) to the asset name in that function's `flush()`.
- [ ] **Transaction type blank for House and Senate PTRs** — SCOTUS transactions correctly show "Sale"/"Purchase" but House shows mostly blank (a few "Sale (Partial)") and Senate is entirely blank. Investigate: `build_transactions.py` House parser likely has the tx_type extraction working for some PDF layouts but not others; Senate HTML parser may not be extracting the transaction type column at all. The `tx_type` field in the DB row tuple should be checked.
- [ ] **Ticker column blank for Senate PTRs** — `extract_ticker()` is probably not being called or is failing to find ticker symbols in Senate HTML asset names. Check the Senate PTR parser in build_transactions.py.
- [ ] **Schedule A/B detection is fragile** — currently relies on a date-in-value skip to discard 3,802 Schedule B (transaction) rows that leak into the holdings parser. Better fix: use `page.extract_words()` / `extract_text()` to detect the "Part II — Transactions" heading as a non-table text element and set `in_assets = False` at that point. An `is_tx_header()` approach using column names was tried but was too broad ("Type of Income" in Part I headers also matches).
- [ ] #12: Deduplicate transactions table
- [ ] #13: Show complete member roster (545 members, including those with zero holdings)
- [ ] #14: Add Executive branch PTR transactions
- [ ] #15: Sync left sidebar filters with top search bar

### ACTION REQUIRED — rebuild from Windows terminal
```
python build_db.py           # pick up Hon., tx-header, (part)/(add'l) fixes
python build_transactions.py # pick up (part)/(add'l) + Hon. fixes
python query_server.py       # restart server
```

---

## Current status (as of 2026-06-08, session 13)

### Completed this session (session 13)
- ✅ **Senate 403 fixed in `download_annual_filings.py`** — two bugs: (1) wrong date format (`2026-01-01` → `01/01/2026 00:00:00` per Senate eFD's required format), (2) `csrfmiddlewaretoken` missing from search POST payload — this was the actual 403 cause (the working PTR script includes it).
- ✅ **`download_annual_filings.py` FilingType diagnostic added** — now prints the FilingType breakdown of the ZIP so you can see what codes are present and catch unexpected variations.
- ✅ **Low-count warning added** — if fewer than 50 annual reports found in the ZIP, script explains the bulk ZIP lag and what to do.
- ✅ **`check_zips.py` added** — ran it and found the root cause: House bulk ZIP not yet populated (see below).

### House 2025 annual reports — root cause identified
`check_zips.py` output shows:
- `2025FD.zip`: 2410 rows total. FilingType='O' = **1** (Hampton Redmond). FilingType='A' = 78 (candidate/amended annuals). Expected ~430 member originals are NOT there yet.
- `2026FD.zip`: 1105 rows. No 'O' rows, 25 'A' rows (all May–June 2026 dates, likely freshmen).

**The House Clerk hasn't updated the bulk ZIP with the 2025 member annual disclosures.** They were due May 15, 2026 (it's June 8) but the bulk ZIP is updated on a significant delay — likely months after the deadline, possibly only once a year. Individual PDFs are available through the JavaScript-rendered search UI at disclosures-clerk.house.gov, but that search API is not publicly exposed as a simple REST endpoint.

**The 2025 annual report data can't be bulk-downloaded yet.** Options:
1. Wait a few months and re-run `python download_annual_filings.py --year 2025` — the ZIP should eventually populate
2. Implement a Selenium/Playwright or Chrome-based scraper to use the search UI directly

### Open tasks
- [ ] Verify Senate eFD no longer returns 403 after the date-format + csrf fixes
- [ ] Re-run `python download_annual_filings.py --year 2025` in a few months once the bulk ZIP is populated

### Completed this session (session 12)
- ✅ **House PTR smashed-row parser** — `build_transactions.py` now recovers transactions where pdfplumber merges all table columns into column 0 (observed in Delaney PTRs and likely many others). Previously these rows were silently dropped. The new `_parse_smashed_row()` helper reconstructs asset, tx_type, dates, and amount from the merged text, including handling split amounts that span lines.
- ✅ **"F S: New" spurious rows eliminated** — PTR PDFs contain a "Filing Section: New" label printed on every page. The parser now skips standalone rows where this label lands in the asset column, and strips it when appended to real asset text.
- ✅ **April McClain Delaney party fixed** — she is not yet in the `congress-legislators` YAML (new 119th Congress member). Added as a manual entry in `EXEC_PRINCIPALS` in `download_committees.py` with both `'April McClain'` and `'April'` as first-name variants so her transactions resolve to Democrat/MD.
- ✅ **46/46 tests pass** — no regressions.

### ACTION REQUIRED — run from Windows terminal
```
# 1. Re-run download_committees.py to add Delaney to member_info
python download_committees.py

# 2. Re-run build_transactions.py (at minimum for 2025/2026 years)
python build_transactions.py --years 2025 2026

# 3. Restart server to pick up member_info change
python query_server.py
```

### Open tasks
- [ ] Verify Delaney shows Democrat party after rebuild
- [ ] Verify "F S: New" rows are gone and real Delaney transactions appear
- [ ] #12: Deduplicate transactions table (same TX in multiple PTR filings)
- [ ] #13: Show complete member roster (535+ members even with zero holdings)
- [ ] #14: Add Executive branch PTR transactions
- [ ] #15: Sync left sidebar filters with top search bar
- [ ] #16: Fix SCOTUS (part)/(add'l) transaction parsing in build_transactions.py
- [ ] Download 2025 annual filings (House + Senate portals)
- [ ] Rename holdings.db → congressional.db or disclosures.db


---

## Current status (as of 2026-06-08, session 11)

### Completed this session (session 11)
- ✅ **SCOTUS party filter fixed** — party radio filter now includes SCOTUS justices. Previously, selecting Democrat/Republican excluded all SCOTUS because they have no `member_info` rows. Fixed by adding a SQL CASE expression keyed on `last_name` to both `/api/search` (h.last_name) and `/api/transactions` (t.last_name). Prior session had introduced broken Python syntax (multiline f-string on CIFS mount); replaced with safe string concatenation.
- ✅ **Sidebar asset type checkboxes removed** — user decided the long list of asset types is better served by the top search bar dropdown only. Removed the "Asset Type" nav-label, `#qf-asset-group` checkboxes, and all associated JS (checkbox→hidden-input sync, syncSidebar read-back, event wire-up, reset clear).
- ✅ **`test_server.py` truncation repaired** — file was truncated mid-test at line 759 (CIFS corruption). Reconstructed `test_learn_from_transactions_adds_alias` from context. **46/46 tests pass.**

### ACTION REQUIRED — restart server from Windows terminal
```
python query_server.py
```
No DB rebuild needed — all changes are in `query_server.py` and `template.html`.

### Open tasks
- [ ] Verify SCOTUS party filter works after restart (select Democrat → SCOTUS should still appear)
- [ ] Verify April McClain Delaney party shows (re-ran download_committees.py last session)
- [ ] #12: Deduplicate transactions table (same TX in multiple PTR filings)
- [ ] #13: Show complete member roster (435 House + 100 Senate + 9 SCOTUS + 1 President even with zero holdings)
- [ ] #14: Add Executive branch PTR transactions
- [ ] #15: Sync left sidebar filters with top search bar (dropdown ↔ radio buttons)
- [ ] #16: Fix SCOTUS (part)/(add'l) transaction parsing in build_transactions.py
- [ ] Download 2025 annual filings (House + Senate portals)
- [ ] Rename holdings.db → congressional.db or disclosures.db


---

## Current status (as of 2026-06-08, session 10)

### Completed this session (session 10)
- ✅ **`query_server.py` split into `query_server.py` + `template.html`** — the 1496-line HTML/JS template is now in a separate file loaded at startup with `open("template.html")`. `query_server.py` is 661 lines (was 2157). Neither file will ever hit the CIFS-mount Write-tool truncation limit again. This permanently eliminates the "unterminated triple-quoted string" class of errors.
- ✅ **UI layout bug fixed** — sidebar member picker HTML insertion had consumed the state `qf-section` closing `</div>` without restoring it, causing the main panel to render at bottom-left. Fixed by inserting the missing `</div>` before the Member nav-label.
- ✅ **SCOTUS party dots** — justices now show R/D party dot based on appointing president (static JS dict client-side).
- ✅ **Member picker in sidebar** — filterable member dropdown below the State picker. List is filtered by current chamber/party/state selections. Selecting a member sets both the holdings and transactions member fields.
- ✅ **Radio button defaults** — "All chambers" and "All parties" radios now have `checked` by default so one button is always selected.
- ✅ **Browse Members click behavior** — clicking a member card now just selects that member in the sidebar picker (no panel switch). Chamber/party/state changes clear and reload the member list.
- ✅ **2026 PTR data downloaded** — 234 House + 69 Senate PTRs for 2026 fetched.
- ✅ **`build_transactions.py` `--years` flag** — `python build_transactions.py --years 2026` appends only 2026 data (skips DROP TABLE, deletes+reprocesses only the requested years). Avoids full rebuild when a new year of data arrives.
- ✅ **`flush=True` on all progress prints** — build scripts now show live progress on Windows.
- ✅ **`__main__` guard in `build_db.py`** — `conn`, DROP TABLE, `insert()`, and all processing moved inside `if __name__ == '__main__':`. `from build_db import ...` in `build_transactions.py` no longer wipes the holdings table.

### ACTION REQUIRED — run from Windows terminal
```
# 1. Restart the server to pick up template.html split + all UI fixes
python query_server.py

# 2. (If holdings are gone) Rebuild DB — requires convert_pdfs_to_md.py first
python convert_pdfs_to_md.py   # convert SCOTUS PDFs + Trump 278e → .md cache
python build_db.py             # rebuild holdings (SCOTUS + Trump rows included)
python build_transactions.py   # full rebuild
# — OR for just 2026 transactions —
python build_transactions.py --years 2026

# 3. Download 2025 annual filings (due May 15 2026, now available)
python download_house_pdfs.py --year 2025
python download_senate_html.py --year 2025
# then rebuild holdings to include new members (e.g. Nick Begich AK)
python build_db.py
```

### Open tasks
- [ ] Restart `query_server.py` and verify UI layout is correct (sidebar on left, main panel fills right)
- [ ] Verify SCOTUS party dots, member picker, radio button defaults all work after restart
- [ ] Download 2025 annual filings from House/Senate portals (gets new members like Nick Begich)
- [ ] Rebuild holdings after 2025 download to include new members
- [ ] Consider: update `test_server.py` with tests for new member picker endpoint behavior
- [ ] Consider: `refresh.py` — add step for `convert_pdfs_to_md.py` and incremental `--years` PTR download


---

## Previous status (as of 2026-06-08, session 9)

### Completed this session (session 9)
- ✅ **`build_db.py` fully restored** — file was truncated at line 264 mid-regex (`_278E_INC_TYPE`) due to Write/Edit tool size limit on the CIFS-mounted Windows folder. Fixed by writing via bash cp from /tmp (bypasses the tool's CIFS limit). File is now 399 lines, syntax OK.
- ✅ **Trump 278e parser complete** — `parse_trump_278e_md()` handles both Part 6 (EIF-first layout, description on continuation line) and Schedule 1 for Part 2 (description-first, EIF inline). Normalises spelled-out dollar ranges e.g. `$5,000,001 - $25,000,000`.
- ✅ **SCOTUS 278 parser complete** — `parse_scotus_278_md()` reads from `.md` cache. Tested: 25 holdings for Thomas, 86 for Barrett.
- ✅ **`convert_pdfs_to_md.py` complete** — converts SCOTUS PDFs and Trump 278e to `.md` text caches with `<!-- page N -->` markers; skips already-cached files on re-run. Run once before build_db.py.
- ✅ **Markdown cache strategy** — all new parsers read from `.md` files, not PDFs directly, avoiding multi-MB PDF context blowup in Claude sessions and in repeated parse runs.

### ACTION REQUIRED — run from Windows terminal
```
python convert_pdfs_to_md.py   # convert all SCOTUS PDFs + Trump 278e → .md cache
python build_db.py             # rebuild holdings (adds SCOTUS + Trump rows)
python build_transactions.py   # rebuild transactions (adds SCOTUS TX)
# then restart query_server.py
```

### Open tasks
- [ ] Run the above build sequence (cannot run from bash — SQLite needs Windows for CIFS write)
- [ ] Verify SCOTUS and Trump holdings counts after build
- [ ] Possibly add Senate audit (~100 senators), more Executive OGE filers
- [ ] Update test_server.py if new SCOTUS/Executive test cases are warranted

---

## Previous status (as of 2026-06-08, session 8)

### Completed this session (session 8)
- ✅ **Transactions party/state fix** — `_ensure_member_info()` now fixes NULL bioguides in the `transactions` table in addition to `holdings`. Two new blocks added:
  - "Learn from transactions" — same unique-last-name alias learning already done for holdings, now also runs on transactions table
  - `_fix_null_bioguides()` refactored into shared helper and called for both tables
  - Covers members like McCormick, Mullin, McConnell, Boozman, Smith, Whitehouse whose PTR-filing names didn't resolve at build time
- ✅ **"Viewing X–Y" counter fixed** — replaced fixed `ROW_H=35` estimate with DOM-based `offsetTop` measurement in `updateSearchVisibleRange()` and `updateTxVisibleRange()`. Now correct for multi-line SCOTUS rows and any variable-height content.
- ✅ **SCOTUS legend rows filtered** — OGE Form 278 PDFs print a numbered key table at the bottom of each page ("1. Income Gain Codes: A =$1,000 or less..."). These were being parsed as asset rows because they contain dollar amounts. Fixed two ways:
  - **Query-time** (`/api/search`, `/api/export`): permanent WHERE clause filters rows where `asset` starts with a single digit + ". " — eliminates them from all views immediately, no rebuild needed. Verified safe: "3M Corp", "St. Jude Medical", "1st National Bank" all kept correctly.
  - **Parser** (`build_db.py`): `LEGEND_ROW_RE` added; `parse_house_pdf()` now skips these rows before inserting, so future rebuilds won't include them.
- ✅ **3 new regression tests** — `TestFixNullBioguideTransactions`: verifies bioguide is fixed for McConnell/Whitehouse/Boozman transactions, and that party/state appear correctly after fix
- ✅ **Sidebar filter routing fixed** — sidebar filters now route to whichever panel is active:
  - `applyQfFilters()` no longer force-switches to Search; dispatches to active panel (search / transactions / members)
  - `switchPanel()` always syncs sidebar chamber to the target panel and always reloads (was: only loaded transactions on first visit)
  - Party and state sidebar filters now work on the Transactions panel too (new params added to `/api/transactions` endpoint + `_fetchTxBatch()`)
  - `filterByMember()` sets form fields before calling `switchPanel` to avoid double load
- **Run tests from Windows:** `python -m pytest test_server.py -v -p no:cacheprovider` (expect 50/50)
- **Restart query_server.py** — all fixes activate on restart; no DB rebuild needed
- **SCOTUS note:** All 42 SCOTUS rows that showed were legend rows (OGE Form 278 footnotes, no real holdings data). The filter correctly removes them. The SCOTUS PDF parser likely needs work to extract real Part 3 asset data from Form 278 — this is a future task.

## Previous status (as of 2026-06-08, session 7)

### Completed this session (session 7)
- ✅ **Brown / Kelly party+state fix** — `_ensure_member_info()` now does a last-word first-name retry for NULL bioguides after the main learn-from-holdings pass. "Hon.. M Shontel" → tries "shontel" → matches Brown; "Hon.. John Trent" → tries "trent" → matches Kelly. **Verified working: Brown and all 4 Kellys show party/state.**
- ✅ **Member count in Browse Members panel** — upper-right of topbar now shows e.g. "527 members" (updates live with search/filter).
- ✅ **47/47 tests still passing**

### Completed this session (session 6)
- ✅ **New `name_resolver.py` module** — resolves (last, first, year) → bioguide at import time using congress-legislators YAMLs. Year-based filtering disambiguates historical vs. current members (e.g., Thomas Brackett Reed vs. Mike Rogers). Unique-last-name fallback handles cases like Ellzey, Ricketts, Hill.
- ✅ **`build_db.py` updated** — `holdings` table now has `bioguide TEXT` column. All rows get bioguide at parse time via `resolver.resolve(last, first, 2024)`.
- ✅ **`build_transactions.py` updated** — `transactions` table now has `bioguide TEXT` column. Same resolution at parse time.
- ✅ **`query_server.py` refactored** — all three endpoints (`/api/search`, `/api/transactions`, `/api/members`) now use `LEFT JOIN member_info mi ON h.bioguide = mi.bioguide` instead of the previous complex correlated-subquery name matching. WHERE clauses use `h.`/`t.` table aliases throughout.
- ✅ **47/47 tests pass** — `PYTHONPYCACHEPREFIX=/tmp/freshpyc python3 -m pytest test_server.py -v -p no:cacheprovider`
- ✅ **Multiple file truncations repaired** — `test_server.py`, `query_server.py`, `build_transactions.py`, `download_committees.py` were all truncated mid-line; all restored via binary append.

### ACTION REQUIRED — run from Windows terminal (SQLite can't write on CIFS mount from bash)
```
python download_committees.py   # rebuild member_info, name_aliases tables
python build_db.py              # rebuild holdings with bioguide column (~5-10 min)
python build_transactions.py    # rebuild transactions with bioguide column (~10-20 min)
# then restart query_server.py
```
The `holdings.db` file was truncated to 0 bytes during debugging; it will be recreated clean by `build_db.py`.
After rebuild, Brown and Kelly should show party/state automatically (last-word matching runs at startup).

### Known bash-sandbox limitation
SQLite cannot write to the CIFS-mounted Windows folder from the bash sandbox (disk I/O error due to file locking). All DB build scripts must be run from Windows. Test suite runs fine because it uses pytest's isolated in-memory DB via `TEST_DB` env var.

---

## Previous status (as of 2026-06-07, session 5)

### Completed this session (session 5)
- **`download_committees.py` overhauled — 4 fixes for remaining unmatched House members:**
  - Load `legislators-historical.yaml` in addition to current: covers retired/resigned members (Kilmer, Chavez-DeRemer, O'Halleran, Connolly) and non-voting delegates
  - `normalize()` applied to all alias insertions: strips accents so Sanchez==Sanchez, Garcia==Garcia, Barragan==Barragan
  - Added `greg`/`gregory` to NICKNAME_EXPANSIONS (fixes Greg Murphy)
  - Manual last-name overrides: `Arenholz`->Hinson (Ashley Hinson's birth name), `Paulina Luna`->Luna (Anna Paulina Luna's compound name)
  - NICKNAME_EXPANSIONS moved outside inner loop (was recreated per legislator)

### ACTION REQUIRED to activate these fixes
```
python download_committees.py   # then restart query_server.py
```

---

## Previous status (as of 2026-06-07, session 4)

### Completed this session (session 4)
- ✅ **Cruz/Budd/McConnell name matching fixed** via self-healing "learn from holdings" logic in `_ensure_member_info()`
  - Root cause: `legislators-current.yaml` stores `first='Ted'` for Cruz; Senate disclosures use legal name 'Rafael'. No alias existed for 'Rafael'.
  - Fix: on every startup, `_ensure_member_info()` INSERTs any holding filing-name that doesn't match an existing alias, for members whose last name is unique in `member_info`. Idempotent (INSERT OR IGNORE). Covers 'Rafael' (Cruz), 'THEODORE' (Budd), 'A. Mitchell' (McConnell), and any future cases automatically.
  - After restarting the server, Cruz shows "Ted" / TX / Republican everywhere.
- ✅ **`query_server.py` file restored** — file was truncated mid-HTML-template (~line 1764, no closing `"""`). Reconstructed by taking lines 1–522 from the truncated source (which had the correct learn SQL), extracting the full HTML template from the `.pyc` bytecode (61,685 chars), and appending the correct `index` route and `app.run` block from the bytecode disassembly.
- ✅ **Tests: 47/47 pass** (3 tests for `TestLearnAliasesFromHoldings`)
- ✅ Run: `python -m pytest test_server.py -v -p no:cacheprovider` → 47 passed

### Completed this session (session 4, continued)
- ✅ **Amy Coney Barrett → Tom Barrett (R-MI) bug fixed**: learn-from-holdings INSERT now guards `AND LOWER(h.chamber) IN ('senate','house')`. SCOTUS/Executive filers no longer pollute Congress member aliases.
- ✅ **Mike Lee shows "Mike"**: `download_committees.py` now expands known nickname↔formal-name pairs (mike↔michael, ted↔theodore, chuck↔charles, etc.). For each YAML alias, the complementary form is also added to `name_aliases` — covers all members regardless of whether their last name is unique.
- ✅ New regression test: `test_scotus_filing_not_aliased_to_legislator`

### Completed this session (session 3)
- ✅ `update_member_list.py` — rebuilt TSV from 2024FD.zip; 372 members, 36 new PDFs
- ✅ `build_db.py` — 21,928 holdings after annual rebuild
- ✅ `ocr_scanned_pdfs.py` — **COMPLETE**: 2,995 holdings from all 21 scanned members
- ✅ Total holdings: **24,952** / **459 members** in DB
- ✅ `refresh.py --ptrs` — ran; exposed 404 bugs in PTR download scripts → **FIXED**
- ✅ PTR scripts fixed: House now uses bulk ZIP (FilingType=='P'); Senate uses correct `/search/report/data/