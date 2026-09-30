using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Text;
using System.Text.Json;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Input;
using System.Windows.Media;
using BassStation.Models;
using BassStation.Services;

namespace BassStation.Views;

/// <summary>
/// In-app score view: the song's BassScore with the backing, the original bass, a tab synth and a metronome
/// mixed live, tempo change, section loop, and note editing. Every edit is appended to edits.jsonl next to
/// score.json (before / after with recording time and pitch): corrections that feed the transcription training.
///
/// Keys: Space play/pause · Enter play from the selection · arrows move · digits set the fret · Del delete ·
/// X dead note · Ctrl+Up/Down same pitch on the next string · - / + shorter / longer · Ctrl+Z undo ·
/// Ctrl+S save · Ctrl+R review the section.
/// </summary>
public partial class ScoreView : UserControl
{
    private const string Py = @"C:\Users\hongw\AppData\Local\Programs\Python\Python311\python.exe";
    private const string Script = @"E:\BassStation\backend\score_format.py";
    private static readonly double[] Rates = { 1.25, 1.1, 1.0, 0.9, 0.8, 0.7, 0.6, 0.5 };
    private BandEdition _ed = BandEdition.Default;
    private double _baseBpm = 120;
    private int? _anchorBar;                    // Shift+click extends the bar range from here
    // mixer levels are kept across songs
    private static readonly double[] Levels = { 0.9, 0.0, 0.0, 0.0 };

    private readonly SongModel _song;
    private readonly string _gpPath;
    private BassScore? _score;
    private ScorePlayer? _player;
    private string? _editsPath;
    private bool _dirty;
    private bool _loop;
    private int _seq;
    private readonly Stack<(int Bar, ScoreBar Before, int Seq)> _undo = new();
    private (long At, int Val, (int, int, int) Cell)? _lastDigit;

    public event Action? RequestClose;

    public ScoreView(SongModel song, string gpPath)
    {
        InitializeComponent();
        _song = song;
        _gpPath = gpPath;
        txtTitle.Text = song.Title;
        txtArtist.Text = song.Artist;
        _ed = BandEdition.For(song);
        canvas.Edition = _ed;
        canvas.Title = song.Title;
        canvas.Subtitle = song.Version;
        canvas.Artist = song.Artist;
        canvas.Tier = song.Tier;
        canvas.Level = song.Level;
        var edBrush = new SolidColorBrush(_ed.Main);
        edStripe.Background = edBrush;
        edDot.Fill = edBrush;
        txtEdition.Text = $"{_ed.Name} 版";
        cursor.Fill = edBrush;
        volBacking.Value = Levels[0];
        volBass.Value = Levels[1];
        volSynth.Value = Levels[2];
        volClick.Value = Levels[3];
        Loaded += async (_, _) => { Focus(); await OpenAsync(); };
        Unloaded += (_, _) => Shutdown();
        PreviewKeyDown += OnKey;
    }

    // ---------------------------------------------------------------- loading

    private async Task OpenAsync()
    {
        var res = await Task.Run(() => RunPy("open", _song.Id, _gpPath, _song.Title, _song.Artist));
        if (res is not JsonElement r || !r.TryGetProperty("success", out var ok) || !ok.GetBoolean())
        {
            txtLoading.Text = "曲谱打开失败";
            return;
        }
        string path = r.GetProperty("score").GetString()!;
        _score = BassScore.Load(path);
        _editsPath = Path.Combine(Path.GetDirectoryName(path)!, "edits.jsonl");
        _seq = File.Exists(_editsPath) ? File.ReadLines(_editsPath).Count() : 0;
        canvas.Score = _score;
        _player = new ScorePlayer(_score);
        var audio = r.GetProperty("audio");
        _player.Load(Str(audio, "backing"), Str(audio, "bass"), 1.0);
        for (int i = 0; i < 4; i++) _player.SetVolume((ScorePlayer.Track)i, Levels[i]);
        volBass.IsEnabled = _player.HasTrack(ScorePlayer.Track.Bass);
        volBacking.IsEnabled = _player.HasTrack(ScorePlayer.Track.Backing);
        if (!volBacking.IsEnabled) volSynth.Value = Math.Max(volSynth.Value, 0.8);     // nothing else to hear
        canvas.Selection = (0, 0, 0);
        _baseBpm = BaseBpm(_score);
        canvas.Bpm = _baseBpm;
        if (_score.Meta.TryGetValue("audio_offset_ms", out var off) && double.TryParse(off, NumberStyles.Float, CultureInfo.InvariantCulture, out double ms))
            _player.AudioOffsetMs = ms;
        ShowRate();
        ShowOffset();
        txtLoading.Visibility = Visibility.Collapsed;
        CompositionTarget.Rendering += OnFrame;
    }

    /// <summary>Written tempo (quarter notes per minute): median over bars of beats / bar duration.</summary>
    private static double BaseBpm(BassScore s)
    {
        var v = s.Bars.Where(b => b.T1 > b.T0).Select(b => (b.Num * 4.0 / b.Den) / (b.T1 - b.T0) * 60).OrderBy(x => x).ToList();
        return v.Count > 0 ? v[v.Count / 2] : 120;
    }

    private void ShowRate()
    {
        double r = _player?.Rate ?? 1;
        txtRate.Text = $"{Math.Round(r * 100)}%";
        if (!txtBpm.IsKeyboardFocused) txtBpm.Text = Math.Round(_baseBpm * r).ToString(CultureInfo.InvariantCulture);
        stTempo.Text = $"♩ = {Math.Round(_baseBpm)}   ×{r:0.00}   = {Math.Round(_baseBpm * r)}";
    }

    private void ShowOffset()
    {
        double ms = _player?.AudioOffsetMs ?? 0;
        if (!txtOffset.IsKeyboardFocused) txtOffset.Text = $"{ms:+0;-0;0} ms";
        stAlign.Text = $"对齐 {ms:+0;-0;0} ms";
    }

    private static string? Str(JsonElement e, string k) =>
        e.TryGetProperty(k, out var v) && v.ValueKind == JsonValueKind.String ? v.GetString() : null;

    private static JsonElement? RunPy(params string[] args)
    {
        var psi = new ProcessStartInfo
        {
            FileName = File.Exists(Py) ? Py : "python",
            UseShellExecute = false,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
            CreateNoWindow = true,
            StandardOutputEncoding = Encoding.UTF8
        };
        psi.ArgumentList.Add(Script);
        foreach (var a in args) psi.ArgumentList.Add(a);
        psi.EnvironmentVariables["PYTHONUTF8"] = "1";
        psi.EnvironmentVariables["PYTHONIOENCODING"] = "utf-8";
        try
        {
            using var p = Process.Start(psi);
            if (p == null) return null;
            var err = p.StandardError.ReadToEndAsync();
            string stdout = p.StandardOutput.ReadToEnd();
            p.WaitForExit();
            _ = err.Result;
            string? line = stdout.Split('\n').Select(l => l.Trim()).LastOrDefault(l => l.StartsWith('{'));
            if (line == null) return null;
            using var doc = JsonDocument.Parse(line);
            return doc.RootElement.Clone();
        }
        catch (Exception ex)
        {
            Debug.WriteLine(ex);
            return null;
        }
    }

    private void Shutdown()
    {
        CompositionTarget.Rendering -= OnFrame;
        if (_dirty) Save();
        _player?.Dispose();
        _player = null;
    }

    // ---------------------------------------------------------------- transport

    private double _lastCursorY = -1;

    private void OnFrame(object? sender, EventArgs e)
    {
        if (_player == null || _score == null) return;
        double t = _player.Position;
        txtTime.Text = $"{Clock(t)}.{(int)(t * 10) % 10} / {Clock(_player.Duration)}";
        icoPlay.Text = _player.IsPlaying ? "" : "";
        UpdateStatus(t);
        var c = canvas.CursorAt(t);
        if (c == null) return;
        var (x, y, h) = c.Value;
        cursor.Visibility = Visibility.Visible;
        Canvas.SetLeft(cursor, x - 1.25);
        Canvas.SetTop(cursor, y);
        cursor.Height = h;
        if (_player.IsPlaying && Math.Abs(y - _lastCursorY) > 1)
        {
            // keep the playing row in the upper part of the page
            double top = y + page.Margin.Top - canvas.RowH * 0.8;
            if (y + page.Margin.Top < scroll.VerticalOffset || y + page.Margin.Top + h > scroll.VerticalOffset + scroll.ViewportHeight - 40)
                scroll.ScrollToVerticalOffset(Math.Max(0, top));
        }
        _lastCursorY = y;
    }

    private static readonly string[] MajorKeys = { "C♭", "G♭", "D♭", "A♭", "E♭", "B♭", "F", "C", "G", "D", "A", "E", "B", "F♯", "C♯" };
    private static readonly string[] MinorKeys = { "A♭", "E♭", "B♭", "F", "C", "G", "D", "A", "E", "B", "F♯", "C♯", "G♯", "D♯", "A♯" };
    private int _statusBar = -1;

    private void UpdateStatus(double t)
    {
        int bi = _score!.BarAt(t);
        var b = _score.Bars[bi];
        int beats = b.Den == 8 && b.Num % 3 == 0 && b.Num >= 6 ? b.Num / 3 : b.Num;
        int beat = Math.Clamp((int)((t - b.T0) / Math.Max(1e-6, b.T1 - b.T0) * beats) + 1, 1, beats);
        stPos.Text = $"小节 {bi + 1} / {_score.Bars.Count}   第 {beat} 拍";
        if (bi == _statusBar) return;
        _statusBar = bi;
        stMeter.Text = $"{b.Num}/{b.Den}";
        int k = _score.Key.Count > 0 && _score.Key[0].ValueKind == JsonValueKind.Number ? _score.Key[0].GetInt32() : 0;
        if (b.Extra != null && b.Extra.TryGetValue("key", out var bk) && bk.ValueKind == JsonValueKind.Array) k = bk[0].GetInt32();
        bool minor = _score.Key.Count > 1 && _score.Key[1].ValueKind == JsonValueKind.String && _score.Key[1].GetString() == "Minor";
        stKey.Text = minor ? $"{MinorKeys[Math.Clamp(k, -7, 7) + 7]} 小调" : $"{MajorKeys[Math.Clamp(k, -7, 7) + 7]} 大调";
        int reviewed = _score.Bars.Count(x => x.Reviewed);
        stReview.Text = reviewed > 0 ? $"已审核 {reviewed} / {_score.Bars.Count} 小节" : "";
    }

    private static string Clock(double s) => $"{(int)(s / 60)}:{(int)(s % 60):00}";

    private void TogglePlay()
    {
        if (_player == null) return;
        if (_player.IsPlaying) _player.Pause();
        else _player.Play();
    }

    private void BtnPlay_Click(object sender, RoutedEventArgs e) => TogglePlay();

    private void BtnBack_Click(object sender, RoutedEventArgs e) => RequestClose?.Invoke();

    private void Volume_Changed(object sender, RoutedPropertyChangedEventArgs<double> e)
    {
        if (sender is not Slider { Tag: string tag }) return;
        int i = int.Parse(tag, CultureInfo.InvariantCulture);
        Levels[i] = e.NewValue;
        _player?.SetVolume((ScorePlayer.Track)i, e.NewValue);
    }

    private void BtnRate_Click(object sender, RoutedEventArgs e)
    {
        menuRate.Items.Clear();
        foreach (double r in Rates)
        {
            var item = new MenuItem { Header = $"{Math.Round(r * 100)}%", IsCheckable = true, IsChecked = Math.Abs(r - (_player?.Rate ?? 1)) < 1e-6 };
            item.Click += async (_, _) => await SetRateAsync(r);
            menuRate.Items.Add(item);
        }
        menuRate.PlacementTarget = btnRate;
        menuRate.IsOpen = true;
    }

    private async Task SetRateAsync(double rate)
    {
        if (_player == null) return;
        rate = Math.Round(Math.Clamp(rate, 0.5, 1.5), 2);
        txtRate.Text = "…";
        var res = await Task.Run(() => RunPy("stretch", _song.Id, rate.ToString("F2", CultureInfo.InvariantCulture)));
        if (res is JsonElement r && r.TryGetProperty("success", out var ok) && ok.GetBoolean())
            _player.Load(Str(r, "backing"), Str(r, "bass"), rate);
        ShowRate();
        ApplyLoop();
    }

    private async void BtnSlower_Click(object sender, RoutedEventArgs e) => await SetRateAsync((_player?.Rate ?? 1) - 0.05);
    private async void BtnFaster_Click(object sender, RoutedEventArgs e) => await SetRateAsync((_player?.Rate ?? 1) + 0.05);

    private async void ApplyBpmField()
    {
        if (double.TryParse(txtBpm.Text, NumberStyles.Float, CultureInfo.InvariantCulture, out double bpm) && bpm > 0 && _baseBpm > 0)
            await SetRateAsync(bpm / _baseBpm);
        else ShowRate();
    }

    private void TxtBpm_KeyDown(object sender, KeyEventArgs e)
    {
        if (e.Key != Key.Enter) return;
        e.Handled = true;
        Focus();
        ApplyBpmField();
    }

    private void TxtBpm_LostFocus(object sender, KeyboardFocusChangedEventArgs e) => ShowRate();

    private void BtnHome_Click(object sender, RoutedEventArgs e)
    {
        if (_player == null || _score == null) return;
        _player.Seek(_loop && _loopRange is { } r ? _score.Bars[r.A].T0 : 0);
        scroll.ScrollToVerticalOffset(0);
    }

    private void BtnStaff_Click(object sender, RoutedEventArgs e)
    {
        canvas.ShowStaff = !canvas.ShowStaff;
        btnStaff.Tag = canvas.ShowStaff ? "on" : null;
        canvas.Relayout();
    }

    // ---------------------------------------------------------------- alignment

    private void SetOffset(double ms)
    {
        if (_player == null || _score == null) return;
        _player.AudioOffsetMs = Math.Round(Math.Clamp(ms, -2000, 2000));
        _score.Meta["audio_offset_ms"] = _player.AudioOffsetMs.ToString("0", CultureInfo.InvariantCulture);
        _dirty = true;
        dotDirty.Visibility = Visibility.Visible;
        ShowOffset();
    }

    private double Step => Keyboard.Modifiers.HasFlag(ModifierKeys.Shift) ? 50 : 5;
    private void BtnEarlier_Click(object sender, RoutedEventArgs e) => SetOffset((_player?.AudioOffsetMs ?? 0) + Step);
    private void BtnLater_Click(object sender, RoutedEventArgs e) => SetOffset((_player?.AudioOffsetMs ?? 0) - Step);

    private void TxtOffset_KeyDown(object sender, KeyEventArgs e)
    {
        if (e.Key != Key.Enter) return;
        e.Handled = true;
        string s = new string(txtOffset.Text.Where(c => char.IsDigit(c) || c is '-' or '+' or '.').ToArray());
        Focus();
        if (double.TryParse(s, NumberStyles.Float, CultureInfo.InvariantCulture, out double ms)) SetOffset(ms);
        else ShowOffset();
    }

    private void TxtOffset_LostFocus(object sender, KeyboardFocusChangedEventArgs e) => ShowOffset();

    private async void BtnAutoAlign_Click(object sender, RoutedEventArgs e)
    {
        if (_player == null || _score == null) return;
        if (_dirty) Save();          // the aligner reads score.json
        btnAutoAlign.IsEnabled = false;
        txtAuto.Text = "对齐中";
        var res = await Task.Run(() => RunPy("align", _song.Id));
        btnAutoAlign.IsEnabled = true;
        txtAuto.Text = "自动";
        if (res is JsonElement r && r.TryGetProperty("success", out var ok) && ok.GetBoolean())
            SetOffset(r.GetProperty("offset_ms").GetDouble());
        else
            (Application.Current.MainWindow as MainWindow)?.ShowToast("无原曲贝斯轨", isSuccess: false);
    }

    /// <summary>Bars [a, b) of the section around a bar (a section runs until the next section mark).</summary>
    private (int A, int B) SectionOf(int bar)
    {
        int a = bar;
        while (a > 0 && _score!.Bars[a].Section == null) a--;
        int b = bar + 1;
        while (b < _score!.Bars.Count && _score.Bars[b].Section == null) b++;
        return (a, b);
    }

    private (int A, int B)? _loopRange;         // inclusive bars

    private void BtnLoop_Click(object sender, RoutedEventArgs e) => ToggleLoop();

    private void ToggleLoop()
    {
        _loop = !_loop;
        btnLoop.Tag = _loop ? "on" : null;
        ApplyLoop();
    }

    /// <summary>Loops the selected bar range, or the section around the selection when a single bar is selected.</summary>
    private void ApplyLoop()
    {
        if (_player == null || _score == null) return;
        if (!_loop)
        {
            _player.SetLoop(null, null);
            _loopRange = null;
            txtLoop.Text = "循环";
            return;
        }
        (int A, int B) r;
        if (canvas.Range is { } sel && sel.B > sel.A) r = sel;
        else
        {
            var (a, b) = SectionOf(canvas.Selection?.Bar ?? _score.BarAt(_player.Position));
            r = (a, b - 1);
        }
        _loopRange = r;
        canvas.Range = r;
        canvas.InvalidateVisual();
        txtLoop.Text = $"{r.A + 1}–{r.B + 1}";
        _player.SetLoop(_score.Bars[r.A].T0, _score.Bars[r.B].T1);
        double t = _player.Position;
        if (t < _score.Bars[r.A].T0 || t > _score.Bars[r.B].T1) _player.Seek(_score.Bars[r.A].T0);
    }

    // ---------------------------------------------------------------- selection

    private void Canvas_MouseDown(object sender, MouseButtonEventArgs e)
    {
        Focus();
        var hit = canvas.HitTest(e.GetPosition(canvas));
        if (hit == null) return;
        if (Keyboard.Modifiers.HasFlag(ModifierKeys.Shift) && _anchorBar is int a0)
            canvas.Range = (Math.Min(a0, hit.Value.Bar), Math.Max(a0, hit.Value.Bar));
        else
        {
            _anchorBar = hit.Value.Bar;
            canvas.Range = (hit.Value.Bar, hit.Value.Bar);
        }
        canvas.Selection = hit;
        canvas.InvalidateVisual();
        if (e.ClickCount == 2) PlayFromSelection();
        else if (_loop) ApplyLoop();
    }

    private void PlayFromSelection()
    {
        if (_player == null || _score == null || canvas.Selection is not { } s) return;
        _player.Seek(_score.TimeOf(s.Bar, _score.Bars[s.Bar].Beats[s.Beat].Tick));
        _player.Play();
    }

    private void Move(int dBeat, int dStr)
    {
        if (_score == null || canvas.Selection is not { } s) return;
        int bar = s.Bar, beat = s.Beat + dBeat, str = Math.Clamp(s.Str + dStr, 0, _score.Strings - 1);
        while (beat < 0 && bar > 0) { bar--; beat += _score.Bars[bar].Beats.Count; }
        while (beat >= _score.Bars[bar].Beats.Count && bar < _score.Bars.Count - 1) { beat -= _score.Bars[bar].Beats.Count; bar++; }
        beat = Math.Clamp(beat, 0, Math.Max(0, _score.Bars[bar].Beats.Count - 1));
        canvas.Selection = (bar, beat, str);
        canvas.InvalidateVisual();
        var p = canvas.PlaceOf(bar);
        if (p != null)
        {
            double y = p.Y + page.Margin.Top;
            if (y < scroll.VerticalOffset || y + canvas.RowH > scroll.VerticalOffset + scroll.ViewportHeight)
                scroll.ScrollToVerticalOffset(Math.Max(0, y - canvas.RowH));
        }
    }

    // ---------------------------------------------------------------- editing

    private ScoreBeat? SelBeat => canvas.Selection is { } s && _score != null && s.Beat < _score.Bars[s.Bar].Beats.Count
        ? _score.Bars[s.Bar].Beats[s.Beat] : null;

    private ScoreNote? SelNote => canvas.Selection is { } s ? SelBeat?.Notes.FirstOrDefault(n => n.S == s.Str) : null;

    private void OnKey(object sender, KeyEventArgs e)
    {
        if (_score == null) return;
        bool ctrl = Keyboard.Modifiers.HasFlag(ModifierKeys.Control);
        var key = e.Key == Key.System ? e.SystemKey : e.Key;
        e.Handled = true;
        switch (key)
        {
            case Key.Space: TogglePlay(); break;
            case Key.Enter: PlayFromSelection(); break;
            case Key.Left: Move(-1, 0); break;
            case Key.Right: Move(1, 0); break;
            case Key.Up when ctrl: MoveString(1); break;
            case Key.Down when ctrl: MoveString(-1); break;
            case Key.Up: Move(0, 1); break;
            case Key.Down: Move(0, -1); break;
            case Key.Delete or Key.Back: DeleteNote(); break;
            case Key.X when !ctrl: ToggleDead(); break;
            case Key.L when !ctrl: ToggleLoop(); break;
            case Key.Z when ctrl: Undo(); break;
            case Key.S when ctrl: Save(); break;
            case Key.R when ctrl: ReviewSection(); break;
            case Key.OemMinus or Key.Subtract: Shorter(); break;
            case Key.OemPlus or Key.Add: Longer(); break;
            case >= Key.D0 and <= Key.D9: Digit(key - Key.D0); break;
            case >= Key.NumPad0 and <= Key.NumPad9: Digit(key - Key.NumPad0); break;
            case Key.Escape: RequestClose?.Invoke(); break;
            default: e.Handled = false; break;
        }
    }

    private void Digit(int d)
    {
        if (canvas.Selection is not { } s) return;
        long now = Environment.TickCount64;
        int fret = d;
        // two digits in quick succession on the same cell: 1 then 2 = fret 12
        if (_lastDigit is { } l && now - l.At < 800 && l.Cell == (s.Bar, s.Beat, s.Str) && l.Val * 10 + d <= 24)
            fret = l.Val * 10 + d;
        _lastDigit = (now, fret, (s.Bar, s.Beat, s.Str));
        SetFret(fret);
    }

    private void SetFret(int fret)
    {
        if (_score == null || canvas.Selection is not { } s || SelBeat is not { } bt) return;
        var n = SelNote;
        int seq = Snapshot(s.Bar);
        if (n != null)
        {
            var before = n.Clone();
            n.F = fret;
            n.Midi = _score.Tuning[s.Str] + fret;
            n.X = false;
            n.Tie = false;
            n.Src = "user";
            Log(seq, "set", s.Bar, bt, before, n);
        }
        else
        {
            n = new ScoreNote { Id = _score.NewNoteId(), S = s.Str, F = fret, Midi = _score.Tuning[s.Str] + fret, Src = "user" };
            bt.Notes.Add(n);
            bt.Notes.Sort((a, b) => a.S.CompareTo(b.S));
            Log(seq, "add", s.Bar, bt, null, n);
        }
        Changed(false);
    }

    private void DeleteNote()
    {
        if (canvas.Selection is not { } s || SelBeat is not { } bt || SelNote is not { } n) return;
        int seq = Snapshot(s.Bar);
        bt.Notes.Remove(n);
        Log(seq, "delete", s.Bar, bt, n, null);
        Changed(false);
    }

    private void ToggleDead()
    {
        if (canvas.Selection is not { } s || SelBeat is not { } bt || SelNote is not { } n) return;
        int seq = Snapshot(s.Bar);
        var before = n.Clone();
        n.X = !n.X;
        n.Src = "user";
        Log(seq, "set", s.Bar, bt, before, n);
        Changed(false);
    }

    /// <summary>Same pitch on the neighbouring string (fingering change).</summary>
    private void MoveString(int dir)
    {
        if (_score == null || canvas.Selection is not { } s || SelBeat is not { } bt || SelNote is not { } n) return;
        int to = s.Str + dir;
        if (to < 0 || to >= _score.Strings || bt.Notes.Any(x => x.S == to)) return;
        int fret = n.Midi - _score.Tuning[to];
        if (fret < 0 || fret > 24) return;
        int seq = Snapshot(s.Bar);
        var before = n.Clone();
        n.S = to;
        n.F = fret;
        n.Src = "user";
        Log(seq, "set", s.Bar, bt, before, n);
        canvas.Selection = (s.Bar, s.Beat, to);
        Changed(false);
    }

    /// <summary>Halves the beat and fills the freed half with a rest (the bar stays full).</summary>
    private void Shorter()
    {
        if (_score == null || canvas.Selection is not { } s || SelBeat is not { } bt) return;
        if (bt.Dots > 0 || bt.Tuplet > 0 || bt.Value >= 64 || bt.Dur % 2 != 0) return;
        int seq = Snapshot(s.Bar);
        var bar = _score.Bars[s.Bar];
        int half = bt.Dur / 2;
        bt.Dur = half;
        bt.Value *= 2;
        bar.Beats.Insert(s.Beat + 1, new ScoreBeat { Tick = bt.Tick + half, Dur = half, Value = bt.Value });
        LogRhythm(seq, s.Bar);
        Changed(true);
    }

    /// <summary>Takes the following rest into the beat: same length doubles the value, half length adds a dot.</summary>
    private void Longer()
    {
        if (_score == null || canvas.Selection is not { } s || SelBeat is not { } bt) return;
        var bar = _score.Bars[s.Bar];
        if (s.Beat + 1 >= bar.Beats.Count || bt.Tuplet > 0) return;
        var next = bar.Beats[s.Beat + 1];
        if (!next.IsRest || next.Tuplet > 0) return;
        bool dbl = next.Dur == bt.Dur && bt.Dots == 0 && bt.Value > 1 && bt.Tick % (2 * bt.Dur) == 0;
        bool dot = next.Dur * 2 == bt.Dur && bt.Dots == 0;
        if (!dbl && !dot) return;
        int seq = Snapshot(s.Bar);
        if (dbl) bt.Value /= 2; else bt.Dots = 1;
        bt.Dur += next.Dur;
        bar.Beats.RemoveAt(s.Beat + 1);
        LogRhythm(seq, s.Bar);
        Changed(true);
    }

    private void ReviewSection()
    {
        if (_score == null || canvas.Selection is not { } s) return;
        var (a, b) = SectionOf(s.Bar);
        bool value = !_score.Bars[a].Reviewed;
        int seq = ++_seq;
        for (int i = a; i < b; i++)
        {
            _undo.Push((i, CloneBar(_score.Bars[i]), seq));
            _score.Bars[i].Reviewed = value;
        }
        Append(new Dictionary<string, object?> { ["op"] = "review", ["bars"] = new[] { a, b }, ["value"] = value,
                                                  ["t"] = new[] { _score.Bars[a].T0, _score.Bars[b - 1].T1 } }, seq);
        Changed(false);
    }

    private void BtnReview_Click(object sender, RoutedEventArgs e) => ReviewSection();

    private int Snapshot(int bar)
    {
        int seq = ++_seq;
        _undo.Push((bar, CloneBar(_score!.Bars[bar]), seq));
        return seq;
    }

    private static ScoreBar CloneBar(ScoreBar b) => JsonSerializer.Deserialize<ScoreBar>(JsonSerializer.Serialize(b))!;

    private void Undo()
    {
        if (_score == null || _undo.Count == 0) return;
        int seq = _undo.Peek().Seq;
        while (_undo.Count > 0 && _undo.Peek().Seq == seq)
        {
            var (bar, before, _) = _undo.Pop();
            _score.Bars[bar] = before;
        }
        Append(new Dictionary<string, object?> { ["op"] = "undo", ["of"] = seq }, ++_seq);
        if (canvas.Selection is { } s && s.Beat >= _score.Bars[s.Bar].Beats.Count)
            canvas.Selection = (s.Bar, Math.Max(0, _score.Bars[s.Bar].Beats.Count - 1), s.Str);
        Changed(true);
    }

    private void Changed(bool relayout)
    {
        _dirty = true;
        dotDirty.Visibility = Visibility.Visible;
        if (relayout) canvas.Relayout(); else canvas.InvalidateVisual();
        _player?.Refresh();
    }

    // ---------------------------------------------------------------- edit log (training data)

    private object? NoteRecord(int bar, ScoreBeat bt, ScoreNote? n) => n == null ? null : new Dictionary<string, object?>
    {
        ["id"] = n.Id, ["s"] = n.S, ["f"] = n.F, ["midi"] = n.Midi, ["x"] = n.X, ["src"] = n.Src, ["conf"] = n.Conf,
        ["tick"] = bt.Tick, ["dur"] = bt.Dur, ["time"] = Math.Round(_score!.TimeOf(bar, bt.Tick), 4),
        ["end"] = Math.Round(_score.TimeOf(bar, bt.Tick + bt.Dur), 4)
    };

    private void Log(int seq, string op, int bar, ScoreBeat bt, ScoreNote? before, ScoreNote? after) =>
        Append(new Dictionary<string, object?>
        {
            ["op"] = op, ["bar"] = bar, ["note_id"] = (after ?? before)?.Id,
            ["before"] = NoteRecord(bar, bt, before), ["after"] = NoteRecord(bar, bt, after)
        }, seq);

    private void LogRhythm(int seq, int bar)
    {
        var b = _score!.Bars[bar];
        Append(new Dictionary<string, object?>
        {
            ["op"] = "set_rhythm", ["bar"] = bar,
            ["after"] = b.Beats.Select(bt => new { bt.Tick, bt.Dur, v = bt.Value, d = bt.Dots, notes = bt.Notes.Select(n => n.Id) })
        }, seq);
    }

    private void Append(Dictionary<string, object?> entry, int seq)
    {
        if (_editsPath == null) return;
        entry["seq"] = seq;
        entry["ts"] = DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss");
        try
        {
            File.AppendAllText(_editsPath, JsonSerializer.Serialize(entry) + "\n");
        }
        catch (IOException ex) { Debug.WriteLine(ex); }
    }

    private void Save()
    {
        if (_score == null) return;
        try
        {
            _score.Save();
            _dirty = false;
            dotDirty.Visibility = Visibility.Collapsed;
        }
        catch (Exception ex)
        {
            Debug.WriteLine(ex);
            (Application.Current.MainWindow as MainWindow)?.ShowToast("保存失败", isSuccess: false);
        }
    }

    private void BtnSave_Click(object sender, RoutedEventArgs e) => Save();

    // Debug: offline mix of the current tracks at a tempo, with the given volumes (backing, bass, synth, click).
    internal async Task DebugRenderAsync(string wav, double rate, double from, double seconds, double[] vols)
    {
        for (int i = 0; i < 300 && _player == null; i++) await Task.Delay(100);
        if (_player == null) return;
        if (rate < 0.999) await SetRateAsync(rate);
        for (int i = 0; i < 4; i++) _player.SetVolume((ScorePlayer.Track)i, vols[i]);
        // BASSDREAM_LIVE=1: through the real output device, captured from the system loopback
        if (Environment.GetEnvironmentVariable("BASSDREAM_LIVE") == "1")
            File.WriteAllText(wav + ".txt", await _player.DebugLoopbackAsync(wav, from, seconds));
        else
            _player.DebugRender(wav, from, seconds);
    }

    // Debug: selects a cell, types a fret and plays for a moment (render checks without a keyboard).
    internal async Task DebugAsync(int bar, int beat, int str, int? fret, double playSeconds)
    {
        for (int i = 0; i < 300 && _score == null; i++) await Task.Delay(100);
        if (_score == null) return;
        canvas.Selection = (bar, beat, str);
        if (fret.HasValue)
        {
            SetFret(fret.Value);
            Save();
        }
        await Task.Delay(300);
        Move(0, 0);
        if (playSeconds > 0)
        {
            _player?.Seek(_score.TimeOf(bar, 0));
            _player?.Play();
            await Task.Delay(TimeSpan.FromSeconds(playSeconds));
            _player?.Pause();
        }
    }
}
