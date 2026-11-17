"""Local observations only. SSH passwords and private keys are never stored."""
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


class Store:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / "assistant.db"
        with self._connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS snapshots (id INTEGER PRIMARY KEY, host TEXT, at TEXT, data TEXT)")
            db.execute("CREATE TABLE IF NOT EXISTS uefi_tests (id TEXT PRIMARY KEY, host TEXT NOT NULL, at REAL NOT NULL, data TEXT NOT NULL)")

    def _connect(self):
        return sqlite3.connect(self.path)

    def save_uefi_test(self, record):
        # Records are node observations / approvals, never SSH configuration.
        if record.get('action') != 'uefi_test':
            raise ValueError('Not a UEFI test')
        with self._connect() as db:
            previous = db.execute('SELECT data FROM uefi_tests WHERE id=?', (record['id'],)).fetchone()
            history = json.loads(previous[0]).get('connection_events', []) if previous else []
            if record.get('connection_error') and (not history or history[-1]['error'] != record['connection_error']):
                history.append({'at': datetime.now(timezone.utc).isoformat(), 'error': record['connection_error']})
            record = {**record, 'connection_events': history}
            db.execute('INSERT OR REPLACE INTO uefi_tests VALUES (?,?,?,?)',
                       (record['id'], record['host_id'], record.get('created_at', 0), json.dumps(record, ensure_ascii=False)))

    def uefi_tests(self, host_id):
        with self._connect() as db:
            rows = db.execute('SELECT data FROM uefi_tests WHERE host=? ORDER BY at', (host_id,)).fetchall()
        return [json.loads(r[0]) for r in rows]

    def get(self, key, default=None):
        with self._connect() as db:
            row = db.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set(self, key, value):
        with self._connect() as db:
            db.execute("INSERT OR REPLACE INTO kv VALUES (?,?)", (key, json.dumps(value, ensure_ascii=False)))

    def snapshot(self, snapshot):
        from .core import disk_for_member, leaves
        retention = max(10, min(10000, int(self.get('retention', 100))))
        with self._connect() as db:
            db.execute("INSERT INTO snapshots(host,at,data) VALUES (?,?,?)",
                       (snapshot["host_id"], datetime.now(timezone.utc).isoformat(), json.dumps(snapshot)))
            db.execute("DELETE FROM snapshots WHERE id NOT IN (SELECT id FROM snapshots ORDER BY id DESC LIMIT ?)", (retention,))
            for pool in snapshot["pools"]:
                for member in leaves(pool):
                    disk = disk_for_member(snapshot, member)
                    if disk and member.get("guid"):
                        key = "member:" + snapshot["host_id"] + ":" + pool["guid"] + ":" + member["guid"]
                        db.execute("INSERT OR REPLACE INTO kv VALUES (?,?)", (key, json.dumps(disk)))

    def latest(self, host_id):
        with self._connect() as db:
            row = db.execute("SELECT data FROM snapshots WHERE host=? ORDER BY id DESC LIMIT 1", (host_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def position(self, host_id, serial):
        return self.get("position:" + host_id + ":" + serial, "")

    def set_position(self, host_id, serial, position):
        self.set("position:" + host_id + ":" + serial, position)

    def member_identity(self, host_id, pool_guid, member_guid):
        return self.get("member:" + host_id + ":" + pool_guid + ":" + member_guid)

    def historical_disk(self, host_id, path):
        from .core import disk_for
        with self._connect() as db:
            rows = db.execute("SELECT data FROM snapshots WHERE host=? ORDER BY id DESC", (host_id,)).fetchall()
        for row in rows:
            disk = disk_for(json.loads(row[0]), path)
            if disk:
                return disk
        return None

