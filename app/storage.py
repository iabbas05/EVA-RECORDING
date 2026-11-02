"""Small SQLite persistence layer. No EVA password or session cookie is stored."""

from __future__ import annotations

import json
import hashlib
import re
from datetime import date, time
import sqlite3
from contextlib import contextmanager
from pathlib import Path


class Store:
    def __init__(self, path: str = "data/eva_recording_manager.sqlite3"):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def initialize(self):
        with self.connect() as db:
            db.executescript("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS recordings (
                id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                branch TEXT NOT NULL,
                arena_id TEXT NOT NULL,
                session_date TEXT NOT NULL,
                start_time TEXT NOT NULL,
                end_time TEXT NOT NULL,
                official_session_id TEXT,
                status TEXT NOT NULL,
                output_path TEXT,
                error TEXT
            );
            CREATE TABLE IF NOT EXISTS bookings (
                booking_id TEXT PRIMARY KEY,
                order_id TEXT,
                branch TEXT NOT NULL,
                arena_id TEXT NOT NULL,
                session_date TEXT NOT NULL,
                start_time TEXT NOT NULL,
                end_time TEXT NOT NULL,
                official_session_id TEXT,
                status TEXT NOT NULL,
                owner_user_id TEXT,
                attendee_user_ids_json TEXT NOT NULL DEFAULT '[]'
            );
            CREATE TABLE IF NOT EXISTS linked_users (
                user_id TEXT PRIMARY KEY,
                username TEXT,
                name TEXT,
                email TEXT
            );
            CREATE TABLE IF NOT EXISTS recording_booking_links (
                recording_id TEXT NOT NULL,
                booking_id TEXT NOT NULL,
                PRIMARY KEY(recording_id, booking_id)
            );
            CREATE TABLE IF NOT EXISTS delivery_ledger (
                video_sha256 TEXT NOT NULL,
                recipient_email_norm TEXT NOT NULL,
                status TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                message_id TEXT,
                PRIMARY KEY(video_sha256, recipient_email_norm)
            );
            CREATE TABLE IF NOT EXISTS video_assets (
                video_sha256 TEXT PRIMARY KEY,
                local_path TEXT NOT NULL,
                drive_file_id TEXT NOT NULL,
                drive_url TEXT NOT NULL
            );
            """)
            columns = {row[1] for row in db.execute("PRAGMA table_info(delivery_ledger)")}
            if "message_id" not in columns:
                db.execute("ALTER TABLE delivery_ledger ADD COLUMN message_id TEXT")
            for table, additions in {
                'recordings': {'camera_path': 'TEXT', 'started_at': 'TEXT', 'stopped_at': 'TEXT',
                               'game': 'TEXT', 'capture_json': 'TEXT', 'assigned': 'INTEGER NOT NULL DEFAULT 1',
                               'auto_send': 'INTEGER NOT NULL DEFAULT 0', 'recipients_json': "TEXT NOT NULL DEFAULT '[]'",
                               'auto_state': 'TEXT', 'auto_retry_at': 'REAL NOT NULL DEFAULT 0', 'files_removed_at': 'TEXT'},
                'bookings': {'game': 'TEXT'},
            }.items():
                columns = {row[1] for row in db.execute(f'PRAGMA table_info({table})')}
                for name, kind in additions.items():
                    if name not in columns:
                        db.execute(f'ALTER TABLE {table} ADD COLUMN {name} {kind}')
            db.execute('''CREATE TABLE IF NOT EXISTS delivery_jobs (
                recording_id TEXT PRIMARY KEY, payload TEXT NOT NULL, state TEXT NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 0, next_attempt REAL NOT NULL DEFAULT 0,
                error TEXT, updated_at REAL NOT NULL)''')
            db.execute('''CREATE TABLE IF NOT EXISTS session_recipient_drafts (
                session_key TEXT PRIMARY KEY, recipients_json TEXT NOT NULL)''')

    @staticmethod
    def session_key(session):
        day = date.fromisoformat(str(session.get('session_date') or '')).isoformat()
        start = time.fromisoformat(str(session.get('start_time') or '')).isoformat()
        end = time.fromisoformat(str(session.get('end_time') or '')).isoformat()
        identity = [day, start, end, str(session.get('game') or '').strip().casefold(),
                    str(session.get('official_session_id') or ''), 'Bahrain', '81']
        return hashlib.sha256(json.dumps(identity).encode()).hexdigest()

    def session_recipients(self, session):
        with self.connect() as db:
            row = db.execute('SELECT recipients_json FROM session_recipient_drafts WHERE session_key=?',
                             (self.session_key(session),)).fetchone()
            return json.loads(row[0]) if row else []

    def save_session_recipients(self, session, recipients):
        if not isinstance(recipients, list) or len(recipients) > 100:
            raise ValueError('Use up to 100 additional recipient emails')
        clean = {}
        for item in recipients:
            if not isinstance(item, dict):
                raise ValueError('Enter a valid recipient')
            email = str(item.get('email') or '').strip().casefold()
            if len(email) > 254 or not re.fullmatch(r'[A-Za-z0-9.!#$%&\x27*+/=?^_`{|}~-]+@[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?)+', email):
                raise ValueError('Enter a valid email address')
            clean[email] = {'email': email, 'name': str(item.get('name') or '').strip()[:120],
                            'roles': ['Added'], 'booking_ids': []}
        result = list(clean.values())
        with self.connect() as db:
            db.execute('INSERT INTO session_recipient_drafts VALUES (?,?) ON CONFLICT(session_key) '
                       'DO UPDATE SET recipients_json=excluded.recipients_json',
                       (self.session_key(session), json.dumps(result)))
        return result

    def create_recording(self, row: dict):
        with self.connect() as db:
            db.execute("""INSERT INTO recordings
                (id, created_at, branch, arena_id, session_date, start_time, end_time,
                 official_session_id, status, output_path, error)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (row["id"], row["created_at"], row["branch"], row["arena_id"], row["session_date"],
                 row["start_time"], row["end_time"], row.get("official_session_id"), row["status"],
                 row.get("output_path"), row.get("error")))
        return row

    def update_recording(self, recording_id: str, **updates):
        if set(updates) - {'status', 'output_path', 'camera_path', 'error', 'started_at', 'stopped_at', 'game', 'capture_json',
                           'session_date','start_time','end_time','official_session_id','assigned','auto_send',
                           'recipients_json','auto_state','auto_retry_at','files_removed_at'}:
            raise ValueError('Invalid recording field')
        if not updates:
            return
        columns = ", ".join(f"{name} = ?" for name in updates)
        with self.connect() as db:
            db.execute(f"UPDATE recordings SET {columns} WHERE id = ?", (*updates.values(), recording_id))

    def get_recording(self, recording_id: str):
        with self.connect() as db:
            row = db.execute("SELECT * FROM recordings WHERE id = ?", (recording_id,)).fetchone()
            return dict(row) if row else None

    def recent_recordings(self, limit: int = 30):
        with self.connect() as db:
            return [dict(x) for x in db.execute("SELECT * FROM recordings ORDER BY created_at DESC LIMIT ?", (limit,))]

    def automatic_recordings(self, states):
        if not states:
            return []
        with self.connect() as db:
            placeholders=','.join('?' for _ in states)
            return [dict(x) for x in db.execute(f'SELECT * FROM recordings WHERE auto_state IN ({placeholders}) ORDER BY created_at',tuple(states))]

    def import_selected_booking_data(self, bookings: list[dict], users: list[dict], recording_id: str | None = None):
        """Import a deliberately scoped payload: caller supplies selected bookings and their linked users only."""
        with self.connect() as db:
            for user in users:
                db.execute("INSERT INTO linked_users(user_id, username, name, email) VALUES (?, ?, ?, ?) "
                           "ON CONFLICT(user_id) DO UPDATE SET username=excluded.username, name=excluded.name, email=excluded.email",
                           (str(user["user_id"]), user.get("username", ""), user.get("name", ""), user.get("email", "")))
            for booking in bookings:
                db.execute("""INSERT INTO bookings
                    (booking_id, order_id, branch, arena_id, session_date, start_time, end_time,
                     official_session_id, status, owner_user_id, attendee_user_ids_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(booking_id) DO UPDATE SET order_id=excluded.order_id, branch=excluded.branch,
                     arena_id=excluded.arena_id, session_date=excluded.session_date, start_time=excluded.start_time,
                     end_time=excluded.end_time, official_session_id=excluded.official_session_id,
                     status=excluded.status, owner_user_id=excluded.owner_user_id,
                     attendee_user_ids_json=excluded.attendee_user_ids_json""",
                    (str(booking["booking_id"]), booking.get("order_id"), booking.get("branch", "Bahrain"),
                     str(booking["arena_id"]), booking["session_date"], booking["start_time"], booking["end_time"],
                     booking.get("official_session_id"), booking.get("status", "active"), booking.get("owner_user_id"),
                     json.dumps([str(x) for x in booking.get("attendee_user_ids", [])])))
                db.execute('UPDATE bookings SET game=? WHERE booking_id=?',
                           (str(booking.get('game') or ''), str(booking['booking_id'])))
            if recording_id:
                db.execute("DELETE FROM recording_booking_links WHERE recording_id = ?", (recording_id,))
                db.executemany("INSERT INTO recording_booking_links(recording_id, booking_id) VALUES (?, ?)",
                               [(recording_id, str(booking["booking_id"])) for booking in bookings])

    def matched_data(self, recording_id: str | None = None):
        with self.connect() as db:
            bookings = []
            query = "SELECT * FROM bookings"
            args = ()
            if recording_id:
                linked = db.execute("SELECT COUNT(*) FROM recording_booking_links WHERE recording_id = ?",
                                    (recording_id,)).fetchone()[0]
                if linked:
                    query = ("SELECT b.* FROM bookings b JOIN recording_booking_links l "
                             "ON l.booking_id=b.booking_id WHERE l.recording_id=?")
                    args = (recording_id,)
            for row in db.execute(query, args):
                item = dict(row)
                item["attendee_user_ids"] = json.loads(item.pop("attendee_user_ids_json"))
                bookings.append(item)
            users = {x["user_id"]: dict(x) for x in db.execute("SELECT * FROM linked_users")}
            return bookings, users

    def reserve_delivery(self, video_sha256: str, recipient_email: str, status: str, updated_at: str) -> bool:
        """Reserve a video/recipient pair. Only a known failed attempt can be retried."""
        with self.connect() as db:
            digest = video_sha256.lower().strip()
            email = recipient_email.strip().casefold()
            cur = db.execute("INSERT OR IGNORE INTO delivery_ledger "
                             "(video_sha256, recipient_email_norm, status, updated_at) VALUES (?, ?, ?, ?)",
                             (digest, email, status, updated_at))
            if cur.rowcount == 1:
                return True
            cur = db.execute("UPDATE delivery_ledger SET status=?, updated_at=?, message_id=NULL "
                             "WHERE video_sha256=? AND recipient_email_norm=? AND status='failed'",
                             (status, updated_at, digest, email))
            return cur.rowcount == 1

    def update_delivery(self, video_sha256: str, recipient_email: str, status: str, message_id: str | None = None):
        from datetime import datetime, timezone
        with self.connect() as db:
            db.execute("UPDATE delivery_ledger SET status=?, updated_at=?, message_id=COALESCE(?, message_id) "
                       "WHERE video_sha256=? AND recipient_email_norm=?",
                       (status, datetime.now(timezone.utc).isoformat(timespec="seconds"), message_id,
                        video_sha256.lower().strip(), recipient_email.strip().casefold()))

    def get_delivery(self, video_sha256: str, recipient_email: str):
        with self.connect() as db:
            row = db.execute("SELECT * FROM delivery_ledger WHERE video_sha256=? AND recipient_email_norm=?",
                             (video_sha256.lower().strip(), recipient_email.strip().casefold())).fetchone()
            return dict(row) if row else None

    def get_video_asset(self, video_sha256: str):
        with self.connect() as db:
            row = db.execute("SELECT * FROM video_assets WHERE video_sha256=?", (video_sha256,)).fetchone()
            return dict(row) if row else None

    def save_video_asset(self, video_sha256: str, local_path: str, drive_file_id: str, drive_url: str):
        with self.connect() as db:
            db.execute("INSERT INTO video_assets VALUES (?, ?, ?, ?) ON CONFLICT(video_sha256) DO UPDATE SET "
                       "local_path=excluded.local_path, drive_file_id=excluded.drive_file_id, drive_url=excluded.drive_url",
                       (video_sha256, local_path, drive_file_id, drive_url))

    def healthcheck(self) -> str:
        with self.connect() as db:
            return str(db.execute("PRAGMA quick_check").fetchone()[0])

    def unfinished_recordings(self):
        with self.connect() as db:
            return [dict(r) for r in db.execute("SELECT * FROM recordings WHERE status IN ('starting','recording','stop_failed','interrupted') ORDER BY created_at")]

    def enqueue(self, recording_id, payload):
        import time
        with self.connect() as db:
            db.execute("INSERT OR IGNORE INTO delivery_jobs(recording_id,payload,state,updated_at) VALUES (?,?,'queued',?)",
                       (recording_id, json.dumps(payload), time.time()))

    def jobs(self):
        with self.connect() as db:
            return [dict(r) for r in db.execute('SELECT * FROM delivery_jobs ORDER BY updated_at DESC')]

    def job_update(self, recording_id, **updates):
        import time
        if set(updates) - {'state', 'attempts', 'next_attempt', 'error'}:
            raise ValueError('Invalid queue field')
        updates['updated_at'] = time.time()
        with self.connect() as db:
            db.execute('UPDATE delivery_jobs SET ' + ', '.join(f'{k}=?' for k in updates) + ' WHERE recording_id=?',
                       (*updates.values(), recording_id))

    def backup(self, target):
        with self.connect() as source:
            dest = sqlite3.connect(target)
            try:
                source.backup(dest)
            finally:
                dest.close()
