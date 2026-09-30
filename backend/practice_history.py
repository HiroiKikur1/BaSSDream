import os
import paths
import sqlite3
import datetime
from typing import Dict, Any, List

DB_PATH = paths.DB

def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    conn.execute("PRAGMA journal_mode=WAL;")
    return conn

def init_history_db():
    import schema
    conn = get_db()
    schema.ensure(conn)
    conn.close()

# Ensure table exists on import
init_history_db()

def record_session(song_id: str, song_title: str, artist: str, duration_seconds: float, start_time: str = None) -> int:
    conn = get_db()
    cur = conn.cursor()
    now_iso = datetime.datetime.now().isoformat()
    if not start_time:
        start_time = (datetime.datetime.now() - datetime.timedelta(seconds=duration_seconds)).isoformat()

    cur.execute('''
        INSERT INTO practice_sessions (song_id, song_title, artist, start_time, end_time, duration_seconds, notes)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    ''', (song_id, song_title, artist, start_time, now_iso, round(duration_seconds, 1), ""))
    new_id = cur.lastrowid
    conn.commit()
    conn.close()
    return new_id

def get_heatmap_data() -> Dict[str, Any]:
    """Returns practice minutes aggregated by date 'YYYY-MM-DD' for calendar heatmap."""
    conn = get_db()
    cur = conn.cursor()
    # Extract date YYYY-MM-DD from start_time
    cur.execute('''
        SELECT substr(start_time, 1, 10) as day, SUM(duration_seconds) as total_sec, COUNT(*) as sessions
        FROM practice_sessions
        GROUP BY day
        ORDER BY day ASC
    ''')
    rows = cur.fetchall()
    conn.close()

    result = {}
    for r in rows:
        day_str = r[0]
        mins = round(r[1] / 60.0, 1) if r[1] else 0.0
        result[day_str] = {
            "minutes": mins,
            "seconds": r[1],
            "sessions": r[2]
        }
    return result

def get_practice_stats() -> Dict[str, Any]:
    """Calculates summary statistics: total time, sessions count, streak, top songs."""
    conn = get_db()
    cur = conn.cursor()

    cur.execute("SELECT SUM(duration_seconds), COUNT(*) FROM practice_sessions")
    row = cur.fetchone()
    total_seconds = row[0] or 0.0
    total_sessions = row[1] or 0

    today_str = datetime.date.today().isoformat()
    cur.execute("SELECT SUM(duration_seconds) FROM practice_sessions WHERE start_time LIKE ?", (today_str + "%",))
    today_seconds = cur.fetchone()[0] or 0.0

    # Top songs
    cur.execute('''
        SELECT song_title, artist, SUM(duration_seconds) as total_time, COUNT(*) as play_count
        FROM practice_sessions
        GROUP BY song_title, artist
        ORDER BY total_time DESC
        LIMIT 5
    ''')
    top_songs = [{"title": r[0], "artist": r[1], "minutes": round(r[2]/60.0, 1), "plays": r[3]} for r in cur.fetchall()]

    # Streak calculation
    cur.execute("SELECT DISTINCT substr(start_time, 1, 10) FROM practice_sessions ORDER BY start_time DESC")
    dates = [datetime.date.fromisoformat(r[0]) for r in cur.fetchall() if r[0]]
    streak = 0
    today = datetime.date.today()
    check_date = today
    if dates:
        if dates[0] == today or dates[0] == today - datetime.timedelta(days=1):
            check_date = dates[0]
            for d in dates:
                if d == check_date:
                    streak += 1
                    check_date -= datetime.timedelta(days=1)
                else:
                    break

    conn.close()
    return {
        "total_hours": round(total_seconds / 3600.0, 1),
        "total_minutes": round(total_seconds / 60.0, 1),
        "total_sessions": total_sessions,
        "today_minutes": round(today_seconds / 60.0, 1),
        "streak_days": streak,
        "top_songs": top_songs
    }

def get_recent_sessions(limit: int = 20) -> List[Dict[str, Any]]:
    conn = get_db()
    cur = conn.cursor()
    cur.execute('''
        SELECT id, song_id, song_title, artist, start_time, end_time, duration_seconds
        FROM practice_sessions
        ORDER BY id DESC
        LIMIT ?
    ''', (limit,))
    rows = cur.fetchall()
    conn.close()
    return [{
        "id": r[0],
        "song_id": r[1],
        "song_title": r[2],
        "artist": r[3],
        "start_time": r[4],
        "end_time": r[5],
        "duration_minutes": round(r[6] / 60.0, 1),
        "duration_seconds": r[6]
    } for r in rows]

if __name__ == "__main__":
    init_history_db()
    print("History DB initialized.")
