"""Versioned, persistent SQLite state; never stored in the plugin checkout."""
from __future__ import annotations
import json
import sqlite3
from pathlib import Path
from typing import Any


class Store:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path)
        version = self.db.execute('PRAGMA user_version').fetchone()[0]
        if version > 1:
            self.db.close()
            raise ValueError('数据库由较新版本创建，请勿用旧插件覆盖。')
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('PRAGMA busy_timeout=5000')
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS state (
                game_id TEXT NOT NULL, country TEXT NOT NULL,
                payload TEXT NOT NULL, PRIMARY KEY(game_id,country));
            CREATE TABLE IF NOT EXISTS history (
                game_id TEXT NOT NULL, country TEXT NOT NULL,
                fingerprint TEXT NOT NULL, timestamp TEXT NOT NULL,
                payload TEXT NOT NULL,
                PRIMARY KEY(game_id,country,timestamp));
            CREATE INDEX IF NOT EXISTS history_time ON history(game_id,country,timestamp);
            CREATE TABLE IF NOT EXISTS cache (
                key TEXT PRIMARY KEY, expires REAL NOT NULL, payload TEXT NOT NULL);
            PRAGMA user_version=1;
        ''')
        self.db.commit()

    def get(self, game_id: str, country: str) -> dict[str, Any]:
        row = self.db.execute('SELECT payload FROM state WHERE game_id=? AND country=?',
                              (game_id, country)).fetchone()
        return json.loads(row[0]) if row else {}

    def put(self, game_id: str, country: str, state: dict) -> None:
        with self.db:
            self.db.execute('INSERT OR REPLACE INTO state VALUES(?,?,?)',
                            (game_id, country, json.dumps(state, ensure_ascii=False)))

    def all_states(self) -> list[tuple[str, str, dict]]:
        return [(g, c, json.loads(p)) for g, c, p in self.db.execute('SELECT * FROM state')]

    def merge_history(self, game_id: str, country: str, entries: list[dict]) -> int:
        import hashlib
        old = self.db.total_changes
        with self.db:
            for row in entries:
                serialized = json.dumps(row, sort_keys=True, ensure_ascii=False)
                fingerprint = hashlib.sha256(serialized.encode()).hexdigest()
                self.db.execute('INSERT INTO history VALUES(?,?,?,?,?) ON CONFLICT(game_id,country,timestamp) DO UPDATE SET fingerprint=excluded.fingerprint,payload=excluded.payload WHERE history.fingerprint != excluded.fingerprint',
                    (game_id, country, fingerprint, row['timestamp'], serialized))
        return self.db.total_changes - old

    def history(self, game_id: str, country: str) -> list[dict]:
        return [json.loads(r[0]) for r in self.db.execute(
            'SELECT payload FROM history WHERE game_id=? AND country=? ORDER BY timestamp,fingerprint',
            (game_id, country))]

    def cache_get(self, key: str, now: float) -> Any | None:
        row = self.db.execute('SELECT expires,payload FROM cache WHERE key=?', (key,)).fetchone()
        return json.loads(row[1]) if row and row[0] > now else None

    def cache_put(self, key: str, value: Any, expires: float) -> None:
        with self.db:
            self.db.execute('INSERT OR REPLACE INTO cache VALUES(?,?,?)',
                            (key, expires, json.dumps(value, ensure_ascii=False)))

    def prune_cache(self, now: float, limit: int = 2048) -> None:
        """Bound disposable lookup metadata; never delete price history/state."""
        with self.db:
            self.db.execute('DELETE FROM cache WHERE expires<=?', (now,))
            self.db.execute("DELETE FROM cache WHERE key IN (SELECT key FROM cache WHERE key NOT IN ('daily-run','daily-backoff') ORDER BY expires DESC, key LIMIT -1 OFFSET ?)", (limit,))

    def close(self) -> None:
        self.db.close()
