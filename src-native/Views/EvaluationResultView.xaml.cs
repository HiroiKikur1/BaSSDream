using System;
using System.Globalization;
using System.IO;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Documents;
using System.Windows.Media;
using System.Windows.Media.Animation;
using System.Windows.Media.Imaging;
using System.Windows.Shapes;
using Path = System.Windows.Shapes.Path;
using BassStation.Models;

using BassStation.Services;

namespace BassStation.Views;

/// <summary>Full-window result screen after a take (GBP live-result layout), with the per-note report view.</summary>
public partial class EvaluationResultView : UserControl
{
    public event Action? RequestClose;

    private readonly SongModel _song;
    private readonly PerformanceScoreDetailModel _score;
    private ReportView? _report;

    public EvaluationResultView(SongModel song, PerformanceScoreDetailModel score)
    {
        InitializeComponent();
        _song = song;
        _score = score;
        Fill();
        Loaded += (_, _) => PlayIntro();
    }

    private void Fill()
    {
        // section / slow takes are their own charts: name them next to the song
        txtTitle.Text = _score.ChartLabel == TakeModel.Chart("", 1.0) ? _song.Title : $"{_song.Title}  ·  {_score.ChartLabel}";
        txtLevel.Text = _song.Level.ToString();
        txtTier.Text = _song.Tier.ToUpperInvariant();
        bdTier.Background = new SolidColorBrush(TierChipColor(_song.Tier));

        txtCoverInitial.Text = string.IsNullOrEmpty(_song.Title) ? "" : _song.Title[..1].ToUpperInvariant();
        try
        {
            if (!string.IsNullOrEmpty(_song.CoverImagePath) && File.Exists(_song.CoverImagePath))
            {
                var bmp = new BitmapImage();
                bmp.BeginInit();
                bmp.UriSource = new Uri(_song.CoverImagePath);
                bmp.CacheOption = BitmapCacheOption.OnLoad;
                bmp.DecodePixelWidth = 600;
                bmp.EndInit();
                bmp.Freeze();
                imgCover.Source = bmp;
            }
        }
        catch { }

        txtScore.Text = _score.Points.ToString(CultureInfo.InvariantCulture);
        // as in the game: the high score before this take
        txtBest.Text = _score.PrevBest.HasValue
            ? PerformanceScoreDetailModel.ToPoints(_score.PrevBest.Value).ToString(CultureInfo.InvariantCulture)
            : "0";

        Counter(nPerfect, _score.Perfect);
        Counter(nGreat, _score.Great);
        Counter(nGood, _score.Good);
        Counter(nBad, _score.Bad);
        Counter(nMiss, _score.Miss);
        Counter(nFast, _score.Fast);
        Counter(nSlow, _score.Slow);
        Counter(nCombo, _score.MaxCombo);
        nWrong.Text = _score.Wrong.ToString(CultureInfo.InvariantCulture);
        nMissNote.Text = _score.Miss.ToString(CultureInfo.InvariantCulture);
        nExtra.Text = _score.ExtraNotes.ToString(CultureInfo.InvariantCulture);

        var judges = new (string Text, Brush Fill)[]
        {
            ("PERFECT", GbpText.Horizontal("#F58FB0", "#F7C27A", "#8FE3C3", "#8BB0EF", "#C79BF2")),
            ("GREAT", GbpText.Vertical("#FF6FD0", "#FA009F")),
            ("GOOD", GbpText.Vertical("#E4FF3C", "#9BE31C", "#2DB84A")),
            ("BAD", GbpText.Vertical("#57C8FF", "#0A78E6")),
            ("MISS", GbpText.Vertical("#A6A1AE", "#57575B")),
        };
        for (int i = 0; i < judges.Length; i++)
        {
            var t = new GbpText { Text = judges[i].Text, Size = 29, Fill = judges[i].Fill, Rim = 3, ShadowOpacity = 0.2 };
            Canvas.SetLeft(t, 24);
            Canvas.SetTop(t, 99 + i * 37.5);
            judgeLayer.Children.Add(t);
        }

        var (clear, clearFill) = _score.ComboBadge switch
        {
            "ALL PERFECT" => ("ALL PERFECT!", (Brush)GbpText.Horizontal("#FF63C8", "#FFB347", "#5FE3C0", "#6FA8FF", "#C67BFF")),
            "FULL COMBO" => ("FULL COMBO!", GbpText.Vertical("#FF63C8", "#FD0AA3", "#E0008F")),
            "FAILED" => ("FAILED", GbpText.Vertical("#A6A1AE", "#57575B")),
            _ => ("CLEAR!", GbpText.Vertical("#FFD35C", "#FF9F1A")),
        };
        clearHost.Children.Add(new GbpText
        {
            Text = clear, Size = 28, Italic = true, Fill = clearFill, Rim = 3.5,
            Outer = new SolidColorBrush(Color.FromRgb(0x8C, 0x0B, 0x63)), OuterWidth = 1, ShadowOpacity = 0.3,
            HorizontalAlignment = HorizontalAlignment.Center, VerticalAlignment = VerticalAlignment.Center
        });
        if (_score.IsNewBest)
        {
            newRecordHost.Children.Add(new GbpText
            {
                Text = "NEW RECORD!", Size = 27, Italic = true, Fill = GbpText.Vertical("#FF69C9", "#EC27A9", "#CE0393"),
                Rim = 3.5, Outer = new SolidColorBrush(Color.FromRgb(0x6E, 0x0F, 0x5C)), OuterWidth = 1.2, ShadowOpacity = 0.3,
                HorizontalAlignment = HorizontalAlignment.Right, VerticalAlignment = VerticalAlignment.Center
            });
        }

        DrawDeco();
        DrawRankBadge(_score.Grade);
    }

    private static Color TierChipColor(string tier) => tier.ToUpperInvariant() switch
    {
        "EASY" => Color.FromRgb(0x2E, 0x8B, 0xF0),
        "NORMAL" => Color.FromRgb(0x2F, 0xC0, 0x58),
        "HARD" => Color.FromRgb(0xFF, 0xA8, 0x00),
        "EXPERT" => Color.FromRgb(0xF0, 0x2B, 0x4B),
        _ => Color.FromRgb(0xFF, 0x00, 0x96)
    };

    /// <summary>Game-style 4-digit counter: the padding zeros are greyed out.</summary>
    private static void Counter(TextBlock tb, int n)
    {
        string digits = n.ToString(CultureInfo.InvariantCulture);
        string pad = digits.Length < 4 ? new string('0', 4 - digits.Length) : "";
        tb.Inlines.Clear();
        if (pad.Length > 0) tb.Inlines.Add(new Run(pad) { Foreground = new SolidColorBrush(Color.FromRgb(0xD2, 0xD2, 0xD2)) });
        tb.Inlines.Add(new Run(digits));
    }

    /// <summary>White line-art stars beside the illustration, as on the result screen.</summary>
    private void DrawDeco()
    {
        var star = (Geometry)FindResource("StarGeometry");
        var spots = new (double X, double Y, double Size, double Opacity)[]
        {
            (612, 210, 34, 0.75), (650, 330, 24, 0.55), (606, 440, 42, 0.7), (668, 548, 28, 0.5), (620, 628, 22, 0.6), (574, 136, 20, 0.6)
        };
        foreach (var (x, y, size, op) in spots)
        {
            var p = new Path
            {
                Data = star, Stretch = Stretch.Uniform, Width = size, Height = size * 0.95,
                Stroke = Brushes.White, StrokeThickness = 2, StrokeLineJoin = PenLineJoin.Round, Opacity = op
            };
            decoLayer.Children.Add(p.At(x, y));
        }
    }

    private void DrawRankBadge(string grade)
    {
        rankBadge.Children.Clear();
        var c = new Point(150, 150);

        // soft light rays behind the medal
        var rays = new StreamGeometry();
        using (var ctx = rays.Open())
        {
            const int n = 16;
            for (int i = 0; i < n; i++)
            {
                double a0 = 2 * Math.PI * i / n, a1 = 2 * Math.PI * (i + 0.45) / n;
                ctx.BeginFigure(c, true, true);
                ctx.LineTo(new Point(c.X + 150 * Math.Cos(a0), c.Y + 150 * Math.Sin(a0)), false, false);
                ctx.LineTo(new Point(c.X + 150 * Math.Cos(a1), c.Y + 150 * Math.Sin(a1)), false, false);
            }
        }
        rays.Freeze();
        rankBadge.Children.Add(new Path
        {
            Data = rays,
            Fill = new RadialGradientBrush(Color.FromArgb(120, 255, 255, 255), Color.FromArgb(0, 255, 255, 255))
        });

        // gold ring with short radial stripes
        rankBadge.Children.Add(new Ellipse { Width = 148, Height = 148, Fill = new SolidColorBrush(Color.FromRgb(0xF9, 0xB6, 0x02)) }.At(c.X - 74, c.Y - 74));
        var stripes = new GeometryGroup();
        for (int i = 0; i < 40; i++)
        {
            double a = 2 * Math.PI * i / 40;
            stripes.Children.Add(new LineGeometry(new Point(c.X + 62 * Math.Cos(a), c.Y + 62 * Math.Sin(a)),
                                                  new Point(c.X + 72 * Math.Cos(a), c.Y + 72 * Math.Sin(a))));
        }
        rankBadge.Children.Add(new Path { Data = stripes, Stroke = new SolidColorBrush(Color.FromRgb(0xFF, 0xE7, 0x86)), StrokeThickness = 4.5 });
        rankBadge.Children.Add(new Ellipse { Width = 124, Height = 124, Fill = Brushes.White }.At(c.X - 62, c.Y - 62));
        rankBadge.Children.Add(new Ellipse
        {
            Width = 114, Height = 114,
            Fill = new RadialGradientBrush(Color.FromRgb(0xFF, 0xFF, 0xFF), Color.FromRgb(0xFF, 0xF1, 0xC4))
        }.At(c.X - 57, c.Y - 57));

        // letters: marbled rainbow fill, purple outline, white rim
        string g = string.IsNullOrEmpty(grade) ? "C" : grade;
        var ft = new FormattedText(g, CultureInfo.InvariantCulture, FlowDirection.LeftToRight,
            new Typeface((FontFamily)FindResource("RoundFont"), FontStyles.Normal, FontWeights.Heavy, FontStretches.Normal),
            g.Length > 1 ? 70 : 82, Brushes.Black, VisualTreeHelper.GetDpi(this).PixelsPerDip);
        var geo = ft.BuildGeometry(new Point(c.X - ft.Width / 2, c.Y - ft.Height / 2 - 3));
        Brush fill = g switch
        {
            "SS" => GbpText.Horizontal("#FF7ADF", "#B77CFF", "#6FE8FF", "#7CF29A", "#FFE45C", "#FF8AD8"),
            "S" => GbpText.Vertical("#FFE45C", "#FFB300"),
            "A" => GbpText.Vertical("#8FD8FF", "#2F7BFF"),
            "B" => GbpText.Vertical("#9BF2B8", "#1DB86A"),
            _ => GbpText.Vertical("#FFC08A", "#FF7A1A")
        };
        rankBadge.Children.Add(new Path { Data = geo, Stroke = Brushes.White, StrokeThickness = 13, StrokeLineJoin = PenLineJoin.Round });
        rankBadge.Children.Add(new Path { Data = geo, Stroke = new SolidColorBrush(Color.FromRgb(0x7B, 0x1F, 0xA2)), StrokeThickness = 7, StrokeLineJoin = PenLineJoin.Round });
        rankBadge.Children.Add(new Path { Data = geo, Fill = fill });

        // curved ribbon
        var ribbon = Geometry.Parse("M 0,14 Q 110,-2 220,14 L 212,31 L 220,48 Q 110,32 0,48 L 8,31 Z");
        rankBadge.Children.Add(new Path
        {
            Data = ribbon, Fill = GbpText.Vertical("#3FD2FF", "#00AEEF"),
            Stroke = Brushes.White, StrokeThickness = 2.5, StrokeLineJoin = PenLineJoin.Round
        }.At(c.X - 110, c.Y + 50));
        var label = new TextBlock
        {
            Text = "SCORE RANK", FontFamily = (FontFamily)FindResource("RoundFont"), FontWeight = FontWeights.Bold, FontSize = 19,
            Foreground = Brushes.White, Width = 220, TextAlignment = TextAlignment.Center
        };
        rankBadge.Children.Add(label.At(c.X - 110, c.Y + 58));
    }

    private void PlayIntro()
    {
        var ease = new BackEase { EasingMode = EasingMode.EaseOut, Amplitude = 0.5 };
        var grow = new DoubleAnimation(1.5, 0.87, TimeSpan.FromMilliseconds(420)) { EasingFunction = ease, BeginTime = TimeSpan.FromMilliseconds(250) };
        rankScale.BeginAnimation(ScaleTransform.ScaleXProperty, grow);
        rankScale.BeginAnimation(ScaleTransform.ScaleYProperty, grow);
        rankBadge.BeginAnimation(OpacityProperty, new DoubleAnimation(0, 1, TimeSpan.FromMilliseconds(200)) { BeginTime = TimeSpan.FromMilliseconds(250) });
        root.BeginAnimation(OpacityProperty, new DoubleAnimation(0, 1, TimeSpan.FromMilliseconds(220)));
        cardResult.RenderTransform = new TranslateTransform();
        cardResult.RenderTransform.BeginAnimation(TranslateTransform.XProperty,
            new DoubleAnimation(60, 0, TimeSpan.FromMilliseconds(320)) { EasingFunction = new CubicEase { EasingMode = EasingMode.EaseOut } });
    }

    internal void ShowReport()
    {
        if (_report == null)
        {
            _reportData = EvaluationReport.Load(_score.ReportPath);
            _report = new ReportView(_song, _score, _reportData);
            _report.BarSelected += OnBarSelected;
            reportHost.Content = _report;
        }
        summaryPanel.Visibility = Visibility.Collapsed;
        rankBadge.Visibility = Visibility.Collapsed;
        reportPanel.Visibility = Visibility.Visible;
        reportScroll.ScrollToTop();
        reportPanel.BeginAnimation(OpacityProperty, new DoubleAnimation(0, 1, TimeSpan.FromMilliseconds(180)));
    }

    internal void DebugScroll(double offset) => reportScroll.ScrollToVerticalOffset(offset);

    private void BtnReport_Click(object sender, RoutedEventArgs e) => ShowReport();

    // ---------------------------------------------------------------- bar replay

    private EvaluationReport? _reportData;
    private ReportBar? _replayBar;
    private readonly Services.ClipPlayer _clips = new();
    private string? _origBass;                 // original bass stem at the take's tempo

    private void OnBarSelected(ReportBar bar)
    {
        _replayBar = bar;
        txtReplayBar.Text = $"第 {bar.Number} 小节";
        replayBar.Visibility = Visibility.Visible;
    }

    /// <summary>The take's own bar: backing time / rate + the evaluator's offset, with a short lead-in.</summary>
    private Services.ClipPlayer.Clip? MineClip()
    {
        if (_reportData?.AudioPath is not { } audio || _replayBar is not { } b) return null;
        double off = _reportData.OffsetMs / 1000.0, r = _reportData.Rate;
        return new(audio, b.T0 / r + off - 0.15, b.T1 / r + off + 0.25);
    }

    private async System.Threading.Tasks.Task<Services.ClipPlayer.Clip?> OrigClipAsync()
    {
        if (_reportData == null || _replayBar is not { } b) return null;
        if (_origBass == null)
        {
            SetReplayEnabled(false);
            _origBass = await System.Threading.Tasks.Task.Run(PrepareOriginalBass);
            SetReplayEnabled(true);
        }
        if (_origBass == null)
        {
            (Application.Current.MainWindow as MainWindow)?.ShowToast("无原曲贝斯轨", isSuccess: false);
            return null;
        }
        double r = _reportData.Rate;
        return new(_origBass, b.T0 / r - 0.15, b.T1 / r + 0.25);
    }

    /// <summary>The original bass of the evaluated score (its '[伴奏].gp' when the score has no audio), at the take's tempo.</summary>
    private string? PrepareOriginalBass()
    {
        string gp = _song.GpPath;
        string derived = System.IO.Path.Combine(System.IO.Path.GetDirectoryName(gp) ?? "", System.IO.Path.GetFileNameWithoutExtension(gp) + " [伴奏].gp");
        if (System.IO.File.Exists(derived)) gp = derived;
        var res = ScoreFormatCli("audio", _song.Id, gp);
        if (res is not System.Text.Json.JsonElement a || !a.TryGetProperty("bass", out var bass) || bass.ValueKind != System.Text.Json.JsonValueKind.String) return null;
        if (Math.Abs(_reportData!.Rate - 1) < 1e-3) return bass.GetString();
        var st = ScoreFormatCli("stretch", _song.Id, _reportData.Rate.ToString("F2", CultureInfo.InvariantCulture));
        return st is System.Text.Json.JsonElement s && s.TryGetProperty("bass", out var sb) && sb.ValueKind == System.Text.Json.JsonValueKind.String ? sb.GetString() : null;
    }

    private static System.Text.Json.JsonElement? ScoreFormatCli(params string[] args)
    {
        var psi = new System.Diagnostics.ProcessStartInfo
        {
            FileName = AppPaths.PythonMl,
            UseShellExecute = false, RedirectStandardOutput = true, RedirectStandardError = true, CreateNoWindow = true,
            StandardOutputEncoding = System.Text.Encoding.UTF8
        };
        psi.ArgumentList.Add(AppPaths.Script("score_format.py"));
        foreach (var a in args) psi.ArgumentList.Add(a);
        psi.EnvironmentVariables["PYTHONUTF8"] = "1";
        try
        {
            using var p = System.Diagnostics.Process.Start(psi);
            if (p == null) return null;
            var err = p.StandardError.ReadToEndAsync();
            string stdout = p.StandardOutput.ReadToEnd();
            p.WaitForExit();
            _ = err.Result;
            string? line = stdout.Split('\n').Select(l => l.Trim()).LastOrDefault(l => l.StartsWith('{'));
            if (line == null) return null;
            using var doc = System.Text.Json.JsonDocument.Parse(line);
            return doc.RootElement.Clone();
        }
        catch { return null; }
    }

    private void SetReplayEnabled(bool on) => btnMine.IsEnabled = btnOrig.IsEnabled = btnAB.IsEnabled = on;

    private void Latch(Button? b)
    {
        foreach (var x in new[] { btnMine, btnOrig, btnAB }) x.Tag = x == b ? "on" : null;
    }

    private void PlayClips(Button b, params Services.ClipPlayer.Clip?[] clips)
    {
        var list = clips.Where(c => c != null).Select(c => c!).ToList();
        if (list.Count == 0) return;
        try
        {
            _clips.Finished -= OnClipsFinished;
            _clips.Finished += OnClipsFinished;
            _clips.Play(list);
            Latch(b);
        }
        catch (Exception)
        {
            (Application.Current.MainWindow as MainWindow)?.ShowToast("播放失败", isSuccess: false);
        }
    }

    private void OnClipsFinished() => Dispatcher.BeginInvoke(new Action(() => Latch(null)));

    private void BtnMine_Click(object sender, RoutedEventArgs e) => PlayClips(btnMine, MineClip());

    private async void BtnOrig_Click(object sender, RoutedEventArgs e) => PlayClips(btnOrig, await OrigClipAsync());

    private async void BtnAB_Click(object sender, RoutedEventArgs e)
    {
        var orig = await OrigClipAsync();
        PlayClips(btnAB, orig, MineClip());
    }

    // Debug: replays a bar through the real output and records the system loopback into a wav.
    internal async System.Threading.Tasks.Task DebugReplayAsync(int barIndex, string which, string wav)
    {
        if (_reportData == null || barIndex >= _reportData.Bars.Count) return;
        OnBarSelected(_reportData.Bars[barIndex]);
        var cap = new NAudio.Wave.WasapiLoopbackCapture();
        var w = new NAudio.Wave.WaveFileWriter(wav, cap.WaveFormat);
        var done = new System.Threading.Tasks.TaskCompletionSource<bool>();
        cap.DataAvailable += (_, a) => w.Write(a.Buffer, 0, a.BytesRecorded);
        cap.RecordingStopped += (_, _) => { w.Dispose(); cap.Dispose(); done.TrySetResult(true); };
        cap.StartRecording();
        if (which == "mine") BtnMine_Click(this, new RoutedEventArgs());
        else if (which == "orig") BtnOrig_Click(this, new RoutedEventArgs());
        else BtnAB_Click(this, new RoutedEventArgs());
        await System.Threading.Tasks.Task.Delay(500);
        for (int i = 0; i < 200 && (_clips.IsPlaying || _origBass == null && which != "mine"); i++) await System.Threading.Tasks.Task.Delay(100);
        await System.Threading.Tasks.Task.Delay(300);
        cap.StopRecording();
        await done.Task;
    }

    private void BtnStopReplay_Click(object sender, RoutedEventArgs e)
    {
        _clips.Stop();
        Latch(null);
    }

    private void BtnReportBack_Click(object sender, RoutedEventArgs e)
    {
        _clips.Stop();
        replayBar.Visibility = Visibility.Collapsed;
        _report?.ClearSelection();
        reportPanel.Visibility = Visibility.Collapsed;
        summaryPanel.Visibility = Visibility.Visible;
        rankBadge.Visibility = Visibility.Visible;
    }

    private void BtnHome_Click(object sender, RoutedEventArgs e) => RequestClose?.Invoke();

    private void BtnCopy_Click(object sender, RoutedEventArgs e)
    {
        if (_report == null) return;
        try
        {
            _report.Measure(new Size(ReportView.PageWidth, double.PositiveInfinity));
            var size = _report.DesiredSize;
            var dpi = VisualTreeHelper.GetDpi(this);
            var rtb = new RenderTargetBitmap((int)Math.Ceiling(size.Width * dpi.DpiScaleX), (int)Math.Ceiling(size.Height * dpi.DpiScaleY),
                                             dpi.PixelsPerInchX, dpi.PixelsPerInchY, PixelFormats.Pbgra32);
            // render through a brush so the copy doesn't depend on the scroll position
            var dv = new DrawingVisual();
            using (var dc = dv.RenderOpen())
                dc.DrawRectangle(new VisualBrush(_report), null, new Rect(size));
            rtb.Render(dv);
            rtb.Freeze();

            var data = new DataObject();
            data.SetImage(rtb);
            data.SetText(_report.ToPlainText());
            Clipboard.SetDataObject(data, true);
            if (Application.Current.MainWindow is MainWindow main) main.ShowToast("已复制", isSuccess: true);
        }
        catch
        {
            if (Application.Current.MainWindow is MainWindow main) main.ShowToast("复制失败", isSuccess: false);
        }
    }
}

internal static class CanvasExt
{
    public static T At<T>(this T el, double x, double y) where T : UIElement
    {
        Canvas.SetLeft(el, x);
        Canvas.SetTop(el, y);
        return el;
    }
}
