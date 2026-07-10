#!/usr/bin/env python3
"""Build a searchable SQLite DB from cppreference + C++ standard HTML pages.

Uses html_parser.py for extraction, outputs Hermes-compatible SessionDB.
"""

import sqlite3, sys, time
from pathlib import Path

# Add scripts dir to path for html_parser import
sys.path.insert(0, str(Path(__file__).resolve().parent))
from html_parser import extract_cppreference, extract_cxxstandard

CPP_DIR = Path("/home/nick/work/cpp")
CXX_DIR = Path("/home/nick/work/cxx")
OUT_DB = Path("/home/nick/work/hermes-agent/cpp_docs.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY, source TEXT, model TEXT DEFAULT 'import',
    started_at REAL, message_count INTEGER DEFAULT 0, title TEXT
);
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY, session_id TEXT NOT NULL, role TEXT NOT NULL,
    content TEXT, tool_call_id TEXT, tool_calls TEXT, tool_name TEXT,
    timestamp REAL, active INTEGER DEFAULT 1, compacted INTEGER DEFAULT 0,
    observed INTEGER DEFAULT 0
);
CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(content, tokenize='porter unicode61');
CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts_trigram USING fts5(content, tokenize='trigram');
"""

def main():
    now = time.time()

    # Remove old
    if OUT_DB.exists():
        OUT_DB.unlink()
        print(f"Removed old {OUT_DB}")

    conn = sqlite3.connect(str(OUT_DB))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)

    next_id = 1
    total_files = 0
    total_chars = 0
    empty_files = 0
    stats = {"cpp": {"files": 0, "chars": 0}, "cxx": {"files": 0, "chars": 0}}

    for source_dir, source_name, extractor, label in [
        (CPP_DIR, "cppreference", extract_cppreference, "cpp"),
        (CXX_DIR, "cxxstandard", extract_cxxstandard, "cxx"),
    ]:
        if not source_dir.exists():
            print(f"SKIP {source_dir} — not found")
            continue

        html_files = sorted(source_dir.rglob("*.html"))
        print(f"\nProcessing {len(html_files)} {label} files...")

        batch = []
        for i, fpath in enumerate(html_files):
            try:
                title, body = extractor(str(fpath))
            except Exception as e:
                print(f"  ERROR {fpath.name}: {e}", file=sys.stderr)
                continue

            if not body or len(body) < 10:
                empty_files += 1
                continue

            # Session per file: one message containing the body
            rel = fpath.relative_to(source_dir).with_suffix("")
            sid = f"{label}:{rel}"
            total_files += 1
            total_chars += len(body)
            stats[label]["files"] += 1
            stats[label]["chars"] += len(body)

            batch.append((sid, source_name, title, now, next_id, body))

            if len(batch) >= 500:
                _flush_batch(conn, batch)
                batch = []
                print(f"  {i+1}/{len(html_files)} ({total_files} total, {total_chars:,} chars)", end="\r")

            next_id += 1

        if batch:
            _flush_batch(conn, batch)
        print(f"  Done: {stats[label]['files']} files, {stats[label]['chars']:,} chars")

    # Index
    print("\nBuilding FTS5 indexes...")
    conn.execute(
        "INSERT INTO messages_fts(rowid, content) "
        "SELECT id, COALESCE(content,'') FROM messages"
    )
    conn.execute(
        "INSERT INTO messages_fts_trigram(rowid, content) "
        "SELECT id, COALESCE(content,'') FROM messages"
    )
    conn.execute("INSERT INTO messages_fts(messages_fts) VALUES('optimize')")
    conn.execute("INSERT INTO messages_fts_trigram(messages_fts_trigram) VALUES('optimize')")
    conn.commit()

    size_mb = OUT_DB.stat().st_size / (1024 * 1024)
    print(f"\n{'='*60}")
    print(f"BUILD COMPLETE: {OUT_DB}")
    print(f"  Size: {size_mb:.1f} MB")
    print(f"  Sessions: {total_files}")
    print(f"  Total characters: {total_chars:,}")
    print(f"  Empty/skipped: {empty_files}")
    print(f"  cppreference: {stats['cpp']['files']} files, {stats['cpp']['chars']:,} chars")
    print(f"  cxx standard: {stats['cxx']['files']} files, {stats['cxx']['chars']:,} chars")

    # Quick verify searches
    print(f"\n{'='*60}")
    print("VERIFICATION SEARCHES")
    print(f"{'='*60}")

    queries = [
        ("vector data()", "std::vector::data"),
        ("hazard_pointer", "std::hazard_pointer"),
        ("move semantics", "move semantics"),
        ("智能指针", "smart pointer (CJK)"),
        ("unordered_map find", "std::unordered_map::find"),
    ]

    for q, label in queries:
        cur = conn.cursor()
        cur.execute(
            "SELECT s.title, m.content FROM messages m "
            "JOIN sessions s ON m.session_id = s.id "
            "WHERE m.id IN (SELECT rowid FROM messages_fts WHERE messages_fts MATCH ? LIMIT 1)",
            (q,)
        )
        row = cur.fetchone()
        if row:
            title = row[0] or "(no title)"
            snippet = row[1][:120].replace("\n", " ")
            print(f"\n  [{label}] '{q}'")
            print(f"    → {title}")
            print(f"    → {snippet}...")
        else:
            print(f"\n  [{label}] '{q}' → NO MATCH")

    conn.close()
    print(f"\nDone. Ready for session_search(db_path='{OUT_DB}').")


def _flush_batch(conn, batch):
    """Insert a batch of sessions + messages."""
    conn.executemany(
        "INSERT OR REPLACE INTO sessions (id, source, model, started_at, message_count, title) "
        "VALUES (?,?,?,?,1,?)",
        [(sid, src, "import", now, title) for sid, src, title, now, _mid, _body in batch]
    )
    conn.executemany(
        "INSERT OR REPLACE INTO messages (id, session_id, role, content, timestamp, active, compacted) "
        "VALUES (?,?,?,?,?,1,0)",
        [(mid, sid, "assistant", body, now) for sid, _src, _title, now, mid, body in batch]
    )


if __name__ == "__main__":
    main()
