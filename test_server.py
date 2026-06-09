"""
Regression tests for query_server.py
Run:  pytest test_server.py -v
  or: python test_server.py

Tests verify:
  - SQL syntax is valid for all three API endpoints
  - Party / state subqueries match across name variants:
      exact name, nickname alias, HON. prefix, compound first name,
      reversed-initial prefix ("H. Morgan" → "Morgan")
  - State fallback (NULL h.state → member_info.state)
  - display_first returned in /api/search and /api/transactions
  - /api/members returns canonical display names
  - All three endpoints return HTTP 200 and expected JSON keys
  - Party / chamber / state filters work on /api/members
"""

import os, sqlite3, json, tempfile, pytest

# ── Point at a fresh test DB BEFORE importing the module ─────────────────────
_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp.close()
TEST_DB = _tmp.name
os.environ["HOLDINGS_DB"] = TEST_DB

import query_server  # noqa: E402  (DB_PATH already set above)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def fresh_conn():
    conn = sqlite3.connect(TEST_DB)
    conn.row_factory = sqlite3.Row
    return conn


def reset_db():
    """Wipe and re-create all tables with representative test data."""
    conn = sqlite3.connect(TEST_DB)
    conn.executescript("""
        DROP TABLE IF EXISTS holdings;
        DROP TABLE IF EXISTS transactions;
        DROP TABLE IF EXISTS member_info;
        DROP TABLE IF EXISTS name_aliases;
        DROP TABLE IF EXISTS committees;
        DROP TABLE IF EXISTS committee_members;

        CREATE TABLE holdings (
            id INTEGER PRIMARY KEY,
            chamber TEXT, last_name TEXT, first_name TEXT, state TEXT,
            asset TEXT, asset_type TEXT, owner TEXT,
            value TEXT, income_type TEXT, income TEXT, source_file TEXT,
            bioguide TEXT
        );

        CREATE TABLE transactions (
            id INTEGER PRIMARY KEY,
            chamber TEXT, last_name TEXT, first_name TEXT, state TEXT, district TEXT,
            transaction_date TEXT, disclosure_date TEXT,
            asset TEXT, asset_type TEXT, transaction_type TEXT,
            amount TEXT, ticker TEXT, comment TEXT, source_file TEXT,
            bioguide TEXT
        );

        CREATE TABLE member_info (
            bioguide      TEXT PRIMARY KEY,
            last_name     TEXT,
            first_name    TEXT,
            display_first TEXT,
            state         TEXT,
            party         TEXT,
            is_current    INTEGER DEFAULT 0
        );

        CREATE TABLE name_aliases (
            bioguide  TEXT,
            last_name TEXT,
            first_name TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_aliases ON name_aliases (last_name, first_name);
    """)

    # ── member_info rows ──────────────────────────────────────────────────────
    # Each row: (bioguide, last, first, display_first, state, party, is_current)
    members = [
        ("S001",  "Smith",     "John",    "John",      "CA", "Democrat",   1),
        ("B001",  "Budd",      "Theodore","Ted",        "NC", "Republican", 1),
        ("C001",  "Cruz",      "Rafael",  "Ted",        "TX", "Republican", 1),
        ("M001",  "McConnell", "Addison", "Mitch",      "KY", "Republican", 1),
        ("J001",  "Jones",     "Mary",    "Mary",       "WA", "Democrat",   1),
        ("O001",  "Morgan",    "Thomas",  "Thomas",     "MS", "Republican", 1),
        ("W001",  "Warren",    "Elizabeth","Elizabeth", "MA", "Democrat",   1),
    ]
    conn.executemany("INSERT INTO member_info VALUES (?,?,?,?,?,?,?)", members)

    # ── name_aliases rows ─────────────────────────────────────────────────────
    aliases = [
        # Smith: only legal name
        ("S001", "Smith",     "John"),
        # Budd: legal + nickname
        ("B001", "Budd",      "Theodore"),
        ("B001", "Budd",      "Ted"),
        # Cruz: legal + nickname
        ("C001", "Cruz",      "Rafael"),
        ("C001", "Cruz",      "Ted"),
        # McConnell: legal + nickname
        ("M001", "McConnell", "Addison"),
        ("M001", "McConnell", "Mitch"),
        # Jones: legal name only
        ("J001", "Jones",     "Mary"),
        # Morgan: legal name only
        ("O001", "Morgan",    "Thomas"),
        # Warren: legal name only
        ("W001", "Warren",    "Elizabeth"),
    ]
    conn.executemany("INSERT INTO name_aliases VALUES (?,?,?)", aliases)

    # ── holdings rows ─────────────────────────────────────────────────────────
    # Columns: id, chamber, last_name, first_name, state,
    #          asset, asset_type, owner, value, income_type, income, source_file, bioguide
    # bioguide is now resolved at import time; tests verify the JOIN works correctly.
    holdings = [
        # 1: bioguide set, state in holdings
        (1, "Senate","Smith",     "John",          "CA",
         "Apple Inc","Stock","","$50K-$100K","Dividends","$1K","f1.pdf", "S001"),
        # 2: bioguide set, state empty → falls back to mi.state
        (2, "Senate","BUDD",      "THEODORE",      "",
         "Tesla Inc","Stock","","$50K-$100K","Dividends","$1K","f2.pdf", "B001"),
        # 3: bioguide set (Cruz with alias 'Ted' in holdings)
        (3, "Senate","Cruz",      "Ted",           "",
         "Amazon","Stock","","$50K-$100K","Dividends","$1K","f3.pdf", "C001"),
        # 4: bioguide set (McConnell)
        (4, "Senate","McConnell", "Mitch",         "",
         "Vanguard","Mutual Fund","","$50K-$100K","None","$0","f4.pdf", "M001"),
        # 5: bioguide set (Jones — HON. prefix handled at import time)
        (5, "House", "Jones",     "Hon.. Mary Beth","",
         "Microsoft","Stock","","$15K-$50K","None","$0","f5.pdf", "J001"),
        # 6: bioguide set (Morgan)
        (6, "House", "Morgan",    "H. Thomas",     "",
         "ExxonMobil","Stock","","$15K-$50K","None","$0","f6.pdf", "O001"),
        # 7: bioguide set, state already in holdings
        (7, "Senate","Warren",    "Elizabeth",     "MA",
         "Index Fund","Mutual Fund","","$50K-$100K","None","$0","f7.pdf", "W001"),
        # 8: bioguide set (Cruz under legal name Rafael)
        (8, "Senate","Cruz",      "Rafael",        "",
         "Alphabet","Stock","","$15K-$50K","None","$0","f8.pdf", "C001"),
        # 9: no bioguide (SCOTUS/unresolved) — party/state should be NULL
        (9, "SCOTUS","Thomas",    "Clarence",      "",
         "Real Estate","Real Estate","","$500K+","Rent","$10K","f9.pdf", None),
    ]
    conn.executemany(
        "INSERT INTO holdings VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", holdings)

    # ── transactions rows ─────────────────────────────────────────────────────
    # Columns: id, chamber, last_name, first_name, state, district,
    #          tx_date, disc_date, asset, asset_type, tx_type, amount, ticker, comment, source_file, bioguide
    transactions = [
        (1,"Senate","Smith","John","CA","","2024-01-15","2024-01-20",
         "Apple Inc","Stock","Purchase","$1K-$15K","AAPL","","t1.pdf","S001"),
        (2,"Senate","Cruz","Ted","","","2024-02-10","2024-02-15",
         "Amazon","Stock","Sale (Full)","$50K-$100K","AMZN","","t2.pdf","C001"),
        (3,"House","Jones","Hon.. Mary Beth","","","2024-03-01","2024-03-05",
         "Microsoft","Stock","Purchase","$1K-$15K","MSFT","","t3.pdf","J001"),
    ]
    conn.executemany(
        "INSERT INTO transactions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        transactions)

    conn.commit()
    conn.close()


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module", autouse=True)
def setup():
    """Populate test DB once for all tests in this module."""
    reset_db()
    yield


@pytest.fixture
def client():
    query_server.app.config["TESTING"] = True
    with query_server.app.test_client() as c:
        yield c


# ─────────────────────────────────────────────────────────────────────────────
# SQL unit tests — run directly against the test DB
# ─────────────────────────────────────────────────────────────────────────────

class TestPartyMatching:
    """Verify party lookup via bioguide JOIN."""

    def _party(self, last, first):
        sql = (
            "SELECT mi.party "
            "FROM holdings h "
            "LEFT JOIN member_info mi ON h.bioguide = mi.bioguide "
            "WHERE LOWER(h.last_name)=? AND LOWER(h.first_name)=?"
        )
        conn = fresh_conn()
        row = conn.execute(sql, (last.lower(), first.lower())).fetchone()
        conn.close()
        return row["party"] if row else None

    def test_exact_match(self):
        assert self._party("Smith", "John") == "Democrat"

    def test_bioguide_resolves_nickname_ted_cruz(self):
        """Holdings say 'Ted' — bioguide was resolved at import time → Republican."""
        assert self._party("Cruz", "Ted") == "Republican"

    def test_bioguide_resolves_mitch(self):
        assert self._party("McConnell", "Mitch") == "Republican"

    def test_legal_name_bioguide_set(self):
        """Cruz stored under legal name Rafael — same bioguide → Republican."""
        assert self._party("Cruz", "Rafael") == "Republican"

    def test_all_caps_with_bioguide(self):
        """THEODORE in holdings, bioguide set → Republican."""
        assert self._party("BUDD", "THEODORE") == "Republican"

    def test_hon_prefix_bioguide_set(self):
        """'Hon.. Mary Beth' — bioguide resolved at import time → Democrat."""
        assert self._party("Jones", "Hon.. Mary Beth") == "Democrat"

    def test_suffix_first_bioguide_set(self):
        """'H. Thomas' — bioguide resolved at import time → Republican."""
        assert self._party("Morgan", "H. Thomas") == "Republican"

    def test_no_bioguide_returns_none(self):
        """SCOTUS row with no bioguide → party is NULL."""
        assert self._party("Thomas", "Clarence") is None


class TestStateMatching:
    """Verify state COALESCE fallback via bioguide JOIN."""

    def _state(self, last, first):
        sql = (
            "SELECT COALESCE(NULLIF(TRIM(h.state),''), mi.state) AS state "
            "FROM holdings h "
            "LEFT JOIN member_info mi ON h.bioguide = mi.bioguide "
            "WHERE LOWER(h.last_name)=? AND LOWER(h.first_name)=?"
        )
        conn = fresh_conn()
        row = conn.execute(sql, (last.lower(), first.lower())).fetchone()
        conn.close()
        return row["state"] if row else None

    def test_state_present_preserved(self):
        """h.state='CA' — should NOT be overwritten."""
        assert self._state("Smith", "John") == "CA"

    def test_state_null_falls_back(self):
        """h.state='' for BUDD — fallback to member_info.state='NC'."""
        assert self._state("BUDD", "THEODORE") == "NC"

    def test_bioguide_state_fallback(self):
        """Cruz with empty h.state — bioguide JOIN gives 'TX'."""
        assert self._state("Cruz", "Ted") == "TX"

    def test_state_with_hon_prefix(self):
        """Jones with HON. prefix has bioguide set → state='WA'."""
        assert self._state("Jones", "Hon.. Mary Beth") == "WA"


class TestDisplayNames:
    """Verify display_first via bioguide JOIN."""

    def _display_first(self, last, first):
        sql = (
            "SELECT COALESCE(mi.display_first, h.first_name) AS display_first "
            "FROM holdings h "
            "LEFT JOIN member_info mi ON h.bioguide = mi.bioguide "
            "WHERE LOWER(h.last_name)=? AND LOWER(h.first_name)=?"
        )
        conn = fresh_conn()
        row = conn.execute(sql, (last.lower(), first.lower())).fetchone()
        conn.close()
        return row["display_first"] if row else None

    def test_display_first_nickname(self):
        """Budd stored as THEODORE — bioguide → display_first='Ted'."""
        assert self._display_first("BUDD", "THEODORE") == "Ted"

    def test_display_first_cruz(self):
        assert self._display_first("Cruz", "Rafael") == "Ted"

    def test_display_first_mitch(self):
        assert self._display_first("McConnell", "Mitch") == "Mitch"

    def test_display_first_plain(self):
        assert self._display_first("Smith", "John") == "John"

    def test_no_bioguide_falls_back_to_holdings_name(self):
        """No bioguide (SCOTUS) → display_first falls back to h.first_name."""
        result = self._display_first("Thomas", "Clarence")
        assert result == "Clarence"


class TestLearnAliasesFromHoldings:
    """
    Verify _ensure_member_info() learns filing-name aliases from holdings
    for unique-last-name members.  This covers the real-world case where the
    legislators YAML stores the common/nickname as `first` (e.g. 'Ted' for
    Cruz) but the Senate financial disclosure uses the legal name ('Rafael').
    """

    def test_filing_name_added_as_alias(self):
        """
        DB has Cruz with only 'Ted' alias; holdings row uses 'Rafael'.
        After _ensure_member_info(), 'Rafael' must appear in name_aliases.
        """
        import tempfile, os as _os
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        try:
            conn = sqlite3.connect(tmp.name)
            conn.executescript("""
                CREATE TABLE member_info (
                    bioguide TEXT PRIMARY KEY, last_name TEXT, first_name TEXT,
                    display_first TEXT, state TEXT, party TEXT, is_current INTEGER DEFAULT 0
                );
                CREATE TABLE name_aliases (
                    bioguide TEXT, last_name TEXT, first_name TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_aliases ON name_aliases (last_name, first_name);
                CREATE TABLE holdings (
                    id INTEGER PRIMARY KEY, chamber TEXT, last_name TEXT, first_name TEXT,
                    state TEXT, asset TEXT, asset_type TEXT, owner TEXT,
                    value TEXT, income_type TEXT, income TEXT, source_file TEXT, bioguide TEXT
                );
                INSERT INTO member_info VALUES
                    ('C001098','Cruz','Ted','Ted','TX','Republican',1);
                INSERT INTO name_aliases VALUES ('C001098','Cruz','Ted');
                INSERT INTO holdings VALUES
                    (1,'Senate','Cruz','Rafael','','Alphabet','Stock','',
                     '$15K-$50K','None','$0','f1.pdf',NULL);
            """)
            conn.commit()
            conn.close()

            old_db = query_server.DB_PATH
            query_server.DB_PATH = tmp.name
            try:
                query_server._ensure_member_info()
                conn2 = sqlite3.connect(tmp.name)
                aliases = [r[0] for r in conn2.execute(
                    "SELECT first_name FROM name_aliases WHERE LOWER(last_name)='cruz'"
                ).fetchall()]
                conn2.close()
                assert 'Rafael' in aliases, \
                    f"'Rafael' not learned from holdings; aliases found: {aliases}"
            finally:
                query_server.DB_PATH = old_db
        finally:
            _os.unlink(tmp.name)

    def test_duplicate_last_name_not_auto_aliased(self):
        """
        Two members share the last name 'Brown'; holdings has a 'Brown/Charlie' row
        that doesn't match either. The uniqueness guard must prevent a spurious alias.
        """
        import tempfile, os as _os
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        try:
            conn = sqlite3.connect(tmp.name)
            conn.executescript("""
                CREATE TABLE member_info (
                    bioguide TEXT PRIMARY KEY, last_name TEXT, first_name TEXT,
                    display_first TEXT, state TEXT, party TEXT, is_current INTEGER DEFAULT 0
                );
                CREATE TABLE name_aliases (
                    bioguide TEXT, last_name TEXT, first_name TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_aliases ON name_aliases (last_name, first_name);
                CREATE TABLE holdings (
                    id INTEGER PRIMARY KEY, chamber TEXT, last_name TEXT, first_name TEXT,
                    state TEXT, asset TEXT, asset_type TEXT, owner TEXT,
                    value TEXT, income_type TEXT, income TEXT, source_file TEXT, bioguide TEXT
                );
                INSERT INTO member_info VALUES
                    ('B001','Brown','Sherrod','Sherrod','OH','Democrat',1),
                    ('B002','Brown','Patrick','Patrick','CA','Democrat',1);
                INSERT INTO name_aliases VALUES
                    ('B001','Brown','Sherrod'),
                    ('B002','Brown','Patrick');
                INSERT INTO holdings VALUES
                    (1,'Senate','Brown','Charlie','','Bond Fund','Mutual Fund','',
                     '$1K-$15K','None','$0','f1.pdf',NULL);
            """)
            conn.commit()
            conn.close()

            old_db = query_server.DB_PATH
            query_server.DB_PATH = tmp.name
            try:
                query_server._ensure_member_info()
                conn2 = sqlite3.connect(tmp.name)
                aliases = [r[0] for r in conn2.execute(
                    "SELECT first_name FROM name_aliases WHERE LOWER(last_name)='brown'"
                ).fetchall()]
                conn2.close()
                assert 'Charlie' not in aliases, \
                    f"'Charlie' should NOT be auto-aliased for ambiguous last name; got: {aliases}"
            finally:
                query_server.DB_PATH = old_db
        finally:
            _os.unlink(tmp.name)


    def test_scotus_filing_not_aliased_to_legislator(self):
        """
        A SCOTUS holdings row (chamber='SCOTUS') must NOT create an alias on a
        same-last-name legislator.  Real case: Amy Coney Barrett → Tom Barrett (R-MI).
        """
        import tempfile, os as _os
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        try:
            conn = sqlite3.connect(tmp.name)
            conn.executescript("""
                CREATE TABLE member_info (
                    bioguide TEXT PRIMARY KEY, last_name TEXT, first_name TEXT,
                    display_first TEXT, state TEXT, party TEXT, is_current INTEGER DEFAULT 0
                );
                CREATE TABLE name_aliases (
                    bioguide TEXT, last_name TEXT, first_name TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_aliases ON name_aliases (last_name, first_name);
                CREATE TABLE holdings (
                    id INTEGER PRIMARY KEY, chamber TEXT, last_name TEXT, first_name TEXT,
                    state TEXT, asset TEXT, asset_type TEXT, owner TEXT,
                    value TEXT, income_type TEXT, income TEXT, source_file TEXT, bioguide TEXT
                );
                INSERT INTO member_info VALUES
                    ('B000575','Barrett','Tom','Tom','MI','Republican',1);
                INSERT INTO name_aliases VALUES ('B000575','Barrett','Tom');
                INSERT INTO holdings VALUES
                    (1,'SCOTUS','Barrett','Amy','','Vanguard Total Market','Mutual Fund','',
                     '$100K-$250K','Dividends','$2,500','scotus2024.pdf',NULL);
            """)
            conn.commit()
            conn.close()

            old_db = query_server.DB_PATH
            query_server.DB_PATH = tmp.name
            try:
                query_server._ensure_member_info()
                conn2 = sqlite3.connect(tmp.name)
                aliases = [r[0] for r in conn2.execute(
                    "SELECT first_name FROM name_aliases WHERE LOWER(last_name)='barrett'"
                ).fetchall()]
                conn2.close()
                assert 'Amy' not in aliases, \
                    f"'Amy' (SCOTUS) must NOT be learned as alias for Tom Barrett; got: {aliases}"
            finally:
                query_server.DB_PATH = old_db
        finally:
            _os.unlink(tmp.name)


# ─────────────────────────────────────────────────────────────────────────────
# Flask endpoint tests
# ─────────────────────────────────────────────────────────────────────────────

class TestSearchEndpoint:
    def test_returns_200(self, client):
        r = client.get("/api/search")
        assert r.status_code == 200

    def test_response_has_required_keys(self, client):
        data = client.get("/api/search").get_json()
        assert "total" in data and "rows" in data

    def test_rows_have_party_and_state(self, client):
        rows = client.get("/api/search").get_json()["rows"]
        assert len(rows) > 0
        for row in rows:
            assert "party" in row
            assert "state"  in row

    def test_rows_have_display_first(self, client):
        rows = client.get("/api/search").get_json()["rows"]
        # display_first key must be present (may be None for unmatched rows)
        assert all("display_first" in r for r in rows)

    def test_known_party_returned(self, client):
        rows = client.get("/api/search?member=Smith").get_json()["rows"]
        smith = [r for r in rows if r["last_name"] == "Smith"]
        assert any(r["party"] == "Democrat" for r in smith), \
            f"Expected Democrat for Smith, got: {[r['party'] for r in smith]}"

    def test_state_fallback_active(self, client):
        """BUDD has empty h.state — should get NC from member_info."""
        rows = client.get("/api/search?member=BUDD").get_json()["rows"]
        budd = [r for r in rows if r["last_name"].upper() == "BUDD"]
        assert budd, "No BUDD row returned"
        assert budd[0]["state"] == "NC", f"Expected NC, got {budd[0]['state']}"

    def test_nickname_party_match(self, client):
        """Cruz stored as 'Ted' in holdings — should still resolve to Republican."""
        rows = client.get("/api/search?member=Cruz").get_json()["rows"]
        assert rows, "No Cruz rows returned"
        assert all(r["party"] == "Republican" for r in rows), \
            f"Party mismatch: {[r['party'] for r in rows]}"

    def test_empty_results_not_error(self, client):
        data = client.get("/api/search?member=ZZZnonexistent").get_json()
        assert data["total"] == 0
        assert data["rows"] == []

    def test_sort_param_accepted(self, client):
        r = client.get("/api/search?sort=last_name&dir=asc")
        assert r.status_code == 200

    def test_pagination(self, client):
        r1 = client.get("/api/search?limit=2&offset=0").get_json()
        r2 = client.get("/api/search?limit=2&offset=2").get_json()
        assert r1["rows"] != r2["rows"] or r1["total"] <= 2


class TestTransactionsEndpoint:
    def test_returns_200(self, client):
        assert client.get("/api/transactions").status_code == 200

    def test_rows_have_party_state_display(self, client):
        data = client.get("/api/transactions").get_json()
        rows = data["rows"]
        assert len(rows) > 0
        for r in rows:
            assert "party"         in r
            assert "state"         in r
            assert "display_first" in r

    def test_hon_prefix_party_resolved(self, client):
        """Jones filed as 'Hon.. Mary Beth' — should resolve to Democrat."""
        rows = client.get("/api/transactions?member=Jones").get_json()["rows"]
        assert rows, "No Jones tx rows"
        assert rows[0]["party"] == "Democrat", \
            f"Expected Democrat, got {rows[0]['party']}"

    def test_date_filter_accepted(self, client):
        r = client.get("/api/transactions?date_from=2024-01-01&date_to=2024-12-31")
        assert r.status_code == 200


class TestMembersEndpoint:
    def test_returns_200(self, client):
        assert client.get("/api/members").status_code == 200

    def test_rows_have_required_keys(self, client):
        rows = client.get("/api/members").get_json()
        assert len(rows) > 0
        for r in rows:
            assert "last_name"  in r
            assert "first_name" in r
            assert "chamber"    in r
            assert "state"      in r
            assert "party"      in r

    def test_display_names_canonical(self, client):
        """BUDD+THEODORE in holdings → members endpoint returns 'Budd, Ted'."""
        rows = client.get("/api/members").get_json()
        budd = [r for r in rows if r["last_name"].lower() == "budd"]
        assert budd, "No Budd in members"
        assert budd[0]["first_name"] == "Ted", \
            f"Expected Ted, got {budd[0]['first_name']}"

    def test_cruz_shows_ted(self, client):
        rows = client.get("/api/members").get_json()
        cruz = [r for r in rows if r["last_name"].lower() == "cruz"]
        assert cruz, "No Cruz in members"
        assert cruz[0]["first_name"] == "Ted", \
            f"Expected Ted, got {cruz[0]['first_name']}"

    def test_chamber_filter(self, client):
        rows = client.get("/api/members?chamber=Senate").get_json()
        assert all(r["chamber"] == "Senate" for r in rows), \
            "Chamber filter returned non-Senate rows"

    def test_party_filter_democrat(self, client):
        rows = client.get("/api/members?party=Democrat").get_json()
        assert all("Democrat" in (r["party"] or "") for r in rows), \
            f"Party filter leakage: {[r['party'] for r in rows]}"

    def test_party_filter_republican(self, client):
        rows = client.get("/api/members?party=Republican").get_json()
        assert len(rows) > 0
        assert all("Republican" in (r["party"] or "") for r in rows)

    def test_state_populated_from_fallback(self, client):
        """Members with empty h.state get state from member_info."""
        rows = client.get("/api/members").get_json()
        budd = next((r for r in rows if r["last_name"].lower() == "budd"), None)
        assert budd is not None
        assert budd["state"] == "NC", f"Expected NC, got {budd['state']}"

    def test_empty_query_returns_all(self, client):
        rows = client.get("/api/members").get_json()
        # All 7 distinct members in test data should appear
        assert len(rows) >= 5


class TestFixNullBioguideTransactions:
    """
    _ensure_member_info() must fix NULL bioguides in the transactions table,
    not just holdings, so party/state show for PTR filers.
    """

    def _make_db(self, script):
        import tempfile, os as _os
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        conn = sqlite3.connect(tmp.name)
        conn.executescript(script)
        conn.commit()
        conn.close()
        return tmp.name

    def test_null_bioguide_fixed_in_transactions(self):
        """
        Transaction row for McConnell has bioguide=NULL but 'Mitch' alias exists.
        After _ensure_member_info(), the bioguide must be updated.
        """
        db_path = self._make_db("""
            CREATE TABLE member_info (
                bioguide TEXT PRIMARY KEY, last_name TEXT, first_name TEXT,
                display_first TEXT, state TEXT, party TEXT, is_current INTEGER DEFAULT 0
            );
            CREATE TABLE name_aliases (
                bioguide TEXT, last_name TEXT, first_name TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_aliases ON name_aliases (last_name, first_name);
            CREATE TABLE holdings (
                id INTEGER PRIMARY KEY, chamber TEXT, last_name TEXT, first_name TEXT,
                state TEXT, asset TEXT, asset_type TEXT, owner TEXT,
                value TEXT, income_type TEXT, income TEXT, source_file TEXT, bioguide TEXT
            );
            CREATE TABLE transactions (
                id INTEGER PRIMARY KEY, chamber TEXT, last_name TEXT, first_name TEXT,
                state TEXT, district TEXT, transaction_date TEXT, disclosure_date TEXT,
                asset TEXT, asset_type TEXT, transaction_type TEXT,
                amount TEXT, ticker TEXT, comment TEXT, source_file TEXT, bioguide TEXT
            );
            INSERT INTO member_info VALUES
                ('M001','McConnell','Addison','Mitch','KY','Republican',1);
            INSERT INTO name_aliases VALUES
                ('M001','McConnell','Addison'),
                ('M001','McConnell','Mitch');
            INSERT INTO transactions VALUES
                (1,'Senate','McConnell','Mitch','','','2024-01-10','2024-01-15',
                 'Vanguard','Mutual Fund','Purchase','$1K-$15K','VTI','','t1.pdf',NULL);
        """)
        import os as _os
        old_db = query_server.DB_PATH
        query_server.DB_PATH = db_path
        try:
            query_server._ensure_member_info()
            conn = sqlite3.connect(db_path)
            row = conn.execute(
                "SELECT bioguide FROM transactions WHERE last_name='McConnell'"
            ).fetchone()
            conn.close()
            assert row is not None
            assert row[0] == 'M001', \
                f"McConnell transaction bioguide not fixed; got: {row[0]}"
        finally:
            query_server.DB_PATH = old_db
            _os.unlink(db_path)

    def test_transaction_party_visible_after_bioguide_fix(self):
        """
        After _ensure_member_info() fixes bioguide, the transactions endpoint
        should return party='Republican' for McConnell's transaction.
        """
        db_path = self._make_db("""
            CREATE TABLE member_info (
                bioguide TEXT PRIMARY KEY, last_name TEXT, first_name TEXT,
                display_first TEXT, state TEXT, party TEXT, is_current INTEGER DEFAULT 0
            );
            CREATE TABLE name_aliases (
                bioguide TEXT, last_name TEXT, first_name TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_aliases ON name_aliases (last_name, first_name);
            CREATE TABLE holdings (
                id INTEGER PRIMARY KEY, chamber TEXT, last_name TEXT, first_name TEXT,
                state TEXT, asset TEXT, asset_type TEXT, owner TEXT,
                value TEXT, income_type TEXT, income TEXT, source_file TEXT, bioguide TEXT
            );
            CREATE TABLE transactions (
                id INTEGER PRIMARY KEY, chamber TEXT, last_name TEXT, first_name TEXT,
                state TEXT, district TEXT, transaction_date TEXT, disclosure_date TEXT,
                asset TEXT, asset_type TEXT, transaction_type TEXT,
                amount TEXT, ticker TEXT, comment TEXT, source_file TEXT, bioguide TEXT
            );
            INSERT INTO member_info VALUES
                ('W001','Whitehouse','Sheldon','Sheldon','RI','Democrat',1);
            INSERT INTO name_aliases VALUES
                ('W001','Whitehouse','Sheldon');
            INSERT INTO transactions VALUES
                (1,'Senate','Whitehouse','Sheldon','','','2024-02-01','2024-02-05',
                 'Apple Inc','Stock','Sale (Full)','$50K-$100K','AAPL','','t1.pdf',NULL);
        """)
        import os as _os
        old_db = query_server.DB_PATH
        query_server.DB_PATH = db_path
        try:
            query_server._ensure_member_info()
            # Simulate the transactions endpoint query
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT mi.party, mi.state "
                "FROM transactions t "
                "LEFT JOIN member_info mi ON t.bioguide = mi.bioguide "
                "WHERE t.last_name='Whitehouse'"
            ).fetchone()
            conn.close()
            assert row is not None
            assert row['party'] == 'Democrat', \
                f"Expected Democrat after bioguide fix; got: {row['party']}"
            assert row['state'] == 'RI', \
                f"Expected RI after bioguide fix; got: {row['state']}"
        finally:
            query_server.DB_PATH = old_db
            _os.unlink(db_path)

    def test_learn_from_transactions_adds_alias(self):
        """
        Unique-last-name member 'Boozman/John' in transactions with NULL bioguide
        but unique match in member_info — alias should be learned and bioguide fixed.
        """
        db_path = self._make_db("""
            CREATE TABLE member_info (
                bioguide TEXT PRIMARY KEY, last_name TEXT, first_name TEXT,
                display_first TEXT, state TEXT, party TEXT, is_current INTEGER DEFAULT 0
            );
            CREATE TABLE name_aliases (
                bioguide TEXT, last_name TEXT, first_name TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_aliases ON name_aliases (last_name, first_name);
            CREATE TABLE holdings (
                id INTEGER PRIMARY KEY, chamber TEXT, last_name TEXT, first_name TEXT,
                state TEXT, asset TEXT, asset_type TEXT, owner TEXT,
                value TEXT, income_type TEXT, income TEXT, source_file TEXT, bioguide TEXT
            );
            CREATE TABLE transactions (
                id INTEGER PRIMARY KEY, chamber TEXT, last_name TEXT, first_name TEXT,
                state TEXT, district TEXT, transaction_date TEXT, disclosure_date TEXT,
                asset TEXT, asset_type TEXT, transaction_type TEXT,
                amount TEXT, ticker TEXT, comment TEXT, source_file TEXT, bioguide TEXT
            );
            INSERT INTO member_info VALUES
                ('B001','Boozman','John','John','AR','Republican',1);
            INSERT INTO name_aliases VALUES
                ('B001','Boozman','John');
            INSERT INTO transactions VALUES
                (1,'Senate','Boozman','John','','','2024-03-01','2024-03-05',
                 'Apple Inc','Stock','Purchase','$15K-$50K','AAPL','','t1.pdf',NULL);
        """)
        import os as _os
        old_db = query_server.DB_PATH
        query_server.DB_PATH = db_path
        try:
            query_server._ensure_member_info()
            conn = sqlite3.connect(db_path)
            row = conn.execute(
                "SELECT bioguide FROM transactions WHERE last_name='Boozman'"
            ).fetchone()
            conn.close()
            assert row is not None
            assert row[0] == 'B001', \
                f"Boozman transaction bioguide not fixed; got: {row[0]}"
        finally:
            query_server.DB_PATH = old_db
            _os.unlink(db_path)
