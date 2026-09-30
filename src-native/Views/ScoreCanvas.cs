using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using System.Text.Json;
using System.Windows;
using System.Windows.Media;
using System.Windows.Media.Imaging;
using BassStation.Models;
using BassStation.Services;

namespace BassStation.Views;

/// <summary>
/// The score as a page of a commemorative score book: running head, outlined title in the band's colours, foil rule,
/// ribbon bookmark, then systems of standard notation over TAB (as Guitar Pro lays them out). Selection (a cell and a
/// bar range) and low-confidence marks. The play cursor is an overlay placed from <see cref="CursorAt"/>.
/// </summary>
public class ScoreCanvas : FrameworkElement
{
    public const double PageWidth = 1180;
    private const double PageTop = 14;          // the bookmark hangs above the page edge
    private const double Mx = 58;               // page margins
    private const double TitleH = 170;
    private const double TabGap = 15;
    private const double SysGap = 22;

    public static readonly FontFamily Round = new(new Uri(System.IO.Path.Combine(AppPaths.Assets, "fonts") + System.IO.Path.DirectorySeparatorChar), "./#Resource Han Rounded CN, Microsoft YaHei UI");
    private static readonly Typeface RoundBold = new(Round, FontStyles.Normal, FontWeights.Bold, FontStretches.Normal);
    private static readonly Typeface RoundHeavy = new(Round, FontStyles.Normal, FontWeights.Black, FontStretches.Normal);
    private static readonly Typeface Din = new(new FontFamily("Bahnschrift"), FontStyles.Normal, FontWeights.SemiBold, FontStretches.Normal);
    private static readonly Typeface DinBold = new(new FontFamily("Bahnschrift"), FontStyles.Normal, FontWeights.Bold, FontStretches.Normal);

    private static readonly Color Ink = C("#2B2630"), Sub = C("#8A8089"), Hair = C("#D3C8CF"), Rule = C("#2E2932"),
        Doubt = C("#FF9A1F"), Desk = C("#E7E1E6");

    private BassScore? _score;
    private BandEdition _ed = BandEdition.Default;
    private double _dpi = 1;
    private static ImageBrush? _grain;

    public record BarPlace(int Index, double X, double W, double Y, List<double> Xs, bool RowStart);
    private readonly List<BarPlace> _bars = new();
    private readonly List<(double Y, double X0, double X1, int First, double HeaderW)> _rows = new();
    private double _height;

    public (int Bar, int Beat, int Str)? Selection { get; set; }
    /// <summary>Selected bar range (inclusive), for looping.</summary>
    public (int A, int B)? Range { get; set; }
    public bool ShowStaff { get; set; } = true;
    public string Title { get; set; } = "";
    public string Subtitle { get; set; } = "";
    public string Artist { get; set; } = "";
    public string Tier { get; set; } = "";
    public int Level { get; set; }
    public double Bpm { get; set; }

    public BassScore? Score
    {
        get => _score;
        set { _score = value; Relayout(); }
    }

    public BandEdition Edition
    {
        get => _ed;
        set { _ed = value; InvalidateVisual(); }
    }

    private int Strings => Math.Max(4, _score?.Strings ?? 4);
    private double TabH => (Strings - 1) * TabGap;
    private double StaffTopOff => ShowStaff ? 50 : 0;
    private double TabTopOff => ShowStaff ? StaffTopOff + StaffDrawer.Height + 44 : 34;
    public double RowH => TabTopOff + TabH + (ShowStaff ? 22 : 48) + SysGap;

    public static Color C(string hex) => (Color)ColorConverter.ConvertFromString(hex);

    public void Relayout()
    {
        InvalidateMeasure();
        InvalidateVisual();
    }

    // ---------------------------------------------------------------- key / meter helpers

    private int KeyOf(int bar)
    {
        if (_score == null) return 0;
        for (int i = bar; i >= 0; i--)
        {
            var ex = _score.Bars[i].Extra;
            if (ex != null && ex.TryGetValue("key", out var k) && k.ValueKind == JsonValueKind.Array && k.GetArrayLength() > 0) return k[0].GetInt32();
        }
        return _score.Key.Count > 0 && _score.Key[0].ValueKind == JsonValueKind.Number ? _score.Key[0].GetInt32() : 0;
    }

    private bool MeterChanges(int bar) => bar == 0 || _score!.Bars[bar].Num != _score.Bars[bar - 1].Num || _score.Bars[bar].Den != _score.Bars[bar - 1].Den;

    private static bool Compound(ScoreBar b) => b.Den == 8 && b.Num % 3 == 0 && b.Num >= 6;

    // ---------------------------------------------------------------- layout

    private double Slot(ScoreBeat bt) => (ShowStaff ? 22 : 18) + 30 * Math.Sqrt(Math.Max(bt.Dur / (double)_score!.Tpq, 0.03));

    private double NaturalWidth(int i, bool rowStart)
    {
        var b = _score!.Bars[i];
        double w = Math.Max(90, 26 + b.Beats.Sum(Slot));
        if (ShowStaff && !rowStart && MeterChanges(i)) w += 24;
        return w;
    }

    private double HeaderWidth(int bar) => ShowStaff ? StaffDrawer.HeaderWidth(KeyOf(bar), MeterChanges(bar)) : 30;

    protected override Size MeasureOverride(Size available)
    {
        _dpi = VisualTreeHelper.GetDpi(this).PixelsPerDip;
        Layout();
        return new Size(PageWidth + 40, _height);
    }

    private double PageX => 20;

    private void Layout()
    {
        _bars.Clear();
        _rows.Clear();
        double y = PageTop + TitleH;
        if (_score == null || _score.Bars.Count == 0) { _height = y + 80; return; }
        double x0 = PageX + Mx, x1 = PageX + PageWidth - Mx;
        var row = new List<int>();
        double used = 0;
        void Flush(bool last)
        {
            if (row.Count == 0) return;
            double hw = HeaderWidth(row[0]);
            double avail = x1 - x0 - hw;
            double scale = last && used < 0.7 * avail ? 1.0 : avail / used;
            double x = x0 + hw;
            for (int k = 0; k < row.Count; k++)
            {
                int i = row[k];
                var b = _score.Bars[i];
                double w = NaturalWidth(i, k == 0) * scale;
                double lead = ShowStaff && k > 0 && MeterChanges(i) ? 24 * scale : 0;
                var xs = new List<double>();
                double off = 0, pad = 14 * scale;
                foreach (var bt in b.Beats)
                {
                    xs.Add(x + lead + pad + (off + 8) * scale);
                    off += Slot(bt);
                }
                _bars.Add(new BarPlace(i, x, w, y, xs, k == 0));
                x += w;
            }
            _rows.Add((y, x0, x, row[0], hw));
            y += RowH;
            row.Clear();
            used = 0;
        }
        double availFirst = x1 - x0;
        for (int i = 0; i < _score.Bars.Count; i++)
        {
            double hw = row.Count == 0 ? HeaderWidth(i) : HeaderWidth(row[0]);
            double w = NaturalWidth(i, row.Count == 0);
            // a named section starts a new system, as rehearsal marks do on paper
            if (row.Count > 0 && (used + w > availFirst - hw || _score.Bars[i].Section != null)) Flush(false);
            row.Add(i);
            used += NaturalWidth(i, row.Count == 1);
        }
        Flush(true);
        _height = y + 60;
    }

    // ---------------------------------------------------------------- geometry for the view

    public BarPlace? PlaceOf(int bar) => bar >= 0 && bar < _bars.Count ? _bars[bar] : null;

    /// <summary>Play cursor (x, top, height) at a recording time, interpolated between the beats of its bar.</summary>
    public (double X, double Y, double H)? CursorAt(double t)
    {
        if (_score == null || _bars.Count == 0) return null;
        int bi = _score.BarAt(t);
        var b = _score.Bars[bi];
        var p = _bars[bi];
        int len = b.Length(_score.Tpq);
        double tick = Math.Clamp((t - b.T0) / Math.Max(1e-6, b.T1 - b.T0), 0, 1) * len;
        var ticks = b.Beats.Select(bt => (double)bt.Tick).Append(len).ToList();
        var xs = p.Xs.Append(p.X + p.W - 4).ToList();
        double x = p.X + p.W * tick / Math.Max(1, len);
        for (int k = 0; k + 1 < ticks.Count && b.Beats.Count > 0; k++)
        {
            if (tick < ticks[k + 1] || k + 2 == ticks.Count)
            {
                double f = (tick - ticks[k]) / Math.Max(1, ticks[k + 1] - ticks[k]);
                x = xs[k] + Math.Clamp(f, 0, 1) * (xs[k + 1] - xs[k]);
                break;
            }
        }
        double top = p.Y + (ShowStaff ? StaffTopOff - 14 : TabTopOff - 10);
        return (x, top, p.Y + TabTopOff + TabH + 10 - top);
    }

    /// <summary>Cell under a point: nearest beat of the bar; on the TAB the nearest string, on the staff the current one.</summary>
    public (int Bar, int Beat, int Str)? HitTest(Point pt)
    {
        if (_score == null) return null;
        foreach (var p in _bars)
        {
            if (pt.Y < p.Y || pt.Y > p.Y + RowH || pt.X < p.X || pt.X > p.X + p.W) continue;
            if (p.Xs.Count == 0) return null;
            int beat = 0;
            for (int k = 1; k < p.Xs.Count; k++)
                if (Math.Abs(p.Xs[k] - pt.X) < Math.Abs(p.Xs[beat] - pt.X)) beat = k;
            double tabTop = p.Y + TabTopOff;
            int str;
            if (pt.Y >= tabTop - TabGap)
                str = Strings - 1 - Math.Clamp((int)Math.Round((pt.Y - tabTop) / TabGap), 0, Strings - 1);
            else
            {
                var notes = _score.Bars[p.Index].Beats[beat].Notes;
                str = Selection?.Str ?? (notes.Count > 0 ? notes[0].S : 0);
            }
            return (p.Index, beat, str);
        }
        return null;
    }

    // ---------------------------------------------------------------- drawing

    /// <summary>Shared paper grain (tiled noise), for every paper surface.</summary>
    internal static ImageBrush Grain()
    {
        if (_grain != null) return _grain;
        const int n = 160;
        var px = new byte[n * n * 4];
        var rng = new Random(11);
        for (int i = 0; i < n * n; i++)
        {
            byte v = (byte)rng.Next(60, 120);
            px[i * 4] = v; px[i * 4 + 1] = (byte)(v * 0.85); px[i * 4 + 2] = (byte)(v * 0.95);
            px[i * 4 + 3] = (byte)rng.Next(0, 22);
        }
        var bmp = BitmapSource.Create(n, n, 96, 96, PixelFormats.Bgra32, null, px, n * 4);
        bmp.Freeze();
        _grain = new ImageBrush(bmp) { TileMode = TileMode.Tile, Viewport = new Rect(0, 0, n, n), ViewportUnits = BrushMappingMode.Absolute, Opacity = 0.5 };
        _grain.Freeze();
        return _grain;
    }

    protected override void OnRender(DrawingContext dc)
    {
        double W = PageWidth;
        double px = PageX, pageH = Math.Max(300, _height - PageTop - 20);
        var page = new Rect(px, PageTop, W, pageH);
        // soft contact shadow under the sheet
        for (int k = 1; k <= 4; k++)
            dc.DrawRectangle(new SolidColorBrush(Color.FromArgb((byte)(16 - k * 3), 60, 30, 45)), null, new Rect(px - k + 1, PageTop + k, W + 2 * k - 2, pageH + k));
        dc.DrawRectangle(new SolidColorBrush(_ed.Paper), null, page);
        dc.DrawRectangle(Grain(), null, page);

        DrawTitleBlock(dc, px, W);
        if (_score == null) return;

        var ink = new SolidColorBrush(Ink);
        var hair = Frozen(new Pen(new SolidColorBrush(Hair), 0.8));
        var rule = Frozen(new Pen(ink, 1.15));
        var lastHeads = new Dictionary<int, Point>();

        foreach (var r in _rows)
        {
            double y = r.Y, staffTop = y + StaffTopOff, tabTop = y + TabTopOff, tabBot = tabTop + TabH;
            var rowBars = _bars.Where(b => b.Y == y).ToList();
            double xEnd = rowBars[^1].X + rowBars[^1].W;

            // selection range wash
            foreach (var p in rowBars)
            {
                var sb = _score.Bars[p.Index];
                double top = y + (ShowStaff ? StaffTopOff - 16 : TabTopOff - 12), bot = tabBot + 12;
                if (Range is { } rg && p.Index >= rg.A && p.Index <= rg.B)
                    dc.DrawRectangle(new SolidColorBrush(Color.FromArgb(150, _ed.Tint.R, _ed.Tint.G, _ed.Tint.B)), null, new Rect(p.X, top, p.W, bot - top));
                if (sb.Filled != sb.Length(_score.Tpq))
                    dc.DrawRectangle(new SolidColorBrush(Color.FromArgb(44, 255, 51, 119)), null, new Rect(p.X, tabTop - 6, p.W, TabH + 12));
            }

            // staves
            if (ShowStaff)
            {
                StaffDrawer.DrawLines(dc, r.X0, xEnd, staffTop, hair);
                StaffDrawer.DrawHeader(dc, r.X0 + 2, staffTop, KeyOf(r.First), MeterChanges(r.First) ? (_score.Bars[r.First].Num, _score.Bars[r.First].Den) : null, ink, _dpi);
                dc.DrawLine(rule, new Point(r.X0, staffTop), new Point(r.X0, staffTop + StaffDrawer.Height));
            }
            for (int s = 0; s < Strings; s++) dc.DrawLine(hair, new Point(r.X0, tabTop + s * TabGap), new Point(xEnd, tabTop + s * TabGap));
            double ty = tabTop + TabH / 2 - 21;
            foreach (var ch in new[] { "T", "A", "B" })
            {
                var t = Text(ch, DinBold, 12.5, Sub);
                dc.DrawText(t, new Point(r.X0 + 10 - t.Width / 2, ty));
                ty += 14;
            }
            dc.DrawLine(rule, new Point(r.X0, tabTop), new Point(r.X0, tabBot));
            if (ShowStaff) dc.DrawLine(rule, new Point(r.X0, staffTop), new Point(r.X0, tabBot));   // system bracket line

            foreach (var p in rowBars) DrawBar(dc, p, staffTop, tabTop, tabBot, ink, rule, lastHeads);
        }
    }

    private void DrawTitleBlock(DrawingContext dc, double px, double W)
    {
        var main = new SolidColorBrush(_ed.Main);
        double x0 = px + Mx, x1 = px + W - Mx;
        // running head
        var head = Text($"BaSSDream 贝斯谱集　{_ed.Name} 版", RoundBold, 11.5, Sub);
        dc.DrawText(head, new Point(x0, PageTop + 22));
        var folio = Text(_score == null ? "" : $"{_score.Bars.Count} 小节", DinBold, 12, _ed.Main);
        dc.DrawText(folio, new Point(x1 - 60 - folio.Width, PageTop + 22));
        dc.DrawLine(Frozen(new Pen(new SolidColorBrush(Hair), 0.75)), new Point(x0, PageTop + 42), new Point(x1 - 52, PageTop + 42));

        // ribbon bookmark over the top edge
        double bx = x1 - 36;
        var rib = new StreamGeometry();
        using (var c = rib.Open())
        {
            c.BeginFigure(new Point(bx, 0), true, true);
            c.LineTo(new Point(bx + 26, 0), true, false);
            c.LineTo(new Point(bx + 26, 132), true, false);
            c.LineTo(new Point(bx + 13, 121), true, false);
            c.LineTo(new Point(bx, 132), true, false);
        }
        rib.Freeze();
        dc.DrawGeometry(main, null, rib);
        dc.DrawRectangle(new SolidColorBrush(Color.FromArgb(110, _ed.Deep.R, _ed.Deep.G, _ed.Deep.B)), null, new Rect(bx, 0, 26, PageTop));

        // centred title, outlined gradient as on the GBP result screen
        double cx = px + W / 2;
        var ft = Text(Title, RoundHeavy, 38, Colors.Black);
        var geo = ft.BuildGeometry(new Point(cx - ft.Width / 2, PageTop + 60));
        dc.DrawGeometry(null, new Pen(Brushes.White, 8) { LineJoin = PenLineJoin.Round }, geo);
        dc.DrawGeometry(_ed.TitleBrush(), null, geo);
        double titleRight = cx + ft.Width / 2;
        if (Subtitle.Length > 0)
        {
            var st = Text(Subtitle, RoundBold, 13.5, Sub);
            dc.DrawText(st, new Point(cx - st.Width / 2, PageTop + 108));
        }
        dc.DrawRectangle(_ed.FoilBrush(), null, new Rect(cx - 120, PageTop + 134, 240, 3));
        var dia = new StreamGeometry();
        using (var c = dia.Open())
        {
            c.BeginFigure(new Point(cx, PageTop + 129.5), true, true);
            c.LineTo(new Point(cx + 6, PageTop + 135.5), true, false);
            c.LineTo(new Point(cx, PageTop + 141.5), true, false);
            c.LineTo(new Point(cx - 6, PageTop + 135.5), true, false);
        }
        dia.Freeze();
        dc.DrawGeometry(_ed.FoilBrush(), null, dia);
        // sparkles (the "rich" decoration level the user picked)
        Star(dc, titleRight + 22, PageTop + 66, 9, new SolidColorBrush(_ed.Title[0]));
        Star(dc, titleRight + 38, PageTop + 86, 4.5, main);
        Star(dc, cx - ft.Width / 2 - 26, PageTop + 92, 5, new SolidColorBrush(_ed.Title[1]));
        Star(dc, titleRight + 8, PageTop + 104, 3, new SolidColorBrush(_ed.Title[2]));

        // tier ribbon + level (left), credits (right) as a score's composer block
        var tierCol = C(Tier.ToUpperInvariant() switch { "EASY" => "#3E8BFF", "NORMAL" => "#34C274", "HARD" => "#FF9F1A", "EXPERT" => "#F02B4B", "SPECIAL" => "#E04BD6", _ => "#FF3377" });
        var tag = Text($"{Tier.ToUpperInvariant()}  Lv.{Level}", DinBold, 13, Colors.White);
        double tw = tag.Width + 30, ty = PageTop + 66;
        var rg = new StreamGeometry();
        using (var c = rg.Open())
        {
            c.BeginFigure(new Point(x0, ty), true, true);
            c.LineTo(new Point(x0 + tw, ty), true, false);
            c.LineTo(new Point(x0 + tw - 8, ty + 12), true, false);
            c.LineTo(new Point(x0 + tw, ty + 24), true, false);
            c.LineTo(new Point(x0, ty + 24), true, false);
        }
        rg.Freeze();
        dc.DrawGeometry(new SolidColorBrush(tierCol), null, rg);
        dc.DrawText(tag, new Point(x0 + 11, ty + 12 - tag.Height / 2));
        Tape(dc, x0 + tw - 20, ty - 9, 40, 16);

        double rx = x1 - 60;
        var a = Text(Artist, RoundHeavy, 15, Ink);
        dc.DrawText(a, new Point(rx - a.Width, PageTop + 60));
        var m = Text($"{Strings}弦　♩ = {Math.Round(Bpm)}", Din, 13, Sub);
        dc.DrawText(m, new Point(rx - m.Width, PageTop + 84));
    }

    private void Tape(DrawingContext dc, double x, double y, double w, double h)
    {
        dc.PushTransform(new RotateTransform(18, x + w / 2, y + h / 2));
        dc.DrawRectangle(new SolidColorBrush(Color.FromArgb(120, _ed.Title[0].R, _ed.Title[0].G, _ed.Title[0].B)), null, new Rect(x, y, w, h));
        dc.Pop();
    }

    private static void Star(DrawingContext dc, double x, double y, double r, Brush fill)
    {
        double k = r * 0.28;
        var g = new StreamGeometry();
        using (var c = g.Open())
        {
            c.BeginFigure(new Point(x, y - r), true, true);
            c.QuadraticBezierTo(new Point(x + k, y - k), new Point(x + r, y), true, true);
            c.QuadraticBezierTo(new Point(x + k, y + k), new Point(x, y + r), true, true);
            c.QuadraticBezierTo(new Point(x - k, y + k), new Point(x - r, y), true, true);
            c.QuadraticBezierTo(new Point(x - k, y - k), new Point(x, y - r), true, true);
        }
        g.Freeze();
        dc.DrawGeometry(fill, null, g);
    }

    private void DrawBar(DrawingContext dc, BarPlace p, double staffTop, double tabTop, double tabBot, Brush ink, Pen rule, Dictionary<int, Point> lastHeads)
    {
        var b = _score!.Bars[p.Index];
        double xr = p.X + p.W;
        dc.DrawLine(rule, new Point(xr, ShowStaff ? staffTop : tabTop), new Point(xr, ShowStaff ? staffTop + StaffDrawer.Height : tabBot));
        dc.DrawLine(rule, new Point(xr, tabTop), new Point(xr, tabBot));

        // header: bar number (every system start and every bar in small type), section sticker, review tick
        double ay = p.Y + 6;
        var num = Text((p.Index + 1).ToString(CultureInfo.InvariantCulture), Din, p.RowStart ? 12 : 10.5, Sub);
        dc.DrawText(num, new Point(p.RowStart ? p.X - HeaderWidthAt(p) + 2 : p.X + 3, ay));
        double tx = p.X + 6 + (p.RowStart ? 0 : num.Width + 4);
        if (b.Section != null)
        {
            var t = Text(b.Section, RoundHeavy, 12, Colors.White);
            double w = t.Width + 18, h = 22;
            dc.PushTransform(new RotateTransform(-3, tx + w / 2, ay + h / 2));
            dc.DrawRectangle(new SolidColorBrush(Color.FromArgb(40, 60, 20, 40)), null, new Rect(tx + 1.5, ay + 2.5, w, h));
            dc.DrawRectangle(Brushes.White, null, new Rect(tx, ay, w, h));
            dc.DrawRectangle(new SolidColorBrush(_ed.Main), null, new Rect(tx + 3, ay + 3, w - 6, h - 6));
            dc.DrawText(t, new Point(tx + 9, ay + h / 2 - t.Height / 2));
            dc.Pop();
            Tape(dc, tx + w - 14, ay - 7, 30, 12);
        }

        if (ShowStaff && !p.RowStart && MeterChanges(p.Index))
        {
            var a = Text(b.Num.ToString(CultureInfo.InvariantCulture), new Typeface(new FontFamily("Times New Roman"), FontStyles.Normal, FontWeights.Bold, FontStretches.Normal), 21, Ink);
            var d = Text(b.Den.ToString(CultureInfo.InvariantCulture), new Typeface(new FontFamily("Times New Roman"), FontStyles.Normal, FontWeights.Bold, FontStretches.Normal), 21, Ink);
            dc.DrawText(a, new Point(p.X + 8, staffTop + StaffDrawer.Gap - a.Height / 2 + 1));
            dc.DrawText(d, new Point(p.X + 8, staffTop + 3 * StaffDrawer.Gap - d.Height / 2 + 1));
        }

        // selected cell
        if (Selection is { } sel && sel.Bar == p.Index && sel.Beat < p.Xs.Count)
        {
            double sx = p.Xs[sel.Beat], sy = tabTop + (Strings - 1 - sel.Str) * TabGap;
            double top = ShowStaff ? staffTop - 12 : tabTop - 10;
            dc.DrawRectangle(new SolidColorBrush(Color.FromArgb(46, _ed.Main.R, _ed.Main.G, _ed.Main.B)), null, new Rect(sx - 12, top, 24, tabBot + 10 - top));
            dc.DrawRectangle(null, Frozen(new Pen(new SolidColorBrush(_ed.Main), 1.6)), new Rect(sx - 12, sy - 9.5, 24, 19));
        }

        // standard notation
        if (ShowStaff)
        {
            var beats = new List<StaffBeat>();
            for (int i = 0; i < b.Beats.Count; i++)
            {
                var bt = b.Beats[i];
                var sb = new StaffBeat { X = p.Xs[i], Pos = bt.Tick / (double)_score.Tpq, Value = bt.Value, Dots = bt.Dots, Tuplet = bt.Tuplet };
                foreach (var n in bt.Notes) sb.Notes.Add((n.Midi, n.Tie, n.X));
                beats.Add(sb);
            }
            StaffDrawer.DrawBar(dc, beats, staffTop, KeyOf(p.Index), Compound(b), ink, lastHeads, _dpi, xr);
        }

        // TAB numbers
        for (int i = 0; i < b.Beats.Count; i++)
        {
            var bt = b.Beats[i];
            double x = p.Xs[i];
            if (bt.IsRest && !ShowStaff)
            {
                var r = Text(bt.Value switch { <= 1 => "\U0001D13B", 2 => "\U0001D13C", 4 => "\U0001D13D", 8 => "\U0001D13E", 16 => "\U0001D13F", _ => "\U0001D140" },
                             new Typeface("Segoe UI Symbol"), 22, Sub);
                dc.DrawText(r, new Point(x - r.Width / 2, (tabTop + tabBot) / 2 - r.Height / 2));
            }
            foreach (var n in bt.Notes)
            {
                if (n.S < 0 || n.S >= Strings) continue;
                DrawFret(dc, n, x, tabTop + (Strings - 1 - n.S) * TabGap);
            }
        }
        if (!ShowStaff) DrawTabRhythm(dc, b, p.Xs, tabBot);
    }

    private double HeaderWidthAt(BarPlace p) => _rows.First(r => r.Y == p.Y).HeaderW;

    private void DrawFret(DrawingContext dc, ScoreNote n, double x, double y)
    {
        string label = n.X ? "x" : n.Tie ? $"({n.F})" : n.F.ToString(CultureInfo.InvariantCulture);
        bool doubt = n.Flag || n.Conf is < 0.6;
        var col = n.Tie ? Sub : Ink;
        var t = Text(label, RoundHeavy, 13.5, col);
        double w = Math.Max(t.Width + 6, 15), h = 15;
        var r = new Rect(x - w / 2, y - h / 2, w, h);
        dc.DrawRectangle(new SolidColorBrush(_ed.Paper), doubt ? Frozen(new Pen(new SolidColorBrush(Doubt), 1.4) { DashStyle = new DashStyle(new double[] { 2, 1.5 }, 0) }) : null, r);
        dc.DrawText(t, new Point(x - t.Width / 2, y - t.Height / 2));
    }

    private static int Beams(int value) => value switch { 8 => 1, 16 => 2, 32 => 3, 64 => 4, >= 128 => 5, _ => 0 };

    /// <summary>Rhythm stems under the TAB (only when the standard staff is hidden).</summary>
    private void DrawTabRhythm(DrawingContext dc, ScoreBar bar, List<double> xs, double tabBot)
    {
        var ink = new SolidColorBrush(C("#4A4754"));
        var stemPen = new Pen(ink, 1.3);
        double top = tabBot + 10, bottom = top + 21;
        int tpq = _score!.Tpq;
        double group = Compound(bar) ? 1.5 * tpq : tpq;
        var groups = new List<List<int>>();
        for (int i = 0; i < bar.Beats.Count; i++)
        {
            var bt = bar.Beats[i];
            if (bt.IsRest || bt.Value <= 1) continue;
            dc.DrawLine(stemPen, new Point(xs[i], bt.Value == 2 ? bottom - 11 : top), new Point(xs[i], bottom));
            if (Beams(bt.Value) == 0) continue;
            int g = (int)Math.Floor(bt.Tick / group);
            var last = groups.Count > 0 ? groups[^1] : null;
            if (last != null && last[^1] == i - 1 && (int)Math.Floor(bar.Beats[i - 1].Tick / group) == g) last.Add(i);
            else groups.Add(new List<int> { i });
        }
        foreach (var g in groups)
        {
            for (int k = 0; k < 5; k++)
            {
                double y = bottom - 3 - k * 4.5;
                for (int a = 0; a < g.Count; a++)
                {
                    int i = g[a];
                    if (Beams(bar.Beats[i].Value) <= k) continue;
                    bool next = a < g.Count - 1 && Beams(bar.Beats[g[a + 1]].Value) > k;
                    if (next) dc.DrawRectangle(ink, null, new Rect(xs[i] - 0.65, y, xs[g[a + 1]] - xs[i] + 1.3, 3));
                    else if (g.Count == 1) dc.DrawLine(stemPen, new Point(xs[i], bottom - k * 4.5), new Point(xs[i] + 7, bottom - k * 4.5 - 6));
                }
            }
        }
    }

    private FormattedText Text(string s, Typeface tf, double size, Color col) =>
        new(s, CultureInfo.InvariantCulture, FlowDirection.LeftToRight, tf, size, new SolidColorBrush(col), _dpi);

    private static Pen Frozen(Pen p)
    {
        p.Freeze();
        return p;
    }
}
