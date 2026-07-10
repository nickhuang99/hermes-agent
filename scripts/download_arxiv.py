#!/usr/bin/env python3
"""Download recent cs.AI papers from arxiv, extract text, build searchable DB."""

import json, os, re, sqlite3, subprocess, sys, time, urllib.request
from pathlib import Path
from xml.etree import ElementTree as ET

ARXIV_API = "http://export.arxiv.org/api/query"
DOWNLOAD_DIR = Path.home() / "Downloads" / "arxiv" / "ai"
DB_PATH = DOWNLOAD_DIR / "papers.db"
MAX_PAPERS = 50

def fetch_papers(max_results=MAX_PAPERS):
    """Fetch recent cs.AI papers via arxiv API."""
    params = urllib.parse.urlencode({
        "search_query": "cat:cs.AI",
        "sortBy": "submittedDate",
        "sortOrder": "descending",
        "max_results": max_results,
    })
    url = f"{ARXIV_API}?{params}"
    print(f"Fetching: {url}")
    with urllib.request.urlopen(url, timeout=30) as resp:
        return resp.read().decode("utf-8")

def parse_papers(xml_text):
    """Parse arxiv Atom XML response."""
    ns = {
        "atom": "http://www.w3.org/2005/Atom",
        "arxiv": "http://arxiv.org/schemas/atom",
    }
    root = ET.fromstring(xml_text)
    papers = []
    for entry in root.findall("atom:entry", ns):
        aid_el = entry.find("atom:id", ns)
        title_el = entry.find("atom:title", ns)
        summary_el = entry.find("atom:summary", ns)
        published_el = entry.find("atom:published", ns)
        authors = [a.find("atom:name", ns).text
                   for a in entry.findall("atom:author", ns)]
        links = entry.findall("atom:link", ns)
        pdf_url = None
        for link in links:
            if link.get("title") == "pdf":
                pdf_url = link.get("href")
                break
        arxiv_id = aid_el.text.strip() if aid_el is not None else ""
        # Extract ID from http://arxiv.org/abs/XXXX.XXXXX
        arxiv_id = arxiv_id.split("/abs/")[-1] if "/abs/" in arxiv_id else arxiv_id
        papers.append({
            "arxiv_id": arxiv_id,
            "title": title_el.text.strip().replace("\n", " ") if title_el is not None else "",
            "summary": summary_el.text.strip().replace("\n", " ") if summary_el is not None else "",
            "published": published_el.text.strip() if published_el is not None else "",
            "authors": "; ".join(authors),
            "pdf_url": pdf_url,
        })
    return papers

def download_pdf(paper, download_dir):
    """Download a single PDF."""
    safe_id = re.sub(r"[^\w.-]", "_", paper["arxiv_id"])
    pdf_path = download_dir / f"{safe_id}.pdf"
    if pdf_path.exists():
        print(f"  [SKIP] {safe_id} — already downloaded")
        return pdf_path, False

    url = paper["pdf_url"]
    if not url:
        print(f"  [SKIP] {safe_id} — no PDF URL")
        return None, False

    print(f"  [GET] {safe_id} ...", end=" ", flush=True)
    try:
        urllib.request.urlretrieve(url, pdf_path)
        size_kb = pdf_path.stat().st_size / 1024
        print(f"{size_kb:.0f} KB")
        return pdf_path, True
    except Exception as e:
        print(f"ERROR: {e}")
        return None, False

def extract_text(pdf_path):
    """Extract text from PDF using pdftotext."""
    txt_path = pdf_path.with_suffix(".txt")
    if txt_path.exists():
        return txt_path.read_text()[:50000]
    try:
        subprocess.run(
            ["pdftotext", "-l", "10", str(pdf_path), str(txt_path)],
            capture_output=True, timeout=30, check=True
        )
        return txt_path.read_text()[:50000]
    except Exception as e:
        print(f"    pdftotext error: {e}")
        return ""

def build_db(papers, db_path):
    """Create SQLite DB with FTS5 indexes."""
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS papers (
            id INTEGER PRIMARY KEY,
            arxiv_id TEXT UNIQUE,
            title TEXT,
            summary TEXT,
            published TEXT,
            authors TEXT,
            pdf_path TEXT,
            content TEXT
        );
        CREATE VIRTUAL TABLE IF NOT EXISTS papers_fts USING fts5(
            title, summary, authors, content, tokenize='porter unicode61'
        );
    """)
    conn.commit()

    inserted = 0
    for p in papers:
        try:
            conn.execute(
                "INSERT OR REPLACE INTO papers (arxiv_id, title, summary, published, authors, pdf_path, content) "
                "VALUES (?,?,?,?,?,?,?)",
                (p["arxiv_id"], p["title"], p["summary"],
                 p["published"], p["authors"], str(p.get("pdf_path", "")), p.get("content", ""))
            )
            inserted += 1
        except Exception as e:
            print(f"  DB insert error for {p['arxiv_id']}: {e}")

    # Rebuild FTS
    conn.execute("DELETE FROM papers_fts")
    conn.execute(
        "INSERT INTO papers_fts(rowid, title, summary, authors, content) "
        "SELECT id, title, summary, authors, content FROM papers"
    )
    conn.execute("INSERT INTO papers_fts(papers_fts) VALUES('optimize')")
    conn.commit()

    count = conn.execute("SELECT count(*) FROM papers").fetchone()[0]
    conn.close()
    return count

def main():
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    print(f"Download dir: {DOWNLOAD_DIR}")
    print()

    # Fetch
    print("=== Fetching papers from arxiv ===")
    xml_text = fetch_papers()
    papers = parse_papers(xml_text)
    print(f"Found {len(papers)} papers\n")

    # Download
    print("=== Downloading PDFs ===")
    new_downloads = 0
    for p in papers:
        pdf_path, is_new = download_pdf(p, DOWNLOAD_DIR)
        p["pdf_path"] = pdf_path
        if is_new:
            new_downloads += 1
            time.sleep(1)  # Be polite to arxiv
    print(f"Downloaded {new_downloads} new PDFs\n")

    # Extract text
    print("=== Extracting text ===")
    for p in papers:
        if p["pdf_path"] and p["pdf_path"].exists():
            p["content"] = extract_text(p["pdf_path"])
            print(f"  {p['arxiv_id']}: {len(p['content'])} chars")
    print()

    # Build DB
    print("=== Building database ===")
    count = build_db(papers, DB_PATH)
    size_mb = DB_PATH.stat().st_size / (1024 * 1024)
    print(f"DB: {DB_PATH} — {count} papers, {size_mb:.1f} MB\n")

    # Quick search test
    print("=== Search test ===")
    conn = sqlite3.connect(str(DB_PATH))
    for q in ["reinforcement learning", "transformer", "language model"]:
        row = conn.execute(
            "SELECT title FROM papers WHERE rowid IN "
            "(SELECT rowid FROM papers_fts WHERE papers_fts MATCH ? LIMIT 1)",
            (q,)
        ).fetchone()
        if row:
            print(f"  '{q}' → {row[0][:100]}")
        else:
            print(f"  '{q}' → no match")
    conn.close()
    print("\nDone.")

if __name__ == "__main__":
    main()
