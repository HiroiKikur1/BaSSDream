using System.Windows;
using System.Windows.Media;

namespace BassStation.Services;

/// <summary>Themes/Palette.xaml for code: the same names, read from the application resources.</summary>
public static class Palette
{
    private static Color Get(string key) =>
        Application.Current?.TryFindResource(key + "Color") is Color c ? c : Colors.Magenta;   // magenta = missing key, visible on purpose

    private static SolidColorBrush Frozen(Color c)
    {
        var b = new SolidColorBrush(c);
        b.Freeze();
        return b;
    }

    public static Color Brand => Get("Brand");
    public static Color BrandLight => Get("BrandLight");
    public static Color BrandLip => Get("BrandLip");
    public static Color Text => Get("Text");
    public static Color Sub => Get("Sub");
    public static Color Muted => Get("Muted");

    public static Brush BrandBrush => Frozen(Brand);
    public static Brush TextBrush => Frozen(Text);

    /// <summary>Difficulty tier colour (EASY … SPECIAL).</summary>
    public static Color Tier(string? tier) => (tier ?? "").ToUpperInvariant() switch
    {
        "EASY" => Get("Easy"),
        "NORMAL" => Get("Normal"),
        "HARD" => Get("Hard"),
        "EXPERT" => Get("Expert"),
        "SPECIAL" => Get("Special"),
        _ => Brand,
    };

    public static string Hex(Color c) => $"#{c.R:X2}{c.G:X2}{c.B:X2}";
}
