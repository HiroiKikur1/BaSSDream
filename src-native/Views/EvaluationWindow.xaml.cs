using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Linq;
using System.IO;
using System.Text;
using System.Text.Json;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;
using System.Windows.Media.Imaging;
using System.Windows.Threading;
using BassStation.Models;
using BassStation.Services;

namespace BassStation.Views;

public partial class EvaluationWindow : Window
{
    // librosa lives in the ML environment, not in the backend's default Python
    private static readonly string EvalScript = AppPaths.Script("performance_evaluator.py");
    private static readonly string SessionScript = AppPaths.Script("eval_session.py");
    private static readonly string TakesDir = AppPaths.CachePath(@"evaluations\takes");

    public event Action? RequestClose;
    public event Action<PerformanceScoreDetailModel>? ScoreUpdated;
    /// <summary>A finished evaluation, for the result screen.</summary>
    public event Action<PerformanceScoreDetailModel>? ResultReady;

    private readonly DatabaseService _dbService = new();
    private readonly SongModel _song;

    /// <summary>The score to evaluate against: scores without embedded audio get their backing from the
    /// derived '&lt;name&gt; [伴奏].gp' (same notation plus aligned audio), which is also what gets practised in GP.</summary>
    private string EvalGpPath
    {
        get
        {
            if (string.IsNullOrEmpty(_song.GpPath)) return "";
            string derived = Path.Combine(Path.GetDirectoryName(_song.GpPath) ?? "",
                                          Path.GetFileNameWithoutExtension(_song.GpPath) + " [伴奏].gp");
            return File.Exists(derived) ? derived : _song.GpPath;
        }
    }

    private enum TakeState { Idle, Preparing, Recording, Analyzing }
    private TakeState _state = TakeState.Idle;
    private TakeRecorder? _recorder;
    private DispatcherTimer? _takeTimer;
    private double _countIn, _beat, _leadS;
    private PracticeSection? _takeSection;      // range / tempo of the take being recorded
    private double _takeRate = 1.0;

    // what the next take covers: the whole song or one section, at a backing tempo (kept across sheets)
    private List<PracticeSection> _sections = new();
    private PracticeSection? _section;
    private static double _rate = 1.0;
    private static readonly double[] Rates = { 1.0, 0.9, 0.8, 0.7, 0.6 };
    private List<TakeModel> _takes = new();

    public EvaluationWindow(SongModel song)
    {
        InitializeComponent();
        _song = song;
        if (Content is FrameworkElement root)
        {
            root.Loaded += EvaluationWindow_Loaded;
            // the sheet can be dismissed mid-take
            root.Unloaded += (_, _) => AbortTake();
        }
    }

    private async void EvaluationWindow_Loaded(object sender, RoutedEventArgs e)
    {
        txtSongTitle.Text = _song.Title;
        txtSongArtist.Text = _song.Artist;
        txtSongBpm.Text = _song.DisplayBpm;
        txtTier.Text = _song.Tier;
        txtLevel.Text = $"Lv.{_song.Level}";

        var conv = new BrushConverter();
        bdTier.Background = (SolidColorBrush)conv.ConvertFromString(_song.TierBgColor)!;
        txtTier.Foreground = txtLevel.Foreground = (SolidColorBrush)conv.ConvertFromString(_song.TierColor)!;

        try
        {
            if (!string.IsNullOrEmpty(_song.CoverImagePath) && File.Exists(_song.CoverImagePath))
            {
                var bmp = new BitmapImage();
                bmp.BeginInit();
                bmp.UriSource = new Uri(_song.CoverImagePath);
                bmp.CacheOption = BitmapCacheOption.OnLoad;
                bmp.DecodePixelWidth = 100;
                bmp.EndInit();
                bmp.Freeze();
                imgSongCover.Source = bmp;
            }
        }
        catch
        {
            imgSongCover.Source = null;
        }

        RefreshInputLabel();
        RefreshRangeLabels();

        var best = await _dbService.LoadPerformanceDetailAsync(_song.Id);
        if (best != null && best.OverallScore > 0) ApplyScoreUI(best);
        else ApplyEmptyUI();
        await RefreshTakesAsync();
        await LoadSectionsAsync();
    }

    private async Task RefreshTakesAsync()
    {
        _takes = await _dbService.LoadTakesAsync(_song.Id);
        icTakes.ItemsSource = _takes;
        cardHistory.Visibility = _takes.Count > 0 ? Visibility.Visible : Visibility.Collapsed;
    }

    private async Task LoadSectionsAsync()
    {
        string gp = EvalGpPath;
        if (string.IsNullOrEmpty(gp) || !File.Exists(gp)) return;
        try
        {
            var res = await Task.Run(() => RunPythonJson(SessionScript, gp, "--sections"));
            if (res is not JsonElement r || !r.TryGetProperty("success", out var ok) || !ok.GetBoolean()) return;
            _sections = r.GetProperty("sections").EnumerateArray().Select(x => new PracticeSection
            {
                Name = x.GetProperty("name").GetString() ?? "",
                Start = x.GetProperty("start").GetInt32(),
                End = x.GetProperty("end").GetInt32(),
                Bar = x.GetProperty("bar").GetInt32(),
                Notes = x.GetProperty("notes").GetInt32(),
                Seconds = x.GetProperty("seconds").GetDouble(),
            }).ToList();
        }
        catch (Exception ex) { Debug.WriteLine(ex); }
    }

    private void ApplyEmptyUI()
    {
        SetRank("", "#B3ACBC", "#FFF7FA", "#F1DCE5");
        txtOverallScore.Text = "--";
        bdCombo.Visibility = Visibility.Collapsed;
        bdNewBest.Visibility = Visibility.Collapsed;
        txtEvaluatedAt.Text = "";
        SetJudgments(null);
        SetDimensions(null);
        icMeasures.ItemsSource = null;
    }

    private void ApplyScoreUI(PerformanceScoreDetailModel s)
    {
        SetRank(s.Grade, s.GradeColor, s.GradeBgColor, s.GradeColor);
        txtOverallScore.Text = s.Points.ToString();
        bdCombo.Visibility = string.IsNullOrEmpty(s.ComboBadge) ? Visibility.Collapsed : Visibility.Visible;
        txtComboBadge.Text = s.ComboBadge;
        bdNewBest.Visibility = s.IsNewBest ? Visibility.Visible : Visibility.Collapsed;
        txtEvaluatedAt.Text = s.EvaluatedAt.Length >= 16 ? s.EvaluatedAt[..16] : s.EvaluatedAt;

        // rows from the old heuristic evaluator carry only a score
        SetJudgments(s.HasJudgments ? s : null);
        SetDimensions(s.HasJudgments ? s : null);
        icMeasures.ItemsSource = s.Measures.Count > 0 ? s.Measures : null;
    }

    private void SetRank(string grade, string fg, string bg, string border)
    {
        var conv = new BrushConverter();
        var fgBrush = (SolidColorBrush)conv.ConvertFromString(fg)!;
        txtRankGrade.Text = string.IsNullOrEmpty(grade) ? "--" : grade;
        txtRankGrade.Foreground = txtRankLabel.Foreground = fgBrush;
        bdRankCircle.Background = (SolidColorBrush)conv.ConvertFromString(bg)!;
        bdRankCircle.BorderBrush = (SolidColorBrush)conv.ConvertFromString(border)!;
    }

    private void SetJudgments(PerformanceScoreDetailModel? s)
    {
        txtPerfect.Text = s?.Perfect.ToString() ?? "--";
        txtGreat.Text = s?.Great.ToString() ?? "--";
        txtGood.Text = s?.Good.ToString() ?? "--";
        txtBad.Text = s?.Bad.ToString() ?? "--";
        txtMiss.Text = s?.Miss.ToString() ?? "--";
        txtMaxCombo.Text = s?.MaxCombo.ToString() ?? "--";
    }

    private void SetDimensions(PerformanceScoreDetailModel? s)
    {
        pbTiming.Value = s?.TimingScore ?? 0;
        pbPitch.Value = s?.PitchScore ?? 0;
        pbComplete.Value = s?.CompleteScore ?? 0;
        pbClean.Value = s?.CleanScore ?? 0;
        txtTiming.Text = s == null ? "--" : Math.Round(s.TimingScore).ToString();
        txtPitch.Text = s == null ? "--" : Math.Round(s.PitchScore).ToString();
        txtComplete.Text = s == null ? "--" : Math.Round(s.CompleteScore).ToString();
        txtClean.Text = s == null ? "--" : Math.Round(s.CleanScore).ToString();
    }

    private async void BtnUploadAudio_Click(object sender, RoutedEventArgs e)
    {
        if (_state != TakeState.Idle) return;
        var dlg = new Microsoft.Win32.OpenFileDialog
        {
            Title = "选择录音",
            Filter = "音频 (*.wav;*.mp3;*.m4a;*.flac;*.ogg)|*.wav;*.mp3;*.m4a;*.flac;*.ogg|所有文件 (*.*)|*.*"
        };
        if (dlg.ShowDialog(Application.Current.MainWindow) == true)
            await EvaluateAsync(dlg.FileName, null, _section, _rate);
    }

    internal async Task EvaluateAsync(string audioPath, double? lagHint = null, PracticeSection? section = null, double rate = 1.0)
    {
        _state = TakeState.Analyzing;
        SetButtonsEnabled(false);
        txtUpload.Text = "分析中";
        try
        {
            var (result, error) = await Task.Run(() => RunEvaluator(audioPath, lagHint, section, rate));
            if (result == null)
            {
                Notify(error ?? "分析失败");
                return;
            }
            bool songBest = result.IsNewBest && result.ChartLabel == TakeModel.Chart("", 1.0);
            if (songBest) ApplyScoreUI(result);
            if (songBest) ScoreUpdated?.Invoke(result);
            await RefreshTakesAsync();
            ResultReady?.Invoke(result);
        }
        catch (Exception ex)
        {
            Debug.WriteLine(ex);
            Notify("分析失败");
        }
        finally
        {
            txtUpload.Text = "选择录音";
            ResetTakeUI();
        }
    }

    private static void Notify(string message)
    {
        (Application.Current.MainWindow as MainWindow)?.ShowToast(message, isSuccess: false);
    }

    // ---------------------------------------------------------------- in-app take

    private void RefreshInputLabel()
    {
        string name = "默认输入";
        try
        {
            var saved = TakeRecorder.SavedDeviceId;
            var cur = TakeRecorder.InputDevices().Find(d => d.Id == saved);
            if (cur.Name != null) name = cur.Name;
        }
        catch { }
        txtInput.Text = name;
        btnInput.ToolTip = name;
    }

    private void BtnInput_Click(object sender, RoutedEventArgs e)
    {
        menuInput.Items.Clear();
        var saved = TakeRecorder.SavedDeviceId;
        var def = new MenuItem { Header = "默认输入", IsCheckable = true, IsChecked = string.IsNullOrEmpty(saved) };
        def.Click += (_, _) => { TakeRecorder.SavedDeviceId = ""; RefreshInputLabel(); };
        menuInput.Items.Add(def);
        try
        {
            foreach (var (id, name) in TakeRecorder.InputDevices())
            {
                var item = new MenuItem { Header = name, IsCheckable = true, IsChecked = id == saved };
                item.Click += (_, _) => { TakeRecorder.SavedDeviceId = id; RefreshInputLabel(); };
                menuInput.Items.Add(item);
            }
        }
        catch { }
        menuInput.PlacementTarget = btnInput;
        menuInput.Placement = System.Windows.Controls.Primitives.PlacementMode.Top;
        menuInput.IsOpen = true;
    }

    private void RefreshRangeLabels()
    {
        txtSection.Text = _section?.Name ?? "全曲";
        txtRate.Text = $"{Math.Round(_rate * 100)}%";
    }

    private static readonly Brush SubInk = new SolidColorBrush(Color.FromRgb(0x9A, 0x93, 0xA6));

    // Debug: opens the practice-range menu once the sections are loaded; returns it for rendering.
    internal async Task<FrameworkElement> DebugOpenSectionMenuAsync()
    {
        for (int i = 0; i < 100 && _sections.Count == 0; i++) await Task.Delay(100);
        BtnSection_Click(btnSection, new RoutedEventArgs());
        return menuSection;
    }

    /// <summary>Grade of the latest complete take that covered a section (a section take, or its part of a longer take).</summary>
    private string? LastSectionGrade(PracticeSection sec)
    {
        foreach (var t in _takes)
        {
            if (!t.Complete) continue;
            if (t.RangeStart == sec.Start && t.RangeEnd == sec.End) return t.Grade;
            if (t.SectionResults().TryGetValue(sec.Key, out var r)) return r.Grade;
        }
        return null;
    }

    private void BtnSection_Click(object sender, RoutedEventArgs e)
    {
        menuSection.Items.Clear();
        var all = new MenuItem { Header = "全曲", IsCheckable = true, IsChecked = _section == null };
        all.Click += (_, _) => { _section = null; RefreshRangeLabels(); };
        menuSection.Items.Add(all);
        foreach (var sec in _sections)
        {
            var header = new TextBlock();
            header.Inlines.Add(new System.Windows.Documents.Run(sec.Name));
            // scores without section markers get "9–16 小节" groups, which already name their bars
            if (!sec.Name.EndsWith("小节"))
                header.Inlines.Add(new System.Windows.Documents.Run($"   {sec.Bar}") { FontWeight = FontWeights.Normal, Foreground = SubInk });
            var item = new MenuItem
            {
                Header = header,
                IsCheckable = true,
                IsChecked = _section?.Key == sec.Key,
                InputGestureText = LastSectionGrade(sec) ?? ""
            };
            item.Click += (_, _) => { _section = sec; RefreshRangeLabels(); };
            menuSection.Items.Add(item);
        }
        menuSection.PlacementTarget = btnSection;
        menuSection.Placement = System.Windows.Controls.Primitives.PlacementMode.Top;
        menuSection.IsOpen = true;
    }

    private void BtnRate_Click(object sender, RoutedEventArgs e)
    {
        menuRate.Items.Clear();
        foreach (double r in Rates)
        {
            var item = new MenuItem { Header = $"{Math.Round(r * 100)}%", IsCheckable = true, IsChecked = Math.Abs(r - _rate) < 1e-6 };
            item.Click += (_, _) => { _rate = r; RefreshRangeLabels(); };
            menuRate.Items.Add(item);
        }
        menuRate.PlacementTarget = btnRate;
        menuRate.Placement = System.Windows.Controls.Primitives.PlacementMode.Top;
        menuRate.IsOpen = true;
    }

    private void TakeRow_Click(object sender, RoutedEventArgs e)
    {
        if (_state != TakeState.Idle || sender is not Button { Tag: TakeModel t } || !t.HasReport) return;
        try
        {
            var m = PerformanceScoreDetailModel.FromReport(_song.Id, t.ReportPath!);
            if (m == null) return;
            m.RangeLabel = t.RangeLabel;
            m.IsNewBest = t.NewBest;
            // the chart's best before this take, as the result screen shows it
            double earlier = _takes.SkipWhile(x => x != t).Skip(1)
                                   .Where(x => x.Complete && x.RangeStart == t.RangeStart && x.RangeEnd == t.RangeEnd && Math.Abs(x.Rate - t.Rate) < 1e-6)
                                   .Select(x => x.OverallScore).DefaultIfEmpty(-1).Max();
            m.PrevBest = earlier >= 0 ? earlier : null;
            ResultReady?.Invoke(m);
        }
        catch (Exception ex)
        {
            Debug.WriteLine(ex);
            Notify("报告读取失败");
        }
    }

    private void SetButtonsEnabled(bool on) =>
        btnUploadAudio.IsEnabled = btnRecord.IsEnabled = btnInput.IsEnabled = btnSection.IsEnabled = btnRate.IsEnabled = on;

    private void SetRecordButton(string text, bool recording)
    {
        txtRecord.Text = text;
        icoRecord.Visibility = recording ? Visibility.Collapsed : Visibility.Visible;
        icoStop.Visibility = recording ? Visibility.Visible : Visibility.Collapsed;
    }

    private async void BtnRecord_Click(object sender, RoutedEventArgs e)
    {
        if (_state == TakeState.Recording)
        {
            _recorder?.Stop();
            return;
        }
        if (_state != TakeState.Idle) return;
        if (string.IsNullOrEmpty(_song.GpPath) || !File.Exists(_song.GpPath))
        {
            Notify("曲谱文件不存在");
            return;
        }

        _state = TakeState.Preparing;
        SetButtonsEnabled(false);
        SetRecordButton("准备中", recording: false);
        try
        {
            string gp = EvalGpPath;
            var args = new List<string> { gp };
            if (_section != null) args.AddRange(new[] { "--range", _section.Start.ToString(), _section.End.ToString() });
            if (_rate < 0.999) args.AddRange(new[] { "--rate", _rate.ToString("F2", System.Globalization.CultureInfo.InvariantCulture) });
            _takeSection = _section;
            _takeRate = _rate;
            var session = await Task.Run(() => RunPythonJson(SessionScript, args.ToArray()));
            if (session is not JsonElement s || !s.TryGetProperty("success", out var ok) || !ok.GetBoolean())
            {
                Notify("伴奏准备失败");
                ResetTakeUI();
                return;
            }
            _countIn = s.GetProperty("count_in").GetDouble();
            _beat = s.GetProperty("beat_s").GetDouble();
            _leadS = s.GetProperty("lead_s").GetDouble();

            Directory.CreateDirectory(TakesDir);
            string outWav = Path.Combine(TakesDir, $"{SafeName(_song.Id)}_{DateTime.Now:yyyyMMdd_HHmmss}.wav");
            _recorder = new TakeRecorder(outWav);
            _recorder.Completed += r => Dispatcher.BeginInvoke(new Action(() => OnTakeCompleted(r)));
            await _recorder.StartAsync(s.GetProperty("wav").GetString()!, TakeRecorder.SavedDeviceId);

            _state = TakeState.Recording;
            btnRecord.IsEnabled = true;
            _takeTimer = new DispatcherTimer(DispatcherPriority.Render) { Interval = TimeSpan.FromMilliseconds(50) };
            _takeTimer.Tick += (_, _) => UpdateTakeProgress();
            _takeTimer.Start();
            UpdateTakeProgress();
        }
        catch (Exception ex)
        {
            Debug.WriteLine(ex);
            AbortTake();
            Notify("录音设备不可用");
            ResetTakeUI();
        }
    }

    private void UpdateTakeProgress()
    {
        if (_recorder == null) return;
        double pos = _recorder.Position.TotalSeconds;
        if (pos < _countIn)
        {
            int beat = Math.Clamp(4 - (int)Math.Floor(pos / Math.Max(_beat, 0.05)), 1, 4);
            SetRecordButton(beat.ToString(), recording: true);
        }
        else
        {
            var t = TimeSpan.FromSeconds(pos - _countIn);
            SetRecordButton($"结束  {(int)t.TotalMinutes}:{t.Seconds:00}", recording: true);
        }
    }

    private async void OnTakeCompleted(TakeRecorder r)
    {
        _takeTimer?.Stop();
        double played = r.Position.TotalSeconds;
        // recording time r <-> backing-audio time r - RecordOffset + lead
        double hint = r.RecordOffset - _leadS;
        string path = r.OutputPath;
        r.Dispose();
        _recorder = null;
        if (played < _countIn + 3)
        {
            TryDelete(path);
            ResetTakeUI();
            return;
        }
        // an in-app take is practice time too (the calendar otherwise only counts time in Guitar Pro)
        double playedS = played - _countIn;
        if (playedS >= 10)
            _ = _dbService.RecordPracticeSessionAsync(_song.Id, _song.Title, _song.Artist, playedS);
        SetRecordButton("分析中", recording: false);
        await EvaluateAsync(path, hint, _takeSection, _takeRate);
    }

    // Debug: evaluate a file as a take of the named section at a tempo (env BASSDREAM_SECTION / BASSDREAM_RATE).
    internal async Task DebugEvaluateAsync(string audioPath, string? sectionName, double rate)
    {
        for (int i = 0; i < 100 && _sections.Count == 0 && sectionName != null; i++) await Task.Delay(100);
        _section = _sections.FirstOrDefault(x => x.Name == sectionName);
        _rate = rate;
        RefreshRangeLabels();
        await EvaluateAsync(audioPath, null, _section, _rate);
    }

    // Debug: one short take through the real record path; returns the take file.
    internal async Task<string?> DebugTakeAsync(double seconds)
    {
        BtnRecord_Click(this, new RoutedEventArgs());
        for (int i = 0; i < 600 && _state != TakeState.Recording; i++) await Task.Delay(100);
        if (_recorder == null) return null;
        string path = _recorder.OutputPath;
        await Task.Delay(TimeSpan.FromSeconds(seconds));
        _recorder?.Stop();
        for (int i = 0; i < 1200 && _state != TakeState.Idle; i++) await Task.Delay(100);
        return path;
    }

    private void AbortTake()
    {
        _takeTimer?.Stop();
        if (_recorder == null) return;
        var path = _recorder.OutputPath;
        _recorder.Cancel();
        _recorder.Dispose();
        _recorder = null;
        TryDelete(path);
        _state = TakeState.Idle;
    }

    private void ResetTakeUI()
    {
        _state = TakeState.Idle;
        SetButtonsEnabled(true);
        SetRecordButton("开始录制", recording: false);
    }

    private static void TryDelete(string path)
    {
        try { if (File.Exists(path)) File.Delete(path); } catch { }
    }

    private static string SafeName(string s)
    {
        var sb = new StringBuilder();
        foreach (char c in s) sb.Append(char.IsLetterOrDigit(c) ? c : '_');
        return sb.Length > 40 ? sb.ToString(0, 40) : sb.ToString();
    }

    // ---------------------------------------------------------------- backend

    /// <summary>Runs a backend script in the resident ML worker; returns the JSON on its last stdout line.</summary>
    private static JsonElement? RunPythonJson(string script, params string[] args) =>
        File.Exists(script) ? PyHost.Run(Path.GetFileName(script), TimeSpan.FromMinutes(30), args) : null;

    private (PerformanceScoreDetailModel?, string?) RunEvaluator(string audioPath, double? lagHint, PracticeSection? section, double rate)
    {
        if (!File.Exists(EvalScript)) return (null, null);
        if (string.IsNullOrEmpty(_song.GpPath) || !File.Exists(_song.GpPath)) return (null, "曲谱文件不存在");

        var psi = new List<string> { audioPath, _song.Id, EvalGpPath };
        if (lagHint.HasValue)
        {
            psi.Add("--lag-hint");
            psi.Add(lagHint.Value.ToString("F4", System.Globalization.CultureInfo.InvariantCulture));
        }
        if (section != null)
        {
            psi.Add("--range");
            psi.Add(section.Start.ToString());
            psi.Add(section.End.ToString());
            psi.Add("--label");
            psi.Add(section.Name);
        }
        if (rate < 0.999)
        {
            psi.Add("--rate");
            psi.Add(rate.ToString("F2", System.Globalization.CultureInfo.InvariantCulture));
        }

        if (RunPythonJson(EvalScript, psi.ToArray()) is not JsonElement root) return (null, null);
        if (!root.TryGetProperty("success", out var ok) || !ok.GetBoolean())
            return (null, root.TryGetProperty("error", out var err) ? err.GetString() : null);

        var dims = root.GetProperty("dimensions");
        var score = new PerformanceScoreDetailModel
        {
            SongId = _song.Id,
            OverallScore = root.GetProperty("overall_score").GetDouble(),
            Grade = root.GetProperty("grade").GetString() ?? "",
            ComboBadge = root.GetProperty("combo_badge").GetString() ?? "",
            CoachComment = root.GetProperty("coach_comment").GetString() ?? "",
            EvaluatedAt = DateTime.Now.ToString("yyyy-MM-dd HH:mm"),
            IsNewBest = root.TryGetProperty("new_best", out var nb) && nb.GetBoolean(),
            TimingScore = dims.GetProperty("timing").GetDouble(),
            PitchScore = dims.GetProperty("pitch").GetDouble(),
            CompleteScore = dims.GetProperty("complete").GetDouble(),
            CleanScore = dims.GetProperty("clean").GetDouble(),
            MaxCombo = root.GetProperty("max_combo").GetInt32(),
            NotesTotal = root.GetProperty("notes_total").GetInt32(),
            Fast = root.TryGetProperty("fast", out var fa) ? fa.GetInt32() : 0,
            Slow = root.TryGetProperty("slow", out var sl) ? sl.GetInt32() : 0,
            Wrong = root.TryGetProperty("wrong", out var wr) ? wr.GetInt32() : 0,
            ExtraNotes = root.TryGetProperty("extra_notes", out var ex) ? ex.GetInt32() : 0,
            PrevBest = root.TryGetProperty("prev_best", out var pb) && pb.ValueKind == JsonValueKind.Number ? pb.GetDouble() : null,
            ReportPath = root.TryGetProperty("report_path", out var rp) && rp.ValueKind == JsonValueKind.String ? rp.GetString() : null,
            RangeLabel = section?.Name ?? "",
            Rate = rate,
            Complete = !root.TryGetProperty("coverage", out var cv) || cv.GetDouble() >= TakeModel.FullCoverage
        };
        score.ApplyJudgments(root.GetProperty("judgments"));
        score.ApplyHeatmap(root.GetProperty("heatmap"));
        return (score, null);
    }
}
