"""
Run: python diagnose_cruz.py
Diagnoses why Cruz (and similar nicknames) aren't displaying correctly.
"""
import sqlite3, os

DB_PATH = os.path.join(os.path.dirname(__file__), "holdings.db")
conn = sqlite3.connect(DB_PATH)

print("=== member_info for Cruz ===")
for r in conn.execute("SELECT bioguide, last_name, first_name, display_first, state, party FROM member_info WHERE LOWER(last_name)='cruz'"):
    print(dict(zip(['bioguide','last_name','first_name','display_first','state','party'], r)))

print("\n=== name_aliases for Cruz ===")
for r in conn.execute("SELECT * FROM name_aliases WHERE LOWER(last_name)='cruz'"):
    print(r)

print("\n=== holdings for Cruz (all distinct first_name values) ===")
for r in conn.execute("SELECT DISTINCT first_name, last_name, state, chamber FROM holdings WHERE LOWER(last_name)='cruz'"):
    print(dict(zip(['first_name','last_name','state','chamber'], r)))

print("\n=== Live subquery test (what party subquery returns for each Cruz row) ===")
q = """
SELECT h.first_name, h.chamber,
  (SELECT mi.party FROM member_info mi 
   JOIN name_aliases na ON na.bioguide=mi.bioguide
   WHERE LOWER(na.last_name)=LOWER(h.last_name)
   AND (LOWER(na.first_name)=LOWER(CASE WHEN LOWER(SUBSTR(h.first_name,1,4))='hon.' 
        THEN TRIM(SUBSTR(h.first_name, INSTR(h.first_name,' ')+1)) ELSE h.first_name END)
     OR LOWER(CASE WHEN LOWER(SUBSTR(h.first_name,1,4))='hon.'
        THEN TRIM(SUBSTR(h.first_name, INSTR(h.first_name,' ')+1)) ELSE h.first_name END) 
        LIKE LOWER(na.first_name)||' %'
     OR LOWER(CASE WHEN LOWER(SUBSTR(h.first_name,1,4))='hon.'
        THEN TRIM(SUBSTR(h.first_name, INSTR(h.first_name,' ')+1)) ELSE h.first_name END) 
        LIKE '% '||LOWER(na.first_name))
   LIMIT 1) AS matched_party,
  (SELECT COALESCE(mi.display_first, mi.first_name) FROM member_info mi 
   JOIN name_aliases na ON na.bioguide=mi.bioguide
   WHERE LOWER(na.last_name)=LOWER(h.last_name)
   AND (LOWER(na.first_name)=LOWER(CASE WHEN LOWER(SUBSTR(h.first_name,1,4))='hon.' 
        THEN TRIM(SUBSTR(h.first_name, INSTR(h.first_name,' ')+1)) ELSE h.first_name END)
     OR LOWER(CASE WHEN LOWER(SUBSTR(h.first_name,1,4))='hon.'
        THEN TRIM(SUBSTR(h.first_name, INSTR(h.first_name,' ')+1)) ELSE h.first_name END) 
        LIKE LOWER(na.first_name)||' %'
     OR LOWER(CASE WHEN LOWER(SUBSTR(h.first_name,1,4))='hon.'
        THEN TRIM(SUBSTR(h.first_name, INSTR(h.first_name,' ')+1)) ELSE h.first_name END) 
        LIKE '% '||LOWER(na.first_name))
   LIMIT 1) AS matched_display_first
FROM holdings h WHERE LOWER(h.last_name)='cruz'
LIMIT 5
"""
for r in conn.execute(q):
    print(dict(zip(['h.first_name','chamber','matched_party','matched_display_first'], r)))

print("\n=== Schumer / McConnell / Budd sanity check ===")
for name in ['schumer','mcconnell','budd']:
    r = conn.execute(f"SELECT first_name, last_name FROM holdings WHERE LOWER(last_name)='{name}' LIMIT 1").fetchone()
    alias = conn.execute(f"SELECT first_name FROM name_aliases WHERE LOWER(last_name)='{name}'").fetchall()
    mi = conn.execute(f"SELECT display_first FROM member_info WHERE LOWER(last_name)='{name}'").fetchone()
    print(f"{name}: holdings.first_name={r}, aliases={[a[0] for a in alias]}, display_first={mi}")

conn.close()
