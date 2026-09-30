using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using System.Windows;
using System.Windows.Media;

namespace BassStation.Views;

/// <summary>One beat of a bar for the standard staff: its x on the page (shared with the TAB below) and its notes.</summary>
public sealed class StaffBeat
{
    public double X;
    public double Pos;              // quarter notes from the bar start
    public int Value = 4;           // 1 whole, 2 half, 4 quarter, 8 eighth ...
    public int Dots;
    public int Tuplet;
    public List<(int Midi, bool Tie, bool Dead)> Notes = new();
    public bool IsRest => Notes.Count == 0;
}

/// <summary>
/// Bass-clef staff as in Guitar Pro's standard notation: written an octave above sounding pitch (clef marked 8),
/// spelled from the key signature (in-key letters first, naturals next, then sharps in sharp keys / flats in flat keys),
/// accidentals carried through the bar, flat beams per beat, flags, dots, tuplets, ties and ledger lines.
/// </summary>
public static class StaffDrawer
{
    public const double Gap = 8;                     // staff line spacing
    public const double Height = 4 * Gap;
    private const int BottomStep = 18;               // G2, bottom line of the bass clef
    private const int MiddleStep = 22;               // D3
    private static readonly int[] NaturalPc = { 0, 2, 4, 5, 7, 9, 11 };
    private static readonly int[] SharpOrder = { 3, 0, 4, 1, 5, 2, 6 };    // F C G D A E B
    private static readonly int[] FlatOrder = { 6, 2, 5, 1, 4, 0, 3 };     // B E A D G C F
    private static readonly int[] SharpSteps = { 24, 21, 25, 22, 19, 23, 20 };
    private static readonly int[] FlatSteps = { 20, 23, 19, 22, 18, 21, 17 };

    private static readonly Typeface Sym = new(new FontFamily("Segoe UI Symbol"), FontStyles.Normal, FontWeights.Normal, FontStretches.Normal);
    private static readonly Typeface Serif = new(new FontFamily("Times New Roman"), FontStyles.Normal, FontWeights.Bold, FontStretches.Normal);

    public static double Y(double top, int step) => top + Height - (step - BottomStep) * Gap / 2;

    public static int[] KeyAlterations(int accidentals)
    {
        var alt = new int[7];
        for (int i = 0; i < Math.Min(7, Math.Abs(accidentals)); i++)
        {
            if (accidentals > 0) alt[SharpOrder[i]] = 1; else alt[FlatOrder[i]] = -1;
        }
        return alt;
    }

    /// <summary>Written pitch (midi) -> staff step (C0 = 0, one per letter) and alteration.</summary>
    public static (int Step, int Alt) Spell(int writtenMidi, int accidentals, int[] keyAlt)
    {
        int pc = ((writtenMidi % 12) + 12) % 12;
        int letter = -1, alt = 0;
        for (int l = 0; l < 7 && letter < 0; l++)
            if ((NaturalPc[l] + keyAlt[l] + 12) % 12 == pc) { letter = l; alt = keyAlt[l]; }
        for (int l = 0; l < 7 && letter < 0; l++)
            if (NaturalPc[l] == pc) { letter = l; alt = 0; }
        if (letter < 0)
        {
            int d = accidentals >= 0 ? 1 : -1;
            for (int l = 0; l < 7 && letter < 0; l++)
                if ((NaturalPc[l] + d + 12) % 12 == pc) { letter = l; alt = d; }
        }
        int natural = writtenMidi - alt;
        int octave = (int)Math.Floor(natural / 12.0) - 1;
        return (octave * 7 + letter, alt);
    }

    public static double HeaderWidth(int accidentals, bool timeSig) => 30 + 8.5 * Math.Min(7, Math.Abs(accidentals)) + (timeSig ? 20 : 0) + 6;

    /// <summary>Staff lines from x0 to x1.</summary>
    public static void DrawLines(DrawingContext dc, double x0, double x1, double top, Pen pen)
    {
        for (int k = 0; k < 5; k++) dc.DrawLine(pen, new Point(x0, top + k * Gap), new Point(x1, top + k * Gap));
    }

    /// <summary>Clef (with the octave 8 below), key signature and, when given, the time signature.</summary>
    public static void DrawHeader(DrawingContext dc, double x, double top, int accidentals, (int Num, int Den)? time, Brush ink, double dpi)
    {
        DrawBassClef(dc, x + 4, top, ink);
        var eight = Text("8", Serif, 9, ink, dpi);
        dc.DrawText(eight, new Point(x + 12 - eight.Width / 2, top + Height + 1));
        double kx = x + 30;
        int n = Math.Min(7, Math.Abs(accidentals));
        for (int i = 0; i < n; i++)
        {
            int step = accidentals > 0 ? SharpSteps[i] : FlatSteps[i];
            DrawAccidental(dc, accidentals > 0 ? 1 : -1, kx + 4, Y(top, step), ink, dpi);
            kx += 8.5;
        }
        if (time is { } t)
        {
            var a = Text(t.Num.ToString(CultureInfo.InvariantCulture), Serif, 21, ink, dpi);
            var b = Text(t.Den.ToString(CultureInfo.InvariantCulture), Serif, 21, ink, dpi);
            double cx = kx + 9;
            dc.DrawText(a, new Point(cx - a.Width / 2, top + Gap - a.Height / 2 + 1));
            dc.DrawText(b, new Point(cx - b.Width / 2, top + 3 * Gap - b.Height / 2 + 1));
        }
    }

    /// <summary>F clef drawn as a shape (no music font needed): the curl starts on the F line, two dots straddle it.</summary>
    private static void DrawBassClef(DrawingContext dc, double x, double top, Brush ink)
    {
        double f = top + Gap;                         // F3 line
        var g = new StreamGeometry();
        using (var c = g.Open())
        {
            c.BeginFigure(new Point(x + 2.2, f + 1.2), true, true);
            c.BezierTo(new Point(x + 1, f - 7.5), new Point(x + 13, f - 10.5), new Point(x + 16.5, f - 2.5), true, true);
            c.BezierTo(new Point(x + 19.5, f + 6), new Point(x + 13, f + 17), new Point(x + 1.5, f + 23.5), true, true);
            c.LineTo(new Point(x + 1, f + 22.2), true, true);
            c.BezierTo(new Point(x + 10.5, f + 15.5), new Point(x + 14.5, f + 7), new Point(x + 13, f + 0.5), true, true);
            c.BezierTo(new Point(x + 12, f - 5.5), new Point(x + 6, f - 6.5), new Point(x + 4.5, f - 2.2), true, true);
        }
        g.Freeze();
        dc.DrawGeometry(ink, null, g);
        dc.DrawEllipse(ink, null, new Point(x + 3.8, f + 0.8), 3.3, 3.3);
        dc.DrawEllipse(ink, null, new Point(x + 21.5, f - Gap / 2), 1.7, 1.7);
        dc.DrawEllipse(ink, null, new Point(x + 21.5, f + Gap / 2), 1.7, 1.7);
    }

    private static void DrawAccidental(DrawingContext dc, int alt, double x, double y, Brush ink, double dpi)
    {
        var pen = new Pen(ink, 1.1);
        if (alt > 0)
        {
            // sharp: two verticals, two thick slanted bars
            dc.DrawLine(pen, new Point(x - 1.6, y - 7), new Point(x - 1.6, y + 8));
            dc.DrawLine(pen, new Point(x + 1.6, y - 8), new Point(x + 1.6, y + 7));
            var bar = new Pen(ink, 2);
            dc.DrawLine(bar, new Point(x - 3.8, y - 1.6), new Point(x + 3.8, y - 3.2));
            dc.DrawLine(bar, new Point(x - 3.8, y + 3.2), new Point(x + 3.8, y + 1.6));
        }
        else if (alt < 0)
        {
            dc.DrawLine(pen, new Point(x - 2.5, y - 11), new Point(x - 2.5, y + 3));
            var g = new StreamGeometry();
            using (var c = g.Open())
            {
                c.BeginFigure(new Point(x - 2.5, y - 1), true, true);
                c.BezierTo(new Point(x + 2, y - 5), new Point(x + 5.5, y - 1), new Point(x - 2.5, y + 3.2), true, true);
                c.LineTo(new Point(x - 2.5, y + 1.8), true, true);
                c.BezierTo(new Point(x + 3, y - 1), new Point(x + 1.5, y - 3.5), new Point(x - 2.5, y + 0.2), true, true);
            }
            g.Freeze();
            dc.DrawGeometry(ink, null, g);
        }
        else
        {
            dc.DrawLine(pen, new Point(x - 2, y - 7), new Point(x - 2, y + 3.5));
            dc.DrawLine(pen, new Point(x + 2, y - 3.5), new Point(x + 2, y + 7));
            var bar = new Pen(ink, 1.9);
            dc.DrawLine(bar, new Point(x - 2, y - 1.8), new Point(x + 2, y - 2.8));
            dc.DrawLine(bar, new Point(x - 2, y + 2.8), new Point(x + 2, y + 1.8));
        }
    }

    private static int Beams(int value) => value switch { 8 => 1, 16 => 2, 32 => 3, 64 => 4, >= 128 => 5, _ => 0 };

    /// <summary>
    /// One bar. <paramref name="lastHeads"/> carries each sounding pitch's last head across bars (for ties).
    /// </summary>
    public static void DrawBar(DrawingContext dc, IReadOnlyList<StaffBeat> beats, double top, int accidentals, bool compound,
                               Brush ink, Dictionary<int, Point> lastHeads, double dpi, double barRight)
    {
        var keyAlt = KeyAlterations(accidentals);
        var carried = new Dictionary<int, int>();            // step -> alteration in force in this bar
        var stemPen = new Pen(ink, 1.15);
        var linePen = new Pen(ink, 1.0);
        double groupLen = compound ? 1.5 : 1.0;

        // heads of every beat first: they decide stems and beams
        var heads = new List<List<(double Y, int Step, bool Dead, int Midi, bool Tie)>>();
        foreach (var bt in beats)
        {
            var hs = new List<(double, int, bool, int, bool)>();
            foreach (var n in bt.Notes.OrderBy(n => n.Midi))
            {
                var (step, alt) = Spell(n.Midi + 12, accidentals, keyAlt);
                hs.Add((Y(top, step), step, n.Dead, n.Midi, n.Tie));
                int inForce = carried.TryGetValue(step, out int a) ? a : keyAlt[((step % 7) + 7) % 7];
                if (!n.Tie && alt != inForce && !n.Dead) DrawAccidental(dc, alt, bt.X - 11.5, Y(top, step), ink, dpi);
                carried[step] = alt;
            }
            heads.Add(hs);
        }

        // beam groups: consecutive sounding beats shorter than a quarter inside one beat
        var groups = new List<List<int>>();
        for (int i = 0; i < beats.Count; i++)
        {
            var bt = beats[i];
            if (bt.IsRest || Beams(bt.Value) == 0) continue;
            int g = (int)Math.Floor((bt.Pos + 1e-6) / groupLen);
            var last = groups.Count > 0 ? groups[^1] : null;
            if (last != null && last[^1] == i - 1 && (int)Math.Floor((beats[i - 1].Pos + 1e-6) / groupLen) == g) last.Add(i);
            else groups.Add(new List<int> { i });
        }
        var groupOf = new Dictionary<int, List<int>>();
        foreach (var g in groups) foreach (int i in g) groupOf[i] = g;

        bool StemUp(IEnumerable<int> idx) => idx.SelectMany(i => heads[i]).Select(h => h.Step).DefaultIfEmpty(MiddleStep).Average() < MiddleStep;

        var stemTip = new Dictionary<int, double>();
        var stemX = new Dictionary<int, double>();
        var upOf = new Dictionary<int, bool>();
        foreach (var g in groups.Where(g => g.Count > 1))
        {
            bool up = StemUp(g);
            double tip = up ? g.Min(i => heads[i].Min(h => h.Y)) - 3.5 * Gap : g.Max(i => heads[i].Max(h => h.Y)) + 3.5 * Gap;
            // keep beams off the far side of the staff
            tip = up ? Math.Min(tip, top + 2 * Gap) : Math.Max(tip, top + 2 * Gap);
            foreach (int i in g) { stemTip[i] = tip; upOf[i] = up; }
        }

        for (int i = 0; i < beats.Count; i++)
        {
            var bt = beats[i];
            double x = bt.X;
            if (bt.IsRest)
            {
                DrawRest(dc, bt.Value, x, top, ink, dpi);
                continue;
            }
            bool up = upOf.TryGetValue(i, out bool u) ? u : StemUp(new[] { i });
            bool hollow = bt.Value <= 2;
            // ledger lines
            foreach (var h in heads[i])
            {
                for (int s = BottomStep - 2; s >= h.Step; s -= 2) dc.DrawLine(linePen, new Point(x - 8, Y(top, s)), new Point(x + 8, Y(top, s)));
                for (int s = BottomStep + 10; s <= h.Step; s += 2) dc.DrawLine(linePen, new Point(x - 8, Y(top, s)), new Point(x + 8, Y(top, s)));
            }
            foreach (var h in heads[i])
            {
                if (h.Dead) DrawX(dc, x, h.Y, ink);
                else DrawHead(dc, x, h.Y, hollow, ink);
                for (int d = 0; d < bt.Dots; d++)
                {
                    double dy = h.Step % 2 == 0 ? h.Y - Gap / 2 : h.Y;    // dots sit in spaces
                    dc.DrawEllipse(ink, null, new Point(x + 9 + d * 4.5, dy), 1.6, 1.6);
                }
                if (h.Tie && lastHeads.TryGetValue(h.Midi, out var p) && p.X < x) DrawTie(dc, p, new Point(x, h.Y), !up, ink);
                lastHeads[h.Midi] = new Point(x, h.Y);
            }
            if (bt.Value <= 1) continue;
            double sx = up ? x + 4.3 : x - 4.3;
            double far = up ? heads[i].Min(h => h.Y) : heads[i].Max(h => h.Y);
            double near = up ? heads[i].Max(h => h.Y) : heads[i].Min(h => h.Y);
            double tip2 = stemTip.TryGetValue(i, out double t) ? t : far + (up ? -3.5 * Gap : 3.5 * Gap);
            dc.DrawLine(stemPen, new Point(sx, near), new Point(sx, tip2));
            stemX[i] = sx;
            stemTip[i] = tip2;
            if (!groupOf.TryGetValue(i, out var grp) || grp.Count == 1)
            {
                for (int k = 0; k < Beams(bt.Value); k++) DrawFlag(dc, sx, tip2 + (up ? k * 6 : -k * 6), up, ink);
            }
        }

        foreach (var g in groups.Where(g => g.Count > 1))
        {
            bool up = upOf[g[0]];
            double y0 = stemTip[g[0]];
            double th = 3.6, step = 6.2;
            for (int k = 0; k < 5; k++)
            {
                double y = up ? y0 + k * step : y0 - k * step - th;
                for (int a = 0; a < g.Count; a++)
                {
                    int i = g[a];
                    if (Beams(beats[i].Value) <= k) continue;
                    bool next = a < g.Count - 1 && Beams(beats[g[a + 1]].Value) > k;
                    bool prev = a > 0 && Beams(beats[g[a - 1]].Value) > k;
                    if (next) dc.DrawRectangle(ink, null, new Rect(stemX[i] - 0.6, y, stemX[g[a + 1]] - stemX[i] + 1.2, th));
                    else if (!prev && k > 0)
                    {
                        double w = 7;
                        double hx = a < g.Count - 1 ? stemX[i] : stemX[i] - w;
                        dc.DrawRectangle(ink, null, new Rect(hx - 0.6, y, w + 1.2, th));
                    }
                }
            }
            if (beats[g[0]].Tuplet > 0)
            {
                var tt = Text(beats[g[0]].Tuplet.ToString(CultureInfo.InvariantCulture), Serif, 11, ink, dpi);
                double cx = (stemX[g[0]] + stemX[g[^1]]) / 2;
                dc.DrawText(tt, new Point(cx - tt.Width / 2, up ? y0 - tt.Height - 1 : y0 + 2));
            }
        }
    }

    private static void DrawHead(DrawingContext dc, double x, double y, bool hollow, Brush ink)
    {
        var e = new EllipseGeometry(new Point(x, y), 5.2, 3.7) { Transform = new RotateTransform(-22, x, y) };
        if (!hollow) { dc.DrawGeometry(ink, null, e); return; }
        var inner = new EllipseGeometry(new Point(x, y), 4.2, 1.9) { Transform = new RotateTransform(-35, x, y) };
        dc.DrawGeometry(ink, null, new CombinedGeometry(GeometryCombineMode.Exclude, e, inner));
    }

    private static void DrawX(DrawingContext dc, double x, double y, Brush ink)
    {
        var pen = new Pen(ink, 1.5);
        dc.DrawLine(pen, new Point(x - 3.8, y - 3.8), new Point(x + 3.8, y + 3.8));
        dc.DrawLine(pen, new Point(x - 3.8, y + 3.8), new Point(x + 3.8, y - 3.8));
    }

    private static void DrawFlag(DrawingContext dc, double sx, double tip, bool up, Brush ink)
    {
        var g = new StreamGeometry();
        double d = up ? 1 : -1;
        using (var c = g.Open())
        {
            c.BeginFigure(new Point(sx, tip), true, true);
            c.BezierTo(new Point(sx + 1, tip + 6 * d), new Point(sx + 9, tip + 9 * d), new Point(sx + 6.5, tip + 17 * d), true, true);
            c.BezierTo(new Point(sx + 7.5, tip + 11 * d), new Point(sx + 3, tip + 8.5 * d), new Point(sx, tip + 6.5 * d), true, true);
        }
        g.Freeze();
        dc.DrawGeometry(ink, null, g);
    }

    private static void DrawTie(DrawingContext dc, Point a, Point b, bool below, Brush ink)
    {
        double d = below ? 1 : -1;
        var g = new StreamGeometry();
        using (var c = g.Open())
        {
            var p0 = new Point(a.X + 5, a.Y + 4 * d);
            var p1 = new Point(b.X - 5, b.Y + 4 * d);
            double mx = (p0.X + p1.X) / 2;
            c.BeginFigure(p0, true, true);
            c.BezierTo(new Point(mx - (p1.X - p0.X) / 4, p0.Y + 7 * d), new Point(mx + (p1.X - p0.X) / 4, p1.Y + 7 * d), p1, true, true);
            c.BezierTo(new Point(mx + (p1.X - p0.X) / 4, p1.Y + 5 * d), new Point(mx - (p1.X - p0.X) / 4, p0.Y + 5 * d), p0, true, true);
        }
        g.Freeze();
        dc.DrawGeometry(ink, null, g);
    }

    private static void DrawRest(DrawingContext dc, int value, double x, double top, Brush ink, double dpi)
    {
        double mid = top + 2 * Gap;
        switch (value)
        {
            case <= 1:
                dc.DrawRectangle(ink, null, new Rect(x - 5, top + Gap, 10, Gap / 2));
                return;
            case 2:
                dc.DrawRectangle(ink, null, new Rect(x - 5, mid - Gap / 2, 10, Gap / 2));
                return;
            case 4:
            {
                var g = new StreamGeometry();
                using (var c = g.Open())
                {
                    c.BeginFigure(new Point(x - 2, mid - 12), false, false);
                    c.LineTo(new Point(x + 3, mid - 5), true, true);
                    c.LineTo(new Point(x - 1.5, mid + 1), true, true);
                    c.LineTo(new Point(x + 3, mid + 7), true, true);
                    c.BezierTo(new Point(x - 3, mid + 4), new Point(x - 4, mid + 10), new Point(x + 0.5, mid + 13), true, true);
                }
                g.Freeze();
                dc.DrawGeometry(null, new Pen(ink, 2.6) { StartLineCap = PenLineCap.Round, EndLineCap = PenLineCap.Round, LineJoin = PenLineJoin.Round }, g);
                return;
            }
            default:
            {
                int flags = Beams(value);
                var pen = new Pen(ink, 1.3);
                double y0 = mid - 4;
                dc.DrawLine(pen, new Point(x + 3.5, y0 - 3), new Point(x - 1.5, y0 + 8 + (flags - 1) * 7));
                for (int k = 0; k < flags; k++)
                {
                    double yy = y0 + k * 7;
                    dc.DrawEllipse(ink, null, new Point(x - 2.2, yy - 1.2), 2.2, 2.2);
                    dc.DrawLine(pen, new Point(x - 2.2, yy - 0.5), new Point(x + 3.5 - k * 1.2, yy - 3 + k * 0.4));
                }
                return;
            }
        }
    }

    private static FormattedText Text(string s, Typeface tf, double size, Brush ink, double dpi) =>
        new(s, CultureInfo.InvariantCulture, FlowDirection.LeftToRight, tf, size, ink, dpi);
}
