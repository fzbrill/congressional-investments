"""
convert_pdfs_to_md.py — Convert disclosure PDFs to Markdown text cache
Run once (or re-run to refresh) before build_db.py / build_transactions.py.

Creates:
  scotus_md_2024/<LastName_FirstName>.md  — one file per SCOTUS justice
  trump_md/Trump_Donald.md               — Trump's 278e (234 pages, ~2 MB text)

Parsers then read from .md files instead of reopening PDFs, which is faster
and makes the extracted text inspectable / debuggable as plain files.
"""
import os, re
import pdfplumber

BASE = os.path.dirname(os.path.abspath(__file__))

def pdf_to_text(pdf_path):
    """Extract all page text from a PDF, separated by page markers."""
    pages = []
    with pdfplumber.open(pdf_path) as pdf:
        for i, page in enumerate(pdf.pages, 1):
            text = page.extract_text() or ''
            pages.append(f"<!-- page {i} -->\n{text}")
    return '\n\n'.join(pages)

def convert_dir(pdf_dir, md_dir, label):
    os.makedirs(md_dir, exist_ok=True)
    pdfs = sorted(f for f in os.listdir(pdf_dir) if f.lower().endswith('.pdf'))
    print(f"Converting {len(pdfs)} {label} PDFs → {md_dir}")
    for fname in pdfs:
        stem = fname[:-4]  # strip .pdf
        md_path = os.path.join(md_dir, stem + '.md')
        if os.path.exists(md_path):
            print(f"  skip (cached): {stem}.md")
            continue
        pdf_path = os.path.join(pdf_dir, fname)
        try:
            text = pdf_to_text(pdf_path)
            with open(md_path, 'w', encoding='utf-8') as f:
                f.write(text)
            size_kb = os.path.getsize(md_path) // 1024
            print(f"  {stem}.md  ({size_kb} KB)")
        except Exception as e:
            print(f"  ERROR {fname}: {e}")

def convert_single(pdf_path, md_path, label):
    if os.path.exists(md_path):
        print(f"skip (cached): {os.path.basename(md_path)}")
        return
    os.makedirs(os.path.dirname(md_path), exist_ok=True)
    print(f"Converting {label} PDF ({os.path.getsize(pdf_path)//1024} KB)...")
    try:
        text = pdf_to_text(pdf_path)
        with open(md_path, 'w', encoding='utf-8') as f:
            f.write(text)
        size_kb = os.path.getsize(md_path) // 1024
        print(f"  → {os.path.basename(md_path)}  ({size_kb} KB)")
    except Exception as e:
        print(f"  ERROR: {e}")

# ── SCOTUS PDFs ───────────────────────────────────────────────────────────────
scotus_pdf_dir = os.path.join(BASE, 'scotus_pdfs_2024')
scotus_md_dir  = os.path.join(BASE, 'scotus_md_2024')
if os.path.isdir(scotus_pdf_dir):
    convert_dir(scotus_pdf_dir, scotus_md_dir, 'SCOTUS')
else:
    print("scotus_pdfs_2024/ not found, skipping SCOTUS.")

# ── Trump 278e ────────────────────────────────────────────────────────────────
trump_pdf = os.path.join(BASE, 'Trump, Donald J. 2025 Annual 278.pdf')
trump_md  = os.path.join(BASE, 'trump_md', 'Trump_Donald.md')
if os.path.exists(trump_pdf):
    convert_single(trump_pdf, trump_md, 'Trump 278e')
else:
    print("Trump PDF not found, skipping.")

print("\nDone. Re-run at any time to pick up new PDFs (cached files are skipped).")
print("To force re-conversion, delete the .md files or the cache directory.")
