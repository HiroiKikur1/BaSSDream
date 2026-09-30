using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Text.Json;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Input;
using Microsoft.Win32;

using BassStation.Services;

namespace BassStation.Views;

public class TabSearchItem
{
    public string Id { get; set; } = "";
    public string Title { get; set; } = "";
    public string Artist { get; set; } = "";
    public int TracksCount { get; set; } = 4;
    public bool HasBass { get; set; } = true;
    public string DownloadUrl { get; set; } = "";
    public string SubInfo => $"{Artist} · {TracksCount} 轨" + (HasBass ? " · 贝斯" : " · 无贝斯");
}

public class AudioSearchItem
{
    public long AudioId { get; set; }
    public string Title { get; set; } = "";
    public string Artist { get; set; } = "";
    public string Album { get; set; } = "";
    public double Duration { get; set; }
    public string Source { get; set; } = "原声";
    public string SubInfo
    {
        get
        {
            int durSec = (int)Math.Round(Duration);
            int min = durSec / 60;
            int sec = durSec % 60;
            return $"{Artist} · {min}:{sec:D2}";
        }
    }
}

public class BilibiliSearchItem
{
    public string BvId { get; set; } = "";
    public string Title { get; set; } = "";
    public string Author { get; set; } = "";
    public string Duration { get; set; } = "";
    public long Play { get; set; }
    public string CoverUrl { get; set; } = "";
    public string Url { get; set; } = "";
    public string SubInfo => string.IsNullOrEmpty(Duration) ? Author : $"{Author} · {Duration}";
}

public partial class AddSongWindow : Window
{

    public event Action? RequestClose;
    // raised on the UI thread when a song landed in the library, even if the sheet was dismissed meanwhile
    public event Action<string>? SongAdded;

    private readonly FrameworkElement _root;
    private bool _busy;

    public AddSongWindow()
    {
        InitializeComponent();
        _root = (FrameworkElement)Content;   // the host moves this into its sheet
    }

    private void Finish(string title)
    {
        SongAdded?.Invoke(title);
        // only close our own sheet; after a dismissal the host may be showing a different one
        if (_root.IsLoaded) RequestClose?.Invoke();
    }

    private bool BeginJob(string status)
    {
        if (_busy) return false;
        _busy = true;
        scrollResults.IsEnabled = false;
        btnSearch.IsEnabled = false;
        txtStatus.Text = status;
        return true;
    }

    private void EndJob()
    {
        _busy = false;
        scrollResults.IsEnabled = true;
        btnSearch.IsEnabled = true;
    }

    private static TabCli.Result RunCli(TimeSpan timeout, params string[] args) => TabCli.Run(timeout, args);

    private static string Str(JsonElement el, string name, string fallback = "") => TabCli.Str(el, name, fallback);

    private static long Long(JsonElement el, string name, long fallback = 0)
    {
        if (!el.TryGetProperty(name, out var v)) return fallback;
        if (v.ValueKind == JsonValueKind.Number && v.TryGetInt64(out var n)) return n;
        return v.ValueKind == JsonValueKind.String && long.TryParse(v.GetString(), out n) ? n : fallback;
    }

    private static double Dbl(JsonElement el, string name, double fallback)
    {
        if (!el.TryGetProperty(name, out var v)) return fallback;
        if (v.ValueKind == JsonValueKind.Number && v.TryGetDouble(out var d)) return d;
        return v.ValueKind == JsonValueKind.String && double.TryParse(v.GetString(), out d) ? d : fallback;
    }

    private static bool Bool(JsonElement el, string name, bool fallback) =>
        el.TryGetProperty(name, out var v) ? v.ValueKind switch { JsonValueKind.True => true, JsonValueKind.False => false, _ => fallback } : fallback;

    private static IEnumerable<JsonElement> Arr(JsonElement root, string name) =>
        root.ValueKind == JsonValueKind.Object && root.TryGetProperty(name, out var a) && a.ValueKind == JsonValueKind.Array
            ? a.EnumerateArray() : Enumerable.Empty<JsonElement>();

    private void TxtQuery_TextChanged(object sender, TextChangedEventArgs e)
    {
        txtPlaceholder.Visibility = string.IsNullOrEmpty(txtQuery.Text) ? Visibility.Visible : Visibility.Collapsed;
    }

    private void TxtQuery_KeyDown(object sender, KeyEventArgs e)
    {
        if (e.Key == Key.Enter)
        {
            BtnSearch_Click(sender, e);
        }
    }

    private async void BtnSearch_Click(object sender, RoutedEventArgs e)
    {
        string keyword = txtQuery.Text.Trim();
        if (string.IsNullOrEmpty(keyword) || _busy) return;

        btnSearch.IsEnabled = false;
        txtStatus.Text = "";
        pnlEmptyState.Visibility = Visibility.Collapsed;
        scrollResults.Visibility = Visibility.Collapsed;
        pnlLoading.Visibility = Visibility.Visible;

        try
        {
            var data = await Task.Run(() => SearchAllViaCli(keyword));
            pnlLoading.Visibility = Visibility.Collapsed;

            bool hasTab = data.tabs != null && data.tabs.Count > 0;
            bool hasAudio = data.audio != null && data.audio.Count > 0;
            bool hasBili = data.bilibili != null && data.bilibili.Count > 0;

            if (!hasTab && !hasAudio && !hasBili)
            {
                txtStatus.Text = "无结果";
                pnlEmptyState.Visibility = Visibility.Visible;
                scrollResults.Visibility = Visibility.Collapsed;
            }
            else
            {
                scrollResults.Visibility = Visibility.Visible;
                pnlEmptyState.Visibility = Visibility.Collapsed;

                if (hasTab)
                {
                    lblOnlineTabs.Visibility = Visibility.Visible;
                    icTabs.Visibility = Visibility.Visible;
                    icTabs.ItemsSource = data.tabs;
                }
                else
                {
                    lblOnlineTabs.Visibility = Visibility.Collapsed;
                    icTabs.Visibility = Visibility.Collapsed;
                    icTabs.ItemsSource = null;
                }

                if (hasAudio)
                {
                    lblAudioSources.Visibility = Visibility.Visible;
                    icAudio.Visibility = Visibility.Visible;
                    icAudio.ItemsSource = data.audio;
                }
                else
                {
                    lblAudioSources.Visibility = Visibility.Collapsed;
                    icAudio.Visibility = Visibility.Collapsed;
                    icAudio.ItemsSource = null;
                }

                if (hasBili)
                {
                    lblBiliReferences.Visibility = Visibility.Visible;
                    icBili.Visibility = Visibility.Visible;
                    icBili.ItemsSource = data.bilibili;
                }
                else
                {
                    lblBiliReferences.Visibility = Visibility.Collapsed;
                    icBili.Visibility = Visibility.Collapsed;
                    icBili.ItemsSource = null;
                }

                txtStatus.Text = "";
            }
        }
        catch (Exception ex)
        {
            pnlLoading.Visibility = Visibility.Collapsed;
            pnlEmptyState.Visibility = Visibility.Visible;
            txtStatus.Text = $"检索失败: {ex.Message}";
        }
        finally
        {
            btnSearch.IsEnabled = true;
        }
    }

    private (List<TabSearchItem> tabs, List<AudioSearchItem> audio, List<BilibiliSearchItem> bilibili) SearchAllViaCli(string keyword)
    {
        var r = RunCli(TimeSpan.FromSeconds(90), "search-all", keyword);
        if (r.Root.ValueKind != JsonValueKind.Object)
            throw new InvalidOperationException(r.Error);
        var root = r.Root;

        // each row is parsed on its own: one odd field no longer drops the whole result set
        var tabs = Arr(root, "tabs").Select(el => new TabSearchItem
        {
            Id = Str(el, "id"),
            Title = Str(el, "title", keyword),
            Artist = Str(el, "artist", "未知"),
            TracksCount = (int)Long(el, "tracks_count", 4),
            HasBass = Bool(el, "has_bass", true),
            DownloadUrl = Str(el, "download_url")
        }).Where(t => t.DownloadUrl.Length > 0).ToList();

        var audio = Arr(root, "audio").Select(el => new AudioSearchItem
        {
            Title = Str(el, "title", keyword),
            Artist = Str(el, "artist", "未知"),
            Duration = Dbl(el, "duration", 180.0),
            AudioId = Long(el, "audio_id"),
            Album = Str(el, "album"),
            Source = Str(el, "source", "原声")
        }).ToList();

        var bili = Arr(root, "bilibili").Select(el => new BilibiliSearchItem
        {
            BvId = Str(el, "bvid"),
            Title = Str(el, "title"),
            Author = Str(el, "author"),
            Duration = Str(el, "duration"),
            Play = Long(el, "play"),
            CoverUrl = Str(el, "cover_url"),
            Url = Str(el, "url")
        }).Where(v => v.Url.Length > 0).ToList();

        return (tabs, audio, bili);
    }

    private void BtnOpenReference_Click(object sender, RoutedEventArgs e)
    {
        if (sender is Button btn && btn.Tag is BilibiliSearchItem item && !string.IsNullOrEmpty(item.Url))
        {
            try
            {
                Process.Start(new ProcessStartInfo
                {
                    FileName = item.Url,
                    UseShellExecute = true
                });
            }
            catch (Exception ex)
            {
                txtStatus.Text = $"打开失败: {ex.Message}";
            }
        }
    }

    private async void BtnImportItem_Click(object sender, RoutedEventArgs e)
    {
        if (sender is not Button { Tag: TabSearchItem item } || !BeginJob("导入中...")) return;
        // download + backing alignment (up to ~15 min per candidate recording)
        var r = await Task.Run(() => RunCli(TimeSpan.FromMinutes(60), "confirm-tab", item.Title, item.Artist, item.DownloadUrl));
        EndJob();
        if (r.Ok) Finish(SongTitle(r.Root, item.Title));
        else txtStatus.Text = Fail("导入失败", r.Error);
    }

    private async void BtnTranscribeItem_Click(object sender, RoutedEventArgs e)
    {
        if (sender is not Button { Tag: AudioSearchItem item } || !BeginJob("扒谱中...")) return;
        // max-quality separation + transcription can take about an hour
        var r = await Task.Run(() => RunCli(TimeSpan.FromHours(3), "transcribe-audio", item.Title, item.Artist,
                                            item.AudioId > 0 ? item.AudioId.ToString() : "", item.Album));
        EndJob();
        if (r.Ok) Finish(SongTitle(r.Root, item.Title));
        else txtStatus.Text = Fail("扒谱失败", r.Error);
    }

    private static string SongTitle(JsonElement root, string fallback) =>
        root.ValueKind == JsonValueKind.Object && root.TryGetProperty("song", out var song) ? Str(song, "title", fallback) : fallback;

    private static string Fail(string head, string detail)
    {
        detail = (detail ?? "").Split('\n')[0].Trim();
        if (detail.Length > 60) detail = detail[..60] + "…";
        return detail.Length > 0 ? $"{head}: {detail}" : head;
    }

    private void BtnBrowseLocal_Click(object sender, RoutedEventArgs e)
    {
        OpenLocalFileDialog();
    }

    private void OpenLocalFileDialog()
    {
        var dlg = new OpenFileDialog
        {
            Title = "选择乐谱或音频",
            Filter = "乐谱 / 音频|*.gp;*.gp5;*.gpx;*.gp4;*.pdf;*.mp3;*.wav;*.flac;*.m4a;*.ogg;*.aac;*.opus|所有文件 (*.*)|*.*",
            Multiselect = false
        };

        if (dlg.ShowDialog(Application.Current.MainWindow) == true)
        {
            ImportLocalFile(dlg.FileName);
        }
    }

    private void Window_DragOver(object sender, DragEventArgs e)
    {
        if (e.Data.GetDataPresent(DataFormats.FileDrop))
        {
            e.Effects = DragDropEffects.Copy;
            e.Handled = true;
        }
    }

    private void Window_Drop(object sender, DragEventArgs e)
    {
        if (e.Data.GetDataPresent(DataFormats.FileDrop))
        {
            string[] files = (string[])e.Data.GetData(DataFormats.FileDrop);
            if (files != null && files.Length > 0)
            {
                ImportLocalFile(files[0]);
            }
        }
    }

    private static readonly string[] AudioExts = { ".mp3", ".wav", ".flac", ".m4a", ".ogg", ".aac", ".opus" };

    internal async void ImportLocalFile(string filePath)
    {
        bool audio = AudioExts.Contains(Path.GetExtension(filePath).ToLowerInvariant());
        if (!File.Exists(filePath) || !BeginJob(audio ? "扒谱中..." : "导入中...")) return;
        var r = await Task.Run(() => RunCli(audio ? TimeSpan.FromHours(3) : TimeSpan.FromMinutes(60), "import-local", filePath));
        EndJob();
        if (r.Ok) Finish(audio ? SongTitle(r.Root, Path.GetFileNameWithoutExtension(filePath)) : Path.GetFileNameWithoutExtension(filePath));
        else txtStatus.Text = Fail(audio ? "扒谱失败" : "导入失败", r.Error);
    }

    private void BtnClose_Click(object sender, RoutedEventArgs e)
    {
        RequestClose?.Invoke();
    }
}