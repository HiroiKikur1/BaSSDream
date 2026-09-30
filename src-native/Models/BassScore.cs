using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text.Json;
using System.Text.Json.Serialization;

namespace BassStation.Models;

/// <summary>
/// BaSSDream score (backend/score_format.py): bars in playback order, each with its start / end in seconds
/// of the backing recording; beats carry the written rhythm, notes the fingering.
/// </summary>
public class BassScore
{
    [JsonPropertyName("format")] public string Format { get; set; } = "bassdream-score";
    [JsonPropertyName("version")] public int Version { get; set; } = 1;
    [JsonPropertyName("tpq")] public int Tpq { get; set; } = 960;
    [JsonPropertyName("meta")] public Dictionary<string, string> Meta { get; set; } = new();
    [JsonPropertyName("tuning")] public List<int> Tuning { get; set; } = new();
    [JsonPropertyName("capo")] public int Capo { get; set; }
    [JsonPropertyName("key")] public List<JsonElement> Key { get; set; } = new();
    [JsonPropertyName("bars")] public List<ScoreBar> Bars { get; set; } = new();
    // fields this view does not edit (model info, song_doc ...) are kept as they are
    [JsonExtensionData] public Dictionary<string, JsonElement>? Extra { get; set; }

    [JsonIgnore] public string Path { get; private set; } = "";
    [JsonIgnore] public int Strings => Tuning.Count;

    private static readonly JsonSerializerOptions Opts = new()
    {
        // required fields are always written (string 0, fret 0 and tick 0 are real values); optional flags opt out below
        DefaultIgnoreCondition = JsonIgnoreCondition.Never,
        Encoder = System.Text.Encodings.Web.JavaScriptEncoder.UnsafeRelaxedJsonEscaping
    };

    public static BassScore Load(string path)
    {
        var s = JsonSerializer.Deserialize<BassScore>(File.ReadAllText(path), Opts) ?? new BassScore();
        s.Path = path;
        return s;
    }

    public void Save()
    {
        Meta["modified"] = DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss");
        string tmp = Path + ".part";
        File.WriteAllText(tmp, JsonSerializer.Serialize(this, Opts));
        File.Move(tmp, Path, overwrite: true);
    }

    /// <summary>Recording time of a position inside a bar (linear between the bar's ends).</summary>
    public double TimeOf(int bar, int tick)
    {
        var b = Bars[bar];
        return b.T0 + (b.T1 - b.T0) * tick / Math.Max(1, b.Length(Tpq));
    }

    /// <summary>Bar containing a recording time (clamped).</summary>
    public int BarAt(double t)
    {
        int lo = 0, hi = Bars.Count - 1;
        while (lo < hi)
        {
            int mid = (lo + hi + 1) / 2;
            if (Bars[mid].T0 <= t) lo = mid; else hi = mid - 1;
        }
        return lo;
    }

    /// <summary>Sounding notes (onset, duration in seconds, midi), ties merged, for the tab synth.</summary>
    public List<(double T, double Dur, int Midi, bool Dead)> SoundingNotes()
    {
        var list = new List<(double, double, int, bool)>();
        var open = new Dictionary<int, int>();              // string -> index of the note a tie continues
        for (int bi = 0; bi < Bars.Count; bi++)
        {
            foreach (var bt in Bars[bi].Beats)
            {
                double t0 = TimeOf(bi, bt.Tick), t1 = TimeOf(bi, bt.Tick + bt.Dur);
                foreach (var n in bt.Notes)
                {
                    if (n.Tie && open.TryGetValue(n.S, out int k))
                    {
                        var p = list[k];
                        list[k] = (p.Item1, t1 - p.Item1, p.Item3, p.Item4);
                        continue;
                    }
                    open[n.S] = list.Count;
                    list.Add((t0, t1 - t0, n.Midi, n.X));
                }
            }
        }
        list.Sort((a, b) => a.Item1.CompareTo(b.Item1));
        return list;
    }

    public IEnumerable<ScoreNote> AllNotes() => Bars.SelectMany(b => b.Beats).SelectMany(bt => bt.Notes);

    public string NewNoteId()
    {
        int max = AllNotes().Select(n => n.Id.Length > 1 && int.TryParse(n.Id[1..], out int k) ? k : -1).DefaultIfEmpty(-1).Max();
        return $"n{max + 1}";
    }
}

public class ScoreBar
{
    [JsonPropertyName("written")] public int Written { get; set; }
    [JsonPropertyName("occ")] public int Occ { get; set; }
    [JsonPropertyName("num")] public int Num { get; set; } = 4;
    [JsonPropertyName("den")] public int Den { get; set; } = 4;
    [JsonPropertyName("section")] public string? Section { get; set; }
    [JsonPropertyName("t0")] public double T0 { get; set; }
    [JsonPropertyName("t1")] public double T1 { get; set; }
    [JsonPropertyName("beats")] public List<ScoreBeat> Beats { get; set; } = new();
    /// <summary>The user checked this bar against the recording: its notes count as ground truth.</summary>
    [JsonIgnore(Condition = JsonIgnoreCondition.WhenWritingDefault)] [JsonPropertyName("reviewed")] public bool Reviewed { get; set; }
    [JsonExtensionData] public Dictionary<string, JsonElement>? Extra { get; set; }

    public int Length(int tpq) => Num * tpq * 4 / Den;
    [JsonIgnore] public int Filled => Beats.Count == 0 ? 0 : Beats[^1].Tick + Beats[^1].Dur;
}

public class ScoreBeat
{
    [JsonPropertyName("tick")] public int Tick { get; set; }
    [JsonPropertyName("dur")] public int Dur { get; set; }
    [JsonPropertyName("v")] public int Value { get; set; } = 4;       // 1 whole, 2 half, 4 quarter, 8 eighth ...
    [JsonPropertyName("d")] public int Dots { get; set; }
    [JsonPropertyName("t")] public int Tuplet { get; set; }
    [JsonPropertyName("notes")] public List<ScoreNote> Notes { get; set; } = new();
    [JsonExtensionData] public Dictionary<string, JsonElement>? Extra { get; set; }

    [JsonIgnore] public bool IsRest => Notes.Count == 0;

    /// <summary>Written length in ticks from value, dots and tuplet (3 in the time of 2, 5 in 4, 6 in 4 ...).</summary>
    public static int Ticks(int tpq, int value, int dots, int tuplet)
    {
        double q = 4.0 / value;
        double d = q;
        for (int i = 0; i < dots; i++) { d /= 2; q += d; }
        if (tuplet > 0)
        {
            int normal = tuplet switch { 3 => 2, 5 or 6 or 7 => 4, 9 => 8, _ => tuplet - 1 };
            q = q * normal / tuplet;
        }
        return (int)Math.Round(q * tpq);
    }
}

public class ScoreNote
{
    [JsonPropertyName("id")] public string Id { get; set; } = "";
    [JsonPropertyName("s")] public int S { get; set; }             // string, 0 = lowest
    [JsonPropertyName("f")] public int F { get; set; }             // fret
    [JsonPropertyName("midi")] public int Midi { get; set; }
    [JsonIgnore(Condition = JsonIgnoreCondition.WhenWritingDefault)] [JsonPropertyName("tie")] public bool Tie { get; set; }
    [JsonIgnore(Condition = JsonIgnoreCondition.WhenWritingDefault)] [JsonPropertyName("x")] public bool X { get; set; }            // dead note
    [JsonIgnore(Condition = JsonIgnoreCondition.WhenWritingDefault)] [JsonPropertyName("slide")] public int Slide { get; set; }
    [JsonIgnore(Condition = JsonIgnoreCondition.WhenWritingDefault)] [JsonPropertyName("hopo")] public bool Hopo { get; set; }
    [JsonIgnore(Condition = JsonIgnoreCondition.WhenWritingDefault)] [JsonPropertyName("slap")] public bool Slap { get; set; }
    [JsonIgnore(Condition = JsonIgnoreCondition.WhenWritingDefault)] [JsonPropertyName("pop")] public bool Pop { get; set; }
    [JsonPropertyName("src")] public string Src { get; set; } = "tab";
    [JsonIgnore(Condition = JsonIgnoreCondition.WhenWritingDefault)] [JsonPropertyName("conf")] public double? Conf { get; set; }
    [JsonIgnore(Condition = JsonIgnoreCondition.WhenWritingDefault)] [JsonPropertyName("flag")] public bool Flag { get; set; }      // low-confidence transcription
    [JsonExtensionData] public Dictionary<string, JsonElement>? Extra { get; set; }   // time, end, cand ...

    public ScoreNote Clone()
    {
        var c = (ScoreNote)MemberwiseClone();
        c.Extra = Extra == null ? null : new Dictionary<string, JsonElement>(Extra);
        return c;
    }
}
