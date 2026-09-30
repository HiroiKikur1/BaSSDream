using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using System.Windows;
using System.Windows.Media;
using BassStation.Models;
using BassStation.Services;

namespace BassStation.Views;

/// <summary>
/// Full-song evaluation report as a tab sheet in a hard-edged, industrial layout: data header,
/// section grades, then every bar (playback order) with per-note judgments, bar grades and timing marks.
/// </summary>
public class ReportView : FrameworkElement
{
    public const double PageWidth = 1200;
    private const double Margin = 34;
    private const double ClefW = 30;
    private const double StringGap = 15;
    private const double AnnotH = 48;          // bar number / grade tag / memo sticker / timing marks above the staff
    private const double RhythmH = 52;         // judgment ribbon + rhythm stems below the staff
    private const double RowGap = 10;
    private const double SectionHeadH = 50;
    private const double Chamfer = 14;

    private static readonly FontFamily DinFamily = new("Bahnschrift");
    private static readonly Typeface Din = new(DinFamily, FontStyles.Normal, FontWeights.Normal, FontStretches.Normal);
    private static readonly Typeface DinSemi = new(DinFamily, FontStyles.Normal, FontWeights.SemiBold, FontStretches.Normal);
    private static readonly Typeface DinBold = new(DinFamily, FontStyles.Normal, FontWeights.Bold, FontStretches.Normal);
    private static readonly Typeface DinCond = new(new FontFamily("Bahnschrift SemiBold SemiCondensed, Bahnschrift"), FontStyles.Normal, FontWeights.SemiBold, FontStretches.SemiCondensed);
    private static readonly Typeface Cjk = new(new FontFamily("Microsoft YaHei UI"), FontStyles.Normal, FontWeights.Bold, FontStretches.Normal);
    private static readonly Typeface CjkRegular = new(new FontFamily("Microsoft YaHei UI"), FontStyles.Normal, FontWeights.Normal, FontStretches.Normal);
    private static readonly Typeface Fret = new(DinFamily, FontStyles.Normal, FontWeights.Bold, FontStretches.Normal);
    private static readonly Typeface Music = new(new FontFamily("Segoe UI Symbol"), FontStyles.Normal, FontWeights.Normal, FontStretches.Normal);

    // judgment colours as in the game: PERFECT iridescent (see Iridescent), GREAT pink, GOOD green, BAD blue, MISS grey
    public static readonly Color PerfectC = C("#FF3377"), GreatC = C("#FF4DB8"), GoodC = C("#58C322"),
        BadC = C("#2F8CFF"), MissC = C("#8C8998"), WrongC = C("#8E44EC"), FastC = C("#2F7BFF"), SlowC = C("#FF7A1A"),
        InkC = C("#24232B"), SubC = C("#6E6A78"), MutedC = C("#A9A5B2"), LineC = C("#C4C0CC"), RuleC = C("#2B2A33"),
        AccentC = C("#FF2E7E"), PaperC = C("#FFFFFF");

    private readonly SongModel _song;
    private readonly PerformanceScoreDetailModel _score;
    private readonly EvaluationReport? _report;
    private double _dpi = 1.0;

    private record RowLayout(int Section, List<(ReportBar Bar, double X, double W)> Bars, double Y);
    private readonly List<RowLayout> _rows = new();
    private readonly List<(int Section, double Y)> _sectionHeads = new();
    private double _headerH, _height;

    private readonly BandEdition _ed;
    private ReportBar? _selected;

    /// <summary>A bar was clicked (bar replay).</summary>
    public event Action<ReportBar>? BarSelected;

    public ReportView(SongModel song, PerformanceScoreDetailModel score, EvaluationReport? report)
    {
        _song = song;
        _score = score;
        _report = report;
        _ed = BandEdition.For(song);
        Width = PageWidth;
        SnapsToDevicePixels = true;
        Cursor = report?.CanReplay == true ? System.Windows.Input.Cursors.Hand : null;
    }

    public void ClearSelection()
    {
        _selected = null;
        InvalidateVisual();
    }

    protected override void OnMouseLeftButtonDown(System.Windows.Input.MouseButtonEventArgs e)
    {
        base.OnMouseLeftButtonDown(e);
        if (_report?.CanReplay != true) return;
        var pt = e.GetPosition(this);
        foreach (var row in _rows)
        {
            if (pt.Y < row.Y || pt.Y > row.Y + RowH) continue;
            foreach (var (bar, bx, bw) in row.Bars)
            {
                if (pt.X < bx || pt.X > bx + bw) continue;
                _selected = bar;
                InvalidateVisual();
                BarSelected?.Invoke(bar);
                return;
            }
        }
    }

    /// <summary>Colour of an iridescent PERFECT at a page position: the hue drifts along the line.</summary>
    public static Color Iridescent(double x)
    {
        Color[] stops = { C("#FF7AC8"), C("#FFC06A"), C("#7EE8C6"), C("#86B4FF"), C("#C99BFF"), C("#FF7AC8") };
        double f = (x / 150.0) % (stops.Length - 1);
        if (f < 0) f += stops.Length - 1;
        int i = (int)f;
        double t = f - i;
        Color a = stops[i], b = stops[i + 1];
        return Color.FromRgb((byte)(a.R + (b.R - a.R) * t), (byte)(a.G + (b.G - a.G) * t), (byte)(a.B + (b.B - a.B) * t));
    }

    private int Strings => _report?.Tuning.Count is > 0 and var n ? n : 4;
    private double StaffH => (Strings - 1) * StringGap;
    private double RowH => AnnotH + StaffH + RhythmH + RowGap;

    // ---------------------------------------------------------------- layout

    protected override Size MeasureOverride(Size availableSize)
    {
        _dpi = VisualTreeHelper.GetDpi(this).PixelsPerDip;
        Layout();
        return new Size(PageWidth, _height);
    }

    private static double Slot(double lenQ) => 20 + 30 * Math.Sqrt(Math.Max(lenQ, 0.03));

    private static double NaturalWidth(ReportBar b) => Math.Max(96, 24 + b.Beats.Sum(bt => Slot(bt.Len)));

    private void Layout()
    {
        _rows.Clear();
        _sectionHeads.Clear();
        _headerH = HeaderHeight();
        double y = _headerH;
        if (_report == null || _report.Bars.Count == 0)
        {
            _height = y + 60;
            return;
        }
        double avail = PageWidth - 2 * Margin - ClefW;
        for (int si = 0; si < _report.Sections.Count; si++)
        {
            var sec = _report.Sections[si];
            _sectionHeads.Add((si, y));
            y += SectionHeadH;
            var row = new List<ReportBar>();
            double used = 0;
            void Flush(bool last)
            {
                if (row.Count == 0) return;
                double scale = last && used < 0.72 * avail ? 1.0 : avail / used;
                double x = Margin + ClefW;
                var placed = new List<(ReportBar, double, double)>();
                foreach (var b in row)
                {
                    double w = NaturalWidth(b) * scale;
                    placed.Add((b, x, w));
                    x += w;
                }
                _rows.Add(new RowLayout(si, placed, y));
                y += RowH;
                row.Clear();
                used = 0;
            }
            for (int i = sec.Start; i < sec.End && i < _report.Bars.Count; i++)
            {
                double w = NaturalWidth(_report.Bars[i]);
                if (row.Count > 0 && used + w > avail) Flush(false);
                row.Add(_report.Bars[i]);
                used += w;
            }
            Flush(true);
            y += 10;
        }
        _height = y + Margin;
    }

    private double HeaderHeight()
    {
        double h = Margin + 104 + 14 + 66 + 16 + 22;      // title block, stat cells, dimension bars
        if (!string.IsNullOrEmpty(_score.CoachComment)) h += 16 + 30;
        if (_report != null) h += 20 + SectionTagRows() * 38 + 30;
        return h + 14;
    }

    private int SectionTagRows()
    {
        if (_report == null) return 0;
        double x = 0, avail = PageWidth - 2 * Margin;
        int rows = 1;
        foreach (var s in _report.Sections)
        {
            double w = TagWidth(s);
            if (x > 0 && x + w > avail) { rows++; x = 0; }
            x += w + 8;
        }
        return rows;
    }

    private double TagWidth(ReportSection s) => Text(s.Name, Cjk, 13, InkC).Width + 30 + 58;

    // ---------------------------------------------------------------- render

    protected override void OnRender(DrawingContext dc)
    {
        dc.DrawRectangle(new SolidColorBrush(_ed.Paper), null, new Rect(0, 0, PageWidth, _height));
        dc.DrawRectangle(ScoreCanvas.Grain(), null, new Rect(0, 0, PageWidth, _height));
        DrawHeader(dc);
        if (_report == null || _report.Bars.Count == 0) return;
        foreach (var (si, y) in _sectionHeads) DrawSectionHead(dc, si, _report.Sections[si], y);
        foreach (var row in _rows) DrawRow(dc, row);
    }

    private static void SparkleStar(DrawingContext dc, double x, double y, double r, Brush fill)
    {
        double k = r * 0.28;
        dc.DrawGeometry(fill, null, Geometry.Parse(FormattableString.Invariant(
            $"M {x},{y - r} Q {x + k},{y - k} {x + r},{y} Q {x + k},{y + k} {x},{y + r} Q {x - k},{y + k} {x - r},{y} Q {x - k},{y - k} {x},{y - r} Z")));
    }

    /// <summary>Rectangle with the top-right (and optionally bottom-left) corner cut off.</summary>
    private static Geometry Cut(Rect r, double c, bool both = false)
    {
        var g = new StreamGeometry();
        using (var ctx = g.Open())
        {
            ctx.BeginFigure(new Point(r.Left, r.Top), true, true);
            ctx.LineTo(new Point(r.Right - c, r.Top), false, false);
            ctx.LineTo(new Point(r.Right, r.Top + c), false, false);
            ctx.LineTo(new Point(r.Right, r.Bottom), false, false);
            if (both)
            {
                ctx.LineTo(new Point(r.Left + c, r.Bottom), false, false);
                ctx.LineTo(new Point(r.Left, r.Bottom - c), false, false);
            }
            else ctx.LineTo(new Point(r.Left, r.Bottom), false, false);
        }
        g.Freeze();
        return g;
    }

    private void DrawHeader(DrawingContext dc)
    {
        double x0 = Margin, w = PageWidth - 2 * Margin, y = Margin;

        // title block of a commemorative score page: running head, outlined title in the band's colours,
        // foil rule, ribbon bookmark; the score is a sticker slapped on the left, the credits sit on the right
        var round = new Typeface(ScoreCanvas.Round, FontStyles.Normal, FontWeights.Black, FontStretches.Normal);
        var roundB = new Typeface(ScoreCanvas.Round, FontStyles.Normal, FontWeights.Bold, FontStretches.Normal);
        dc.DrawText(Text($"BaSSDream 演奏报告　{_ed.Name} 版", roundB, 11.5, SubC), new Point(x0, y - 16));
        dc.DrawLine(new Pen(new SolidColorBrush(LineC), 0.75), new Point(x0, y + 2), new Point(x0 + w - 54, y + 2));
        double bmx = x0 + w - 34;
        dc.DrawGeometry(new SolidColorBrush(_ed.Main), null, Geometry.Parse($"M {bmx},0 H {bmx + 24} V 118 L {bmx + 12},108 L {bmx},118 Z"));

        double cx = x0 + w / 2;
        var ft = Text(_song.Title, round, 34, Colors.Black, w - 560);
        var titleGeo = ft.BuildGeometry(new Point(cx - ft.Width / 2, y + 14));
        dc.DrawGeometry(null, new Pen(Brushes.White, 7) { LineJoin = PenLineJoin.Round }, titleGeo);
        dc.DrawGeometry(_ed.TitleBrush(), null, titleGeo);
        var date = Text(_score.EvaluatedAt, Din, 12.5, SubC);
        dc.DrawText(date, new Point(cx - date.Width / 2, y + 14 + ft.Height + 4));
        dc.DrawRectangle(_ed.FoilBrush(), null, new Rect(cx - 110, y + 14 + ft.Height + 26, 220, 3));
        SparkleStar(dc, cx + ft.Width / 2 + 18, y + 18, 8, new SolidColorBrush(_ed.Title[0]));
        SparkleStar(dc, cx + ft.Width / 2 + 32, y + 36, 4, new SolidColorBrush(_ed.Main));

        // score sticker
        var sv = Text(_score.Points.ToString("N0", CultureInfo.InvariantCulture), DinBold, 30, Colors.White);
        string badge = string.IsNullOrEmpty(_score.ComboBadge) ? $"RANK {_score.Grade}" : $"RANK {_score.Grade}   {_score.ComboBadge}";
        var cb = Text(badge, DinBold, 11.5, Colors.White);
        double sw = Math.Max(sv.Width, cb.Width) + 40, sh = 72, sx = x0 + 2, sy = y + 16;
        dc.PushTransform(new RotateTransform(-3, sx + sw / 2, sy + sh / 2));
        dc.DrawRectangle(new SolidColorBrush(Color.FromArgb(46, 60, 20, 40)), null, new Rect(sx + 2, sy + 3, sw, sh));
        dc.DrawRectangle(Brushes.White, null, new Rect(sx, sy, sw, sh));
        dc.DrawRectangle(new SolidColorBrush(_ed.Main), null, new Rect(sx + 4, sy + 4, sw - 8, sh - 8));
        dc.DrawText(sv, new Point(sx + sw / 2 - sv.Width / 2, sy + 10));
        dc.DrawText(cb, new Point(sx + sw / 2 - cb.Width / 2, sy + 48));
        dc.Pop();
        dc.PushTransform(new RotateTransform(16, sx + sw - 18, sy - 2));
        dc.DrawRectangle(new SolidColorBrush(Color.FromArgb(130, _ed.Title[0].R, _ed.Title[0].G, _ed.Title[0].B)), null, new Rect(sx + sw - 42, sy - 9, 48, 15));
        dc.Pop();

        double rx = x0 + w - 60;
        var tierC = TierColor(_song.Tier);
        var tier = Text($"{_song.Tier.ToUpperInvariant()}  Lv.{_song.Level}", DinBold, 13, Colors.White);
        double tierW = tier.Width + 26;
        dc.DrawGeometry(new SolidColorBrush(tierC), null, Geometry.Parse(
            $"M {rx - tierW},{y + 18} H {rx} L {rx - 7},{y + 29} L {rx},{y + 40} H {rx - tierW} Z"));
        dc.DrawText(tier, new Point(rx - tierW + 10, y + 29 - tier.Height / 2));
        var ar = Text(_song.Artist, round, 14, InkC);
        dc.DrawText(ar, new Point(rx - ar.Width, y + 50));
        y += 104 + 14;

        // stat cells
        var cells = new (string Label, int N, Color Col)[]
        {
            ("PERFECT", _score.Perfect, PerfectC), ("GREAT", _score.Great, GreatC), ("GOOD", _score.Good, GoodC),
            ("BAD", _score.Bad, BadC), ("MISS", _score.Miss, MissC), ("FAST", _score.Fast, FastC),
            ("SLOW", _score.Slow, SlowC), ("错音", _score.Wrong, WrongC), ("MAX COMBO", _score.MaxCombo, InkC)
        };
        double cw = w / cells.Length;
        var rule = new Pen(new SolidColorBrush(RuleC), 1);
        for (int i = 0; i < cells.Length; i++)
        {
            var (label, n, col) = cells[i];
            var r = new Rect(x0 + i * cw, y, cw, 66);
            dc.DrawRectangle(new SolidColorBrush(Tint(col, 0.06)), rule, r);
            Brush stripe = label == "PERFECT"
                ? new LinearGradientBrush(new GradientStopCollection { new(Iridescent(0), 0), new(Iridescent(60), 0.35), new(Iridescent(140), 0.7), new(Iridescent(220), 1) }, 0)
                : new SolidColorBrush(col);
            dc.DrawRectangle(stripe, null, new Rect(r.X, r.Y, r.Width, 4));
            var lt = Text(label, label.Length <= 2 ? Cjk : DinSemi, 11, col == InkC ? SubC : col);
            dc.DrawText(lt, new Point(r.X + 10, r.Y + 10));
            var nt = Text(n.ToString(CultureInfo.InvariantCulture), DinBold, 26, InkC);
            dc.DrawText(nt, new Point(r.Right - 10 - nt.Width, r.Y + 28));
        }
        y += 66 + 16;

        // dimension gauges: framed bar with 10 % ticks
        var dims = new (string, double, Color)[]
        {
            ("节奏", _score.TimingScore, PerfectC), ("音准", _score.PitchScore, GreatC),
            ("完整", _score.CompleteScore, GoodC), ("干净", _score.CleanScore, BadC)
        };
        double dw = (w - 3 * 24) / 4;
        double x = x0;
        foreach (var (label, v, col) in dims)
        {
            var lt = Text(label, Cjk, 12, InkC);
            dc.DrawText(lt, new Point(x, y + (14 - lt.Height) / 2 + 1));
            double bx = x + 36, bw = dw - 36 - 36;
            var frame = new Rect(bx, y + 3, bw, 10);
            dc.DrawRectangle(new SolidColorBrush(C("#EEEDF1")), null, frame);
            dc.DrawRectangle(new SolidColorBrush(col), null, new Rect(bx, y + 3, bw * Math.Clamp(v / 100, 0, 1), 10));
            var tp = new Pen(new SolidColorBrush(Color.FromArgb(150, 255, 255, 255)), 1);
            for (int k = 1; k < 10; k++) dc.DrawLine(tp, new Point(bx + bw * k / 10, y + 3), new Point(bx + bw * k / 10, y + 13));
            dc.DrawRectangle(null, rule, frame);
            var vt = Text(Math.Round(v).ToString(CultureInfo.InvariantCulture), DinBold, 14, InkC);
            dc.DrawText(vt, new Point(x + dw - vt.Width, y + (14 - vt.Height) / 2 + 1));
            x += dw + 24;
        }
        y += 22;

        if (!string.IsNullOrEmpty(_score.CoachComment))
        {
            y += 16;
            var r = new Rect(x0, y, w, 30);
            dc.DrawRectangle(new SolidColorBrush(C("#F6F5F8")), rule, r);
            dc.DrawRectangle(new SolidColorBrush(InkC), null, new Rect(x0, y, 46, 30));
            var tag = Text("点评", Cjk, 12, Colors.White);
            dc.DrawText(tag, new Point(x0 + 23 - tag.Width / 2, y + (30 - tag.Height) / 2));
            var ct = Text(_score.CoachComment, CjkRegular, 13, InkC, w - 70);
            dc.DrawText(ct, new Point(x0 + 58, y + (30 - ct.Height) / 2));
            y += 30;
        }

        if (_report == null) return;

        // section tags
        y += 20;
        x = x0;
        foreach (var s in _report.Sections)
        {
            double tw = TagWidth(s);
            if (x > x0 && x - x0 + tw > w) { x = x0; y += 38; }
            var col = s.Grade != null ? GradeColor(s.Grade) : MutedC;
            var r = new Rect(x, y, tw, 30);
            var geo = Cut(r, 8);
            dc.DrawGeometry(new SolidColorBrush(PaperC), rule, geo);
            dc.DrawRectangle(new SolidColorBrush(col), null, new Rect(x + 0.5, y + 0.5, 30, 29));
            var g = Text(s.Grade ?? "–", DinBold, s.Grade?.Length > 1 ? 13 : 15, Colors.White);
            dc.DrawText(g, new Point(x + 15.5 - g.Width / 2, y + 15 - g.Height / 2));
            var nt = Text(s.Name, Cjk, 13, InkC);
            dc.DrawText(nt, new Point(x + 40, y + (30 - nt.Height) / 2));
            var pct = Text(s.Acc.HasValue ? $"{s.Acc.Value * 100:0}%" : "–", DinBold, 13, col);
            dc.DrawText(pct, new Point(x + tw - 14 - pct.Width, y + (30 - pct.Height) / 2));
            x += tw + 8;
        }
        y += 38 + 8;

        // legend
        x = x0;
        foreach (var (label, col) in new[] { ("PERFECT", PerfectC), ("GREAT", GreatC), ("GOOD", GoodC), ("BAD", BadC), ("MISS", MissC), ("错音", WrongC) })
        {
            var t = Text(label, label.Length <= 2 ? Cjk : DinSemi, 11, SubC);
            var swatch = new Rect(x, y + 6, 16, 7);
            if (label == "PERFECT")
                dc.DrawRectangle(new LinearGradientBrush(Iridescent(0), Iridescent(120), 0), null, swatch);
            else if (label == "MISS")
                dc.DrawRectangle(null, new Pen(new SolidColorBrush(MissC), 1) { DashStyle = new DashStyle(new double[] { 2, 2 }, 0) }, swatch);
            else
                dc.DrawRectangle(new SolidColorBrush(col), null, swatch);
            x += 4;
            dc.DrawText(t, new Point(x + 17, y + (20 - t.Height) / 2));
            x += 17 + t.Width + 18;
        }
        var fast = Text("−ms 快", Cjk, 11, FastC);
        dc.DrawText(fast, new Point(x, y + (20 - fast.Height) / 2));
        x += fast.Width + 16;
        var slow = Text("+ms 慢", Cjk, 11, SlowC);
        dc.DrawText(slow, new Point(x, y + (20 - slow.Height) / 2));
    }

    private void DrawSectionHead(DrawingContext dc, int index, ReportSection s, double y)
    {
        var col = s.Grade != null ? GradeColor(s.Grade) : MutedC;
        var r = new Rect(Margin, y + 10, PageWidth - 2 * Margin, 30);
        dc.DrawGeometry(new SolidColorBrush(InkC), null, Cut(r, Chamfer));
        dc.DrawRectangle(new SolidColorBrush(col), null, new Rect(Margin, y + 10, 44, 30));
        var g = Text(s.Grade ?? "–", DinBold, 17, Colors.White);
        dc.DrawText(g, new Point(Margin + 22 - g.Width / 2, y + 25 - g.Height / 2));
        var idx = Text($"{index + 1:00}", DinBold, 13, C("#8C8898"));
        dc.DrawText(idx, new Point(Margin + 56, y + 25 - idx.Height / 2));
        var name = Text(s.Name, Cjk, 15, Colors.White);
        dc.DrawText(name, new Point(Margin + 56 + idx.Width + 10, y + 25 - name.Height / 2));
        if (s.Acc.HasValue)
        {
            var p = Text($"{s.Acc.Value * 100:0.0}%", DinBold, 15, col);
            dc.DrawText(p, new Point(Margin + 56 + idx.Width + 10 + name.Width + 16, y + 25 - p.Height / 2));
        }
        if (_report != null && s.End > s.Start)
        {
            var range = Text($"BAR {_report.Bars[s.Start].Number}–{_report.Bars[Math.Min(s.End, _report.Bars.Count) - 1].Number}", DinSemi, 12, C("#8C8898"));
            dc.DrawText(range, new Point(r.Right - Chamfer - 12 - range.Width, y + 25 - range.Height / 2));
        }
    }

    private void DrawRow(DrawingContext dc, RowLayout row)
    {
        double staffTop = row.Y + AnnotH;
        double staffBot = staffTop + StaffH;
        double x0 = Margin, x1 = row.Bars[^1].X + row.Bars[^1].W;
        var linePen = new Pen(new SolidColorBrush(LineC), 1);
        linePen.Freeze();

        foreach (var (bar, bx, bw) in row.Bars)
        {
            if (bar != _selected) continue;
            var sel = new Rect(bx, staffTop - 10, bw, StaffH + 34);
            dc.DrawRectangle(new SolidColorBrush(Color.FromArgb(110, _ed.Tint.R, _ed.Tint.G, _ed.Tint.B)), new Pen(new SolidColorBrush(_ed.Main), 1.4), sel);
        }

        for (int s = 0; s < Strings; s++)
        {
            double y = staffTop + s * StringGap;
            dc.DrawLine(linePen, new Point(x0, y), new Point(x1, y));
        }
        double cy = staffTop + StaffH / 2 - 21;
        foreach (var ch in new[] { "T", "A", "B" })
        {
            var t = Text(ch, DinBold, 12, MutedC);
            dc.DrawText(t, new Point(x0 + 10, cy));
            cy += 14;
        }
        var barPen = new Pen(new SolidColorBrush(RuleC), 1.2);
        dc.DrawLine(barPen, new Point(x0, staffTop), new Point(x0, staffBot));

        var marks = new List<(double X, string J)>();
        foreach (var (bar, bx, bw) in row.Bars)
        {
            dc.DrawLine(barPen, new Point(bx + bw, staffTop), new Point(bx + bw, staffBot));
            DrawBarNotes(dc, bar, bx, bw, staffTop, staffBot, marks);
        }
        DrawRibbon(dc, marks, staffBot + 7, x1);
        // headers last: memo stickers lie on top of the page
        foreach (var (bar, bx, bw) in row.Bars) DrawBarHeader(dc, bar, bx, bw, row.Y);
    }

    private static Color JudgeColor(string j, double x) => j switch
    {
        "P" => Iridescent(x), "G" => GreatC, "D" => GoodC, "W" => WrongC, _ => BadC
    };

    /// <summary>
    /// Judgment ribbon: one continuous band under the row whose colour flows from note to note — iridescent through
    /// PERFECT runs, pink / green / blue at GREAT / GOOD / BAD, purple at a wrong pitch — torn open at every MISS.
    /// </summary>
    private void DrawRibbon(DrawingContext dc, List<(double X, string J)> marks, double y, double xEnd)
    {
        if (marks.Count == 0) return;
        const double h = 7, pad = 8;
        var pts = marks.GroupBy(m => Math.Round(m.X, 1))
                       .Select(g => (X: g.Key, J: g.Any(m => m.J == "M") ? "M" : g.Any(m => m.J == "W") ? "W" : g.First().J))
                       .OrderBy(m => m.X).ToList();
        int i = 0;
        while (i < pts.Count)
        {
            if (pts[i].J == "M")
            {
                // the missing piece: an empty dashed slot
                var slot = new Rect(pts[i].X - pad + 2, y, 2 * pad - 4, h);
                dc.DrawRectangle(null, new Pen(new SolidColorBrush(MissC), 1) { DashStyle = new DashStyle(new double[] { 2, 2 }, 0) }, slot);
                i++;
                continue;
            }
            int j = i;
            while (j + 1 < pts.Count && pts[j + 1].J != "M") j++;
            bool tornL = i > 0, tornR = j < pts.Count - 1;
            double a = pts[i].X - pad, b = pts[j].X + pad;
            var brush = new LinearGradientBrush { StartPoint = new Point(0, 0), EndPoint = new Point(1, 0) };
            double span = Math.Max(1, b - a);
            brush.GradientStops.Add(new GradientStop(JudgeColor(pts[i].J, pts[i].X), 0));
            for (int k = i; k <= j; k++)
            {
                brush.GradientStops.Add(new GradientStop(JudgeColor(pts[k].J, pts[k].X), (pts[k].X - a) / span));
                // long PERFECT stretches shimmer between notes as well
                if (k < j && pts[k].J == "P" && pts[k + 1].J == "P")
                {
                    double mx = (pts[k].X + pts[k + 1].X) / 2;
                    brush.GradientStops.Add(new GradientStop(Iridescent(mx), (mx - a) / span));
                }
            }
            brush.GradientStops.Add(new GradientStop(JudgeColor(pts[j].J, pts[j].X), 1));
            brush.Freeze();
            var g = new StreamGeometry();
            using (var c = g.Open())
            {
                c.BeginFigure(new Point(a, y), true, true);
                c.LineTo(new Point(b, y), true, false);
                if (tornR)
                {
                    c.LineTo(new Point(b - 3, y + h * 0.3), true, false);
                    c.LineTo(new Point(b + 1, y + h * 0.55), true, false);
                    c.LineTo(new Point(b - 2.5, y + h * 0.8), true, false);
                }
                c.LineTo(new Point(b, y + h), true, false);
                c.LineTo(new Point(a, y + h), true, false);
                if (tornL)
                {
                    c.LineTo(new Point(a + 2.5, y + h * 0.75), true, false);
                    c.LineTo(new Point(a - 1, y + h * 0.5), true, false);
                    c.LineTo(new Point(a + 3, y + h * 0.25), true, false);
                }
            }
            g.Freeze();
            dc.DrawGeometry(brush, null, g);
            i = j + 1;
        }
    }

    private void DrawBarHeader(DrawingContext dc, ReportBar bar, double bx, double bw, double y)
    {
        var num = Text(bar.Number.ToString(), DinSemi, 11, SubC);
        dc.DrawText(num, new Point(bx + 3, y + 2));
        double x = bx + 3 + num.Width + 5;
        double limit = bx + bw - 2;
        if (bar.Grade != null)
        {
            var g = Text(bar.Grade, DinCond, 9.5, Colors.White);
            var r = new Rect(x, y + 3, g.Width + 8, 13);
            if (r.Right <= limit)
            {
                dc.DrawRectangle(new SolidColorBrush(BarGradeColor(bar.Grade)), null, r);
                dc.DrawText(g, new Point(r.X + 4, r.Y + (13 - g.Height) / 2));
                x = r.Right + 3;
            }
        }
        // the teacher's note: a sticky note on the bar that needs work
        var lines = new List<string>();
        if (bar.Tendency is double t) lines.Add($"{(t < 0 ? "抢拍" : "拖拍")} {(t < 0 ? "−" : "+")}{Math.Abs(t):0} ms");
        if (bar.Wrong > 0) lines.Add($"错音 {bar.Wrong}");
        if (bar.Miss > 0) lines.Add($"漏音 {bar.Miss}");
        if (lines.Count == 0) return;
        var note = Text(string.Join("　", lines.Take(2)), new Typeface(ScoreCanvas.Round, FontStyles.Normal, FontWeights.Bold, FontStretches.Normal), 11.5, C("#5A4636"));
        double nw = note.Width + 16, nh = 22;
        double nx = Math.Max(x + 2, bx + bw - nw - 6), ny = y + 2;
        if (nx + nw > bx + bw + 30) return;         // too narrow a bar: the ribbon still tells
        double rot = (bar.Number % 2 == 0) ? 2.2 : -1.8;
        dc.PushTransform(new RotateTransform(rot, nx + nw / 2, ny + nh / 2));
        dc.DrawRectangle(new SolidColorBrush(Color.FromArgb(40, 70, 50, 20)), null, new Rect(nx + 1.5, ny + 2.5, nw, nh));
        dc.DrawRectangle(new SolidColorBrush(C("#FFF3B8")), null, new Rect(nx, ny, nw, nh));
        dc.DrawText(note, new Point(nx + 8, ny + nh / 2 - note.Height / 2));
        dc.Pop();
        dc.PushTransform(new RotateTransform(-6, nx + nw / 2, ny - 2));
        dc.DrawRectangle(new SolidColorBrush(Color.FromArgb(120, _ed.Title[1].R, _ed.Title[1].G, _ed.Title[1].B)), null, new Rect(nx + nw / 2 - 16, ny - 6, 32, 10));
        dc.Pop();
    }

    private void DrawBarNotes(DrawingContext dc, ReportBar bar, double bx, double bw, double staffTop, double staffBot, List<(double X, string J)> marks)
    {
        double scale = bw / NaturalWidth(bar);
        double pad = 12 * scale;
        var xs = new List<double>();
        double off = 0;
        foreach (var bt in bar.Beats)
        {
            xs.Add(bx + pad + (off + 10) * scale);
            off += Slot(bt.Len);
        }

        for (int i = 0; i < bar.Beats.Count; i++)
        {
            var bt = bar.Beats[i];
            double x = xs[i];
            if (bt.IsRest)
            {
                DrawRest(dc, bt, x, (staffTop + staffBot) / 2);
                continue;
            }
            var timed = bt.Notes.FirstOrDefault(n => n.Dt.HasValue && !n.Wrong && n.Judge is "G" or "D" or "B");
            if (timed != null)
            {
                double dt = timed.Dt!.Value;
                var t = Text(dt < 0 ? $"−{-dt:0}" : $"+{dt:0}", DinBold, 9.5, dt < 0 ? FastC : SlowC);
                dc.DrawText(t, new Point(x - t.Width / 2, staffTop - 15));
            }
            foreach (var n in bt.Notes)
            {
                if (n.String < 0 || n.String >= Strings) continue;
                DrawNote(dc, n, x, staffTop + (Strings - 1 - n.String) * StringGap);
                if (n.Judge != null && !n.Tie) marks.Add((x, n.Wrong ? "W" : n.Judge));
            }
        }

        DrawRhythm(dc, bar, xs, staffBot);
    }

    private void DrawNote(DrawingContext dc, ReportNote n, double x, double y)
    {
        // neutral numbers on the paper (the ribbon below carries the judgment); missed notes greyed
        string label = n.Dead ? "x" : n.Tie ? $"({n.Fret})" : n.Fret.ToString();
        var ink = n.Tie || n.Judge == "M" ? MutedC : n.Wrong ? WrongC : InkC;
        var t = Text(label, new Typeface(ScoreCanvas.Round, FontStyles.Normal, FontWeights.Black, FontStretches.Normal), 13, ink);
        double w = Math.Max(t.Width + 6, 14), h = 15;
        var r = new Rect(x - w / 2, y - h / 2, w, h);
        dc.DrawRectangle(new SolidColorBrush(_ed.Paper), null, r);
        dc.DrawText(t, new Point(x - t.Width / 2, y - t.Height / 2));
        if (n.Wrong)
        {
            var mark = Text("✕", Cjk, 8, WrongC);
            dc.DrawText(mark, new Point(r.Right - 1, r.Top - 8));
        }
    }

    private void DrawRest(DrawingContext dc, ReportBeat bt, double x, double midY)
    {
        string glyph = bt.Value switch
        {
            <= 1 => "\U0001D13B", 2 => "\U0001D13C", 4 => "\U0001D13D", 8 => "\U0001D13E", 16 => "\U0001D13F", _ => "\U0001D140"
        };
        var t = Text(glyph, Music, 24, MissC);
        dc.DrawText(t, new Point(x - t.Width / 2, midY - t.Height / 2));
    }

    private static int Beams(int value) => value switch { 8 => 1, 16 => 2, 32 => 3, 64 => 4, >= 128 => 5, _ => 0 };

    private void DrawRhythm(DrawingContext dc, ReportBar bar, List<double> xs, double staffBot)
    {
        var ink = new SolidColorBrush(C("#4A4754"));
        var stemPen = new Pen(ink, 1.3);
        double top = staffBot + 20, bottom = top + 22;     // below the judgment ribbon
        double group = bar.Den == 8 && bar.Num % 3 == 0 && bar.Num >= 6 ? 1.5 : 1.0;

        // beam groups: consecutive sounding beats shorter than a quarter within the same beat
        var groups = new List<List<int>>();
        for (int i = 0; i < bar.Beats.Count; i++)
        {
            var bt = bar.Beats[i];
            if (bt.IsRest || bt.Value <= 1) continue;
            double stemTop = bt.Value == 2 ? bottom - 11 : top;
            dc.DrawLine(stemPen, new Point(xs[i], stemTop), new Point(xs[i], bottom));
            for (int d = 0; d < bt.Dots; d++)
                dc.DrawRectangle(ink, null, new Rect(xs[i] + 4 + d * 4, bottom - 4, 2.4, 2.4));
            if (Beams(bt.Value) == 0) continue;
            int g = (int)Math.Floor((bt.Pos + 1e-6) / group);
            var last = groups.Count > 0 ? groups[^1] : null;
            if (last != null && last[^1] == i - 1 && (int)Math.Floor((bar.Beats[i - 1].Pos + 1e-6) / group) == g)
                last.Add(i);
            else
                groups.Add(new List<int> { i });
        }

        foreach (var g in groups)
        {
            if (g.Count == 1)
            {
                int i = g[0];
                for (int k = 0; k < Beams(bar.Beats[i].Value); k++)
                {
                    double y = bottom - k * 4.5;
                    dc.DrawLine(stemPen, new Point(xs[i], y), new Point(xs[i] + 7, y - 6));
                }
                continue;
            }
            for (int k = 0; k < 5; k++)
            {
                double y = bottom - 3 - k * 4.5;
                for (int a = 0; a < g.Count; a++)
                {
                    int i = g[a];
                    if (Beams(bar.Beats[i].Value) <= k) continue;
                    bool prev = a > 0 && Beams(bar.Beats[g[a - 1]].Value) > k;
                    bool next = a < g.Count - 1 && Beams(bar.Beats[g[a + 1]].Value) > k;
                    if (next)
                        dc.DrawRectangle(ink, null, new Rect(xs[i] - 0.65, y, xs[g[a + 1]] - xs[i] + 1.3, 3));
                    else if (!prev)
                    {
                        double dir = a < g.Count - 1 ? 1 : -1;
                        double x2 = xs[i] + dir * 7;
                        dc.DrawRectangle(ink, null, new Rect(Math.Min(xs[i], x2) - 0.65, y, 7 + 1.3, 3));
                    }
                }
            }
            if (bar.Beats[g[0]].Tuplet > 0)
            {
                var t = Text(bar.Beats[g[0]].Tuplet.ToString(), DinBold, 9.5, C("#4A4754"));
                dc.DrawText(t, new Point((xs[g[0]] + xs[g[^1]]) / 2 - t.Width / 2, bottom + 2));
            }
        }
        // tuplets outside beam groups (quarter triplets)
        for (int i = 0; i < bar.Beats.Count; i++)
        {
            var bt = bar.Beats[i];
            if (bt.Tuplet == 0 || Beams(bt.Value) > 0) continue;
            if (i > 0 && bar.Beats[i - 1].Tuplet == bt.Tuplet && Beams(bar.Beats[i - 1].Value) == 0) continue;
            int j = i;
            while (j + 1 < bar.Beats.Count && bar.Beats[j + 1].Tuplet == bt.Tuplet && Beams(bar.Beats[j + 1].Value) == 0 && j + 1 - i < bt.Tuplet - 1) j++;
            var t = Text(bt.Tuplet.ToString(), DinBold, 9.5, C("#4A4754"));
            dc.DrawText(t, new Point((xs[i] + xs[j]) / 2 - t.Width / 2, bottom + 2));
        }
    }

    // ---------------------------------------------------------------- helpers

    private FormattedText Text(string s, Typeface tf, double size, Color col, double maxWidth = 0)
    {
        var ft = new FormattedText(s, CultureInfo.CurrentUICulture, FlowDirection.LeftToRight, tf, size,
                                   new SolidColorBrush(col), _dpi);
        if (maxWidth > 0)
        {
            ft.MaxTextWidth = maxWidth;
            ft.MaxLineCount = 1;
            ft.Trimming = TextTrimming.CharacterEllipsis;
        }
        return ft;
    }

    public static Color C(string hex) => (Color)ColorConverter.ConvertFromString(hex);

    public static Color Tint(Color c, double a) => Color.FromRgb(
        (byte)(255 - (255 - c.R) * a), (byte)(255 - (255 - c.G) * a), (byte)(255 - (255 - c.B) * a));

    public static Color GradeColor(string g) => g switch
    {
        "SS" => C("#EC4899"), "S" => C("#EAB308"), "A" => C("#3B82F6"), "B" => C("#10B981"), _ => C("#F97316")
    };

    public static Color BarGradeColor(string g) => g switch
    {
        "PERFECT" => PerfectC, "GREAT" => GreatC, "GOOD" => GoodC, _ => BadC
    };

    public static Color TierColor(string tier) => tier.ToUpperInvariant() switch
    {
        "EASY" => C("#3E8BFF"), "NORMAL" => C("#34C274"), "HARD" => C("#FFAE1A"),
        "EXPERT" => C("#FF4058"), "SPECIAL" => C("#E04BD6"), _ => C("#FF3377")
    };

    /// <summary>Plain-text version for the clipboard.</summary>
    public string ToPlainText()
    {
        var sb = new System.Text.StringBuilder();
        sb.AppendLine($"{_song.Title} — {_song.Artist}  [{_song.Tier} Lv.{_song.Level}]");
        sb.AppendLine($"得分 {_score.Points}  评级 {_score.Grade}  {_score.ComboBadge}");
        sb.AppendLine($"PERFECT {_score.Perfect}  GREAT {_score.Great}  GOOD {_score.Good}  BAD {_score.Bad}  MISS {_score.Miss}");
        sb.AppendLine($"FAST {_score.Fast}  SLOW {_score.Slow}  错音 {_score.Wrong}  MAX COMBO {_score.MaxCombo}");
        sb.AppendLine($"节奏 {_score.TimingScore:0}  音准 {_score.PitchScore:0}  完整 {_score.CompleteScore:0}  干净 {_score.CleanScore:0}");
        if (!string.IsNullOrEmpty(_score.CoachComment)) sb.AppendLine(_score.CoachComment);
        if (_report == null) return sb.ToString();
        sb.AppendLine();
        foreach (var s in _report.Sections)
        {
            sb.Append($"[{s.Name}] ");
            sb.AppendLine(s.Grade != null ? $"{s.Grade} {s.Acc * 100:0.0}%" : "–");
            for (int i = s.Start; i < s.End && i < _report.Bars.Count; i++)
            {
                var b = _report.Bars[i];
                if (b.Grade == null || (b.Grade == "PERFECT" && b.Tendency == null)) continue;
                var parts = new List<string> { b.Grade };
                if (b.Tendency is double t) parts.Add($"{(t < 0 ? "快" : "慢")} {Math.Abs(t):0}ms");
                if (b.Wrong > 0) parts.Add($"错音 {b.Wrong}");
                if (b.Miss > 0) parts.Add($"漏音 {b.Miss}");
                sb.AppendLine($"  第 {b.Number} 小节  {string.Join("  ", parts)}");
            }
        }
        return sb.ToString();
    }
}
