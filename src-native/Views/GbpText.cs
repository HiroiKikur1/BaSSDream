using System;
using System.Globalization;
using System.Windows;
using System.Windows.Media;

namespace BassStation.Views;

/// <summary>
/// Game-style lettering: gradient fill, white rim, optional coloured outer rim and soft drop shadow
/// (the PERFECT / FULL COMBO! / NEW RECORD! labels of the live result screen).
/// </summary>
public class GbpText : FrameworkElement
{
    public string Text { get; set; } = "";
    public double Size { get; set; } = 24;
    public FontWeight Weight { get; set; } = FontWeights.Black;
    public bool Italic { get; set; }
    public Brush Fill { get; set; } = Brushes.Black;
    public double Rim { get; set; } = 4;                 // white rim thickness
    public Brush? Outer { get; set; }                     // drawn outside the white rim
    public double OuterWidth { get; set; } = 2;
    public double ShadowOpacity { get; set; } = 0.25;
    public FontFamily? Family { get; set; }

    private Geometry? _geo;

    private Geometry Build()
    {
        var family = Family ?? (FontFamily)Application.Current.FindResource("RoundFont");
        var ft = new FormattedText(Text, CultureInfo.InvariantCulture, FlowDirection.LeftToRight,
            new Typeface(family, Italic ? FontStyles.Italic : FontStyles.Normal, Weight, FontStretches.Normal),
            Size, Brushes.Black, VisualTreeHelper.GetDpi(this).PixelsPerDip);
        var g = ft.BuildGeometry(new Point(0, 0));
        if (Italic)
        {
            // the rounded face has no italic: slant it the way the game lettering is slanted
            g = g.Clone();
            g.Transform = new MatrixTransform(1, 0, -0.2, 1, 0.2 * ft.Height, 0);
            g = g.GetFlattenedPathGeometry();
        }
        return g;
    }

    private double Pad => Rim + (Outer != null ? OuterWidth : 0) + 2;

    protected override Size MeasureOverride(Size availableSize)
    {
        _geo = Build();
        var b = _geo.Bounds;
        if (b.IsEmpty) return new Size(0, 0);
        return new Size(b.Right + 2 * Pad, b.Bottom + 2 * Pad);
    }

    protected override void OnRender(DrawingContext dc)
    {
        _geo ??= Build();
        dc.PushTransform(new TranslateTransform(Pad, Pad));
        double rim = Rim * 2;
        if (ShadowOpacity > 0)
        {
            dc.PushTransform(new TranslateTransform(0, 2));
            dc.PushOpacity(ShadowOpacity);
            dc.DrawGeometry(Brushes.Black, new Pen(Brushes.Black, rim + (Outer != null ? OuterWidth * 2 : 0)) { LineJoin = PenLineJoin.Round }, _geo);
            dc.Pop();
            dc.Pop();
        }
        if (Outer != null)
            dc.DrawGeometry(null, new Pen(Outer, rim + OuterWidth * 2) { LineJoin = PenLineJoin.Round }, _geo);
        if (Rim > 0)
            dc.DrawGeometry(null, new Pen(Brushes.White, rim) { LineJoin = PenLineJoin.Round }, _geo);
        dc.DrawGeometry(Fill, null, _geo);
        dc.Pop();
    }

    public static LinearGradientBrush Vertical(params string[] hex)
    {
        var b = new LinearGradientBrush { StartPoint = new Point(0, 0), EndPoint = new Point(0, 1) };
        for (int i = 0; i < hex.Length; i++)
            b.GradientStops.Add(new GradientStop((Color)ColorConverter.ConvertFromString(hex[i]), hex.Length == 1 ? 0 : (double)i / (hex.Length - 1)));
        return b;
    }

    public static LinearGradientBrush Horizontal(params string[] hex)
    {
        var b = Vertical(hex);
        b.EndPoint = new Point(1, 0);
        return b;
    }
}
