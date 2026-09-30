import os
import sqlite3
from typing import Dict, Any, List, Optional
from datetime import datetime, timedelta

DB_PATH = r"E:\BassStation\backend\data.db"

DEFAULT_BADGES = [
    {
        "id": "title_first_clear",
        "name": "初登舞台",
        "rarity": "silver",
        "description": "完成任意 1 首贝斯曲目的完整练习与评测",
        "franchise": "BanG Dream!"
    },
    {
        "id": "title_full_combo",
        "name": "FULL COMBO 达成者",
        "rarity": "gold",
        "description": "在单曲评测中达成 0 漏音 (FULL COMBO)",
        "franchise": "BanG Dream!"
    },
    {
        "id": "title_all_perfect",
        "name": "ALL PERFECT 极",
        "rarity": "rainbow",
        "description": "在单曲评测中斩获 98% 以上绝对精准度 (ALL PERFECT)",
        "franchise": "BanG Dream!"
    },
    {
        "id": "title_mygo_master",
        "name": "迷星叫 极",
        "rarity": "rainbow",
        "description": "攻克 MyGO!!!!! 核心曲目且评级达 S 级以上",
        "franchise": "MyGO!!!!!"
    },
    {
        "id": "title_roselia_speed",
        "name": "顶点的狂咲",
        "rarity": "rainbow",
        "description": "攻克 Roselia 高速曲目 (BPM >= 180) 且综合得分达 90 分",
        "franchise": "Roselia"
    },
    {
        "id": "title_5string_pioneer",
        "name": "五弦开拓者",
        "rarity": "gold",
        "description": "完成 5 弦 Low-B 贝斯专属曲目的实弹练习",
        "franchise": "BanG Dream!"
    },
    {
        "id": "title_slap_virtuoso",
        "name": "爆裂 Slap 狂魔",
        "rarity": "gold",
        "description": "攻克包含击勾弦 (Slap & Pop) 技巧的进阶曲目",
        "franchise": "BanG Dream!"
    },
    {
        "id": "title_streak_7",
        "name": "七日同调",
        "rarity": "gold",
        "description": "连续 7 天坚持练琴打卡不间断",
        "franchise": "BanG Dream!"
    },
    {
        "id": "title_century_club",
        "name": "百战琴匠",
        "rarity": "rainbow",
        "description": "累计练琴次数突破 100 次",
        "franchise": "BanG Dream!"
    },
    {
        "id": "title_tone_alchemist",
        "name": "音色炼金师",
        "rarity": "silver",
        "description": "单曲音色质感与低频干净度得分突破 95 分",
        "franchise": "BanG Dream!"
    }
]

def init_gamification_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute('''
        CREATE TABLE IF NOT EXISTS badges (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            rarity TEXT NOT NULL,
            description TEXT,
            franchise TEXT
        )
    ''')
    cur.execute('''
        CREATE TABLE IF NOT EXISTS user_badges (
            badge_id TEXT PRIMARY KEY,
            unlocked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            is_equipped INTEGER DEFAULT 0
        )
    ''')
    cur.execute('''
        CREATE TABLE IF NOT EXISTS song_mastery (
            song_id TEXT PRIMARY KEY,
            play_count INTEGER DEFAULT 0,
            best_score INTEGER DEFAULT 0,
            mastery_level TEXT DEFAULT '初见',
            last_practiced_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Insert default badges if missing
    for b in DEFAULT_BADGES:
        cur.execute('''
            INSERT OR IGNORE INTO badges (id, name, rarity, description, franchise)
            VALUES (?, ?, ?, ?, ?)
        ''', (b["id"], b["name"], b["rarity"], b["description"], b["franchise"]))

    # Default equip first badge if user has any unlocked
    conn.commit()
    conn.close()

def get_user_badges() -> Dict[str, Any]:
    """Retrieves all badges, unlocked status, and currently equipped badge."""
    init_gamification_db()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    cur.execute('''
        SELECT b.id, b.name, b.rarity, b.description, b.franchise,
               ub.unlocked_at, ub.is_equipped
        FROM badges b
        LEFT JOIN user_badges ub ON b.id = ub.badge_id
        ORDER BY 
            CASE b.rarity WHEN 'rainbow' THEN 1 WHEN 'gold' THEN 2 ELSE 3 END,
            ub.unlocked_at DESC
    ''')
    rows = cur.fetchall()
    conn.close()

    badges = []
    equipped = None
    for r in rows:
        is_unlocked = r["unlocked_at"] is not None
        item = {
            "id": r["id"],
            "name": r["name"],
            "rarity": r["rarity"],
            "description": r["description"],
            "franchise": r["franchise"],
            "is_unlocked": is_unlocked,
            "unlocked_at": r["unlocked_at"],
            "is_equipped": bool(r["is_equipped"])
        }
        badges.append(item)
        if item["is_equipped"] and is_unlocked:
            equipped = item

    # If no equipped, fallback to first unlocked or placeholder
    if not equipped:
        for b in badges:
            if b["is_unlocked"]:
                equipped = b
                break

    return {
        "badges": badges,
        "equipped": equipped or {
            "id": "title_novice",
            "name": "新人贝斯手",
            "rarity": "silver",
            "description": "开启你的少女乐团贝斯修行旅程",
            "is_unlocked": True,
            "is_equipped": True
        }
    }

def equip_badge(badge_id: str) -> bool:
    """Equips an unlocked badge."""
    init_gamification_db()
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    # Verify user owns it
    cur.execute("SELECT badge_id FROM user_badges WHERE badge_id = ?", (badge_id,))
    if not cur.fetchone():
        conn.close()
        return False

    cur.execute("UPDATE user_badges SET is_equipped = 0")
    cur.execute("UPDATE user_badges SET is_equipped = 1 WHERE badge_id = ?", (badge_id,))
    conn.commit()
    conn.close()
    return True

def unlock_badge(badge_id: str) -> bool:
    """Unlocks badge for user."""
    init_gamification_db()
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute('''
        INSERT OR IGNORE INTO user_badges (badge_id, is_equipped)
        VALUES (?, 0)
    ''', (badge_id,))
    changed = cur.rowcount > 0
    conn.commit()
    conn.close()
    return changed

def update_song_mastery(song_id: str, score: int) -> Dict[str, Any]:
    """Updates play count, best score, and calculates mastery level."""
    init_gamification_db()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    cur.execute("SELECT * FROM song_mastery WHERE song_id = ?", (song_id,))
    row = cur.fetchone()

    now = datetime.now().isoformat()
    if row:
        play_cnt = row["play_count"] + 1
        best = max(row["best_score"], score)
    else:
        play_cnt = 1
        best = score

    # Calculate mastery: 初见 (1) -> 视奏 (2-3) -> 熟练 (4-9 或 >=85分) -> 精通 (>=10次且>=90分)
    if play_cnt >= 10 and best >= 90:
        level = "精通"
    elif play_cnt >= 4 or best >= 85:
        level = "熟练"
    elif play_cnt >= 2 or best >= 75:
        level = "视奏"
    else:
        level = "初见"

    cur.execute('''
        INSERT OR REPLACE INTO song_mastery (song_id, play_count, best_score, mastery_level, last_practiced_at)
        VALUES (?, ?, ?, ?, ?)
    ''', (song_id, play_cnt, best, level, now))
    conn.commit()
    conn.close()

    # Trigger automatic badge checks
    newly_unlocked = []
    if unlock_badge("title_first_clear"):
        newly_unlocked.append("初登舞台")

    if best >= 95 and unlock_badge("title_tone_alchemist"):
        newly_unlocked.append("音色炼金师")

    return {
        "song_id": song_id,
        "play_count": play_cnt,
        "best_score": best,
        "mastery_level": level,
        "newly_unlocked": newly_unlocked
    }

def get_all_mastery() -> Dict[str, Dict[str, Any]]:
    """Returns all song mastery records keyed by song_id."""
    init_gamification_db()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT * FROM song_mastery")
    rows = cur.fetchall()
    conn.close()

    res = {}
    for r in rows:
        res[r["song_id"]] = {
            "play_count": r["play_count"],
            "best_score": r["best_score"],
            "mastery_level": r["mastery_level"],
            "last_practiced_at": r["last_practiced_at"]
        }
    return res
