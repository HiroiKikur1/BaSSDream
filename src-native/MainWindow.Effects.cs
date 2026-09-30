using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Input;
using System.Windows.Media;
using System.Windows.Media.Animation;
using System.Windows.Media.Effects;
using System.Windows.Media.Imaging;
using Path = System.Windows.Shapes.Path;

namespace BassStation;

// GBP-style tap feedback: a random band logo pops up and coloured stars scatter; dragging leaves a star trail.
public partial class MainWindow
{
    private const string BandLogoDir = @"E:\BassStation\assets\band_logos";   // official large logos
    private const string BandIconDir = @"E:\BassStation\assets\band_icons_line";   // official small emblems
    private static readonly string[] LogoKeys = { "popipa", "afterglow", "pasupare", "roselia", "hhw", "morfonica", "ras", "mygo", "avemujica" };
    private static readonly Brush[] StarBrushes = MakeStarBrushes();
    private static Brush[] MakeStarBrushes()
    {
        Color[] colors =
        {
            Color.FromRgb(255, 92, 140), Color.FromRgb(255, 206, 60), Color.FromRgb(90, 170, 255),
            Color.FromRgb(180, 120, 255), Color.FromRgb(90, 220, 170), Color.FromRgb(255, 140, 90)
        };
        var brushes = new Brush[colors.Length];
        for (int i = 0; i < colors.Length; i++) { brushes[i] = new SolidColorBrush(colors[i]); brushes[i].Freeze(); }
        return brushes;
    }
    private Geometry? _starGeometry;
    private readonly List<AnimationClock> _driftClocks = new();
    private readonly Dictionary<string, ImageSource?> _logoCache = new();
    private Point _lastTrail, _lastTrailLogo;
    private int _fxLive;

    internal static (Brush Brush, FontFamily Font, string Text) BandLogoStyle(string key)
    {
        (string hex, string font, string text) = key switch
        {
            "popipa" => ("#FF3B72", "Segoe UI Black", "Poppin'Party"),
            "afterglow" => ("#E53344", "Segoe Script", "Afterglow"),
            "pasupare" => ("#33C9A0", "Segoe Script", "Pastel*Palettes"),
            "roselia" => ("#3344AA", "Gabriola", "Roselia"),
            "hhw" => ("#F5B800", "Segoe UI Black", "Hello, Happy World!"),
            "morfonica" => ("#33AAFF", "Gabriola", "Morfonica"),
            "ras" => ("#1FB8A8", "Impact", "RAISE A SUILEN"),
            "mygo" => ("#3388BB", "Ink Free", "MyGO!!!!!"),
            "avemujica" => ("#881144", "Gabriola", "Ave Mujica"),
            _ => ("#FF3B72", "Segoe UI Black", "BaSSDream")
        };
        return (new SolidColorBrush((Color)ColorConverter.ConvertFromString(hex)), new FontFamily(font), text);
    }

    internal ImageSource? LogoImage(string key, bool icon = false)
    {
        string cacheKey = (icon ? "i:" : "l:") + key;
        if (_logoCache.TryGetValue(cacheKey, out var img)) return img;
        img = null;
        string p = System.IO.Path.Combine(icon ? BandIconDir : BandLogoDir, key + ".png");
        if (File.Exists(p))
        {
            var b = new BitmapImage();
            b.BeginInit();
            b.UriSource = new Uri(p);
            b.CacheOption = BitmapCacheOption.OnLoad;
            b.DecodePixelHeight = 120;
            b.EndInit();
            b.Freeze();
            img = b;
        }
        _logoCache[cacheKey] = img;
        return img;
    }

    private readonly Dictionary<string, ImageSource?> _emblemCache = new();

    private FrameworkElement? BuildLogoSticker(string key)
    {
        if (!_emblemCache.TryGetValue(key, out var baked))
        {
            // official artwork only: small emblem first, then the band logo; nothing self-drawn
            var icon = LogoImage(key, icon: true);
            var src = icon ?? LogoImage(key);
            baked = src == null ? null : BakeEmblem(WithSilhouette(src, icon != null ? 50 : 34));
            _emblemCache[key] = baked;
        }
        return baked == null ? null : new Image { Source = baked, Width = baked.Width, Height = baked.Height };
    }

    // Renders emblem + silhouette once (2x for crisp scaling). A live OpacityMask per emblem forced an
    // offscreen pass for every emblem on every frame and stalled rendering during fast strokes.
    private static ImageSource BakeEmblem(FrameworkElement el)
    {
        double w = el.Width + 2, h = el.Height + 2;   // room for the silhouette offset
        el.Measure(new Size(w, h));
        el.Arrange(new Rect(0, 0, w, h));
        var rtb = new RenderTargetBitmap((int)Math.Ceiling(w * 2), (int)Math.Ceiling(h * 2), 192, 192, PixelFormats.Pbgra32);
        rtb.Render(el);
        rtb.Freeze();
        return rtb;
    }

    private static readonly Brush SilhouetteBrush = MakeFrozen(Color.FromArgb(110, 40, 30, 50));
    private static Brush MakeFrozen(Color c) { var b = new SolidColorBrush(c); b.Freeze(); return b; }

    // The emblems are white line art: a dark offset silhouette keeps them readable on white cards
    // without a DropShadowEffect (effects on many overlapping, scaling elements re-render every frame).
    private static FrameworkElement WithSilhouette(ImageSource src, double height)
    {
        double width = height * src.Width / Math.Max(1, src.Height);
        var mask = new ImageBrush(src) { Stretch = Stretch.Uniform };
        mask.Freeze();
        var shadow = new System.Windows.Shapes.Rectangle
        {
            Width = width, Height = height, Fill = SilhouetteBrush, OpacityMask = mask,
            RenderTransform = new TranslateTransform(1.2, 1.8)
        };
        var grid = new Grid { Width = width, Height = height };
        grid.Children.Add(shadow);
        grid.Children.Add(new Image { Source = src, Width = width, Height = height });
        return grid;
    }

    private void Fx_MouseDown(object sender, MouseButtonEventArgs e)
    {
        if (_scoreView != null) return;     // no emblems over the score editor
        var p = e.GetPosition(fxLayer);
        _lastTrail = _lastTrailLogo = p;
        Burst(p, logo: true, stars: 9);
    }

    private const double EmblemOpacity = 0.75;   // peak opacity of tap / trail emblems
    private const double TrailSpacing = 38;   // emblems are ~50 px tall: at this pitch they join into a line

    private void Fx_MouseMove(object sender, MouseEventArgs e)
    {
        if (e.LeftButton != MouseButtonState.Pressed || _scoreView != null) return;
        TrailTo(e.GetPosition(fxLayer));
    }

    private void TrailTo(Point p)
    {
        var delta = p - _lastTrailLogo;
        double len = delta.Length;
        if (len < TrailSpacing) return;
        // mouse events arrive every 10-40 px: fill the gap so the line has no holes (bounded for flings)
        int steps = (int)(len / TrailSpacing);
        if (steps > 4)
        {
            // fling: keep the line shape with at most 4 emblems per event, spaced out along the segment
            delta.Normalize();
            double pitch = len / 4;
            for (int i = 1; i <= 4; i++)
                Burst(_lastTrailLogo + delta * (pitch * i), logo: true, stars: 0, trail: true);
            _lastTrailLogo = p;
            _lastTrail = p;
            return;
        }
        delta.Normalize();
        for (int i = 1; i <= steps; i++)
            Burst(_lastTrailLogo + delta * (TrailSpacing * i), logo: true, stars: 1, trail: true);
        _lastTrailLogo += delta * (TrailSpacing * steps);
        _lastTrail = p;
    }

    private void Burst(Point p, bool logo, int stars, bool trail = false)
    {
        TrimFx(FxCap - (logo ? 1 : 0) - stars);
        var rnd = Random.Shared;
        var sticker = logo ? BuildLogoSticker(LogoKeys[rnd.Next(LogoKeys.Length)]) : null;
        if (sticker != null)
        {
            sticker.Measure(new Size(double.PositiveInfinity, double.PositiveInfinity));
            var sz = sticker.DesiredSize;
            // centred where it was triggered; clicks get a little jitter so repeated taps do not stack exactly
            double jitter = trail ? 0 : rnd.Next(-10, 10);
            double x = p.X + jitter - sz.Width / 2, y = p.Y + (trail ? 0 : jitter) - sz.Height / 2;
            x = Math.Clamp(x, 4, Math.Max(4, fxLayer.ActualWidth - sz.Width - 4));
            Canvas.SetLeft(sticker, x);
            Canvas.SetTop(sticker, y);
            // pop in, then fade out in place: tap 0.55 s, trail 0.4 s
            int popMs = trail ? 120 : 200, holdMs = trail ? 150 : 300, fadeMs = trail ? 250 : 250;
            var st = new ScaleTransform(trail ? 0.6 : 0.4, trail ? 0.6 : 0.4, sz.Width / 2, sz.Height / 2);
            var rt = new RotateTransform(rnd.Next(trail ? -6 : -12, trail ? 6 : 12), sz.Width / 2, sz.Height / 2);
            sticker.RenderTransform = new TransformGroup { Children = { st, rt } };
            Spawn(sticker, holdMs + fadeMs);
            var pop = new DoubleAnimationUsingKeyFrames { Duration = TimeSpan.FromMilliseconds(popMs) };
            pop.KeyFrames.Add(new EasingDoubleKeyFrame(trail ? 1.05 : 1.15, KeyTime.FromTimeSpan(TimeSpan.FromMilliseconds(popMs * 0.6)), new BackEase { EasingMode = EasingMode.EaseOut }));
            pop.KeyFrames.Add(new EasingDoubleKeyFrame(1.0, KeyTime.FromTimeSpan(TimeSpan.FromMilliseconds(popMs))));
            st.BeginAnimation(ScaleTransform.ScaleXProperty, pop);
            st.BeginAnimation(ScaleTransform.ScaleYProperty, pop);
            sticker.Opacity = EmblemOpacity;
            sticker.BeginAnimation(OpacityProperty, new DoubleAnimation(EmblemOpacity, 0, TimeSpan.FromMilliseconds(fadeMs)) { BeginTime = TimeSpan.FromMilliseconds(holdMs) });
        }
        for (int i = 0; i < stars; i++)
        {
            double size = rnd.Next(10, logo ? 22 : 15);
            var star = new Path
            {
                Data = _starGeometry ??= (Geometry)FindResource("StarGeometry"), Stretch = Stretch.Uniform, Width = size, Height = size,
                Fill = StarBrushes[rnd.Next(StarBrushes.Length)], Stroke = Brushes.White, StrokeThickness = 1.2
            };
            Canvas.SetLeft(star, p.X - size / 2);
            Canvas.SetTop(star, p.Y - size / 2);
            var tt = new TranslateTransform();
            var rt = new RotateTransform(0, size / 2, size / 2);
            var sc = new ScaleTransform(1, 1, size / 2, size / 2);
            star.RenderTransform = new TransformGroup { Children = { sc, rt, tt } };
            int ms = trail ? rnd.Next(300, 450) : rnd.Next(420, 620);
            Spawn(star, ms);
            double ang = rnd.NextDouble() * Math.PI * 2, dist = rnd.Next(logo ? 50 : 20, logo ? 110 : 55);
            var ease = new QuadraticEase { EasingMode = EasingMode.EaseOut };
            var d = TimeSpan.FromMilliseconds(ms);
            tt.BeginAnimation(TranslateTransform.XProperty, new DoubleAnimation(0, Math.Cos(ang) * dist, d) { EasingFunction = ease });
            tt.BeginAnimation(TranslateTransform.YProperty, new DoubleAnimation(0, Math.Sin(ang) * dist + 18, d) { EasingFunction = ease });
            rt.BeginAnimation(RotateTransform.AngleProperty, new DoubleAnimation(0, rnd.Next(-220, 220), d));
            sc.BeginAnimation(ScaleTransform.ScaleXProperty, new DoubleAnimation(1, 0.3, d));
            sc.BeginAnimation(ScaleTransform.ScaleYProperty, new DoubleAnimation(1, 0.3, d));
            star.BeginAnimation(OpacityProperty, new DoubleAnimation(1, 0, d) { BeginTime = null });
        }
    }

    private void StartBackgroundDrift()
    {
        // shift by exactly one tile (Viewport width in XAML) so the loop is seamless
        const double tile = 1305;
        Drift(bgTextTopShift, 0, -tile);
        Drift(bgTextBottomShift, -tile, 0);
        // nothing to animate while another window (GP8) is in front or we are minimised
        Activated += (_, _) => SetDriftPaused(false);
        Deactivated += (_, _) => SetDriftPaused(true);
    }

    private void Drift(TranslateTransform target, double from, double to)
    {
        var anim = new DoubleAnimation(from, to, TimeSpan.FromSeconds(30)) { RepeatBehavior = RepeatBehavior.Forever };
        var clock = anim.CreateClock();
        target.ApplyAnimationClock(TranslateTransform.XProperty, clock);
        _driftClocks.Add(clock);
    }

    private void SetDriftPaused(bool paused)
    {
        foreach (var c in _driftClocks)
        {
            if (c.Controller == null) continue;
            if (paused) c.Controller.Pause(); else c.Controller.Resume();
        }
    }

    // simulated rapid clicking (select next song + tap burst every 120 ms) while timing every rendered frame
    internal async System.Threading.Tasks.Task<string> DebugPerfClicks(TimeSpan duration, string mode = "songs")
    {
        var frames = new List<double>();
        var sw = System.Diagnostics.Stopwatch.StartNew();
        double last = 0;
        void OnRender(object? s, EventArgs e) { double now = sw.Elapsed.TotalMilliseconds; frames.Add(now - last); last = now; }
        CompositionTarget.Rendering += OnRender;
        var rnd = new Random(1);
        int clicks = 0;
        var selMs = new List<double>(); var burstMs = new List<double>();
        string flingInfo = "";
        if (mode == "fling")
        {
            // input flood: a fast fling posted at Input priority back-to-back, like a real mouse storm
            var done = new System.Threading.Tasks.TaskCompletionSource();
            double fx = 300; int dir = 1;
            _lastTrailLogo = new Point(fx, 420);
            void Step()
            {
                if (sw.Elapsed >= duration) { done.TrySetResult(); return; }
                fx += dir * 45;
                if (fx > 1300 || fx < 300) dir = -dir;
                var op = System.Diagnostics.Stopwatch.StartNew();
                TrailTo(new Point(fx, 420 + 120 * Math.Sin(fx / 90)));
                burstMs.Add(op.Elapsed.TotalMilliseconds); selMs.Add(0);
                clicks++;
                Dispatcher.BeginInvoke(System.Windows.Threading.DispatcherPriority.Input, (Action)Step);
            }
            Dispatcher.BeginInvoke(System.Windows.Threading.DispatcherPriority.Input, (Action)Step);
            await done.Task;
            int atEnd = fxLayer.Children.Count;
            await System.Threading.Tasks.Task.Delay(1500);
            flingInfo = $" children_at_end={atEnd} children_after_1.5s={fxLayer.Children.Count}";
        }
        while (mode != "fling" && sw.Elapsed < duration)
        {
            var op = System.Diagnostics.Stopwatch.StartNew();
            if (mode == "drag")
            {
                // medium-speed stroke: ~900 px/s, a mouse event every 8 ms, sweeping back and forth
                double t = sw.Elapsed.TotalSeconds;
                double x = 300 + 900 * Math.Abs((t * 0.9) % 2 - 1), y = 420 + 160 * Math.Sin(t * 3);
                if (clicks == 0) _lastTrailLogo = new Point(x, y);
                TrailTo(new Point(x, y));
                selMs.Add(0);
                burstMs.Add(op.Elapsed.TotalMilliseconds);
                clicks++;
                await System.Threading.Tasks.Task.Delay(8);
                continue;
            }
            if (mode == "songs" && lstSongs.Items.Count > 0) lstSongs.SelectedIndex = (lstSongs.SelectedIndex + 1) % lstSongs.Items.Count;
            UpdateLayout();
            selMs.Add(op.Elapsed.TotalMilliseconds);
            op.Restart();
            Burst(mode == "same" ? new Point(900, 420) : new Point(rnd.Next(300, 1300), rnd.Next(150, 700)), true, 9);
            burstMs.Add(op.Elapsed.TotalMilliseconds);
            clicks++;
            await System.Threading.Tasks.Task.Delay(mode == "same" ? 70 : 120);
        }
        CompositionTarget.Rendering -= OnRender;
        frames.RemoveAt(0);
        frames.Sort();
        double P(double q) => frames[Math.Min(frames.Count - 1, (int)(q * frames.Count))];
        return $"[{mode}]{flingInfo} fx_live_end={_fxLive} clicks={clicks} frames={frames.Count} fps={frames.Count / duration.TotalSeconds:F1} " +
               $"avg={frames.Average():F1}ms p50={P(0.5):F1} p95={P(0.95):F1} p99={P(0.99):F1} max={frames[^1]:F1} " +
               $">33ms={frames.Count(f => f > 33.4)} >50ms={frames.Count(f => f > 50)}\n" +
               $"select: avg={selMs.Average():F1} max={selMs.Max():F1} [{string.Join(",", selMs.Where(x => x > 20).Select(x => x.ToString("F0")))}]\n" +
               $"burst: avg={burstMs.Average():F1} max={burstMs.Max():F1}";
    }

    internal void DebugBurst()
    {
        Burst(new Point(900, 300), true, 9);
        Burst(new Point(420, 520), true, 9);
        // a stroke, as a medium-speed drag would leave it
        _lastTrailLogo = new Point(560, 700);
        for (double x = 560; x <= 1340; x += 12) TrailTo(new Point(x, 700 - (x - 560) * 0.25));
    }

    internal void DebugOpenFilter() => BtnFilter_Click(this, new RoutedEventArgs());
    internal void DebugOpenSheet(string which)
    {
        if (which == "cal") BtnCalendar_Click(this, new RoutedEventArgs());
        else if (which == "add") BtnAddSong_Click(this, new RoutedEventArgs());
        else if (which == "delete") { if (_selectedSong != null) _ = ConfirmSongAsync(_selectedSong, "删除乐曲", "删除"); }
        else if (which == "toastwarn") ShowToast("乐谱文件丢失", isSuccess: false);
        else if (which == "toastlong") ShowToast("启动失败: 系统找不到指定的路径。(0x80070003) 请检查 Guitar Pro 8 是否已正确安装于默认目录", isSuccess: false);
        else CardScore_MouseDown(this, null!);
    }

    // One sweeper at Normal priority removes expired effects. The old per-element timers ran at Background
    // priority, below mouse input: during a fast stroke they never fired, the layer filled up to the cap,
    // finished effects stayed on screen and every new one was refused.
    private const int FxCap = 140;
    private readonly LinkedList<(UIElement El, long Expires)> _fx = new();
    private readonly System.Diagnostics.Stopwatch _fxClock = System.Diagnostics.Stopwatch.StartNew();
    private System.Windows.Threading.DispatcherTimer? _fxSweeper;

    private void Spawn(UIElement el, int lifeMs)
    {
        fxLayer.Children.Add(el);
        _fx.AddLast((el, _fxClock.ElapsedMilliseconds + lifeMs + 40));
        _fxLive = _fx.Count;
        if (_fxSweeper == null)
        {
            _fxSweeper = new System.Windows.Threading.DispatcherTimer(System.Windows.Threading.DispatcherPriority.Normal)
                { Interval = TimeSpan.FromMilliseconds(50) };
            _fxSweeper.Tick += (_, _) => SweepFx();
        }
        _fxSweeper.Start();
    }

    private void SweepFx()
    {
        long now = _fxClock.ElapsedMilliseconds;
        for (var node = _fx.First; node != null;)
        {
            var next = node.Next;
            if (node.Value.Expires <= now)
            {
                fxLayer.Children.Remove(node.Value.El);
                _fx.Remove(node);
            }
            node = next;
        }
        _fxLive = _fx.Count;
        if (_fx.Count == 0) _fxSweeper?.Stop();
    }

    private void TrimFx(int keep)
    {
        while (_fx.Count > Math.Max(0, keep) && _fx.First != null)
        {
            fxLayer.Children.Remove(_fx.First.Value.El);
            _fx.RemoveFirst();
        }
        _fxLive = _fx.Count;
    }
}
