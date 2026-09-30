using System;
using System.Collections.Generic;
using System.IO;
using System.Text.Json;
using System.Threading.Tasks;
using Microsoft.Data.Sqlite;
using BassStation.Models;

namespace BassStation.Services;

public class DatabaseService
{
    private static readonly string DbPath = AppPaths.Db;
    private readonly string _connectionString = $"Data Source={DbPath};";

    public async Task<List<SongModel>> LoadSongsAsync()
    {
        var list = new List<SongModel>();
        if (!File.Exists(DbPath)) return list;

        using var conn = new SqliteConnection(_connectionString);
        await conn.OpenAsync();
        await EnsureIdentityColumnsAsync(conn);

        string sql = @"
            SELECT 
                s.id, s.folder_name, s.title, s.artist, s.franchise, 
                s.gp_path, s.pdf_path, s.tempo, s.duration, s.measures, 
                s.is_5string, s.tuning, s.has_backing_track, s.audio_path, 
                s.cover_url, s.level, s.tier, s.tags_json,
                p.overall_score, p.grade,
                m.mastery_level,
                s.version, s.group_key, s.alt_gp_path
            FROM song_cache s
            LEFT JOIN performance_scores p ON s.id = p.song_id
            LEFT JOIN song_mastery m ON s.id = m.song_id
            ORDER BY s.level DESC, s.title ASC;
        ";

        using var cmd = new SqliteCommand(sql, conn);
        using var reader = await cmd.ExecuteReaderAsync();

        while (await reader.ReadAsync())
        {
            var song = new SongModel
            {
                Id = reader.IsDBNull(0) ? "" : reader.GetString(0),
                FolderName = reader.IsDBNull(1) ? "" : reader.GetString(1),
                Title = reader.IsDBNull(2) ? "" : reader.GetString(2),
                Artist = reader.IsDBNull(3) ? "" : reader.GetString(3),
                Franchise = reader.IsDBNull(4) ? "" : reader.GetString(4),
                GpPath = reader.IsDBNull(5) ? "" : reader.GetString(5),
                PdfPath = reader.IsDBNull(6) ? "" : reader.GetString(6),
                Tempo = reader.IsDBNull(7) ? 120.0 : reader.GetDouble(7),
                Duration = reader.IsDBNull(8) ? 0.0 : reader.GetDouble(8),
                Measures = reader.IsDBNull(9) ? 0 : reader.GetInt32(9),
                Is5String = !reader.IsDBNull(10) && reader.GetInt32(10) == 1,
                Tuning = reader.IsDBNull(11) ? "" : reader.GetString(11),
                HasBackingTrack = !reader.IsDBNull(12) && reader.GetInt32(12) == 1,
                AudioPath = reader.IsDBNull(13) ? "" : reader.GetString(13),
                CoverUrl = reader.IsDBNull(14) ? "" : reader.GetString(14),
                Level = reader.IsDBNull(15) ? 10 : reader.GetInt32(15),
                Tier = reader.IsDBNull(16) ? "HARD" : reader.GetString(16),
            };

            // Parse tags_json
            if (!reader.IsDBNull(17))
            {
                string tagsRaw = reader.GetString(17);
                try
                {
                    var parsed = JsonSerializer.Deserialize<List<string>>(tagsRaw);
                    if (parsed != null) song.Tags = parsed;
                }
                catch
                {
                    // Ignore tag parse error
                }
            }

            // Score & Grade
            if (!reader.IsDBNull(18)) song.BestScore = reader.GetDouble(18);
            if (!reader.IsDBNull(19)) song.BestGrade = reader.GetString(19);
            if (!reader.IsDBNull(20)) song.MasteryLevel = reader.GetString(20);
            if (!reader.IsDBNull(21)) song.Version = reader.GetString(21);
            if (!reader.IsDBNull(22)) song.GroupKey = reader.GetString(22);
            if (!reader.IsDBNull(23)) song.AltGpPath = reader.GetString(23);

            list.Add(song);
        }

        return list;
    }

    // version / group_key / alt_gp_path are written by tab_scanner; older databases lack them
    // (mirror of backend/schema.py, for a database opened before any script ran)
    private static async Task EnsureIdentityColumnsAsync(SqliteConnection conn)
    {
        var cols = new HashSet<string>();
        using (var info = new SqliteCommand("PRAGMA table_info(song_cache);", conn))
        using (var r = await info.ExecuteReaderAsync())
            while (await r.ReadAsync()) cols.Add(r.GetString(1));
        foreach (var col in new[] { "version", "group_key", "alt_gp_path" })
        {
            if (cols.Contains(col)) continue;
            using var alter = new SqliteCommand($"ALTER TABLE song_cache ADD COLUMN {col} TEXT DEFAULT '';", conn);
            await alter.ExecuteNonQueryAsync();
        }
    }

    // mirror of backend/schema.py
    private async Task EnsureFavoritesTableAsync(SqliteConnection conn)
    {
        using var cmd = new SqliteCommand("CREATE TABLE IF NOT EXISTS favorites (song_id TEXT NOT NULL, slot INTEGER NOT NULL, PRIMARY KEY (song_id, slot));", conn);
        await cmd.ExecuteNonQueryAsync();
    }

    public async Task<Dictionary<string, HashSet<int>>> LoadFavoritesAsync()
    {
        var map = new Dictionary<string, HashSet<int>>();
        if (!File.Exists(DbPath)) return map;
        using var conn = new SqliteConnection(_connectionString);
        await conn.OpenAsync();
        await EnsureFavoritesTableAsync(conn);
        using var cmd = new SqliteCommand("SELECT song_id, slot FROM favorites;", conn);
        using var r = await cmd.ExecuteReaderAsync();
        while (await r.ReadAsync())
        {
            string id = r.GetString(0);
            if (!map.TryGetValue(id, out var set)) map[id] = set = new HashSet<int>();
            set.Add(r.GetInt32(1));
        }
        return map;
    }

    public async Task SetFavoriteAsync(string songId, int slot, bool on)
    {
        if (!File.Exists(DbPath)) return;
        using var conn = new SqliteConnection(_connectionString);
        await conn.OpenAsync();
        await EnsureFavoritesTableAsync(conn);
        using var cmd = new SqliteCommand(on
            ? "INSERT OR IGNORE INTO favorites (song_id, slot) VALUES ($id, $slot);"
            : "DELETE FROM favorites WHERE song_id = $id AND slot = $slot;", conn);
        cmd.Parameters.AddWithValue("$id", songId);
        cmd.Parameters.AddWithValue("$slot", slot);
        await cmd.ExecuteNonQueryAsync();
    }

    public async Task<List<BadgeModel>> LoadBadgesAsync()
    {
        var list = new List<BadgeModel>();
        if (!File.Exists(DbPath)) return list;

        using var conn = new SqliteConnection(_connectionString);
        await conn.OpenAsync();

        string sql = @"
            SELECT b.id, b.name, b.rarity, b.description, b.franchise,
                   COALESCE(ub.is_equipped, 0) as is_equipped,
                   CASE WHEN ub.unlocked_at IS NOT NULL THEN 1 ELSE 0 END as is_unlocked
            FROM badges b
            LEFT JOIN user_badges ub ON b.id = ub.badge_id
            ORDER BY b.id ASC;
        ";

        using var cmd = new SqliteCommand(sql, conn);
        using var reader = await cmd.ExecuteReaderAsync();

        while (await reader.ReadAsync())
        {
            list.Add(new BadgeModel
            {
                Id = reader.GetString(0),
                Name = reader.GetString(1),
                Rarity = reader.GetString(2),
                Description = reader.GetString(3),
                Franchise = reader.GetString(4),
                IsEquipped = reader.GetInt32(5) == 1,
                IsUnlocked = reader.GetInt32(6) == 1
            });
        }

        return list;
    }

    public async Task EquipBadgeAsync(string badgeId)
    {
        using var conn = new SqliteConnection(_connectionString);
        await conn.OpenAsync();

        using var tx = conn.BeginTransaction();

        // Ensure record exists in user_badges and is unlocked
        using var cmdInsert = new SqliteCommand(@"
            INSERT INTO user_badges (badge_id, unlocked_at, is_equipped)
            VALUES (@id, datetime('now'), 0)
            ON CONFLICT(badge_id) DO UPDATE SET unlocked_at = COALESCE(unlocked_at, datetime('now'));", conn, tx);
        cmdInsert.Parameters.AddWithValue("@id", badgeId);
        await cmdInsert.ExecuteNonQueryAsync();

        using var cmd1 = new SqliteCommand("UPDATE user_badges SET is_equipped = 0;", conn, tx);
        await cmd1.ExecuteNonQueryAsync();

        using var cmd2 = new SqliteCommand("UPDATE user_badges SET is_equipped = 1 WHERE badge_id = @id;", conn, tx);
        cmd2.Parameters.AddWithValue("@id", badgeId);
        await cmd2.ExecuteNonQueryAsync();

        tx.Commit();
    }

    public async Task<PracticeStatsModel> LoadPracticeStatsAsync()
    {
        var stats = new PracticeStatsModel();
        if (!File.Exists(DbPath)) return stats;

        using var conn = new SqliteConnection(_connectionString);
        await conn.OpenAsync();

        // 1. Total hours & sessions
        using (var cmd = new SqliteCommand("SELECT COALESCE(SUM(duration_seconds), 0), COUNT(*) FROM practice_sessions;", conn))
        using (var reader = await cmd.ExecuteReaderAsync())
        {
            if (await reader.ReadAsync())
            {
                double totalSec = reader.GetDouble(0);
                stats.TotalHours = Math.Round(totalSec / 3600.0, 1);
                stats.TotalMinutes = Math.Round(totalSec / 60.0, 1);
                stats.TotalSessions = reader.GetInt32(1);
            }
        }

        // 2. Today minutes
        string todayStr = DateTime.Today.ToString("yyyy-MM-dd");
        using (var cmd = new SqliteCommand("SELECT COALESCE(SUM(duration_seconds), 0) FROM practice_sessions WHERE start_time LIKE @today;", conn))
        {
            cmd.Parameters.AddWithValue("@today", todayStr + "%");
            var res = await cmd.ExecuteScalarAsync();
            if (res != null && double.TryParse(res.ToString(), out double todaySec))
            {
                stats.TodayMinutes = Math.Round(todaySec / 60.0, 1);
            }
        }

        // 3. Heatmap
        using (var cmd = new SqliteCommand(@"
            SELECT substr(start_time, 1, 10) as day, SUM(duration_seconds)
            FROM practice_sessions
            GROUP BY day
            ORDER BY day DESC;
        ", conn))
        using (var reader = await cmd.ExecuteReaderAsync())
        {
            while (await reader.ReadAsync())
            {
                string day = reader.GetString(0);
                double sec = reader.GetDouble(1);
                stats.DailyMinutes[day] = Math.Round(sec / 60.0, 1);
            }
        }

        // 4. Streak calculation
        using (var cmd = new SqliteCommand(@"
            SELECT DISTINCT substr(start_time, 1, 10) 
            FROM practice_sessions 
            ORDER BY start_time DESC;
        ", conn))
        using (var reader = await cmd.ExecuteReaderAsync())
        {
            var dates = new List<DateTime>();
            while (await reader.ReadAsync())
            {
                if (DateTime.TryParse(reader.GetString(0), out var dt))
                    dates.Add(dt.Date);
            }

            int streak = 0;
            var checkDate = DateTime.Today;
            if (dates.Count > 0)
            {
                if (dates[0] == checkDate || dates[0] == checkDate.AddDays(-1))
                {
                    checkDate = dates[0];
                    foreach (var d in dates)
                    {
                        if (d == checkDate)
                        {
                            streak++;
                            checkDate = checkDate.AddDays(-1);
                        }
                        else break;
                    }
                }
            }
            stats.StreakDays = streak;
        }

        // 5. Recent sessions
        using (var cmd = new SqliteCommand(@"
            SELECT id, song_id, song_title, artist, start_time, duration_seconds
            FROM practice_sessions
            ORDER BY id DESC
            LIMIT 30;
        ", conn))
        using (var reader = await cmd.ExecuteReaderAsync())
        {
            while (await reader.ReadAsync())
            {
                stats.RecentSessions.Add(new PracticeSessionModel
                {
                    Id = reader.GetInt32(0),
                    SongId = reader.IsDBNull(1) ? "" : reader.GetString(1),
                    SongTitle = reader.IsDBNull(2) ? "" : reader.GetString(2),
                    Artist = reader.IsDBNull(3) ? "" : reader.GetString(3),
                    StartTime = DateTime.TryParse(reader.GetString(4), out var st) ? st : DateTime.Now,
                    DurationMinutes = Math.Round(reader.GetDouble(5) / 60.0, 1)
                });
            }
        }

        return stats;
    }

    public async Task RecordPracticeSessionAsync(string songId, string songTitle, string artist, double durationSeconds)
    {
        if (!File.Exists(DbPath) || durationSeconds < 5.0) return;
        using var conn = new SqliteConnection(_connectionString);
        await conn.OpenAsync();

        string nowIso = DateTime.Now.ToString("yyyy-MM-ddTHH:mm:ss");
        string startIso = DateTime.Now.AddSeconds(-durationSeconds).ToString("yyyy-MM-ddTHH:mm:ss");

        using var cmd = new SqliteCommand(@"
            INSERT INTO practice_sessions (song_id, song_title, artist, start_time, end_time, duration_seconds, notes)
            VALUES (@song_id, @title, @artist, @start, @end, @dur, '');
        ", conn);
        cmd.Parameters.AddWithValue("@song_id", songId);
        cmd.Parameters.AddWithValue("@title", songTitle);
        cmd.Parameters.AddWithValue("@artist", artist);
        cmd.Parameters.AddWithValue("@start", startIso);
        cmd.Parameters.AddWithValue("@end", nowIso);
        cmd.Parameters.AddWithValue("@dur", Math.Round(durationSeconds, 1));

        await cmd.ExecuteNonQueryAsync();
    }

    /// <summary>Best score of a song. Written only by backend/performance_evaluator.py (keeps the best take).</summary>
    public async Task<PerformanceScoreDetailModel?> LoadPerformanceDetailAsync(string songId)
    {
        if (!File.Exists(DbPath)) return null;
        using var conn = new SqliteConnection(_connectionString);
        await conn.OpenAsync();

        // SELECT * + lookup by name: rows written before the score-referenced evaluator lack the newer columns
        using var cmd = new SqliteCommand("SELECT * FROM performance_scores WHERE song_id = @song_id;", conn);
        cmd.Parameters.AddWithValue("@song_id", songId);

        using var reader = await cmd.ExecuteReaderAsync();
        if (!await reader.ReadAsync()) return null;

        var cols = new Dictionary<string, int>(StringComparer.OrdinalIgnoreCase);
        for (int i = 0; i < reader.FieldCount; i++) cols[reader.GetName(i)] = i;
        double Num(string c) => cols.TryGetValue(c, out int i) && !reader.IsDBNull(i) ? reader.GetDouble(i) : 0;
        string Str(string c) => cols.TryGetValue(c, out int i) && !reader.IsDBNull(i) ? reader.GetValue(i).ToString() ?? "" : "";

        var score = new PerformanceScoreDetailModel
        {
            SongId = songId,
            OverallScore = Num("overall_score"),
            Grade = Str("grade"),
            ComboBadge = Str("combo_badge"),
            CoachComment = Str("coach_comment"),
            EvaluatedAt = Str("evaluated_at"),
        };
        string judgments = Str("judgments_json");
        if (judgments.Length > 0)
        {
            // only the new evaluator's rows carry meaningful dimensions
            score.TimingScore = Num("timing_score");
            score.PitchScore = Num("pitch_score");
            score.CompleteScore = Num("complete_score");
            score.CleanScore = Num("clean_score");
            try
            {
                using (var j = JsonDocument.Parse(judgments)) score.ApplyJudgments(j.RootElement);
                string heat = Str("heatmap_json");
                if (heat.Length > 0)
                {
                    using var h = JsonDocument.Parse(heat);
                    score.ApplyHeatmap(h.RootElement);
                }
            }
            catch (JsonException) { }
        }
        return score;
    }

    /// <summary>Latest takes of a song, newest first (table written by backend/performance_evaluator.py).</summary>
    public async Task<List<TakeModel>> LoadTakesAsync(string songId, int limit = 30)
    {
        var list = new List<TakeModel>();
        if (!File.Exists(DbPath)) return list;
        using var conn = new SqliteConnection(_connectionString);
        await conn.OpenAsync();
        using var cmd = new SqliteCommand(@"
            SELECT evaluated_at, overall_score, grade, combo_badge, coverage, range_start, range_end, range_label,
                   rate, complete, new_best, report_path, sections_json
            FROM performance_takes WHERE song_id = @song_id ORDER BY id DESC LIMIT @limit;", conn);
        cmd.Parameters.AddWithValue("@song_id", songId);
        cmd.Parameters.AddWithValue("@limit", limit);
        try
        {
            using var r = await cmd.ExecuteReaderAsync();
            while (await r.ReadAsync())
            {
                list.Add(new TakeModel
                {
                    EvaluatedAt = r.IsDBNull(0) ? "" : r.GetString(0),
                    OverallScore = r.IsDBNull(1) ? 0 : r.GetDouble(1),
                    Grade = r.IsDBNull(2) ? "" : r.GetString(2),
                    ComboBadge = r.IsDBNull(3) ? "" : r.GetString(3),
                    Coverage = r.IsDBNull(4) ? 1 : r.GetDouble(4),
                    RangeStart = r.IsDBNull(5) ? null : r.GetInt32(5),
                    RangeEnd = r.IsDBNull(6) ? null : r.GetInt32(6),
                    RangeLabel = r.IsDBNull(7) ? "" : r.GetString(7),
                    Rate = r.IsDBNull(8) ? 1 : r.GetDouble(8),
                    Complete = !r.IsDBNull(9) && r.GetInt32(9) != 0,
                    NewBest = !r.IsDBNull(10) && r.GetInt32(10) != 0,
                    ReportPath = r.IsDBNull(11) ? null : r.GetString(11),
                    SectionsJson = r.IsDBNull(12) ? "" : r.GetString(12),
                });
            }
        }
        catch (SqliteException) { }     // no take logged yet: the evaluator creates the table
        return list;
    }

    public async Task AddSongRecordAsync(string id, string folderName, string title, string artist, string gpPath, double tempo = 120.0, double duration = 180.0, int level = 15, string tier = "HARD")
    {
        if (!File.Exists(DbPath)) return;
        using var conn = new SqliteConnection(_connectionString);
        await conn.OpenAsync();

        using var cmd = new SqliteCommand(@"
            INSERT OR REPLACE INTO song_cache (
                id, folder_name, title, artist, franchise,
                gp_path, pdf_path, tempo, duration, measures,
                is_5string, tuning, has_backing_track, audio_path,
                cover_url, level, tier, tags_json
            ) VALUES (
                @id, @folder, @title, @artist, 'BanG Dream! & Custom',
                @gp_path, '', @tempo, @duration, 60,
                0, '4弦 [EADG]', 0, '',
                '', @level, @tier, '[""自定义新增""]'
            );
        ", conn);
        cmd.Parameters.AddWithValue("@id", id);
        cmd.Parameters.AddWithValue("@folder", folderName);
        cmd.Parameters.AddWithValue("@title", title);
        cmd.Parameters.AddWithValue("@artist", artist);
        cmd.Parameters.AddWithValue("@gp_path", gpPath);
        cmd.Parameters.AddWithValue("@tempo", tempo);
        cmd.Parameters.AddWithValue("@duration", duration);
        cmd.Parameters.AddWithValue("@level", level);
        cmd.Parameters.AddWithValue("@tier", tier);

        await cmd.ExecuteNonQueryAsync();
    }

    public async Task<bool> DeleteSongAsync(string songId, string? folderName)
    {
        try
        {
            if (File.Exists(DbPath))
            {
                using var conn = new SqliteConnection(_connectionString);
                await conn.OpenAsync();
                using var tx = conn.BeginTransaction();
                using (var cmd = new SqliteCommand("DELETE FROM song_cache WHERE id = @id;", conn, tx))
                {
                    cmd.Parameters.AddWithValue("@id", songId);
                    await cmd.ExecuteNonQueryAsync();
                }
                using (var cmd = new SqliteCommand("DELETE FROM performance_scores WHERE song_id = @id;", conn, tx))
                {
                    cmd.Parameters.AddWithValue("@id", songId);
                    await cmd.ExecuteNonQueryAsync();
                }
                using (var cmd = new SqliteCommand("DELETE FROM song_mastery WHERE song_id = @id;", conn, tx))
                {
                    cmd.Parameters.AddWithValue("@id", songId);
                    await cmd.ExecuteNonQueryAsync();
                }
                using (var cmd = new SqliteCommand("DELETE FROM practice_sessions WHERE song_id = @id;", conn, tx))
                {
                    cmd.Parameters.AddWithValue("@id", songId);
                    await cmd.ExecuteNonQueryAsync();
                }
                tx.Commit();
            }

            // Also clean from tabs.db if present
            string tabsDbPath = Path.Combine(AppPaths.Backend, "tabs.db");
            if (File.Exists(tabsDbPath))
            {
                try
                {
                    using var tabsConn = new SqliteConnection($"Data Source={tabsDbPath};");
                    await tabsConn.OpenAsync();
                    using var cmd = new SqliteCommand("DELETE FROM tabs WHERE id = @id;", tabsConn);
                    cmd.Parameters.AddWithValue("@id", songId);
                    await cmd.ExecuteNonQueryAsync();
                }
                catch { }
            }

            // Physically delete song folder
            if (!string.IsNullOrEmpty(folderName))
            {
                string targetDir = Path.Combine(AppPaths.Tabs, folderName);
                if (Directory.Exists(targetDir))
                {
                    Directory.Delete(targetDir, true);
                }
            }

            return true;
        }
        catch (Exception ex)
        {
            Console.WriteLine($"Error deleting song: {ex.Message}");
            return false;
        }
    }
}
