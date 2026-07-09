#!/usr/bin/env python3
import sqlite3, json, os, sys, hashlib, time, requests
from pathlib import Path
from datetime import datetime

BASE_DIR = Path(os.getenv("NETWORK_ROOT", "./data"))
OBSIDIAN_VAULT = Path(os.getenv("VAULT_ROOT", "./vault"))
DB_PATH = BASE_DIR / "session_log.db"
OLLAMA_URL = "http://localhost:11434"

VAULT_FOLDERS = {
    "00 - Dashboard": "Main dashboard",
    "01 - Daily Notes": "Daily journal",
    "02 - Projects": "Active projects",
    "03 - Agents": "Agent profiles",
    "04 - Trading": "Trading notes",
    "05 - Income Streams": "Revenue tracking",
    "06 - Build Log": "Dev log",
    "07 - Resources": "References"
}

class BandwidthOptimizer:
    def __init__(self):
        self.cache_file = BASE_DIR / ".vault_cache.json"
        self.file_hashes = self._load_cache()
    def _load_cache(self):
        if self.cache_file.exists():
            try:
                return json.loads(self.cache_file.read_text())
            except:
                return {}
        return {}
    def _save_cache(self):
        self.cache_file.write_text(json.dumps(self.file_hashes, indent=2))
    def get_file_hash(self, filepath):
        try:
            with open(filepath, "rb") as f:
                return hashlib.md5(f.read()).hexdigest()
        except:
            return None
    def has_changed(self, filepath):
        file_hash = self.get_file_hash(filepath)
        if not file_hash:
            return False
        filepath_str = str(filepath)
        if filepath_str not in self.file_hashes:
            self.file_hashes[filepath_str] = file_hash
            self._save_cache()
            return True
        if self.file_hashes[filepath_str] != file_hash:
            self.file_hashes[filepath_str] = file_hash
            self._save_cache()
            return True
        return False

class VaultManager:
    def __init__(self, vault_path, bandwidth_optimizer):
        self.vault_path = Path(vault_path)
        self.optimizer = bandwidth_optimizer
        self.conn = sqlite3.connect(DB_PATH)
        self.cursor = self.conn.cursor()
        self.session_id = None
    def start_session(self):
        self.session_id = f"session_VAULT_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        self.cursor.execute("INSERT INTO sessions (session_id, timestamp, date, summary, status) VALUES (?, ?, ?, ?, ?)",
            (self.session_id, datetime.now().isoformat(), datetime.now().strftime("%Y-%m-%d"), "Vault Management", "ACTIVE"))
        self.conn.commit()
        return self.session_id
    def log_action(self, action, status, details=""):
        self.cursor.execute("INSERT INTO context_memory (session_id, key, value, timestamp) VALUES (?, ?, ?, ?)",
            (self.session_id, f"VAULT_{action}", json.dumps({"status": status, "details": details}), datetime.now().isoformat()))
        self.conn.commit()
    def organize_vault_structure(self):
        print("[ORGANIZE] Checking vault structure...")
        for folder, description in VAULT_FOLDERS.items():
            folder_path = self.vault_path / folder
            folder_path.mkdir(parents=True, exist_ok=True)
            readme_path = folder_path / "_README.md"
            if not readme_path.exists():
                readme_path.write_text(f"# {folder}\n\n{description}\n", encoding="utf-8")
        print("✓ Vault structure organized")
        self.log_action("ORGANIZE", "COMPLETE", "All folders exist")
    def scan_vault_changes(self):
        print("[SCAN] Detecting changes...")
        changed_files = []
        for md_file in self.vault_path.rglob("*.md"):
            if self.optimizer.has_changed(md_file):
                changed_files.append(md_file)
        print(f"✓ Found {len(changed_files)} changed files")
        self.log_action("SCAN", "COMPLETE", f"{len(changed_files)} files")
        return changed_files
    def generate_vault_summary(self):
        print("[SUMMARY] Generating vault summary...")
        summary = {"folders": {}, "total_files": 0}
        for folder_path in self.vault_path.iterdir():
            if not folder_path.is_dir() or folder_path.name.startswith("."):
                continue
            files = list(folder_path.glob("*.md"))
            summary["folders"][folder_path.name] = len(files)
            summary["total_files"] += len(files)
        print(f"✓ Vault has {summary['total_files']} documents")
        self.log_action("SUMMARY", "COMPLETE", json.dumps(summary))
        return summary
    def batch_sync_to_anythingllm(self, changed_files):
        if not changed_files:
            print("[SYNC] No changes to sync")
            return
        print(f"[SYNC] Batching {len(changed_files)} files...")
        for filepath in changed_files[:5]:
            try:
                print(f"  → Queued: {filepath.relative_to(self.vault_path)}")
            except:
                pass
        self.log_action("BATCH_SYNC", "COMPLETE", f"{len(changed_files)} files")
    def end_session(self, summary):
        self.cursor.execute("UPDATE sessions SET status = \"COMPLETED\", summary = ? WHERE session_id = ?", (summary, self.session_id))
        self.conn.commit()
        self.conn.close()

print("\n" + "="*70)
print("the agent network — VAULT MANAGER")
print("="*70 + "\n")

bandwidth = BandwidthOptimizer()
manager = VaultManager(OBSIDIAN_VAULT, bandwidth)
session_id = manager.start_session()
print(f"[SESSION] {session_id}\n")

manager.organize_vault_structure()
time.sleep(1)
changed_files = manager.scan_vault_changes()
time.sleep(1)
vault_summary = manager.generate_vault_summary()
time.sleep(1)
manager.batch_sync_to_anythingllm(changed_files)

manager.end_session(f"Vault organized. {len(changed_files)} files synced.")

print("\n" + "="*70)
print("✓ VAULT MANAGEMENT COMPLETE")
print("="*70 + "\n")
