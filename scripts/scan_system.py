#!/usr/bin/env python3
"""System scanner — index /etc, systemd, packages, logs, binaries into system.db."""

import hashlib, json, os, sqlite3, subprocess, time
from pathlib import Path
from stat import S_ISREG, S_ISDIR, S_ISLNK

DB = Path.home() / ".hermes" / "system.db"
ETC = Path("/etc")
SCAN_TS = time.time()

# ── helpers ──────────────────────────────────────────────
def hash_file(path):
    try:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None

def file_info(path, root="/"):
    try:
        st = path.stat()
        return {
            "path": str(path),
            "size": st.st_size,
            "mtime": st.st_mtime,
            "mode": oct(st.st_mode)[-4:],
            "uid": st.st_uid,
            "gid": st.st_gid,
            "is_symlink": S_ISLNK(st.st_mode),
            "is_dir": S_ISDIR(st.st_mode),
        }
    except OSError:
        return {"path": str(path), "error": "stat_failed"}

TEXT_EXTS = {".conf", ".cfg", ".ini", ".yml", ".yaml", ".json", ".xml",
             ".txt", ".md", ".sh", ".bash", ".py", ".pl", ".rb", ".lua",
             ".service", ".timer", ".socket", ".target", ".mount",
             ".list", ".sources", ".pref", ".default", ".env", ".rules",
             ".c", ".h", ".cpp", ".hpp", ".toml", ".cnf"}
BIN_EXTS = {".so", ".o", ".a", ".ko", ".bin", ".dat", ".db", ".pyc", ".pyo"}

def is_text(path):
    name = path.name.lower()
    if any(name.endswith(e) for e in TEXT_EXTS):
        return True
    if name.startswith("."):
        return False
    # Files with no extension in /etc are usually config
    if "." not in name and str(path).startswith("/etc"):
        return True
    return False

def is_binary(path):
    name = path.name.lower()
    return any(name.endswith(e) for e in BIN_EXTS)

def read_text(path, max_kb=512):
    """Read text content, capped."""
    try:
        size = path.stat().st_size
        if size > max_kb * 1024:
            with open(path, "rb") as f:
                return f.read(max_kb * 1024).decode("utf-8", errors="replace") + "\n[TRUNCATED]"
        return path.read_text(encoding="utf-8", errors="replace")
    except (OSError, UnicodeDecodeError):
        return None

# ── DB setup ──────────────────────────────────────────────
conn = sqlite3.connect(str(DB))
conn.execute("PRAGMA journal_mode=WAL")

conn.executescript("""
CREATE TABLE IF NOT EXISTS files (
    id INTEGER PRIMARY KEY,
    path TEXT UNIQUE NOT NULL,
    size INTEGER, mtime REAL, mode TEXT,
    uid INTEGER, gid INTEGER,
    is_symlink INTEGER DEFAULT 0,
    is_dir INTEGER DEFAULT 0,
    sha256 TEXT,
    category TEXT,
    content TEXT
);
CREATE TABLE IF NOT EXISTS snapshots (
    id INTEGER PRIMARY KEY,
    ts REAL NOT NULL,
    total_files INTEGER, total_size INTEGER,
    etc_files INTEGER, issues_found TEXT
);
CREATE VIRTUAL TABLE IF NOT EXISTS files_fts USING fts5(path, category, content, tokenize='porter unicode61');
CREATE VIRTUAL TABLE IF NOT EXISTS files_fts_trigram USING fts5(path, category, content, tokenize='trigram');
""")
conn.commit()

# ── scan system ──────────────────────────────────────────
issues = []
stats = {"etc": 0, "systemd": 0, "packages": 0, "logs": 0, "binaries": 0, "failed": 0}

def scan_etc():
    """Full-text index all /etc text files."""
    for path in sorted(ETC.rglob("*")):
        if path.is_symlink() or path.is_dir():
            continue
        if not S_ISREG(path.stat().st_mode):
            continue
        info = file_info(path)
        if is_text(path):
            content = read_text(path)
            if content is None:
                stats["failed"] += 1
                continue
            sha = hashlib.sha256(content.encode()).hexdigest()
            conn.execute(
                "INSERT OR REPLACE INTO files(path,size,mtime,mode,uid,gid,sha256,category,content) "
                "VALUES(?,?,?,?,?,?,?,?,?)",
                (str(path), info["size"], info["mtime"], info["mode"],
                 info["uid"], info["gid"], sha, "etc", content)
            )
            stats["etc"] += 1
        elif is_binary(path):
            sha = hash_file(path)
            if sha:
                conn.execute(
                    "INSERT OR REPLACE INTO files(path,size,mtime,mode,uid,gid,sha256,category) "
                    "VALUES(?,?,?,?,?,?,?,?)",
                    (str(path), info["size"], info["mtime"], info["mode"],
                     info["uid"], info["gid"], sha, "binary")
                )
                stats["binaries"] += 1

    # Health checks: truly world-writable (other-write bit set, mode 0o??2,0o??3,0o??6,0o??7)
    for row in conn.execute(
        "SELECT path,mode FROM files WHERE category='etc' "
        "AND (CAST(substr(mode,-1) AS INTEGER) IN (2,3,6,7))"
    ):
        issues.append(f"world-writable: {row[0]} ({row[1]})")

    # Check for broken /etc symlinks
    for link in ETC.rglob("*"):
        if link.is_symlink() and not link.exists():
            issues.append(f"broken-symlink: {link}")

def scan_systemd():
    """Index systemd unit files and current states."""
    paths = [Path("/lib/systemd/system"), Path("/etc/systemd/system"),
             Path.home() / ".config/systemd/user"]

    for base in paths:
        if not base.exists():
            continue
        for path in sorted(base.rglob("*")):
            if path.is_dir():
                continue
            content = read_text(path)
            if content is None:
                continue
            info = file_info(path)
            sha = hashlib.sha256(content.encode()).hexdigest()
            conn.execute(
                "INSERT OR REPLACE INTO files(path,size,mtime,mode,sha256,category,content) "
                "VALUES(?,?,?,?,?,?,?)",
                (str(path), info["size"], info["mtime"], info["mode"], sha, "systemd", content)
            )
            stats["systemd"] += 1

    # Running services snapshot
    try:
        svcs = subprocess.run(
            ["systemctl", "list-units", "--type=service", "--all", "--no-pager", "--no-legend"],
            capture_output=True, text=True, timeout=10
        ).stdout
        conn.execute(
            "INSERT OR REPLACE INTO files(path,size,mtime,mode,category,content) VALUES(?,?,?,?,?,?)",
            ("__snapshot__/running-services", len(svcs), SCAN_TS, "0644", "systemd-snapshot", svcs)
        )
        # Check for failed services
        for line in svcs.splitlines():
            if "failed" in line.lower():
                issues.append(f"failed-service: {line.strip()[:120]}")
    except Exception:
        pass

def scan_packages():
    """Snapshot installed packages."""
    try:
        pkgs = subprocess.run(
            ["dpkg", "-l"], capture_output=True, text=True, timeout=15
        ).stdout
        conn.execute(
            "INSERT OR REPLACE INTO files(path,size,mtime,mode,category,content) VALUES(?,?,?,?,?,?)",
            ("__snapshot__/packages", len(pkgs), SCAN_TS, "0644", "packages", pkgs)
        )
        stats["packages"] = len(pkgs.splitlines())
    except Exception:
        pass

    # apt sources
    for src in sorted(Path("/etc/apt/sources.list.d").glob("*.list")):
        content = read_text(src)
        if content:
            conn.execute(
                "INSERT OR REPLACE INTO files(path,size,mtime,mode,category,content) VALUES(?,?,?,?,?,?)",
                (str(src), len(content), SCAN_TS, "0644", "apt", content)
            )

def scan_logs():
    """Recent system log entries."""
    for log in ["/var/log/syslog", "/var/log/auth.log", "/var/log/kern.log"]:
        if not Path(log).exists():
            continue
        try:
            tail = subprocess.run(
                ["tail", "-100", log], capture_output=True, text=True, timeout=5
            ).stdout
            conn.execute(
                "INSERT OR REPLACE INTO files(path,size,mtime,mode,category,content) VALUES(?,?,?,?,?,?)",
                (f"__snapshot__/tail-{Path(log).name}", len(tail), SCAN_TS, "0644", "log", tail)
            )
            stats["logs"] += 1
        except Exception:
            pass

    # Check for OOM kills, segfaults
    for row in conn.execute("SELECT content FROM files WHERE category='log'"):
        for line in row[0].splitlines():
            if "oom" in line.lower() or "segfault" in line.lower():
                issues.append(f"log-anomaly: {line.strip()[:120]}")
                break

def scan_mounts():
    """Index fstab and current mounts."""
    for path in [Path("/etc/fstab")]:
        content = read_text(path)
        if content:
            conn.execute(
                "INSERT OR REPLACE INTO files(path,size,mtime,mode,category,content) VALUES(?,?,?,?,?,?)",
                (str(path), len(content), SCAN_TS, "0644", "mounts", content)
            )
    try:
        mounts = subprocess.run(["mount"], capture_output=True, text=True, timeout=5).stdout
        conn.execute(
            "INSERT OR REPLACE INTO files(path,size,mtime,mode,category,content) VALUES(?,?,?,?,?,?)",
            ("__snapshot__/mounts", len(mounts), SCAN_TS, "0644", "mounts", mounts)
        )
    except Exception:
        pass

def scan_network():
    """Network config and state."""
    for path in sorted(Path("/etc/netplan").glob("*.yaml")):
        content = read_text(path)
        if content:
            conn.execute(
                "INSERT OR REPLACE INTO files(path,size,mtime,mode,category,content) VALUES(?,?,?,?,?,?)",
                (str(path), len(content), SCAN_TS, "0644", "network", content)
            )
    for path in [Path("/etc/hosts"), Path("/etc/hostname"), Path("/etc/resolv.conf"),
                 Path("/etc/nsswitch.conf")]:
        content = read_text(path)
        if content:
            conn.execute(
                "INSERT OR REPLACE INTO files(path,size,mtime,mode,category,content) VALUES(?,?,?,?,?,?)",
                (str(path), len(content), SCAN_TS, "0644", "network", content)
            )

def scan_health():
    """Post-scan health diagnostics."""
    # Disk usage
    for mp in ["/", "/home", "/boot"]:
        try:
            st = os.statvfs(mp)
            pct = (1 - st.f_bavail / st.f_blocks) * 100
            if pct > 90:
                issues.append(f"disk-alert: {mp} {pct:.0f}% full")
        except Exception:
            pass

    # Failed services (already captured in scan_systemd)
    # OOM / segfault (already in scan_logs)

scan_health()
print("Scanning /etc ...", flush=True); scan_etc()
print("Scanning systemd ...", flush=True); scan_systemd()
print("Scanning packages ...", flush=True); scan_packages()
print("Scanning logs ...", flush=True); scan_logs()
print("Scanning mounts ...", flush=True); scan_mounts()
print("Scanning network ...", flush=True); scan_network()

scan_health()

# ── FTS rebuild + snapshot record ────────────────────────
conn.execute("DELETE FROM files_fts")
conn.execute("DELETE FROM files_fts_trigram")
conn.execute("INSERT INTO files_fts(rowid, path, category, content) "
             "SELECT id, path, category, COALESCE(content,'') FROM files WHERE content IS NOT NULL")
conn.execute("INSERT INTO files_fts_trigram(rowid, path, category, content) "
             "SELECT id, path, category, COALESCE(content,'') FROM files WHERE content IS NOT NULL")
conn.execute("INSERT INTO files_fts(files_fts) VALUES('optimize')")
conn.execute("INSERT INTO files_fts_trigram(files_fts_trigram) VALUES('optimize')")

total = conn.execute("SELECT count(*), coalesce(sum(size),0) FROM files").fetchone()
conn.execute(
    "INSERT INTO snapshots(ts,total_files,total_size,etc_files,issues_found) VALUES(?,?,?,?,?)",
    (SCAN_TS, total[0], total[1], stats["etc"], json.dumps(issues))
)
conn.commit()

size_mb = Path(DB).stat().st_size / (1024*1024)
print(f"\n=== System Scan Complete ===")
print(f"Files indexed: {total[0]:,} ({total[1]/1024/1024:.1f} MB)")
print(f"  /etc configs:   {stats['etc']}")
print(f"  systemd units:  {stats['systemd']}")
print(f"  packages:       {stats['packages']} lines")
print(f"  log tail:       {stats['logs']} files")
print(f"  binaries:       {stats['binaries']}")
print(f"  failed reads:   {stats['failed']}")
print(f"Issues found:     {len(issues)}")
for i in issues[:10]:
    print(f"  ⚠  {i}")
if len(issues) > 10:
    print(f"  ... and {len(issues)-10} more")
print(f"DB: {str(DB)} ({size_mb:.1f} MB)")
conn.close()
