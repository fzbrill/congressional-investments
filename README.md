# Congressional Investments

A Flask web app for exploring U.S. congressional financial disclosures — holdings, transactions, and committee memberships for House members, Senators, Supreme Court justices, and the President.

## What it does

- **Holdings** — search annual financial disclosure assets by member, chamber, party, state, or asset type
- **Transactions** — browse Periodic Transaction Reports (PTRs) filed within 45 days of each trade
- **Browse Members** — filter the full member roster by chamber, party, and state
- **SQL Explorer** — run raw SQL against the database
- **Ask** — natural language queries via the Anthropic API (requires `ANTHROPIC_API_KEY`)

## Data sources

| Source | Coverage |
|--------|----------|
| House annual disclosures (PDF) | 372 members, 2024 |
| Senate annual disclosures (HTML) | 93 senators, 2024 |
| SCOTUS OGE Form 278 (PDF → Markdown) | 9 justices, 2024 |
| Trump OGE Form 278e (PDF → Markdown) | 2024 |
| House PTRs | 2024–2026 |
| Senate PTRs | 2024–2026 |
| Committee memberships | unitedstates/congress-legislators |

## Setup

### Prerequisites

```
pip install flask pdfplumber beautifulsoup4 lxml pyyaml requests
```

For OCR of scanned PDFs (optional):
```
pip install anthropic
```

### First run

```
# 1. Download data (skip any you already have)
python download_house_pdfs.py
python download_senate_html.py
python download_scotus_pdfs.py
python download_house_ptrs.py
python download_senate_ptrs.py
python download_committees.py

# 2. Convert SCOTUS and Trump PDFs to Markdown cache
python convert_pdfs_to_md.py

# 3. Build the database
python build_db.py
python build_transactions.py

# 4. Start the server
python query_server.py
```

Then open http://localhost:5050.

### Refresh existing data

```
python refresh.py           # full refresh
python refresh.py --ptrs    # PTR transactions only
```

## File inventory

| File | Purpose |
|------|---------|
| `query_server.py` | Flask server — all API endpoints |
| `template.html` | Single-page web UI |
| `build_db.py` | Parse annual disclosures → `holdings` table |
| `build_transactions.py` | Parse PTRs → `transactions` table |
| `name_resolver.py` | Resolve (last, first, year) → bioguide via congress-legislators YAML |
| `download_committees.py` | Download committee/member data → `member_info`, `committees` tables |
| `download_house_pdfs.py` | Download House annual PDFs |
| `download_senate_html.py` | Download Senate annual HTML |
| `download_scotus_pdfs.py` | Download SCOTUS PDFs |
| `download_house_ptrs.py` | Download House PTR PDFs + save manifest |
| `download_senate_ptrs.py` | Download Senate PTR HTML |
| `download_annual_filings.py` | Download annual filing ZIPs (House + Senate) |
| `convert_pdfs_to_md.py` | Convert SCOTUS + Trump PDFs → Markdown cache |
| `update_member_list.py` | Rebuild member TSV from FD ZIP + download new PDFs |
| `ocr_scanned_pdfs.py` | OCR scanned House PDFs via Claude vision API |
| `refresh.py` | Full refresh orchestrator |
| `test_server.py` | pytest test suite |
| `house_members_2024.tsv` | House member list (372 members) |
| `house_members_2025.tsv` | House member list (515 members) |
| `house_members_2026.tsv` | House member list (234 PTR filers) |
| `senate_members_2024.tsv` | Senate member list |

## Database

SQLite file `holdings.db` (not committed — rebuild with the scripts above).

Tables: `holdings`, `transactions`, `committees`, `committee_members`, `member_info`, `name_aliases`

Current counts (2024 annual + 2024–2026 PTRs):
- Holdings: 22,642
- Transactions: 9,739

## Known limitations

- **2025 House annual reports not yet available** — the House Clerk bulk ZIP hasn't been updated with 2025 member annuals (as of June 2026). Re-run `download_annual_filings.py --year 2025` in a few months.
- **Schedule A/B detection is fragile** — 3,802 House annual PDF rows from Part II (Transactions) are filtered out by a date-in-value guard. A more robust fix would detect the "Part II" heading via `page.extract_text()`.
- **Transaction type blank for most House/Senate PTRs** — only SCOTUS and a few House rows populate the Transaction column. Senate PTR ticker extraction also unimplemented.
- **SCOTUS transaction asset names have leading hyphens** — `_clean_asset()` not yet applied in the SCOTUS transaction parser.
- **Deduplication not yet implemented** — the same transaction can appear in multiple PTR filings.

See [CHANGES.md](CHANGES.md) for full development history.
