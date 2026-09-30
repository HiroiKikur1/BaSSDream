using System.Collections.Generic;
using System.IO;
using System.Text.Json;

namespace BassStation.Models;

/// <summary>Per-note evaluation report written by backend/eval_report.py (bars in playback order).</summary>
public class EvaluationReport
{
    public List<int> Tuning { get; } = new();
    public List<ReportSection> Sections { get; } = new();
    public List<ReportBar> Bars { get; } = new();

    // for bar replay: take time = backing time / Rate + OffsetMs / 1000
    public string? AudioPath { get; private set; }
    public double OffsetMs { get; private set; }
    public double Rate { get; private set; } = 1.0;
    public bool CanReplay => AudioPath != null && System.IO.File.Exists(AudioPath) && Bars.Count > 0 && Bars[0].T1 > 0;

    public static EvaluationReport? Load(string? path)
    {
        if (string.IsNullOrEmpty(path) || !File.Exists(path)) return null;
        try
        {
            using var doc = JsonDocument.Parse(File.ReadAllText(path));
            var root = doc.RootElement;
            var r = new EvaluationReport();
            foreach (var t in root.GetProperty("tuning").EnumerateArray()) r.Tuning.Add(t.GetInt32());
            foreach (var s in root.GetProperty("sections").EnumerateArray())
            {
                r.Sections.Add(new ReportSection
                {
                    Name = Str(s, "name") ?? "",
                    Start = s.GetProperty("start").GetInt32(),
                    End = s.GetProperty("end").GetInt32(),
                    Acc = Num(s, "acc"),
                    Grade = Str(s, "grade")
                });
            }
            foreach (var b in root.GetProperty("bars").EnumerateArray())
            {
                var bar = new ReportBar
                {
                    Number = b.GetProperty("bar").GetInt32(),
                    Occurrence = b.GetProperty("occ").GetInt32(),
                    Num = b.GetProperty("num").GetInt32(),
                    Den = b.GetProperty("den").GetInt32(),
                    Section = b.TryGetProperty("si", out var si) ? si.GetInt32() : 0,
                    Acc = Num(b, "acc"),
                    Grade = Str(b, "grade"),
                    Tendency = Num(b, "tend"),
                    Wrong = (int)(Num(b, "wrong") ?? 0),
                    Miss = (int)(Num(b, "miss") ?? 0),
                    T0 = Num(b, "t0") ?? 0,
                    T1 = Num(b, "t1") ?? 0
                };
                foreach (var bt in b.GetProperty("beats").EnumerateArray())
                {
                    var beat = new ReportBeat
                    {
                        Pos = bt.GetProperty("p").GetDouble(),
                        Len = bt.GetProperty("l").GetDouble(),
                        Value = bt.GetProperty("v").GetInt32(),
                        Dots = bt.GetProperty("d").GetInt32(),
                        Tuplet = bt.GetProperty("t").GetInt32()
                    };
                    foreach (var n in bt.GetProperty("n").EnumerateArray())
                    {
                        beat.Notes.Add(new ReportNote
                        {
                            String = n.GetProperty("s").GetInt32(),
                            Fret = n.GetProperty("f").GetInt32(),
                            Tie = n.TryGetProperty("tie", out _),
                            Dead = n.TryGetProperty("x", out _),
                            Wrong = n.TryGetProperty("w", out _),
                            Judge = Str(n, "j"),
                            Dt = Num(n, "dt")
                        });
                    }
                    bar.Beats.Add(beat);
                }
                r.Bars.Add(bar);
            }
            if (root.TryGetProperty("summary", out var sum))
            {
                r.AudioPath = Str(sum, "audio");
                r.OffsetMs = Num(sum, "offset_ms") ?? 0;
                r.Rate = Num(sum, "rate") ?? 1.0;
                // a section take reports only its own bars
                if (sum.TryGetProperty("range", out var rg) && rg.ValueKind == JsonValueKind.Array)
                    r.Crop(rg[0].GetInt32(), rg[1].GetInt32());
            }
            return r;
        }
        catch
        {
            return null;
        }
    }

    /// <summary>Keeps playback bars [start, end) and the sections inside them (section indices renumbered).</summary>
    private void Crop(int start, int end)
    {
        if (start < 0 || end > Bars.Count || start >= end) return;
        var keep = new List<ReportBar>(Bars.GetRange(start, end - start));
        var map = new Dictionary<int, int>();
        var secs = new List<ReportSection>();
        for (int i = 0; i < Sections.Count; i++)
        {
            var s = Sections[i];
            if (s.End <= start || s.Start >= end) continue;
            map[i] = secs.Count;
            secs.Add(new ReportSection
            {
                Name = s.Name, Start = System.Math.Max(s.Start, start) - start, End = System.Math.Min(s.End, end) - start,
                Acc = s.Acc, Grade = s.Grade
            });
        }
        foreach (var b in keep) b.Section = map.TryGetValue(b.Section, out int k) ? k : 0;
        Bars.Clear();
        Bars.AddRange(keep);
        Sections.Clear();
        Sections.AddRange(secs);
    }

    private static string? Str(JsonElement e, string k) =>
        e.TryGetProperty(k, out var v) && v.ValueKind == JsonValueKind.String ? v.GetString() : null;

    private static double? Num(JsonElement e, string k) =>
        e.TryGetProperty(k, out var v) && v.ValueKind == JsonValueKind.Number ? v.GetDouble() : null;
}

public class ReportSection
{
    public string Name { get; set; } = "";
    public int Start { get; set; }
    public int End { get; set; }
    public double? Acc { get; set; }
    public string? Grade { get; set; }
}

public class ReportBar
{
    public int Number { get; set; }
    public int Occurrence { get; set; }
    public int Num { get; set; }
    public int Den { get; set; }
    public int Section { get; set; }
    public double? Acc { get; set; }
    public string? Grade { get; set; }
    /// <summary>Mean timing error (ms) when the bar leans early (negative) or late.</summary>
    public double? Tendency { get; set; }
    public int Wrong { get; set; }
    public int Miss { get; set; }
    public double T0 { get; set; }           // backing-audio seconds (0 in reports written before bar replay)
    public double T1 { get; set; }
    public List<ReportBeat> Beats { get; } = new();
}

public class ReportBeat
{
    public double Pos { get; set; }       // quarter notes from the bar start
    public double Len { get; set; }
    public int Value { get; set; }        // 1 whole, 2 half, 4 quarter, 8 eighth ...
    public int Dots { get; set; }
    public int Tuplet { get; set; }
    public List<ReportNote> Notes { get; } = new();
    public bool IsRest => Notes.Count == 0;
}

public class ReportNote
{
    public int String { get; set; }       // 0 = lowest string
    public int Fret { get; set; }
    public bool Tie { get; set; }
    public bool Dead { get; set; }
    public bool Wrong { get; set; }
    public string? Judge { get; set; }    // P G D B M, null = outside the recording
    public double? Dt { get; set; }       // ms, negative = early
}
