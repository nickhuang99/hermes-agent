#!/usr/bin/env python3
"""Build luke.db from Nick's novels + diabloforum posts."""

import re, sqlite3, sys, time
from html.parser import HTMLParser
from pathlib import Path

WECHAT = Path.home() / "Documents" / "wechat"
DIABLO = Path.home() / "Documents" / "diabloforum"
OUT_DB = Path.home() / ".hermes" / "luke.db"

class SimpleHTMLParser(HTMLParser):
    """Extract text from diabloforum HTML."""
    SKIP = {"script", "style", "meta", "link", "img", "hr", "br"}
    def __init__(self):
        super().__init__()
        self.title = ""
        self.paragraphs = []
        self._in_title = False
        self._current = ""
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag == "title":
            self._in_title = True
        elif tag in self.SKIP:
            return
        elif tag in ("p", "h1", "h2", "h3", "h4", "h5", "h6", "div", "li"):
            self._flush()
        elif tag == "a":
            self._flush()

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False
        elif tag in self.SKIP:
            return
        elif tag in ("p", "h1", "h2", "h3", "h4", "h5", "h6", "div", "li"):
            self._flush()

    def handle_data(self, data):
        if self._in_title:
            self.title += data
            return
        text = data.strip()
        if text:
            self._current += " " + text if self._current else text

    def _flush(self):
        if self._current.strip():
            self.paragraphs.append(self._current.strip())
        self._current = ""

    def get_result(self):
        self._flush()
        return self.title.strip(), "\n\n".join(self.paragraphs)


def build():
    if OUT_DB.exists():
        OUT_DB.unlink()
        print(f"Removed old {OUT_DB}")

    conn = sqlite3.connect(str(OUT_DB))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript("""
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
    """)
    conn.commit()

    now = time.time()
    next_id = 1
    stats = {}

    def add_sessions(source, files, reader, label):
        nonlocal next_id
        batch = []
        count = 0
        chars = 0
        for fpath in sorted(files):
            try:
                title, body = reader(fpath)
            except Exception as e:
                print(f"  SKIP {fpath.name}: {e}", file=sys.stderr)
                continue
            if not body or len(body) < 20:
                continue
            sid = f"{label}:{fpath.stem}"
            batch.append((sid, source, title, now, next_id, body))
            next_id += 1
            count += 1
            chars += len(body)
            if len(batch) >= 500:
                _flush_batch(conn, batch)
                batch = []
                print(f"  {count} files, {chars:,} chars", end="\r")
        if batch:
            _flush_batch(conn, batch)
        print(f"  {label}: {count} files, {chars:,} chars")
        stats[label] = {"files": count, "chars": chars}

    # 1. Chinese originals
    print("\n=== wechat/text (Chinese originals) ===")
    txt_files = sorted(WECHAT.glob("text/*.txt"))
    def read_txt(path):
        text = path.read_text().strip()
        return path.stem, text
    add_sessions("novel-cn", txt_files, read_txt, "novel-cn")

    # 2. English translations
    print("\n=== wechat/english_text ===")
    en_files = sorted(WECHAT.glob("english_text/*.txt"))
    add_sessions("novel-en", en_files, read_txt, "novel-en")

    # 3. Fixed/edited versions
    print("\n=== wechat/fixed_text ===")
    fix_files = sorted(WECHAT.glob("fixed_text/*.txt"))
    add_sessions("novel-fixed", fix_files, read_txt, "novel-fixed")

    # 4. Diablo forum
    print("\n=== diabloforum (*.htm) ===")
    htm_files = sorted(DIABLO.glob("*.htm"))
    def read_htm(path):
        p = SimpleHTMLParser()
        p.feed(path.read_text())
        return p.get_result()
    add_sessions("diabloforum", htm_files, read_htm, "diabloforum")

    # FTS indexes
    print("\n=== Building FTS5 indexes ===")
    conn.execute("INSERT INTO messages_fts(rowid, content) SELECT id, COALESCE(content,'') FROM messages")
    conn.execute("INSERT INTO messages_fts_trigram(rowid, content) SELECT id, COALESCE(content,'') FROM messages")
    conn.execute("INSERT INTO messages_fts(messages_fts) VALUES('optimize')")
    conn.execute("INSERT INTO messages_fts_trigram(messages_fts_trigram) VALUES('optimize')")
    conn.commit()

    size_mb = OUT_DB.stat().st_size / (1024 * 1024)
    total = sum(s["files"] for s in stats.values())
    total_chars = sum(s["chars"] for s in stats.values())
    print(f"\n{'='*50}")
    print(f"luke.db — {total} documents, {total_chars:,} chars, {size_mb:.1f} MB")
    for k, v in stats.items():
        print(f"  {k}: {v['files']} files, {v['chars']:,} chars")

    # Verify
    print(f"\n=== Search tests ===")
    queries = [
        "星辰大海",
        "reinforcement learning",
        "AI 发展",
        "diablo",
    ]
    for q in queries:
        row = conn.execute(
            "SELECT s.title, substr(m.content,1,100) FROM messages m "
            "JOIN sessions s ON m.session_id=s.id "
            "WHERE m.id IN (SELECT rowid FROM messages_fts WHERE messages_fts MATCH ? LIMIT 1)",
            (q,)
        ).fetchone()
        print(f"  '{q}' → {row[0][:60] if row else 'no match'}")

    conn.close()
    print("\nDone.")


def _flush_batch(conn, batch):
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
    build()
