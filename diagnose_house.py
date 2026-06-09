"""
Diagnostic: show raw holdings first_name/last_name for unmatched House members,
plus their current name_aliases entries.
Run: python diagnose_house.py
"""
import sqlite3, os

DB = os.path.join(os.path.dirname(__file__), 'holdings.db')
conn = sqlite3.connect(DB)

suspects = [
    'Amata', 'Arenholz', 'Barragan', 'Chavez-DeRemer', 'Connolly',
    "D'Esposito", "D'esposito", 'Greene', 'Garcia', 'LaMalfa',
    'Gonzales', 'Kilmer', 'Luna', 'Murphy', 'Sanchez', 'Sherrill',
    "O'Halleran", "O'halleran", 'Scott', 'Swalwell',
]

print("=== Holdings rows for suspect last names ===")
for last in suspects:
    rows = conn.execute(
        "SELECT DISTINCT last_name, first_name, chamber FROM holdings "
        "WHERE LOWER(last_name) LIKE LOWER(?) AND chamber='House' "
        "ORDER BY last_name, first_name",
        (last.replace("'", "%") + '%',)
    ).fetchall()
    for r in rows:
        print(f"  holdings: last={r[0]!r:30s} first={r[1]!r:40s} chamber={r[2]}")

# Also check for "Paulina" (Anna Paulina Luna files under compound last name)
print("\n=== Holdings with 'Paulina' or 'Luna' in last_name ===")
for row in conn.execute(
    "SELECT DISTINCT last_name, first_name FROM holdings "
    "WHERE (LOWER(last_name) LIKE '%paulina%' OR LOWER(last_name) LIKE '%luna%') "
    "AND chamber='House'"
).fetchall():
    print(f"  {row[0]!r:35s} {row[1]!r}")

# Show name_aliases for last names that exist
print("\n=== name_aliases for matching last names ===")
for last in suspects:
    rows = conn.execute(
        "SELECT na.last_name, na.first_name, mi.display_first, mi.state, mi.party "
        "FROM name_aliases na JOIN member_info mi ON mi.bioguide=na.bioguide "
        "WHERE LOWER(na.last_name) LIKE LOWER(?)",
        ('%' + last.replace("'", "%") + '%',)
    ).fetchall()
    for r in rows:
        print(f"  alias: {r[0]:20s} {r[1]:20s} | display={r[2]} state={r[3]} party={r[4]}")

conn.close()
