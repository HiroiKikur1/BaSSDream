using System;
using System.Collections.Generic;
using System.IO;

namespace BassStation.Models;

public class SongModel : System.ComponentModel.INotifyPropertyChanged
{
    public event System.ComponentModel.PropertyChangedEventHandler? PropertyChanged;

    private HashSet<int> _favSlots = new();
    public HashSet<int> FavSlots
    {
        get => _favSlots;
        set { _favSlots = value; PropertyChanged?.Invoke(this, new(nameof(IsFavorite))); }
    }
    public bool IsFavorite => _favSlots.Count > 0;
    public void NotifyFavoriteChanged() => PropertyChanged?.Invoke(this, new(nameof(IsFavorite)));

    public string Id { get; set; } = "";
    public string FolderName { get; set; } = "";
    public string Title { get; set; } = "";
    public string Artist { get; set; } = "";
    public string Franchise { get; set; } = "";
    public string GpPath { get; set; } = "";
    public string PdfPath { get; set; } = "";
    public double Tempo { get; set; }
    public double Duration { get; set; }
    public int Measures { get; set; }
    public bool Is5String { get; set; }
    public string Tuning { get; set; } = "";
    public bool HasBackingTrack { get; set; }
    public string AudioPath { get; set; } = "";
    public string CoverUrl { get; set; } = "";
    public int Level { get; set; }
    public string Tier { get; set; } = "HARD";
    public List<string> Tags { get; set; } = new();

    // Version label ("Live", "Short", "#7" …) and grouping: the 4- and 5-string arrangements of one version share
    // GroupKey and are linked as Siblings (separate folders), or the other arrangement is a file in this folder (AltGpPath).
    public string Version { get; set; } = "";
    public string GroupKey { get; set; } = "";
    public string AltGpPath { get; set; } = "";
    public SongModel? Sibling { get; set; }
    public bool HasVersion => Version.Length > 0;
    public bool HasStringPair => Sibling != null || AltGpPath.Length > 0;
    public string StringTag => HasStringPair ? "4/5弦" : Is5String ? "5弦" : "";
    public bool HasStringTag => StringTag.Length > 0;

    public double? BestScore { get; set; }
    public string? BestGrade { get; set; }
    public string? MasteryLevel { get; set; }

    public string DisplayDuration
    {
        get
        {
            if (Duration <= 0) return "0:00";
            int m = (int)(Duration / 60);
            int s = (int)(Duration % 60);
            return $"{m}:{s:D2}";
        }
    }

    public string DisplayBpm => $"BPM {Math.Round(Tempo)}";

    public string DisplayTuning => Is5String ? "5弦 [BEADG]" : "4弦 [EADG]";

    public string TierColor => Tier?.ToUpperInvariant() switch
    {
        "EASY" => "#3E8BFF",
        "NORMAL" => "#34C274",
        "HARD" => "#FFAE1A",
        "EXPERT" => "#FF4058",
        "SPECIAL" => "#E04BD6",
        _ => "#FF3377"
    };

    public string TierBgColor => Tier?.ToUpperInvariant() switch
    {
        "EASY" => "#EFF6FF",
        "NORMAL" => "#ECFDF5",
        "HARD" => "#FFFBEB",
        "EXPERT" => "#FEF2F2",
        "SPECIAL" => "#FAF5FF",
        _ => "#FFF1F5"
    };

    public string CoverImagePath
    {
        get
        {
            var jpg = Path.Combine(@"E:\BassStation\cache\covers", $"{Id}.jpg");
            if (File.Exists(jpg)) return jpg;
            var png = Path.Combine(@"E:\BassStation\cache\covers", $"{Id}.png");
            if (File.Exists(png)) return png;
            return "";
        }
    }
}
