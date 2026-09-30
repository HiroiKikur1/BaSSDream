using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Globalization;
using System.Linq;
using System.Text.Json;
using System.Windows;
using System.Windows.Input;
using System.Windows.Media;
using System.Windows.Media.Imaging;
using BassStation.Services;

namespace BassStation.Views;

/// <summary>
/// Boot animation, drawn over the main window (1440 × 810) on every launch:
/// white page with the traced Atelier Z M#265 as a faint backdrop (the first frame is also the native splash
/// image, assets/boot/splash.png) → B a S S fly in from the left one by one → a line grows down B's left edge →
/// "Dream" rushes in from the right and hits it → the bolt, the star and バンドリ land → the logo shatters and the
/// pieces stream into the main window's logo while the page fades into the UI. A click or a key skips to the end.
/// Motion is computed from the clock each frame (springs, arcs, squash) so pauses and dropped frames never desync.
/// </summary>
public sealed class BootOverlay : FrameworkElement
{
    private const double W = 1440, H = 810, LW = 259, LH = 135, S = 3.4;
    private static readonly double X0 = 720 - LW * S / 2, Y0 = 405 - LH * S / 2;
    // timeline, seconds
    private const double TBass0 = .35, BassStep = .1, BassDur = .46, TWall = 1.12, TDream0 = 1.3, TImpact = 1.54,
        TBolt = 1.64, TStar = 1.76, TKana = 1.86, TShatter = 2.55, TUi0 = 3.05, End = 3.75;
    private const double PlateOpacity = .25, PlateScale = 1.2, PlateX = 720, PlateY = 400;

    private sealed record Glyph(string Name, Geometry Geo, double Cx, double Cy, Rect Box);
    private record struct St(double X, double Y, double S, double R, double Sx, double Sy, double O, bool Moving);

    private readonly Glyph[] _all, _bassRow, _dreamRow, _kana;
    private readonly Glyph _bolt, _star;
    private readonly DrawingGroup _plate;
    private readonly Brush _brand;
    private readonly LinearGradientBrush _halo;
    private readonly double _wallX, _wallTop, _wallBot, _gap;

    private readonly Stopwatch _clock = new();
    private FrameworkElement? _logo;
    private Window? _window;
    private bool _finished;

    // shatter
    private Particle[]? _particles;
    private WriteableBitmap? _fx;
    private int[]? _px;
    private Rect _target;

    public event Action? Completed;

    /// <summary>Debug: draw this moment only (no clock, no skipping).</summary>
    public double? FixedTime { get; init; }

    private double Now => FixedTime ?? _clock.Elapsed.TotalSeconds;

    public BootOverlay()
    {
        Width = W;
        Height = H;
        HorizontalAlignment = HorizontalAlignment.Left;
        VerticalAlignment = VerticalAlignment.Top;
        _brand = Frozen(new SolidColorBrush(Palette.Brand));
        _all = LoadGlyphs();
        Glyph G(string n) => _all.First(g => g.Name == n);
        _bassRow = new[] { G("B"), G("a1"), G("S1"), G("S2") };
        _dreamRow = new[] { G("D"), G("r"), G("e"), G("a2"), G("m") };
        _bolt = G("bolt");
        _star = G("star");
        _kana = _all.Where(g => g.Name.StartsWith("kana", StringComparison.Ordinal)).OrderBy(g => g.Cx).ToArray();
        var b = G("B").Box;
        var d = G("D").Box;
        _wallX = X0 + b.Left * S;                                   // B's left boundary, extended
        _wallTop = Y0 + b.Top * S;                                  // level with the top of B
        _wallBot = Y0 + d.Bottom * S + 26;
        _gap = (d.Left - b.Left) * S;                               // D sits this far right of the line at rest
        _plate = LoadPlate();
        var halo = Palette.Brand;
        _halo = new LinearGradientBrush(Color.FromArgb(0, halo.R, halo.G, halo.B), Color.FromArgb(82, halo.R, halo.G, halo.B), 0);
        _halo.Freeze();
    }

    /// <summary>Starts once the window is up; <paramref name="logo"/> is the main window's logo the pieces fly into.</summary>
    public void Start(FrameworkElement logo)
    {
        _logo = logo;
        logo.Opacity = 0;
        Loaded += (_, _) =>
        {
            if (FixedTime != null) { InvalidateVisual(); return; }
            _window = Window.GetWindow(this);
            if (_window != null)
            {
                _window.PreviewMouseDown += OnSkip;
                _window.PreviewKeyDown += OnSkip;
            }
            _clock.Start();
            CompositionTarget.Rendering += OnFrame;
        };
    }

    private void OnSkip(object sender, InputEventArgs e)
    {
        e.Handled = true;
        Finish();
    }

    private void OnFrame(object? sender, EventArgs e)
    {
        if (_clock.Elapsed.TotalSeconds >= End) Finish();
        else InvalidateVisual();
    }

    private void Finish()
    {
        if (_finished) return;
        _finished = true;
        CompositionTarget.Rendering -= OnFrame;
        if (_window != null)
        {
            _window.PreviewMouseDown -= OnSkip;
            _window.PreviewKeyDown -= OnSkip;
        }
        if (_logo != null) _logo.Opacity = 1;
        (Parent as System.Windows.Controls.Panel)?.Children.Remove(this);
        Completed?.Invoke();
    }

    // ------------------------------------------------------------------ drawing

    protected override void OnRender(DrawingContext dc)
    {
        double t = Now;
        double uo = Clamp((t - TUi0) / .7);
        dc.PushOpacity(1 - uo);
        dc.DrawRectangle(Brushes.White, null, new Rect(0, 0, W, H));
        dc.Pop();

        // backdrop plate: there from the first frame, leaves with the page
        dc.PushOpacity(PlateOpacity * (1 - uo));
        dc.PushTransform(PlateTransform);
        dc.DrawDrawing(_plate);
        dc.Pop();
        dc.Pop();

        // camera shake on the impact and the bolt
        double ti = t - TImpact;
        double sh = (ti > 0 ? 4 * Ring(ti, 26, .06) : 0) + (t > TBolt + .1 ? 3 * Ring(t - TBolt - .1, 30, .05) : 0);
        dc.PushTransform(new TranslateTransform(sh, 0));
        DrawWall(dc, t, ti);
        bool shattered = t >= TShatter;
        if (!shattered)
        {
            foreach (var g in _all)
            {
                if (State(g, t) is not { } st) continue;
                if (st.Moving)
                    for (int k = 3; k >= 1; k--)
                        if (State(g, t - .018 * k) is { } gs) DrawGlyph(dc, g, gs, new[] { .28, .15, .07 }[k - 1]);
                DrawGlyph(dc, g, st, st.O);
            }
        }
        dc.Pop();

        if (shattered)
        {
            EnsureParticles();
            DrawParticles(t);
            if (_fx != null) dc.DrawImage(_fx, new Rect(0, 0, W, H));
        }
        double bf = t - TBolt - .1;
        if (bf > 0 && bf < .25)
            dc.DrawRectangle(new SolidColorBrush(Color.FromArgb((byte)(255 * .7 * Math.Exp(-bf / .06)), 255, 255, 255)), null, new Rect(0, 0, W, H));
    }

    private static readonly Transform PlateTransform = MakePlateTransform();

    private static Transform MakePlateTransform()
    {
        var m = Matrix.Identity;
        m.Translate(-525, -525);
        m.Scale(PlateScale, PlateScale);
        m.Translate(PlateX, PlateY);
        var t = new MatrixTransform(m);
        t.Freeze();
        return t;
    }

    private void DrawGlyph(DrawingContext dc, Glyph g, St st, double opacity)
    {
        var m = Matrix.Identity;
        m.Translate(-g.Cx, -g.Cy);
        m.Scale(st.S * st.Sx, st.S * st.Sy);
        m.Rotate(st.R);
        m.Translate(st.X, st.Y);
        dc.PushOpacity(opacity);
        dc.PushTransform(new MatrixTransform(m));
        dc.DrawGeometry(_brand, null, g.Geo);
        dc.Pop();
        dc.Pop();
    }

    private void DrawWall(DrawingContext dc, double t, double ti)
    {
        if (t < TWall) return;
        double grow = EOutCubic(Clamp((t - TWall) / .18));
        double fade = 1 - Clamp((t - TImpact - .35) / .3);
        if (fade <= 0) return;
        double vib = ti > 0 ? 3 * Ring(ti, 22, .07) : 0, flare = ti > 0 ? Pulse(ti, .05) : 0;
        double h = (_wallBot - _wallTop) * grow, gw = 56 + 110 * flare;
        dc.PushOpacity(fade * (.85 + .15 * flare));
        dc.DrawRectangle(_halo, null, new Rect(_wallX - gw + vib, _wallTop, gw, h));
        dc.DrawRectangle(_brand, null, new Rect(_wallX - 1.6 + vib, _wallTop, 3.2, h));
        dc.Pop();
    }

    // ------------------------------------------------------------------ motion

    private St? State(Glyph g, double t)
    {
        var hp = new Point(X0 + g.Cx * S, Y0 + g.Cy * S);
        int k = Array.IndexOf(_bassRow, g);
        if (k >= 0)
        {
            double t0 = TBass0 + k * BassStep, t1 = t0 + BassDur;
            if (t < t0) return null;
            if (t < t1)
            {
                double u = (t - t0) / BassDur, e = EOutQuart(u);
                var p = Quad(new Point(-160, hp.Y - 40 - k * 14), new Point(hp.X - 190 + k * 20, hp.Y - 300), hp, e);
                double sp = Clamp(4 * Math.Pow(1 - u, 3));
                return new St(p.X, p.Y, S * (.62 + .38 * EOutCubic(u)), -34 * (1 - EOutCubic(u)), 1 + .16 * sp, 1 - .1 * sp, 1, true);
            }
            double tau = t - t1;                               // landed: squash, overshoot, settle
            return new St(hp.X + 14 * Ring(tau, 5, .08), hp.Y, S, 4 * Ring(tau, 4, .09), 1 - .12 * Settle(tau, 7, .4), 1 + .08 * Settle(tau, 7, .4), 1, false);
        }
        k = Array.IndexOf(_dreamRow, g);
        if (k >= 0)
        {
            if (t < TDream0) return null;
            double hit = -_gap, off, sx = 1, sy = 1;
            if (t < TImpact) { double d = TImpact - t; off = hit + 3900 * d + 5200 * d * d; sx = 1.14; sy = .92; }
            else
            {
                double tau = t - TImpact;
                off = hit * Settle(tau, 5, .5);                  // recoil to the logo's own spacing
                off -= 10 * k * Pulse(tau - .014 * k, .045);     // the row concertinas into D, then relaxes
                if (k == 0) { sx = 1 - .2 * Pulse(tau, .035); sy = 1 + .1 * Pulse(tau, .035); }
                else sx = 1 - .08 * Pulse(tau - .014 * k, .04);
            }
            return new St(hp.X + off, hp.Y, S, 0, sx, sy, 1, t < TImpact);
        }
        if (g == _bolt)
        {
            if (t < TBolt) return null;
            double u = Clamp((t - TBolt) / .1);
            if (u < 1) return new St(hp.X + 60 * (1 - u), hp.Y - 560 * (1 - u * u), S, 0, .82, 1.25, 1, true);
            double tau = t - TBolt - .1;
            return new St(hp.X, hp.Y + 8 * Ring(tau, 6, .07), S, 0, 1 + .12 * Settle(tau, 8, .45), 1 - .12 * Settle(tau, 8, .45), 1, false);
        }
        if (g == _star)
        {
            if (t < TStar) return null;
            double tau = t - TStar, s = Math.Max(0, 1 - Settle(tau, 3.2, .36));
            return new St(hp.X, hp.Y, S * s, -150 * Settle(tau, 2.6, .5), 1, 1, 1, false);
        }
        k = Array.IndexOf(_kana, g);
        if (k < 0) return null;
        double tk = t - (TKana + k * .03);
        if (tk < 0) return null;
        return new St(hp.X, hp.Y + 30 * Settle(tk, 4, .5), S, 0, 1, 1, Clamp(tk / .08), false);
    }

    // ------------------------------------------------------------------ shatter

    private sealed class Particle
    {
        public double Sx, Sy, Dx, Dy, Delay, Dur, C1x, C1y, C2x, C2y;
        public int Color;
    }

    private void EnsureParticles()
    {
        if (_particles != null) return;
        // the logo at rest, rasterised, sampled every 5 px
        var dv = new DrawingVisual();
        using (var dc = dv.RenderOpen())
            foreach (var g in _all) DrawGlyph(dc, g, new St(X0 + g.Cx * S, Y0 + g.Cy * S, S, 0, 1, 1, 1, false), 1);
        var rtb = new RenderTargetBitmap((int)W, (int)H, 96, 96, PixelFormats.Pbgra32);
        rtb.Render(dv);
        var src = new int[(int)(W * H)];
        rtb.CopyPixels(src, (int)W * 4, 0);

        // where the pieces land: the main window's logo, in this overlay's coordinates
        if (_logo != null && _logo.ActualHeight > 0)
        {
            var p = _logo.TranslatePoint(new Point(0, 0), this);
            _target = new Rect(p.X, p.Y, _logo.ActualHeight * LW / LH, _logo.ActualHeight);
        }
        else _target = new Rect(18, 10, 72 * LW / LH, 72);
        double m = _target.Height / LH;

        var rnd = new Random(7);
        var brand = Palette.Brand;
        int[] tones = { Argb(brand), Argb(Color.FromRgb(0xFF, 0x6F, 0xA0)), Argb(Color.FromRgb(0xB8, 0x00, 0x3F)) };
        var list = new List<Particle>();
        const int step = 5;
        int x0 = (int)X0, y0 = (int)Y0, x1 = (int)(X0 + LW * S), y1 = (int)(Y0 + LH * S);
        for (int y = y0; y < y1; y += step)
            for (int x = x0; x < x1; x += step)
            {
                if (((src[(y + 2) * (int)W + x + 2] >> 24) & 0xFF) < 120) continue;
                double nx = (x - X0) / (LW * S), ny = (y - Y0) / (LH * S);
                double dx = _target.X + (x - X0) * m / S, dy = _target.Y + (y - Y0) * m / S;
                double ang = Math.Atan2(y - 405, x - 720) + (rnd.NextDouble() - .5) * 1.6, mag = 40 + rnd.NextDouble() * 130;
                double tone = rnd.NextDouble();
                list.Add(new Particle
                {
                    Sx = x, Sy = y, Dx = dx, Dy = dy,
                    Delay = .32 * (nx * .62 + ny * .38) + rnd.NextDouble() * .06,
                    Dur = .58 + rnd.NextDouble() * .22,
                    C1x = x + Math.Cos(ang) * mag, C1y = y + Math.Sin(ang) * mag - 40,
                    C2x = dx + 40 + rnd.NextDouble() * 160, C2y = dy + 30 + rnd.NextDouble() * 140,
                    Color = tone < .12 ? tones[1] : tone < .2 ? tones[2] : tones[0],
                });
            }
        _particles = list.ToArray();
        _fx = new WriteableBitmap((int)W, (int)H, 96, 96, PixelFormats.Pbgra32, null);
        _px = new int[(int)(W * H)];
    }

    private void DrawParticles(double t)
    {
        if (_particles == null || _fx == null || _px == null) return;
        Array.Clear(_px);
        double alpha = 1 - Clamp((t - (End - .15)) / .15);
        bool tension = t < TShatter + .08;
        double endSize = 5 * (_target.Height / LH) / S + .5;
        foreach (var p in _particles)
        {
            double u = Clamp((t - TShatter - .08 - p.Delay) / p.Dur), x, y, size;
            if (u <= 0)
            {
                x = p.Sx + (tension ? 1.2 * Math.Sin(p.Sx * 13.1 + t * 90) : 0);
                y = p.Sy;
                size = 5;
            }
            else
            {
                double e = EInOutCubic(u), v = 1 - e;
                x = v * v * v * p.Sx + 3 * v * v * e * p.C1x + 3 * v * e * e * p.C2x + e * e * e * p.Dx;
                y = v * v * v * p.Sy + 3 * v * v * e * p.C1y + 3 * v * e * e * p.C2y + e * e * e * p.Dy;
                size = 4.2 + (endSize - 4.2) * EOutCubic(u);
            }
            FillSquare((int)Math.Round(x), (int)Math.Round(y), Math.Max(1, (int)Math.Round(size)), Fade(p.Color, alpha));
        }
        _fx.WritePixels(new Int32Rect(0, 0, (int)W, (int)H), _px, (int)W * 4, 0);
    }

    private void FillSquare(int x, int y, int size, int color)
    {
        int w = (int)W, h = (int)H;
        int xa = Math.Max(0, x), xb = Math.Min(w, x + size), ya = Math.Max(0, y), yb = Math.Min(h, y + size);
        for (int yy = ya; yy < yb; yy++)
        {
            int row = yy * w;
            for (int xx = xa; xx < xb; xx++) _px![row + xx] = color;
        }
    }

    private static int Argb(Color c) => (255 << 24) | (c.R << 16) | (c.G << 8) | c.B;

    private static int Fade(int argb, double a)          // premultiplied
    {
        if (a >= 1) return argb;
        int A = (int)(255 * a);
        int R = ((argb >> 16) & 0xFF) * A / 255, G = ((argb >> 8) & 0xFF) * A / 255, B = (argb & 0xFF) * A / 255;
        return (A << 24) | (R << 16) | (G << 8) | B;
    }

    // ------------------------------------------------------------------ assets

    private static Glyph[] LoadGlyphs()
    {
        using var s = Application.GetResourceStream(new Uri("/assets/boot/logo_glyphs.json", UriKind.Relative))!.Stream;
        using var doc = JsonDocument.Parse(s);
        var list = new List<Glyph>();
        foreach (var e in doc.RootElement.EnumerateArray())
        {
            var geo = Geometry.Parse("F1 " + e.GetProperty("d").GetString());
            geo.Transform = new MatrixTransform(SvgTransform(e.GetProperty("tf").GetString() ?? ""));
            geo.Freeze();
            var box = geo.Bounds;
            list.Add(new Glyph(e.GetProperty("n").GetString()!, geo, box.X + box.Width / 2, box.Y + box.Height / 2, box));
        }
        return list.ToArray();
    }

    private static DrawingGroup LoadPlate()
    {
        using var s = Application.GetResourceStream(new Uri("/assets/boot/m265.json", UriKind.Relative))!.Stream;
        using var doc = JsonDocument.Parse(s);
        var r = doc.RootElement;
        var gr = r.GetProperty("grad");
        var grad = new LinearGradientBrush { MappingMode = BrushMappingMode.Absolute,
            StartPoint = new Point(gr.GetProperty("x1").GetDouble(), gr.GetProperty("y1").GetDouble()),
            EndPoint = new Point(gr.GetProperty("x2").GetDouble(), gr.GetProperty("y2").GetDouble()) };
        foreach (var st in gr.GetProperty("stops").EnumerateArray())
            grad.GradientStops.Add(new GradientStop(Hex(st[1].GetString()!), st[0].GetDouble()));
        grad.Freeze();
        var group = new DrawingGroup();
        Geometry? silhouette = null;
        foreach (var l in r.GetProperty("layers").EnumerateArray())
        {
            var geo = Geometry.Parse(l.GetProperty("d").GetString()!);
            geo.Freeze();
            string fill = l.GetProperty("fill").GetString()!;
            Brush brush = fill.StartsWith("url", StringComparison.Ordinal) ? grad : Frozen(new SolidColorBrush(Hex(fill)));
            silhouette ??= geo;
            group.Children.Add(new GeometryDrawing(brush, null, geo));
        }
        var ol = r.GetProperty("outline");
        if (silhouette != null)
        {
            var pen = new Pen(Frozen(new SolidColorBrush(Hex(ol.GetProperty("color").GetString()!))), ol.GetProperty("width").GetDouble()) { LineJoin = PenLineJoin.Round };
            pen.Freeze();
            group.Children.Add(new GeometryDrawing(null, pen, silhouette));
        }
        group.Freeze();
        return group;
    }

    /// <summary>SVG transform list ("matrix(a b c d e f) translate(x,y) …") as one matrix; the rightmost applies first.</summary>
    private static Matrix SvgTransform(string list)
    {
        var result = Matrix.Identity;
        var items = System.Text.RegularExpressions.Regex.Matches(list, @"(\w+)\(([^)]*)\)").Cast<System.Text.RegularExpressions.Match>().ToList();
        for (int i = items.Count - 1; i >= 0; i--)
        {
            var v = items[i].Groups[2].Value.Split(new[] { ' ', ',' }, StringSplitOptions.RemoveEmptyEntries)
                .Select(x => double.Parse(x, CultureInfo.InvariantCulture)).ToArray();
            var m = items[i].Groups[1].Value switch
            {
                "matrix" => new Matrix(v[0], v[1], v[2], v[3], v[4], v[5]),
                "translate" => new Matrix(1, 0, 0, 1, v[0], v.Length > 1 ? v[1] : 0),
                "scale" => new Matrix(v[0], 0, 0, v.Length > 1 ? v[1] : v[0], 0, 0),
                _ => Matrix.Identity,
            };
            result.Append(m);
        }
        return result;
    }

    // ------------------------------------------------------------------ math

    private static double Clamp(double x) => x < 0 ? 0 : x > 1 ? 1 : x;
    private static double EOutCubic(double t) => 1 - Math.Pow(1 - t, 3);
    private static double EOutQuart(double t) => 1 - Math.Pow(1 - t, 4);
    private static double EInOutCubic(double t) => t < .5 ? 4 * t * t * t : 1 - Math.Pow(-2 * t + 2, 3) / 2;

    /// <summary>Damped spring released from 1 toward 0: overshoots and settles.</summary>
    private static double Settle(double tau, double hz, double zeta)
    {
        if (tau <= 0) return 1;
        double w = 2 * Math.PI * hz, wd = w * Math.Sqrt(1 - zeta * zeta);
        return Math.Exp(-zeta * w * tau) * (Math.Cos(wd * tau) + zeta * w / wd * Math.Sin(wd * tau));
    }

    private static double Pulse(double tau, double peak) => tau <= 0 ? 0 : tau / peak * Math.Exp(1 - tau / peak);
    private static double Ring(double tau, double hz, double decay) => tau <= 0 ? 0 : Math.Exp(-tau / decay) * Math.Sin(2 * Math.PI * hz * tau);

    private static Point Quad(Point p0, Point c, Point p1, double t)
    {
        double u = 1 - t;
        return new Point(u * u * p0.X + 2 * u * t * c.X + t * t * p1.X, u * u * p0.Y + 2 * u * t * c.Y + t * t * p1.Y);
    }

    private static Color Hex(string s) => (Color)ColorConverter.ConvertFromString(s);

    private static T Frozen<T>(T f) where T : Freezable
    {
        f.Freeze();
        return f;
    }
}
