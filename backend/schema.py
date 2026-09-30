"""The whole data.db schema in one place.

Every table the backend or the WPF client uses is created here, and columns added after a table first shipped are
listed in COLUMNS so older databases are brought up to date. ensure() is idempotent and cheap; the modules that own
a table (tab_scanner, gamification_service, practice_history, performance_evaluator) call it before touching the
database. The WPF client (Services/DatabaseService.cs) mirrors the favorites table and the song_cache identity
columns for databases it opens before any script has run; keep the two in step.
"""
import sqlite3

SCHEMA_VERSION = 1

TABLES = {
    "song_cache": """
        CREATE TABLE IF NOT EXISTS song_cache (
            id TEXT PRIMARY KEY,
            folder_name TEXT,
            title TEXT,
            artist TEXT,
            franchise TEXT,
            gp_path TEXT,
            pdf_path TEXT,
            mtime REAL,
            tempo REAL,
            duration REAL,
            measures INTEGER,
            notes_count INTEGER,
            is_5string INTEGER,
            tuning TEXT,
            has_backing_track INTEGER,
            audio_path TEXT,
            cover_url TEXT,
            level INTEGER,
            level_exact REAL,
            tier TEXT,
            radar_json TEXT,
            tags_json TEXT,
            peak_nps REAL
        )""",
    "favorites": """
        CREATE TABLE IF NOT EXISTS favorites (
            song_id TEXT NOT NULL,
            slot INTEGER NOT NULL,
            PRIMARY KEY (song_id, slot)
        )""",
    "badges": """
        CREATE TABLE IF NOT EXISTS badges (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            rarity TEXT NOT NULL,
            description TEXT,
            franchise TEXT
        )""",
    "user_badges": """
        CREATE TABLE IF NOT EXISTS user_badges (
            badge_id TEXT PRIMARY KEY,
            unlocked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            is_equipped INTEGER DEFAULT 0
        )""",
    "song_mastery": """
        CREATE TABLE IF NOT EXISTS song_mastery (
            song_id TEXT PRIMARY KEY,
            play_count INTEGER DEFAULT 0,
            best_score INTEGER DEFAULT 0,
            mastery_level TEXT DEFAULT '初见',
            last_practiced_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )""",
    "practice_sessions": """
        CREATE TABLE IF NOT EXISTS practice_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            song_id TEXT,
            song_title TEXT,
            artist TEXT,
            start_time TEXT,
            end_time TEXT,
            duration_seconds REAL,
            notes TEXT
        )""",
    # best full-song take per song
    "performance_scores": """
        CREATE TABLE IF NOT EXISTS performance_scores (
            song_id TEXT PRIMARY KEY,
            overall_score REAL,
            grade TEXT,
            timing_score REAL,
            dynamics_score REAL,
            articulation_score REAL,
            tone_score REAL,
            coach_comment TEXT,
            evaluated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )""",
    # every take
    "performance_takes": """
        CREATE TABLE IF NOT EXISTS performance_takes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            song_id TEXT NOT NULL,
            evaluated_at TEXT,
            overall_score REAL,
            grade TEXT,
            combo_badge TEXT,
            notes_total INTEGER,
            coverage REAL,
            range_start INTEGER,
            range_end INTEGER,
            range_label TEXT,
            rate REAL,
            complete INTEGER,
            new_best INTEGER,
            report_path TEXT,
            audio_path TEXT,
            sections_json TEXT
        )""",
}

# columns added after the table first shipped: table -> {column: declaration}
COLUMNS = {
    "song_cache": {"version": "TEXT DEFAULT ''", "group_key": "TEXT DEFAULT ''", "alt_gp_path": "TEXT DEFAULT ''"},
    "performance_scores": {"combo_badge": "TEXT", "heatmap_json": "TEXT", "pitch_score": "REAL",
                           "complete_score": "REAL", "clean_score": "REAL", "judgments_json": "TEXT"},
}

_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_takes_song ON performance_takes (song_id)",
    "CREATE INDEX IF NOT EXISTS idx_sessions_song ON practice_sessions (song_id)",
]


def ensure(conn: sqlite3.Connection) -> None:
    """Creates missing tables, adds missing columns, records the schema version."""
    cur = conn.cursor()
    for sql in TABLES.values():
        cur.execute(sql)
    for table, cols in COLUMNS.items():
        have = {r[1] for r in cur.execute(f"PRAGMA table_info({table})")}
        for col, decl in cols.items():
            if col not in have:
                cur.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")
    for sql in _INDEXES:
        cur.execute(sql)
    if cur.execute("PRAGMA user_version").fetchone()[0] < SCHEMA_VERSION:
        cur.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    conn.commit()
