using System;
using System.Collections.Generic;
using System.Text.Json;

namespace BassStation.Models;

public class PerformanceScoreDetailModel
{
    public string SongId { get; set; } = "";
    public double OverallScore { get; set; }
    public string Grade { get; set; } = "";
    public string ComboBadge { get; set; } = "";
    public string CoachComment { get; set; } = "";
    public string EvaluatedAt { get; set; } = "";
    public bool IsNewBest { get; set; }

    // 0 = not measured (legacy heuristic rows)
    public double TimingScore { get; set; }
    public double PitchScore { get; set; }
    public double CompleteScore { get; set; }
    public double CleanScore { get; set; }

    public int Perfect { get; set; }
    public int Great { get; set; }
    public int Good { get; set; }
    public int Bad { get; set; }
    public int Miss { get; set; }
    public int MaxCombo { get; set; }
    public int NotesTotal { get; set; }
    public bool HasJudgments => NotesTotal > 0;

    /// <summary>Game-style points: ALL PERFECT = 2,500,000 (OverallScore is the 0–100 accuracy).</summary>
    public const double MaxPoints = 2_500_000;
    public static long ToPoints(double overall) => (long)Math.Round(overall / 100.0 * MaxPoints);
    public long Points => ToPoints(OverallScore);

    // only known for a fresh evaluation (not stored in the best-score row)
    public int Fast { get; set; }
    public int Slow { get; set; }
    public int Wrong { get; set; }
    public int ExtraNotes { get; set; }
    public double? PrevBest { get; set; }
    public string? ReportPath { get; set; }

    public List<MeasureResult> Measures { get; set; } = new();

    // what the take covered: a section (RangeLabel) and / or a slower tempo; incomplete takes never set a best
    public string RangeLabel { get; set; } = "";
    public double Rate { get; set; } = 1.0;
    public bool Complete { get; set; } = true;
    public string ChartLabel => TakeModel.Chart(RangeLabel, Rate);

    /// <summary>A past take from its report file (the "summary" block the evaluator writes).</summary>
    public static PerformanceScoreDetailModel? FromReport(string songId, string reportPath)
    {
        if (!System.IO.File.Exists(reportPath)) return null;
        using var doc = JsonDocument.Parse(System.IO.File.ReadAllText(reportPath));
        if (!doc.RootElement.TryGetProperty("summary", out var s)) return null;
        var dims = s.GetProperty("dimensions");
        int Int(string k) => s.TryGetProperty(k, out var v) && v.ValueKind == JsonValueKind.Number ? v.GetInt32() : 0;
        var m = new PerformanceScoreDetailModel
        {
            SongId = songId,
            OverallScore = s.GetProperty("overall_score").GetDouble(),
            Grade = s.GetProperty("grade").GetString() ?? "",
            ComboBadge = s.GetProperty("combo_badge").GetString() ?? "",
            CoachComment = s.TryGetProperty("coach_comment", out var cc) ? cc.GetString() ?? "" : "",
            EvaluatedAt = s.TryGetProperty("evaluated_at", out var at) ? at.GetString() ?? "" : "",
            TimingScore = dims.GetProperty("timing").GetDouble(),
            PitchScore = dims.GetProperty("pitch").GetDouble(),
            CompleteScore = dims.GetProperty("complete").GetDouble(),
            CleanScore = dims.GetProperty("clean").GetDouble(),
            MaxCombo = Int("max_combo"),
            NotesTotal = Int("notes_total"),
            Fast = Int("fast"),
            Slow = Int("slow"),
            Wrong = Int("wrong"),
            ExtraNotes = Int("extra_notes"),
            Rate = s.TryGetProperty("rate", out var rt) && rt.ValueKind == JsonValueKind.Number ? rt.GetDouble() : 1.0,
            RangeLabel = s.TryGetProperty("label", out var lb) && lb.ValueKind == JsonValueKind.String ? lb.GetString() ?? "" : "",
            Complete = !s.TryGetProperty("coverage", out var cv) || cv.ValueKind != JsonValueKind.Number || cv.GetDouble() >= TakeModel.FullCoverage,
            ReportPath = reportPath
        };
        m.ApplyJudgments(s.GetProperty("judgments"));
        return m;
    }

    /// <summary>Fills judgment counts from the evaluator's judgments object (CLI output or judgments_json).</summary>
    public void ApplyJudgments(JsonElement j)
    {
        int Get(string k) => j.TryGetProperty(k, out var v) && v.ValueKind == JsonValueKind.Number ? v.GetInt32() : 0;
        Perfect = Get("perfect");
        Great = Get("great");
        Good = Get("good");
        Bad = Get("bad");
        Miss = Get("miss");
        if (j.TryGetProperty("max_combo", out _)) MaxCombo = Get("max_combo");
        if (j.TryGetProperty("notes_total", out _)) NotesTotal = Get("notes_total");
    }

    public void ApplyHeatmap(JsonElement arr)
    {
        Measures.Clear();
        if (arr.ValueKind != JsonValueKind.Array) return;
        foreach (var h in arr.EnumerateArray())
        {
            Measures.Add(new MeasureResult
            {
                Measure = h.TryGetProperty("measure", out var m) ? m.GetInt32() : 0,
                Status = h.TryGetProperty("status", out var s) ? s.GetString() ?? "" : "",
                DiffMs = h.TryGetProperty("diff_ms", out var d) ? d.GetDouble() : 0,
                Wrong = h.TryGetProperty("wrong", out var w) ? w.GetInt32() : 0,
                Missed = h.TryGetProperty("miss", out var x) ? x.GetInt32() : 0
            });
        }
    }

    public string GradeColor => Grade?.ToUpperInvariant() switch
    {
        "SS" or "EX" => "#EC4899",
        "S" => "#EAB308",
        "A" => "#3B82F6",
        "B" => "#10B981",
        "C" => "#F97316",
        _ => "#94A3B8"
    };

    public string GradeBgColor => Grade?.ToUpperInvariant() switch
    {
        "SS" or "EX" => "#FDF2F8",
        "S" => "#FEFCE8",
        "A" => "#EFF6FF",
        "B" => "#ECFDF5",
        "C" => "#FFF7ED",
        _ => "#F1F5F9"
    };
}

public class MeasureResult
{
    public int Measure { get; set; }
    public string Status { get; set; } = "";
    public double DiffMs { get; set; }
    public int Wrong { get; set; }
    public int Missed { get; set; }

    public string Color => Status switch
    {
        "PERFECT" => "#FF3377",
        "GREAT" => "#FFAE1A",
        "GOOD" => "#34C274",
        "EARLY" or "LATE" => "#3E8BFF",
        _ => "#D9D3DE"
    };

    public string Tip
    {
        get
        {
            string s = Status switch
            {
                "EARLY" => $"抢拍 {System.Math.Abs(DiffMs):0}ms",
                "LATE" => $"拖拍 {DiffMs:0}ms",
                _ => Status
            };
            if (Wrong > 0) s += $" · 错音 {Wrong}";
            if (Missed > 0) s += $" · 漏音 {Missed}";
            return $"第 {Measure} 小节  {s}";
        }
    }
}

/// <summary>One logged take (performance_takes row).</summary>
public class TakeModel
{
    public const double FullCoverage = 0.9;     // performance_evaluator.FULL_COVERAGE

    public string EvaluatedAt { get; set; } = "";
    public double OverallScore { get; set; }
    public string Grade { get; set; } = "";
    public string ComboBadge { get; set; } = "";
    public double Coverage { get; set; } = 1;
    public int? RangeStart { get; set; }
    public int? RangeEnd { get; set; }
    public string RangeLabel { get; set; } = "";
    public double Rate { get; set; } = 1;
    public bool Complete { get; set; }
    public bool NewBest { get; set; }
    public string? ReportPath { get; set; }
    public string SectionsJson { get; set; } = "";

    /// <summary>"全曲", "C", "全曲 · 80%", "C · 80%".</summary>
    public static string Chart(string rangeLabel, double rate)
    {
        string what = string.IsNullOrEmpty(rangeLabel) ? "全曲" : rangeLabel;
        return rate < 0.999 ? $"{what} · {Math.Round(rate * 100)}%" : what;
    }

    public string ChartText => Chart(RangeLabel, Rate);
    public string When => EvaluatedAt.Length >= 16 ? EvaluatedAt.Substring(5, 11) : EvaluatedAt;
    public string PointsText => PerformanceScoreDetailModel.ToPoints(OverallScore).ToString("N0");
    public string GradeColor => new PerformanceScoreDetailModel { Grade = Grade }.GradeColor;
    public double RowOpacity => Complete ? 1.0 : 0.45;
    public bool HasReport => !string.IsNullOrEmpty(ReportPath) && System.IO.File.Exists(ReportPath);

    /// <summary>Section name -> accuracy (0-1) of this take, from the report's section grades.</summary>
    public Dictionary<string, (int Start, double Acc, string Grade)> SectionResults()
    {
        var map = new Dictionary<string, (int, double, string)>();
        if (string.IsNullOrEmpty(SectionsJson)) return map;
        try
        {
            using var doc = JsonDocument.Parse(SectionsJson);
            foreach (var s in doc.RootElement.EnumerateArray())
            {
                string key = $"{s.GetProperty("start").GetInt32()}-{s.GetProperty("end").GetInt32()}";
                map[key] = (s.GetProperty("start").GetInt32(), s.GetProperty("acc").GetDouble(), s.GetProperty("grade").GetString() ?? "");
            }
        }
        catch (Exception) { }
        return map;
    }
}

/// <summary>A practice range offered for section takes (eval_session.py --sections).</summary>
public class PracticeSection
{
    public string Name { get; set; } = "";
    public int Start { get; set; }
    public int End { get; set; }
    public int Bar { get; set; }
    public int Notes { get; set; }
    public double Seconds { get; set; }
    public string Key => $"{Start}-{End}";
}
