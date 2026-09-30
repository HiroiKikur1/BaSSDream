using System;
using System.Collections.Generic;

namespace BassStation.Models;

public class PracticeSessionModel
{
    public int Id { get; set; }
    public string SongId { get; set; } = "";
    public string SongTitle { get; set; } = "";
    public string Artist { get; set; } = "";
    public DateTime StartTime { get; set; }
    public double DurationMinutes { get; set; }
    private string _displayTime = "";
    public string DisplayTime
    {
        get => string.IsNullOrEmpty(_displayTime) ? StartTime.ToString("yyyy-MM-dd HH:mm") : _displayTime;
        set => _displayTime = value;
    }

    private string _displayDuration = "";
    public string DisplayDuration
    {
        get
        {
            if (!string.IsNullOrEmpty(_displayDuration)) return _displayDuration;
            if (DurationMinutes < 1.0)
            {
                int sec = (int)Math.Max(1, Math.Round(DurationMinutes * 60));
                return $"{sec} 秒";
            }
            int totalSec = (int)Math.Round(DurationMinutes * 60);
            int m = totalSec / 60;
            int s = totalSec % 60;
            if (s == 0) return $"{m} 分钟";
            return $"{m}分{s:D2}秒";
        }
        set => _displayDuration = value;
    }
}

public class CalendarDayModel
{
    public DateTime Date { get; set; }
    public int DayNumber => Date.Day;
    public bool IsCurrentMonth { get; set; } = true;
    public bool IsToday => Date.Date == DateTime.Today;
    public double PracticeMinutes { get; set; }
    public bool HasPracticed => PracticeMinutes > 0;
    public string DisplayMinutes => HasPracticed ? $"{Math.Round(PracticeMinutes)}m" : "";

    public string BackgroundColor
    {
        get
        {
            if (!IsCurrentMonth) return "#F8FAFC";
            if (HasPracticed)
            {
                if (PracticeMinutes >= 60) return "#FF2D75";      // Heavy practice (Vivid Pink)
                if (PracticeMinutes >= 30) return "#FF5C93";      // Moderate practice
                return "#FFA3C2";                                 // Light practice
            }
            if (IsToday) return "#EFF6FF";                        // Today outline
            return "#FFFFFF";
        }
    }

    public string TextColor
    {
        get
        {
            if (!IsCurrentMonth) return "#CBD5E1";
            if (HasPracticed) return "#FFFFFF";
            if (IsToday) return "#2563EB";
            return "#334155";
        }
    }

    public string BorderColor => IsToday && !HasPracticed ? "#3B82F6" : "#E2E8F0";
}

public class PracticeStatsModel
{
    public double TotalHours { get; set; }
    public double TotalMinutes { get; set; }
    public int TotalSessions { get; set; }
    public double TodayMinutes { get; set; }
    public int StreakDays { get; set; }
    public List<PracticeSessionModel> RecentSessions { get; set; } = new();
    public Dictionary<string, double> DailyMinutes { get; set; } = new();
}
