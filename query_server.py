"""
Congressional Investments Query Server
Run:  python query_server.py
Then open: http://localhost:5050

Natural-language /api/ask endpoint requires:
    pip install anthropic
    export ANTHROPIC_API_KEY=sk-ant-...
"""
import sqlite3, json, csv, io, os, re, textwrap
from flask import Flask, make_response, request, jsonify, Response, send_from_directory

DB_PATH = os.environ.get("HOLDINGS_DB", os.path.join(os.path.dirname(__file__), "holdings.db"))
app = Flask(__name__, static_folder=None)

# ── Ensure member_info table exists (graceful degradation if download_committees.py
# hasn't been re-run yet — party dots simply won't show until then) ─────────────
def _ensure_member_info():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""CREATE TABLE IF NOT EXISTS member_info (
        bioguide      TEXT PRIMARY KEY,
        last_name     TEXT,
        first_name    TEXT,
        display_first TEXT,
        state         TEXT,
        party         TEXT
    )""")
    # Migrate: add display_first if missing
    cols = [r[1] for r in conn.execute("PRAGMA table_info(member_info)").fetchall()]
    if 'display_first' not in cols:
        conn.execute("ALTER TABLE member_info ADD COLUMN display_first TEXT")
    if 'is_current' not in cols:
        conn.execute("ALTER TABLE member_info ADD COLUMN is_current INTEGER DEFAULT 0")
    # Add bioguide column to holdings / transactions if missing (schema migration)
    try:
        h_cols = [r[1] for r in conn.execute("PRAGMA table_info(holdings)").fetchall()]
        if 'bioguide' not in h_cols:
            conn.execute("ALTER TABLE holdings ADD COLUMN bioguide TEXT")
    except Exception:
        pass
    try:
        t_cols = [r[1] for r in conn.execute("PRAGMA table_info(transactions)").fetchall()]
        if 'bioguide' not in t_cols:
            conn.execute("ALTER TABLE transactions ADD COLUMN bioguide TEXT")
    except Exception:
        pass
    # name_aliases: robust matching across nicknames / legal names
    conn.execute("""CREATE TABLE IF NOT EXISTS name_aliases (
        bioguide   TEXT,
        last_name  TEXT,
        first_name TEXT
    )""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_aliases ON name_aliases (last_name, first_name)")
    # Expression indexes so LOWER(last_name) comparisons hit the index instead of full-scan
    conn.execute("CREATE INDEX IF NOT EXISTS idx_aliases_lower_last ON name_aliases (LOWER(last_name))")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_member_info_lower_last ON member_info (LOWER(last_name))")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_aliases_bioguide ON name_aliases (bioguide)")
    try:
        conn.execute("CREATE INDEX IF NOT EXISTS idx_holdings_lower_last ON holdings (LOWER(last_name))")
    except Exception:
        pass  # holdings may not exist on first run
    # Seed aliases from member_info if the table is empty
    if conn.execute("SELECT COUNT(*) FROM name_aliases").fetchone()[0] == 0:
        conn.execute("""
            INSERT OR IGNORE INTO name_aliases (bioguide, last_name, first_name)
            SELECT bioguide, last_name, first_name FROM member_info WHERE first_name != ''
        """)
        conn.execute("""
            INSERT OR IGNORE INTO name_aliases (bioguide, last_name, first_name)
            SELECT bioguide, last_name, display_first FROM member_info
            WHERE display_first != '' AND LOWER(display_first) != LOWER(first_name)
        """)
    # Learn filing-name aliases from holdings for unique-last-name members.
    # Handles cases where disclosure forms use legal names the YAML doesn't list:
    #   "Rafael" for Ted Cruz, "THEODORE" for Ted Budd, "A. Mitchell" for McConnell.
    # Restricted to Senate/House to avoid SCOTUS/Executive filers polluting aliases
    #   (e.g. Amy Coney Barrett → Tom Barrett R-MI without this guard).
    # Runs at every startup; INSERT OR IGNORE makes it idempotent.
    try:
        conn.execute("""
            INSERT OR IGNORE INTO name_aliases (bioguide, last_name, first_name)
            SELECT DISTINCT mi.bioguide, h.last_name,
                CASE WHEN LOWER(SUBSTR(h.first_name,1,4))='hon.'
                     THEN TRIM(SUBSTR(h.first_name, INSTR(h.first_name,' ')+1))
                     ELSE h.first_name END
            FROM holdings h
            JOIN member_info mi ON LOWER(mi.last_name)=LOWER(h.last_name)
            WHERE h.first_name != ''
            AND LOWER(h.chamber) IN ('senate', 'house')
            AND (SELECT COUNT(*) FROM member_info mi2
                 WHERE LOWER(mi2.last_name)=LOWER(h.last_name)) = 1
            AND NOT EXISTS (
                SELECT 1 FROM name_aliases na WHERE na.bioguide=mi.bioguide
                AND (LOWER(na.first_name)=LOWER(CASE WHEN LOWER(SUBSTR(h.first_name,1,4))='hon.'
                         THEN TRIM(SUBSTR(h.first_name, INSTR(h.first_name,' ')+1)) ELSE h.first_name END)
                  OR LOWER(CASE WHEN LOWER(SUBSTR(h.first_name,1,4))='hon.'
                         THEN TRIM(SUBSTR(h.first_name, INSTR(h.first_name,' ')+1)) ELSE h.first_name END)
                     LIKE LOWER(na.first_name)||' %'
                  OR LOWER(CASE WHEN LOWER(SUBSTR(h.first_name,1,4))='hon.'
                         THEN TRIM(SUBSTR(h.first_name, INSTR(h.first_name,' ')+1)) ELSE h.first_name END)
                     LIKE '% '||LOWER(na.first_name))
            )
        """)
    except Exception:
        pass  # holdings table may not exist yet on first run
    # Fix unresolved NULL bioguides using last-word first-name matching.
    # Handles filings like "Hon.. M Shontel" Brown (try "shontel") and
    # "Hon.. John Trent" Kelly (try "trent") where name_resolver couldn't
    # match at build time due to non-unique last names.
    def _fix_null_bioguides(table):
        """Fix NULL bioguides in `table` by matching against name_aliases."""
        try:
            unresolved = conn.execute(
                f"SELECT DISTINCT last_name, first_name FROM {table} "
                f"WHERE bioguide IS NULL AND chamber IN ('House','Senate')"
            ).fetchall()
            if not unresolved:
                return 0
            _hon_re = __import__('re').compile(r'^hon\.\.?\s+', __import__('re').IGNORECASE)
            fixed = 0
            for last, first in unresolved:
                stripped = _hon_re.sub('', first.strip()).strip()
                words = stripped.split()
                tries = []
                if words:
                    tries.append(words[-1].lower())   # last word  (Shontel, Trent)
                    tries.append(words[0].lower())    # first word (John, M)
                    tries.append(stripped.lower())    # full stripped
                seen = set()
                for candidate in tries:
                    if candidate in seen:
                        continue
                    seen.add(candidate)
                    row = conn.execute(
                        "SELECT na.bioguide FROM name_aliases na "
                        "WHERE LOWER(na.last_name)=LOWER(?) AND LOWER(na.first_name)=?",
                        (last, candidate)
                    ).fetchone()
                    if row:
                        conn.execute(
                            f"UPDATE {table} SET bioguide=? "
                            f"WHERE last_name=? AND first_name=? AND bioguide IS NULL",
                            (row[0], last, first)
                        )
                        fixed += 1
                        break
            if fixed:
                conn.commit()
            return fixed
        except Exception:
            return 0

    _fix_null_bioguides('holdings')

    # Learn filing-name aliases from transactions (same logic as holdings above).
    # Ensures names used in PTR filings that differ from YAML entries become aliases.
    try:
        conn.execute("""
            INSERT OR IGNORE INTO name_aliases (bioguide, last_name, first_name)
            SELECT DISTINCT mi.bioguide, t.last_name,
                CASE WHEN LOWER(SUBSTR(t.first_name,1,4))='hon.'
                     THEN TRIM(SUBSTR(t.first_name, INSTR(t.first_name,' ')+1))
                     ELSE t.first_name END
            FROM transactions t
            JOIN member_info mi ON LOWER(mi.last_name)=LOWER(t.last_name)
            WHERE t.first_name != ''
            AND LOWER(t.chamber) IN ('senate', 'house')
            AND (SELECT COUNT(*) FROM member_info mi2
                 WHERE LOWER(mi2.last_name)=LOWER(t.last_name)) = 1
            AND NOT EXISTS (
                SELECT 1 FROM name_aliases na WHERE na.bioguide=mi.bioguide
                AND (LOWER(na.first_name)=LOWER(CASE WHEN LOWER(SUBSTR(t.first_name,1,4))='hon.'
                         THEN TRIM(SUBSTR(t.first_name, INSTR(t.first_name,' ')+1)) ELSE t.first_name END)
                  OR LOWER(CASE WHEN LOWER(SUBSTR(t.first_name,1,4))='hon.'
                         THEN TRIM(SUBSTR(t.first_name, INSTR(t.first_name,' ')+1)) ELSE t.first_name END)
                     LIKE LOWER(na.first_name)||' %'
                  OR LOWER(CASE WHEN LOWER(SUBSTR(t.first_name,1,4))='hon.'
                         THEN TRIM(SUBSTR(t.first_name, INSTR(t.first_name,' ')+1)) ELSE t.first_name END)
                     LIKE '% '||LOWER(na.first_name))
            )
        """)
    except Exception:
        pass  # transactions table may not exist yet

    _fix_null_bioguides('transactions')

    conn.commit()
    conn.close()
_ensure_member_info()


# ── helpers ──────────────────────────────────────────────────────────────────

def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def rows_to_list(rows):
    return [dict(r) for r in rows]

def safe_query(sql, params=()):
    """Run a SELECT-only query; raise ValueError for anything else."""
    stripped = sql.strip().lstrip(";").strip()
    if not re.match(r"(?i)^\s*(select|with)\b", stripped):
        raise ValueError("Only SELECT queries are allowed.")
    conn = get_conn()
    try:
        cur = conn.execute(stripped, params)
        cols = [d[0] for d in cur.description]
        data = [dict(zip(cols, row)) for row in cur.fetchall()]
        return cols, data
    finally:
        conn.close()

# ── API endpoints ─────────────────────────────────────────────────────────────

@app.route("/api/stats")
def stats():
    conn = get_conn()
    out = {}
    out["total"]   = conn.execute("SELECT COUNT(*) FROM holdings").fetchone()[0]
    out["members"] = conn.execute("SELECT COUNT(DISTINCT last_name||first_name) FROM holdings").fetchone()[0]
    out["chambers"]= rows_to_list(conn.execute(
        "SELECT chamber, COUNT(*) as count FROM holdings GROUP BY chamber ORDER BY count DESC").fetchall())
    conn.close()
    return jsonify(out)

@app.route("/api/search")
def search():
    member  = request.args.get("member", "").strip()
    asset   = request.args.get("asset", "").strip()
    chamber = request.args.get("chamber", "").strip()
    atype   = request.args.get("asset_type", "").strip()
    party   = request.args.get("party", "").strip()
    state   = request.args.get("state", "").strip()
    limit   = min(int(request.args.get("limit", 500)), 2000)
    offset  = int(request.args.get("offset", 0))

    # Always exclude OGE Form 278 legend rows (e.g. "1. Income Gain Codes: ...").
    # These appear in SCOTUS/Executive filings where the footnote key table is parsed
    # as if it were a holdings row. Pattern: single digit + ". " at the start of asset.
    clauses = ["NOT (h.asset LIKE '%. %' AND CAST(SUBSTR(h.asset,1,1) AS INTEGER) BETWEEN 1 AND 9 AND SUBSTR(h.asset,2,2)='. ')"]
    params = []
    if member:
        # Match raw name, combined name, or canonical display name (so "Cruz, Ted" finds
        # holdings where h.first_name = "Rafael Edward" but mi.display_first = "Ted")
        p = f"%{member}%"
        clauses.append(
            "(h.last_name LIKE ? OR h.first_name LIKE ? "
            "OR (h.last_name||', '||h.first_name) LIKE ? "
            "OR (COALESCE(mi.last_name,h.last_name)||', '||COALESCE(mi.display_first,h.first_name)) LIKE ?)"
        )
        params += [p, p, p, p]
    if asset:
        # Search both asset name and asset_type (so quick filters like "Stock" work)
        clauses.append("(h.asset LIKE ? OR h.asset_type LIKE ?)")
        params += [f"%{asset}%", f"%{asset}%"]
    if chamber:
        clauses.append("h.chamber = ?")
        params.append(chamber)
    if atype:
        atypes = [a.strip() for a in atype.split("|") if a.strip()]
        if atypes:
            none_filter = "__none__" in atypes
            real_atypes = [a for a in atypes if a != "__none__"]
            parts = []
            if real_atypes:
                parts += [f"h.asset_type LIKE ?" for _ in real_atypes]
                params += [f"%{a}%" for a in real_atypes]
            if none_filter:
                parts.append("(h.asset_type IS NULL OR TRIM(h.asset_type) = '')")
            if parts:
                clauses.append("(" + " OR ".join(parts) + ")")
    if party:
        _sp = "CASE LOWER(h.last_name) WHEN 'roberts' THEN 'Republican' WHEN 'thomas' THEN 'Republican' WHEN 'alito' THEN 'Republican' WHEN 'gorsuch' THEN 'Republican' WHEN 'kavanaugh' THEN 'Republican' WHEN 'barrett' THEN 'Republican' WHEN 'sotomayor' THEN 'Democrat' WHEN 'kagan' THEN 'Democrat' WHEN 'jackson' THEN 'Democrat' ELSE NULL END"
        clauses.append("(mi.party LIKE ? OR (h.chamber = 'SCOTUS' AND " + _sp + " LIKE ?))")
        params += [f"%{party}%", f"%{party}%"]
    if state:
        clauses.append("(h.state = ? OR (NULLIF(TRIM(h.state),'') IS NULL AND mi.state = ?))")
        params += [state, state]

    where = "WHERE " + " AND ".join(clauses)

    sort_col = request.args.get("sort_col", "").strip()
    sort_dir = request.args.get("sort_dir", "asc").strip()
    _SORT_COLS = {"chamber","last_name","first_name","state","asset","asset_type","owner","value","income_type"}
    order_by = (f"h.{sort_col} {'DESC' if sort_dir == 'desc' else 'ASC'}" if sort_col in _SORT_COLS
               else "h.chamber, h.last_name, h.first_name")

    conn = get_conn()
    total = conn.execute(
        f"SELECT COUNT(*) FROM holdings h "
        f"LEFT JOIN member_info mi ON h.bioguide = mi.bioguide {where}", params).fetchone()[0]
    rows  = rows_to_list(conn.execute(
        f"SELECT h.id, h.chamber, h.last_name, h.first_name, "
        f"COALESCE(NULLIF(TRIM(h.state),''), mi.state) AS state, "
        f"h.asset, h.asset_type, h.owner, h.value, h.income_type, h.income, "
        f"mi.party, "
        f"COALESCE(mi.display_first, h.first_name) AS display_first "
        f"FROM holdings h "
        f"LEFT JOIN member_info mi ON h.bioguide = mi.bioguide "
        f"{where} ORDER BY {order_by} LIMIT ? OFFSET ?",
        params + [limit, offset]).fetchall())
    conn.close()
    return jsonify({"total": total, "offset": offset, "limit": limit, "rows": rows})

@app.route("/api/sql", methods=["POST"])
def run_sql():
    body = request.get_json(force=True)
    sql  = body.get("sql", "").strip()
    if not sql:
        return jsonify({"error": "No SQL provided"}), 400
    try:
        cols, data = safe_query(sql)
        return jsonify({"columns": cols, "rows": data, "count": len(data)})
    except Exception as e:
        return jsonify({"error": str(e)}), 400

@app.route("/api/export")
def export():
    member  = request.args.get("member", "").strip()
    asset   = request.args.get("asset", "").strip()
    chamber = request.args.get("chamber", "").strip()
    atype   = request.args.get("asset_type", "").strip()
    state   = request.args.get("state", "").strip()

    clauses = ["NOT (asset LIKE '%. %' AND CAST(SUBSTR(asset,1,1) AS INTEGER) BETWEEN 1 AND 9 AND SUBSTR(asset,2,2)='. ')"]
    params = []
    if member:
        clauses.append("(last_name LIKE ? OR first_name LIKE ? OR (last_name||', '||first_name) LIKE ?)")
        p = f"%{member}%"
        params += [p, p, p]
    if asset:
        clauses.append("(asset LIKE ? OR asset_type LIKE ?)")
        params += [f"%{asset}%", f"%{asset}%"]
    if chamber:
        clauses.append("chamber = ?")
        params.append(chamber)
    if atype:
        atypes = [a.strip() for a in atype.split("|") if a.strip()]
        if atypes:
            none_filter = "__none__" in atypes
            real_atypes = [a for a in atypes if a != "__none__"]
            parts = []
            if real_atypes:
                parts += [f"asset_type LIKE ?" for _ in real_atypes]
                params += [f"%{a}%" for a in real_atypes]
            if none_filter:
                parts.append("(asset_type IS NULL OR TRIM(asset_type) = '')")
            if parts:
                clauses.append("(" + " OR ".join(parts) + ")")
    if state:
        clauses.append("state = ?")
        params.append(state)

    where = "WHERE " + " AND ".join(clauses)
    conn = get_conn()
    rows = rows_to_list(conn.execute(
        f"SELECT chamber,last_name,first_name,state,asset,asset_type,owner,value,income_type,income "
        f"FROM holdings {where} ORDER BY chamber,last_name,first_name", params).fetchall())
    conn.close()

    buf = io.StringIO()
    if rows:
        w = csv.DictWriter(buf, fieldnames=rows[0].keys())
        w.writeheader(); w.writerows(rows)
    return Response(buf.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": 'attachment; filename="holdings_export.csv"'})

@app.route("/api/transactions")
def transactions():
    member    = request.args.get("member", "").strip()
    asset     = request.args.get("asset", "").strip()
    atype     = request.args.get("asset_type", "").strip()
    chamber   = request.args.get("chamber", "").strip()
    tx_type   = request.args.get("tx_type", "").strip()
    date_from = request.args.get("date_from", "").strip()
    date_to   = request.args.get("date_to", "").strip()
    party     = request.args.get("party", "").strip()
    state     = request.args.get("state", "").strip()
    limit     = min(int(request.args.get("limit", 500)), 5000)
    offset    = int(request.args.get("offset", 0))

    conn = get_conn()
    # Check if transactions table exists
    has_tx = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='transactions'"
    ).fetchone()
    if not has_tx:
        conn.close()
        return jsonify({"total": 0, "rows": [], "message": "No transactions table — run build_transactions.py"})

    clauses, params = [], []
    if member:
        clauses.append("(t.last_name LIKE ? OR t.first_name LIKE ? OR (t.last_name||', '||t.first_name) LIKE ?)")
        p = f"%{member}%"
        params += [p, p, p]
    if asset:
        clauses.append("(t.asset LIKE ? OR t.ticker LIKE ?)")
        params += [f"%{asset}%", f"%{asset}%"]
    if atype:
        atypes = [a.strip() for a in atype.split("|") if a.strip()]
        if atypes:
            none_filter = "__none__" in atypes
            real_atypes = [a for a in atypes if a != "__none__"]
            parts = []
            if real_atypes:
                parts += ["t.asset_type LIKE ?" for _ in real_atypes]
                params += [f"%{a}%" for a in real_atypes]
            if none_filter:
                parts.append("(t.asset_type IS NULL OR TRIM(t.asset_type) = '')")
            if parts:
                clauses.append("(" + " OR ".join(parts) + ")")
    if chamber:
        clauses.append("t.chamber = ?")
        params.append(chamber)
    if tx_type:
        clauses.append("t.transaction_type LIKE ?")
        params.append(f"%{tx_type}%")
    if date_from:
        clauses.append("t.transaction_date >= ?")
        params.append(date_from)
    if date_to:
        clauses.append("t.transaction_date <= ?")
        params.append(date_to)
    if party:
        _sp = "CASE LOWER(t.last_name) WHEN 'roberts' THEN 'Republican' WHEN 'thomas' THEN 'Republican' WHEN 'alito' THEN 'Republican' WHEN 'gorsuch' THEN 'Republican' WHEN 'kavanaugh' THEN 'Republican' WHEN 'barrett' THEN 'Republican' WHEN 'sotomayor' THEN 'Democrat' WHEN 'kagan' THEN 'Democrat' WHEN 'jackson' THEN 'Democrat' ELSE NULL END"
        clauses.append("(mi.party LIKE ? OR (t.chamber = 'SCOTUS' AND " + _sp + " LIKE ?))")
        params += [f"%{party}%", f"%{party}%"]
    if state:
        clauses.append("(COALESCE(NULLIF(TRIM(t.state),''), mi.state) = ?)")
        params.append(state)

    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""

    tx_sort_col = request.args.get("sort_col", "").strip()
    tx_sort_dir = request.args.get("sort_dir", "desc").strip()
    _TX_SORT = {"chamber","last_name","first_name","state","transaction_date","disclosure_date","asset","asset_type","transaction_type","amount","ticker"}
    tx_order = (f"t.{tx_sort_col} {'DESC' if tx_sort_dir == 'desc' else 'ASC'}" if tx_sort_col in _TX_SORT
               else "t.transaction_date DESC, t.last_name")

    total = conn.execute(
        f"SELECT COUNT(*) FROM transactions t "
        f"LEFT JOIN member_info mi ON t.bioguide = mi.bioguide {where}", params).fetchone()[0]
    rows  = rows_to_list(conn.execute(
        f"SELECT t.id, t.chamber, t.last_name, t.first_name, "
        f"COALESCE(NULLIF(TRIM(t.state),''), mi.state) AS state, "
        f"t.transaction_date, t.disclosure_date, t.asset, t.asset_type, "
        f"t.transaction_type, t.amount, t.ticker, t.comment, "
        f"mi.party, "
        f"COALESCE(mi.display_first, t.first_name) AS display_first "
        f"FROM transactions t "
        f"LEFT JOIN member_info mi ON t.bioguide = mi.bioguide "
        f"{where} ORDER BY {tx_order} LIMIT ? OFFSET ?",
        params + [limit, offset]).fetchall())
    conn.close()
    return jsonify({"total": total, "offset": offset, "limit": limit, "rows": rows})


# ── NL query (requires ANTHROPIC_API_KEY) ────────────────────────────────────

DB_SCHEMA = """
Tables in holdings.db:

holdings — one row per financial holding from annual disclosures
  id, chamber (House/Senate/SCOTUS/Executive), last_name, first_name, state,
  asset (name), asset_type (Stock/Mutual Fund/Real Estate/etc),
  owner (SP=spouse, JT=joint, DC=dependent child, or blank=self),
  value (range string, e.g. "$15,001 - $50,000"),
  income_type (Dividends/Capital Gains/etc), income (range), source_file

transactions — one row per STOCK Act periodic transaction report filing
  id, chamber, last_name, first_name, state, district,
  transaction_date (YYYY-MM-DD), disclosure_date,
  asset (name), asset_type, transaction_type (Purchase/Sale (Full)/Sale (Partial)/Exchange),
  amount (range string), ticker (stock symbol if available), comment, source_file

committees — one row per committee or subcommittee
  thomas_id (e.g. 'HSSY' = House Science), name, chamber (house/senate/joint),
  parent_id (NULL for full committees, parent thomas_id for subcommittees)

committee_members — one row per member-committee assignment
  thomas_id, bioguide, last_name, first_name, state, district,
  party (majority/minority), rank (1=chair), title (Chairman/Ranking Member/etc)

member_info — one row per current legislator with party affiliation
  bioguide, last_name, first_name, state, party (Democrat/Republican/Independent/etc)
  Join: LEFT JOIN member_info mi ON LOWER(h.last_name)=LOWER(mi.last_name) AND LOWER(h.first_name)=LOWER(mi.first_name)

Join pattern for committee questions:
  SELECT h.* FROM holdings h
  JOIN committee_members cm ON LOWER(h.last_name)=LOWER(cm.last_name) AND LOWER(h.first_name)=LOWER(cm.first_name)
  JOIN committees c ON cm.thomas_id=c.thomas_id
  WHERE c.name LIKE '%Science%' AND c.parent_id IS NULL

Notes:
- "social media companies" includes: Meta, Facebook, Twitter/X, Snap, Pinterest, TikTok, LinkedIn, Reddit, YouTube
- "tech companies" includes: Apple, Microsoft, Google/Alphabet, Amazon, NVIDIA, Meta, etc.
- "energy companies" includes: Exxon, Chevron, Shell, BP, ConocoPhillips, Halliburton, etc.
- Value ranges map approx: "$1,001 - $15,000", "$15,001 - $50,000", "$50,001 - $100,000",
  "$100,001 - $250,000", "$250,001 - $500,000", "$500,001 - $1,000,000", "$1,000,001 - $5,000,000", "$5,000,001+"
- All queries must be SELECT only; no INSERT/UPDATE/DELETE
"""

NL_SYSTEM = f"""You are a SQL expert analyzing US congressional financial disclosures.
Convert natural-language questions into SQLite queries against this schema:

{DB_SCHEMA}

Return a JSON object with exactly these keys:
  "sql": the SELECT query (valid SQLite, no backtick escaping needed)
  "explanation": 1-2 sentence plain-English description of what the query does
  "caveats": any important limitations or missing data to note (or null)

Rules:
- Use LIKE '%keyword%' for fuzzy matching on asset names and committee names
- For committee membership questions, join committee_members and committees as shown in schema
- Keep queries under 2000 rows (add LIMIT if none given)
- For "inferences" or "patterns" questions, summarize the most revealing holdings/transactions
- If the question is unanswerable with this data, return sql: null and explain why
"""

def ask_claude(question):
    """Send question to Claude API, return (sql, explanation, caveats, answer_text)."""
    try:
        import anthropic
    except ImportError:
        return None, None, None, "anthropic package not installed — run: pip install anthropic"

    api_key = os.environ.get('ANTHROPIC_API_KEY', '')
    if not api_key:
        return None, None, None, "ANTHROPIC_API_KEY environment variable not set"

    client = anthropic.Anthropic(api_key=api_key)

    # Step 1: translate question → SQL
    msg = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=1024,
        system=NL_SYSTEM,
        messages=[{"role": "user", "content": question}]
    )
    raw = msg.content[0].text.strip()

    # Parse JSON response (handle markdown code blocks)
    raw_json = re.sub(r'^```(?:json)?\s*', '', raw, flags=re.M)
    raw_json = re.sub(r'\s*```$', '', raw_json, flags=re.M).strip()
    try:
        parsed = json.loads(raw_json)
    except Exception:
        # Try to extract JSON from the response
        m = re.search(r'\{.*\}', raw, re.DOTALL)
        if m:
            try: parsed = json.loads(m.group(0))
            except: parsed = {}
        else:
            parsed = {}

    sql         = parsed.get('sql')
    explanation = parsed.get('explanation', '')
    caveats     = parsed.get('caveats')

    if not sql:
        return None, explanation, caveats, parsed.get('explanation', 'Could not generate a SQL query for that question.')

    # Step 2: run the SQL
    try:
        cols, data = safe_query(sql)
    except Exception as e:
        return sql, explanation, caveats, f"SQL error: {e}\n\nGenerated SQL:\n{sql}"

    # Step 3: generate natural-language answer
    data_preview = data[:50]  # send at most 50 rows to the summarizer
    summary_prompt = f"""Question: {question}

SQL run: {sql}

Results ({len(data)} rows total, showing up to 50):
{json.dumps(data_preview, indent=2)}

Write a clear, concise natural-language answer to the original question based on these results.
Be specific — name members, assets, amounts. If results are empty, say so and explain why.
Keep it under 400 words. Don't repeat the SQL."""

    msg2 = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=600,
        messages=[{"role": "user", "content": summary_prompt}]
    )
    answer = msg2.content[0].text.strip()

    return sql, explanation, caveats, answer


@app.route("/api/ask", methods=["POST"])
def ask():
    body     = request.get_json(force=True)
    question = body.get("question", "").strip()
    if not question:
        return jsonify({"error": "No question provided"}), 400

    sql, explanation, caveats, answer = ask_claude(question)

    # Also run the SQL to return raw rows for the UI table
    rows = []
    cols = []
    if sql:
        try:
            cols, rows = safe_query(sql)
            rows = rows[:200]  # cap for UI rendering
        except Exception:
            pass

    return jsonify({
        "question":    question,
        "sql":         sql,
        "explanation": explanation,
        "caveats":     caveats,
        "answer":      answer,
        "columns":     cols,
        "rows":        rows,
        "total_rows":  len(rows),
    })


@app.route("/api/ask_examples")
def ask_examples():
    return jsonify([
        "What financial interests do House members have in social media companies?",
        "Which senators have the most stock holdings?",
        "What do members of the House Financial Services Committee own?",
        "Who bought or sold NVIDIA stock in 2024 or 2025?",
        "What energy company stocks do members of Congress hold?",
        "Show holdings in defense contractors like Raytheon, Lockheed, or Boeing",
        "What are Nancy Pelosi's reported holdings?",
        "Which members of the Armed Services Committee have defense stock holdings?",
        "What are the largest stock holdings reported by senators?",
        "Which members traded pharmaceutical stocks in 2024?",
        "Show all SCOTUS justice holdings",
        "What tech stocks do members of the House Science Committee hold?",
    ])


@app.route("/api/members")
def members():
    q       = request.args.get("q", "").strip()
    chamber = request.args.get("chamber", "").strip()
    state   = request.args.get("state", "").strip()
    party   = request.args.get("party", "").strip()
    conn = get_conn()
    clauses, params = [], []
    if q:
        clauses.append("(h.last_name LIKE ? OR h.first_name LIKE ?)")
        params += [f"%{q}%", f"%{q}%"]
    if chamber:
        clauses.append("h.chamber = ?")
        params.append(chamber)
    if state:
        clauses.append("(h.state = ? OR (NULLIF(TRIM(h.state),'') IS NULL AND mi.state = ?))")
        params += [state, state]
    if party:
        clauses.append("mi.party LIKE ?")
        params.append(f"%{party}%")
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    sql = (
        f"SELECT DISTINCT "
        f"COALESCE(mi.last_name, h.last_name) AS last_name, "
        f"COALESCE(mi.display_first, h.first_name) AS first_name, "
        f"h.chamber, "
        f"COALESCE(NULLIF(TRIM(h.state),''), mi.state) AS state, "
        f"mi.party "
        f"FROM holdings h "
        f"LEFT JOIN member_info mi ON h.bioguide = mi.bioguide "
        f"{where} ORDER BY h.last_name LIMIT 600"
    )
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return jsonify(rows_to_list(rows))

@app.route("/api/asset_types")
def asset_types():
    conn = get_conn()
    rows = conn.execute(
        "SELECT DISTINCT asset_type FROM holdings WHERE asset_type != '' ORDER BY asset_type").fetchall()
    conn.close()
    return jsonify([r[0] for r in rows])

# ── Serve the UI ──────────────────────────────────────────────────────────────

HTML = open(os.path.join(os.path.dirname(__file__), "template.html"), encoding="utf-8").read()

@app.route("/")
def index():
    _ensure_member_info()
    resp = make_response(HTML)
    resp.headers["Cache-Control"] = "no-store"
    return resp

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5050))
    print(f"Starting server on http://localhost:{port}")
    app.run(host="0.0.0.0", port=port, debug=False)
