using System.Windows.Media;
using BassStation.Models;

namespace BassStation.Services;

/// <summary>
/// "Commemorative score" edition of a song: the band's colours for the ribbon bookmark, the outlined title
/// gradient, the foil rule and the stickers. Songs without a BanG Dream! band get the BaSSDream pink edition.
/// </summary>
public sealed class BandEdition
{
    public string Key { get; init; } = "";
    public string Name { get; init; } = "";
    public Color Main { get; init; }
    public Color Deep { get; init; }
    public Color[] Title { get; init; } = System.Array.Empty<Color>();   // top -> bottom
    public Color[] Foil { get; init; } = System.Array.Empty<Color>();    // left -> right
    public Color Paper { get; init; }
    public Color Tint { get; init; }                                    // current bar / selection wash

    private static Color C(string hex) => (Color)ColorConverter.ConvertFromString(hex);

    private static BandEdition Make(string key, string name, string main, string deep, string[] title, string[] foil, string paper, string tint) => new()
    {
        Key = key, Name = name, Main = C(main), Deep = C(deep),
        Title = System.Array.ConvertAll(title, C), Foil = System.Array.ConvertAll(foil, C), Paper = C(paper), Tint = C(tint)
    };

    public static readonly BandEdition Default = Make("bassdream", "BaSSDream", "#E50050", "#A8003B",
        new[] { "#FF8AB5", "#FF4D8A", "#E50050" }, new[] { "#FFD1E0", "#FF8FB3", "#C9A7FF", "#FFE9C9" }, "#FBF6F8", "#FFE1EA");

    private static readonly BandEdition[] All =
    {
        Make("popipa", "Poppin'Party", "#FF3B72", "#B81E4F", new[] { "#FFB0C8", "#FF6A95", "#FF3B72" }, new[] { "#FFD1E0", "#FF8FB3", "#FFC76B", "#FFF0C8" }, "#FBF5F7", "#FFE1EA"),
        Make("afterglow", "Afterglow", "#E53344", "#9E2338", new[] { "#FFB14A", "#FF6A3D", "#E53344" }, new[] { "#FFCF7A", "#FF7E5F", "#E24A6B", "#FFD9A0" }, "#FBF5F3", "#FCE3DF"),
        Make("pasupare", "Pastel*Palettes", "#33C9A0", "#1E8A6E", new[] { "#A8F0D8", "#5ED8B8", "#2FB48E" }, new[] { "#C6FFE9", "#7FE6C8", "#FFC3E1", "#FFF3B0" }, "#F5FAF8", "#DCF4EC"),
        Make("roselia", "Roselia", "#3344AA", "#22196E", new[] { "#9A8CFF", "#5A4FD0", "#33228A" }, new[] { "#D9CCFF", "#8E7CFF", "#5D3FC4", "#EDE6FF" }, "#F7F5F9", "#E4E1F6"),
        Make("hhw", "Hello, Happy World!", "#F5B800", "#A87800", new[] { "#FFE68A", "#FFCC33", "#F5A800" }, new[] { "#FFF0A0", "#FFC94D", "#FF9EC7", "#FFE9C9" }, "#FDFAF1", "#FFF2C4"),
        Make("morfonica", "Morfonica", "#33AAFF", "#2F63B8", new[] { "#9EE1FF", "#6AAEFF", "#8C7BF0" }, new[] { "#BFF0FF", "#8FB8FF", "#C7A8FF", "#E8F6FF" }, "#F6F7FB", "#DFEEFB"),
        Make("ras", "RAISE A SUILEN", "#1FB8A8", "#11756B", new[] { "#7EF0E2", "#33CCC0", "#1FA898" }, new[] { "#B5FFF4", "#5FE0D0", "#C9A0FF", "#E6FFFB" }, "#F4F9F8", "#D8F2EF"),
        Make("mygo", "MyGO!!!!!", "#3388BB", "#1F557A", new[] { "#A8D8F0", "#5FA8D8", "#3378AA" }, new[] { "#C5E8FF", "#7FBCE6", "#FFD27F", "#E6F4FF" }, "#F5F8FA", "#DDEBF4"),
        Make("avemujica", "Ave Mujica", "#881144", "#4A0822", new[] { "#D46A8A", "#A8254F", "#6E0E36" }, new[] { "#E8B4C4", "#B03A60", "#6E1A3A", "#F2D6DE" }, "#F8F4F5", "#F0DDE3"),
    };

    /// <summary>The edition of a song: its first credited band (collaborations take the first name).</summary>
    public static BandEdition For(SongModel? song)
    {
        if (song == null) return Default;
        string s = $"{song.Artist} {song.FolderName}".ToLowerInvariant();
        (string Key, string Pat)[] pats =
        {
            ("roselia", "roselia"), ("afterglow", "afterglow"), ("popipa", "poppin"), ("pasupare", "pastel"),
            ("hhw", "happy world"), ("morfonica", "morfonica"), ("ras", "raise a suilen"), ("ras", "ras_"),
            ("mygo", "mygo"), ("avemujica", "ave mujica"), ("avemujica", "ave_mujica"),
        };
        int best = int.MaxValue;
        string? key = null;
        foreach (var (k, p) in pats)
        {
            int i = s.IndexOf(p, System.StringComparison.Ordinal);
            if (i >= 0 && i < best) { best = i; key = k; }
        }
        foreach (var e in All) if (e.Key == key) return e;
        return Default;
    }

    public LinearGradientBrush TitleBrush()
    {
        var b = new LinearGradientBrush { StartPoint = new(0, 0), EndPoint = new(0, 1) };
        for (int i = 0; i < Title.Length; i++) b.GradientStops.Add(new GradientStop(Title[i], i / (double)(Title.Length - 1)));
        b.Freeze();
        return b;
    }

    public LinearGradientBrush FoilBrush()
    {
        var b = new LinearGradientBrush { StartPoint = new(0, 0), EndPoint = new(1, 0) };
        for (int i = 0; i < Foil.Length; i++) b.GradientStops.Add(new GradientStop(Foil[i], i / (double)(Foil.Length - 1)));
        b.Freeze();
        return b;
    }
}
