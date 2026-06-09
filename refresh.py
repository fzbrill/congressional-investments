"""
refresh.py — one-command data refresh for Congressional Investments
Runs all download and parse steps in sequence.

Usage:
    python refresh.py              # full refresh (all sources, last 2 years of PTRs)
    python refresh.py --quick      # PTRs only (fast, current year only)
    python refresh.py --annual     # annual disclosures only
    python refresh.py --ptrs       # transaction reports only
    python refresh.py --committees # committee membership only (fast, ~5s)

Requirements:
    pip install requests beautifulsoup4 pdfplumber openpyxl anthropic pyyaml

Environment (for NL interface):
    export ANTHROPIC_API_KEY=sk-ant-...
"""
import subprocess, sys, os, time, argparse, sqlite3
from datetime import datetime

BASE = os.path.dirname(os.path.abspath(__file__))
DB   = os.path.join(BASE, 'holdings.db')

parser = argparse.ArgumentParser(description='Refresh all congressional disclosure data')
parser.add_argument('--quick',      action='store_true', help='PTRs current year only')
parser.add_argument('--annual',     action='store_true', help='Annual disclosures only')
parser.add_argument('--ptrs',       action='store_true', help='PTRs only')
parser.add_argument('--committees', action='store_true', help='Committee membership only')
parser.add_argument('--year',       type=int, default=2025)
args = parser.parse_args()

def run(script, extra_args=''):
    cmd = [sys.executable, os.path.join(BASE, script)] + extra_args.split()
    print(f"\n{'═'*60}")
    print(f"  Running: {script} {extra_args}")
    print(f"{'═'*60}")
    start = time.time()
    result = subprocess.run(cmd, cwd=BASE)
    elapsed = time.time() - start
    status = '✓ OK' if result.returncode == 0 else f'✗ FAILED (exit {result.returncode})'
    print(f"\n  {status} — {elapsed:.0f}s")
    return result.returncode == 0

def db_stats():
    if not os.path.exists(DB): return
    conn = sqlite3.connect(DB)
    holdings_n = conn.execute("SELECT COUNT(*) FROM holdings").fetchone()[0]
    members_n  = conn.execute("SELECT COUNT(DISTINCT last_name||first_name) FROM holdings").fetchone()[0]
    try:
        tx_n = conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
    except:
        tx_n = 0
    try:
        cm_n = conn.execute("SELECT COUNT(*) FROM committee_members").fetchone()[0]
    except:
        cm_n = 0
    conn.close()
    print(f"\n  📊 DB stats: {holdings_n:,} holdings, {members_n} members, {tx_n:,} transactions, {cm_n:,} committee assignments")

print(f"""
╔══════════════════════════════════════════════════════════╗
║     Congressional Investments — Data Refresh             ║
║     {datetime.now().strftime('%Y-%m-%d %H:%M')}                                     ║
╚══════════════════════════════════════════════════════════╝
""")

do_annual     = not args.ptrs and not args.committees
do_ptrs       = not args.annual and not args.committees
do_committees = not args.annual and not args.ptrs or args.committees
year_flag     = f'--year {args.year}'

results = {}

if do_annual:
    # ── Annual disclosures ──────────────────────────────────────────────────
    print("\n▶ STEP 1/4 — Download House annual disclosures")
    results['house_annual'] = run('download_house_pdfs.py')

    print("\n▶ STEP 2/4 — Download Senate annual disclosures")
    results['senate_annual'] = run('download_senate_html.py')

    print("\n▶ STEP 3/4 — Download SCOTUS disclosures")
    results['scotus'] = run('download_scotus_pdfs.py')

    print("\n▶ STEP 4/4 — Rebuild holdings database")
    results['build_db'] = run('build_db.py')
    db_stats()
else:
    print("  (skipping annual disclosures)")

if do_ptrs:
    years_flag = '--years 2024 2025' if not args.quick else year_flag

    print(f"\n▶ PTR STEP 1/3 — Download House transaction reports ({years_flag})")
    results['house_ptrs'] = run('download_house_ptrs.py', years_flag)

    print(f"\n▶ PTR STEP 2/3 — Download Senate transaction reports ({years_flag})")
    results['senate_ptrs'] = run('download_senate_ptrs.py', years_flag)

    print("\n▶ PTR STEP 3/3 — Parse transactions into DB")
    results['build_tx'] = run('build_transactions.py')
    db_stats()
else:
    print("  (skipping PTRs)")

if do_committees:
    print("\n▶ COMMITTEE STEP — Download committee membership")
    results['committees'] = run('download_committees.py')
    db_stats()
else:
    print("  (skipping committees)")

# ── Summary ────────────────────────────────────────────────────────────────
print(f"""
{'═'*60}
  REFRESH COMPLETE — {datetime.now().strftime('%Y-%m-%d %H:%M')}
{'═'*60}""")

for step, ok in results.items():
    print(f"  {'✓' if ok else '✗'}  {step}")

db_stats()

print(f"""
  To explore: python query_server.py
  NL queries: set ANTHROPIC_API_KEY and use the Ask panel
""")
