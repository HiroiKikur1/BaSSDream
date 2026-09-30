using System;
using System.Collections.Generic;
using System.Collections.Specialized;
using System.Diagnostics;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Threading;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Data;
using System.Windows.Input;
using System.Windows.Media;
using System.Windows.Media.Imaging;
using System.Windows.Threading;
using BassStation.Models;
using BassStation.Services;

namespace BassStation;

public partial class MainWindow : Window
{
    private readonly DatabaseService _dbService = new();
    private List<SongModel> _allSongs = new();
    private List<SongModel> _filteredSongs = new();
    private SongModel? _selectedSong;

    private string _currentCategory = "ALL";
    private string _currentSort = "LEVEL"; // LEVEL, BPM, TITLE
    private string _currentTierFilter = "ALL";

    private readonly List<(string Id, string Label)> _categories = new()
    {
        ("ALL", "所有"),
        ("FAV", "收藏"),
        ("BANGDREAM", "BanG Dream!"),
        ("SEKAI", "Project SEKAI"),
        ("5STRINGS", "5弦"),
        ("CLASSICAL", "古典练习曲"),
        ("OTHER", "其他")
    };

    // Real-time Guitar Pro 8 practice session tracking
    private bool _isPracticing = false;
    private DateTime? _practiceStartTime;
    private SongModel? _practicingSong;
    private Process? _gpProcess;
    private DispatcherTimer? _practiceTimer;
    private int _practicePollCounter = 0;

    public MainWindow()
    {
        InitializeComponent();
        FitToScreen();
        BuildCategoryMenu();
        InitFilterPanel();
        StartBackgroundDrift();
        Loaded += MainWindow_Loaded;
        Closing += MainWindow_Closing;
    }

    /// <summary>The layout is drawn for 1440 × 810; on a smaller work area everything scales down evenly.</summary>
    private void FitToScreen()
    {
        var wa = SystemParameters.WorkArea;
        double k = Math.Min(1, Math.Min(wa.Width / rootGrid.Width, wa.Height / rootGrid.Height));
        if (k < 1) rootGrid.LayoutTransform = new ScaleTransform(k, k);
    }

    /// <summary>Boot animation over the whole window; ends in the header logo. A fixed time shows one frame (debug).</summary>
    internal void PlayBoot(double? fixedTime = null)
    {
        try
        {
            var boot = new Views.BootOverlay { FixedTime = fixedTime };
            Panel.SetZIndex(boot, 1000);
            rootGrid.Children.Add(boot);
            boot.Start(imgLogo);
        }
        catch (Exception ex)
        {
            // never let the intro keep the app from starting
            Debug.WriteLine(ex);
            imgLogo.Opacity = 1;
        }
    }

    private async void MainWindow_Loaded(object sender, RoutedEventArgs e)
    {
        await LoadDataAsync();
        _ = RunBackingQueueAsync();
    }

    private void BuildCategoryMenu()
    {
        pnlCategories.Children.Clear();
        foreach (var (id, label) in _categories)
        {
            var btn = new Button
            {
                Content = label,
                Tag = id,
                Cursor = Cursors.Hand
            };

            btn.Click += CategoryButton_Click;
            pnlCategories.Children.Add(btn);
        }

        UpdateCategoryButtonsStyle();
    }

    private void UpdateCategoryButtonsStyle()
    {
        foreach (var child in pnlCategories.Children)
        {
            if (child is Button btn && btn.Tag is string id)
            {
                bool isActive = id == _currentCategory;
                btn.Style = (Style)FindResource(isActive ? "CategoryTabActive" : "CategoryTabIdle");
                btn.Background = new SolidColorBrush(BandColor(id));
            }
        }
    }

    private static Color BandColor(string id) => id switch
    {
        "ALL" => Palette.Brand,
        "FAV" => Color.FromRgb(255, 120, 160),
        "BANGDREAM" => Palette.Brand,
        "SEKAI" => Color.FromRgb(51, 170, 255),
        "5STRINGS" => Color.FromRgb(255, 174, 26),
        "CLASSICAL" => Color.FromRgb(160, 120, 60),
        _ => Color.FromRgb(52, 194, 116)
    };

    private void CategoryButton_Click(object sender, RoutedEventArgs e)
    {
        if (sender is Button btn && btn.Tag is string id)
        {
            _currentCategory = id;
            UpdateCategoryButtonsStyle();
            ApplyFilterAndSort();
        }
    }

    private async Task LoadDataAsync()
    {
        _allSongs = await _dbService.LoadSongsAsync();
        LinkStringPairs(_allSongs);
        var favs = await _dbService.LoadFavoritesAsync();
        foreach (var song in _allSongs)
            if (favs.TryGetValue(song.Id, out var slots)) song.FavSlots = slots;
        ApplyFilterAndSort();
    }

    private void ApplyFilterAndSort()
    {
        string query = txtSearch.Text.Trim().ToLowerInvariant();
        IEnumerable<SongModel> list = _allSongs.Where(IsListed);

        // Search Keyword takes priority and searches GLOBALLY across all songs
        if (!string.IsNullOrEmpty(query))
        {
            list = list.Where(s =>
                (s.Title != null && s.Title.ToLowerInvariant().Contains(query)) ||
                (s.Artist != null && s.Artist.ToLowerInvariant().Contains(query)) ||
                (s.FolderName != null && s.FolderName.ToLowerInvariant().Contains(query)) ||
                (s.Franchise != null && s.Franchise.ToLowerInvariant().Contains(query)) ||
                (s.Tags != null && s.Tags.Any(t => t.ToLowerInvariant().Contains(query)))
            );
        }
        else
        {
            list = _currentCategory switch
            {
                "FAV" => list.Where(x => x.IsFavorite || x.Sibling?.IsFavorite == true),
                "BANGDREAM" => list.Where(x => (x.Franchise ?? "").Contains("BanG Dream", StringComparison.OrdinalIgnoreCase)),
                "SEKAI" => list.Where(x => (x.Franchise ?? "").Contains("SEKAI", StringComparison.OrdinalIgnoreCase)),
                "5STRINGS" => list.Where(HasFive),
                "CLASSICAL" => list.Where(IsClassical),
                "OTHER" => list.Where(x => !(x.Franchise ?? "").Contains("BanG Dream", StringComparison.OrdinalIgnoreCase)
                                           && !(x.Franchise ?? "").Contains("SEKAI", StringComparison.OrdinalIgnoreCase) && !IsClassical(x)),
                _ => list
            };
            list = ApplyPanelFilters(list);
        }

        // Sorting
        list = _currentSort switch
        {
            "BPM" => list.OrderByDescending(s => s.Tempo),
            "TITLE" => list.OrderBy(s => s.Title),
            _ => list.OrderByDescending(s => s.Level).ThenBy(s => s.Title)
        };

        _filteredSongs = list.ToList();
        lstSongs.ItemsSource = _filteredSongs;

        // Auto select first or retain current
        if (_filteredSongs.Count > 0)
        {
            var match = _filteredSongs.FirstOrDefault(s => s.Id == _selectedSong?.Id) ?? _filteredSongs[0];
            _selectedSong = match;
            lstSongs.SelectedItem = match;
            if (_stageSong != match) UpdateStage(match);
        }
        else
        {
            ClearStage();
        }
    }

    public void SelectSongByTitle(string title)
    {
        var match = _filteredSongs.FirstOrDefault(s => 
            (s.Title != null && s.Title.Contains(title, StringComparison.OrdinalIgnoreCase)) ||
            (s.FolderName != null && s.FolderName.Contains(title, StringComparison.OrdinalIgnoreCase))
        );
        if (match != null)
        {
            _selectedSong = match;
            lstSongs.SelectedItem = match;
            lstSongs.ScrollIntoView(match);
            if (_stageSong != match) UpdateStage(match);
        }
    }

    public void SelectCategory(string categoryId)
    {
        _currentCategory = categoryId;
        UpdateCategoryButtonsStyle();
        ApplyFilterAndSort();
    }

    private void LstSongs_SelectionChanged(object sender, SelectionChangedEventArgs e)
    {
        if (lstSongs.SelectedItem is SongModel song)
        {
            _selectedSong = song;
            UpdateStage(song);
        }
    }

    private SongModel? _stageSong;
    private string? _coverPath;
    private string? _backdropPath;
    private readonly Dictionary<string, (BitmapSource Sharp, BitmapSource Soft)> _backdropCache = new();

    private static BitmapImage DecodeImage(Uri uri, int width)
    {
        var bmp = new BitmapImage();
        bmp.BeginInit();
        bmp.UriSource = uri;
        bmp.CacheOption = BitmapCacheOption.OnLoad;
        bmp.DecodePixelWidth = width;
        bmp.EndInit();
        bmp.Freeze();
        return bmp;
    }

    // decode off the UI thread so arrowing through the list never stalls; stale results are dropped
    private async void LoadCoverAsync(string? path)
    {
        if (path == _coverPath) return;
        _coverPath = path;
        if (string.IsNullOrEmpty(path)) { imgStageCover.Source = null; return; }
        BitmapSource? bmp = null;
        try { bmp = await Task.Run(() => File.Exists(path) ? DecodeImage(new Uri(path), 600) : null); }
        catch { }
        if (_coverPath == path) imgStageCover.Source = bmp;
    }

    private void UpdateStage(SongModel song)
    {
        _stageSong = song;
        UpdateCheckButton(song);
        txtStageTitle.Text = song.Title;
        txtStageVersion.Text = song.Version;
        bdStageVersion.Visibility = song.HasVersion ? Visibility.Visible : Visibility.Collapsed;
        txtStageTitle.MaxWidth = song.HasVersion ? 270 : 354;
        txtStageArtist.Text = song.Artist;
        bool hasFour = UpdateStringSwitch(song);
        txtStageTelemetry.Text = hasFour
            ? $"BPM {Math.Round(song.Tempo)}   {song.DisplayDuration}"
            : $"BPM {Math.Round(song.Tempo)}   {(song.Is5String ? "5弦" : "4弦")}   {song.DisplayDuration}";

        txtStageLevel.Text = song.Level.ToString();
        UpdateDifficultyTierUI(song.Tier);

        // Score
        if (song.BestScore.HasValue && song.BestScore.Value > 0)
        {
            txtBestScore.Text = Models.PerformanceScoreDetailModel.ToPoints(song.BestScore.Value).ToString();
            txtScoreSub.Text = "";
            var grade = song.BestGrade ?? "A";
            txtBestGrade.Text = grade;

            var (gradeBg, gradeFg) = grade switch
            {
                "SS" => (Color.FromRgb(255, 244, 204), Color.FromRgb(230, 150, 0)),
                "S" => (Color.FromRgb(255, 232, 240), Color.FromRgb(255, 51, 119)),
                "A" => (Color.FromRgb(234, 241, 255), Color.FromRgb(62, 139, 255)),
                "B" => (Color.FromRgb(228, 248, 237), Color.FromRgb(52, 194, 116)),
                _ => (Color.FromRgb(247, 241, 244), Color.FromRgb(140, 132, 156))
            };
            bdBestGrade.Fill = new SolidColorBrush(gradeBg);
            bdBestGrade.Stroke = new SolidColorBrush(gradeFg);
            txtBestGrade.Foreground = new SolidColorBrush(gradeFg);
        }
        else
        {
            txtBestScore.Text = "--";
            txtScoreSub.Text = "";
            txtBestGrade.Text = "";
            bdBestGrade.Fill = new SolidColorBrush(Color.FromRgb(244, 241, 245));
            bdBestGrade.Stroke = new SolidColorBrush(Color.FromRgb(228, 220, 226));
        }

        LoadCoverAsync(song.CoverImagePath);

        // Update Atmospheric Stage Backdrop (Band Group Photo or Enlarged Album Cover)
        UpdateStageBackdrop(song);

        UpdateFavoriteUI(song);

        // Reset Tablet button state
        txtSendTabletLabel.Text = "复制PDF谱面";
        pathCopyIcon.Data = Geometry.Parse("M 4,2 L 12,2 C 12.5,2 13,2.5 13,3 L 13,10 M 2,5 L 9,5 C 9.5,5 10,5.5 10,6 L 10,13 C 10,13.5 9.5,14 9,14 L 2,14 C 1.5,14 1,13.5 1,13 L 1,6 C 1,5.5 1.5,5 2,5 Z");
        pathCopyIcon.Stroke = Palette.BrandBrush;
    }


    private void TxtSearch_TextChanged(object sender, TextChangedEventArgs e)
    {
        txtPlaceholder.Visibility = string.IsNullOrEmpty(txtSearch.Text) ? Visibility.Visible : Visibility.Collapsed;
        ApplyFilterAndSort();
    }

    private void BtnSort_Click(object sender, RoutedEventArgs e)
    {
        if (_currentSort == "LEVEL")
        {
            _currentSort = "BPM";
            SetSortLabel("BPM");
        }
        else if (_currentSort == "BPM")
        {
            _currentSort = "TITLE";
            SetSortLabel("曲名");
        }
        else
        {
            _currentSort = "LEVEL";
            SetSortLabel("难度");
        }

        ApplyFilterAndSort();
    }








    private System.Windows.Threading.DispatcherTimer? _toastTimer;

    private void SetSortLabel(string text)
    {
        if (btnSort.Template?.FindName("txtSortLabel", btnSort) is TextBlock tb) tb.Text = text;
    }

    public void ShowToast(string message, bool isSuccess = true)
    {
        try
        {
            txtToastIcon.Text = isSuccess ? "✓" : "ℹ";
            txtToastIcon.Foreground = new SolidColorBrush(isSuccess ? Color.FromRgb(52, 194, 116) : Color.FromRgb(62, 139, 255));
            txtToastMessage.Text = message;

            bdToast.Visibility = Visibility.Visible;
            var fadeIn = new System.Windows.Media.Animation.DoubleAnimation(0.0, 1.0, TimeSpan.FromMilliseconds(160));
            bdToast.BeginAnimation(UIElement.OpacityProperty, fadeIn);

            _toastTimer?.Stop();
            _toastTimer = new System.Windows.Threading.DispatcherTimer
            {
                Interval = TimeSpan.FromMilliseconds(2400)
            };
            _toastTimer.Tick += (s, e) =>
            {
                _toastTimer.Stop();
                var fadeOut = new System.Windows.Media.Animation.DoubleAnimation(1.0, 0.0, TimeSpan.FromMilliseconds(220));
                fadeOut.Completed += (_, _) => bdToast.Visibility = Visibility.Collapsed;
                bdToast.BeginAnimation(UIElement.OpacityProperty, fadeOut);
            };
            _toastTimer.Start();
        }
        catch { }
    }

    private void UpdateStageBackdrop(SongModel song)
    {
        try
        {
            string artistLower = (song.Artist ?? "").ToLowerInvariant();
            string franchiseLower = (song.Franchise ?? "").ToLowerInvariant();
            string folderLower = (song.FolderName ?? "").ToLowerInvariant();

            string? bandKey = null;

            // 1. BanG Dream! Bands
            if (artistLower.Contains("roselia") || folderLower.Contains("roselia")) bandKey = "roselia";
            else if (artistLower.Contains("mygo") || folderLower.Contains("mygo")) bandKey = "mygo";
            else if (artistLower.Contains("ave mujica") || folderLower.Contains("ave mujica")) bandKey = "ave_mujica";
            else if (artistLower.Contains("morfonica") || folderLower.Contains("morfonica")) bandKey = "morfonica";
            else if (artistLower.Contains("poppin") || folderLower.Contains("poppin")) bandKey = "poppin_party";
            else if (artistLower.Contains("raise a suilen") || artistLower.Contains("ras") || folderLower.Contains("raise a suilen")) bandKey = "raise_a_suilen";
            else if (artistLower.Contains("afterglow") || folderLower.Contains("afterglow")) bandKey = "afterglow";

            // 2. Project SEKAI Bands
            else if (artistLower.Contains("25") || artistLower.Contains("nightcord") || folderLower.Contains("25")) bandKey = "25ji";
            else if (artistLower.Contains("leo") || folderLower.Contains("leo/need") || folderLower.Contains("leo_need")) bandKey = "leo_need";
            else if (artistLower.Contains("vivid") || artistLower.Contains("vbs") || folderLower.Contains("vivid")) bandKey = "vivid_bad_squad";
            else if (artistLower.Contains("more more jump") || folderLower.Contains("more more jump")) bandKey = "more_more_jump";
            else if (artistLower.Contains("ワンダー") || artistLower.Contains("wonderlands") || folderLower.Contains("wonderlands")) bandKey = "wonderlands";

            string backdropsDir = Path.Combine(AppPaths.Assets, @"backdrops");
            string? backdropPath = null;

            if (bandKey != null)
            {
                string candidate = Path.Combine(backdropsDir, $"{bandKey}.jpg");
                if (File.Exists(candidate))
                {
                    backdropPath = candidate;
                }
            }

            ApplyBackdrop(backdropPath ?? "pack://application:,,,/assets/stage_default.jpg", backdropPath != null);
        }
        catch
        {
            _backdropPath = null;
            imgStageBackdropAmbient.Source = null;
            imgStageBackdrop.Source = null;
        }
    }

    private async void ApplyBackdrop(string key, bool bandPhoto)
    {
        if (key == _backdropPath) return;
        _backdropPath = key;
        if (!_backdropCache.TryGetValue(key, out var pair))
        {
            var uri = new Uri(key);
            // the ambient copy is decoded tiny; upscaling it gives the soft wash that a BlurEffect used to.
            // Band photos (2-3 MB JPEGs) decode off the UI thread: a synchronous decode froze every animation
            // for ~100 ms on each first switch to a band. Width 1440 = window width, the photo is never shown larger.
            try
            {
                pair = bandPhoto
                    ? await Task.Run(() => (DecodeImage(uri, 1440), DecodeImage(uri, 64)))
                    : (DecodeImage(uri, 1440), DecodeImage(uri, 64));   // pack:// resource, decoded once
            }
            catch { return; }
            _backdropCache[key] = pair;
            if (_backdropPath != key) return;   // selection moved on while decoding
        }
        imgStageBackdropAmbient.Source = pair.Soft;
        imgStageBackdropAmbient.Opacity = bandPhoto ? 0.22 : 0.2;
        imgStageBackdrop.Source = pair.Sharp;
        imgStageBackdrop.Opacity = bandPhoto ? 0.42 : 0.55;
    }

    // Drag-and-Drop GP & PDF File Handlers (Auto-Import into Library)
    private void MainWindow_DragOver(object sender, DragEventArgs e)
    {
        if (e.Data.GetDataPresent(DataFormats.FileDrop))
        {
            e.Effects = DragDropEffects.Copy;
        }
        else
        {
            e.Effects = DragDropEffects.None;
        }
        e.Handled = true;
    }

    private async void MainWindow_Drop(object sender, DragEventArgs e)
    {
        if (e.Data.GetDataPresent(DataFormats.FileDrop))
        {
            string[] files = (string[])e.Data.GetData(DataFormats.FileDrop);
            if (files != null && files.Length > 0)
            {
                string filePath = files[0];
                string ext = Path.GetExtension(filePath).ToLowerInvariant();
                if (ext is ".gp" or ".gp5" or ".gpx" or ".gp4" or ".gp3" or ".pdf")
                {
                    await ImportLocalFileAsync(filePath);
                }
                else if (ext is ".mp3" or ".wav" or ".flac" or ".m4a" or ".ogg" or ".aac" or ".opus")
                {
                    // a recording: transcribe it, with the add-song sheet showing progress
                    OpenAddSong().ImportLocalFile(filePath);
                }
            }
        }
    }

    private async Task ImportLocalFileAsync(string filePath)
    {
        if (!File.Exists(filePath)) return;
        string songTitle = Path.GetFileNameWithoutExtension(filePath);
        var r = await Task.Run(() => TabCli.Run(TimeSpan.FromMinutes(60), "import-local", filePath));
        if (!r.Ok)
        {
            ShowToast("导入失败", isSuccess: false);
            return;
        }
        await LoadDataAsync();
        var match = _filteredSongs.FirstOrDefault(s => s.Title.Contains(songTitle, StringComparison.OrdinalIgnoreCase));
        if (match != null)
        {
            lstSongs.SelectedItem = match;
            lstSongs.ScrollIntoView(match);
        }
        ShowToast($"已导入: {songTitle}", isSuccess: true);
    }

    private async Task<bool> AutoEnsureBackingTrackAsync(SongModel song)
    {
        string songId = song.Id, title = song.Title, artist = song.Artist, gpPath = song.GpPath ?? "";
        var r = await Task.Run(() => TabCli.Run(TimeSpan.FromMinutes(30), "ensure-backing", songId, title, artist, gpPath));
        if (!r.Ok) return false;
        song.HasBackingTrack = true;
        song.AudioPath = TabCli.Str(r.Root, "audio_path");
        string gp = TabCli.Str(r.Root, "gp_path");
        if (gp.Length > 0 && File.Exists(gp)) song.GpPath = gp;
        return true;
    }

    // Songs without a playable backing track are completed silently in the background, one at a time.
    private readonly HashSet<string> _backingTried = new();
    private async Task RunBackingQueueAsync()
    {
        foreach (var song in _allSongs.Where(x => !x.HasBackingTrack).ToList())
        {
            if (!_backingTried.Add(song.Id)) continue;
            try { await AutoEnsureBackingTrackAsync(song); } catch { }
        }
    }

    // One-Click Backing Track Auto-Completion & GP Linking

    // Native PDF Handling & Clipboard Sharing to Tablet
    private async void BtnSendTablet_Click(object sender, RoutedEventArgs e)
    {
        if (_selectedSong == null) return;

        string? validPdf = null;
        if (!string.IsNullOrEmpty(_selectedSong.PdfPath) && File.Exists(_selectedSong.PdfPath))
        {
            var fi = new FileInfo(_selectedSong.PdfPath);
            if (fi.Length >= 30000)
            {
                validPdf = _selectedSong.PdfPath;
            }
        }

        if (string.IsNullOrEmpty(validPdf) && !string.IsNullOrEmpty(_selectedSong.GpPath) && File.Exists(_selectedSong.GpPath))
        {
            string dir = Path.GetDirectoryName(_selectedSong.GpPath) ?? "";
            if (Directory.Exists(dir))
            {
                var pdfs = Directory.GetFiles(dir, "*.pdf")
                    .Where(p => new FileInfo(p).Length >= 30000)
                    .OrderByDescending(p => File.GetLastWriteTime(p))
                    .ToArray();
                if (pdfs.Length > 0)
                {
                    validPdf = pdfs[0];
                    _selectedSong.PdfPath = validPdf;
                }
            }
        }

        if (string.IsNullOrEmpty(validPdf))
        {
            txtSendTabletLabel.Text = "打开GP8...";
            btnSendTablet.IsEnabled = false;

            string gpPath = _selectedSong.GpPath ?? "";
            var exported = await GuitarProNativeExporter.EnsureOrExportPdfAsync(gpPath);
            btnSendTablet.IsEnabled = true;

            if (!string.IsNullOrEmpty(exported) && File.Exists(exported))
            {
                validPdf = exported;
                _selectedSong.PdfPath = validPdf;
            }
            else
            {
                txtSendTabletLabel.Text = "复制PDF谱面";
                ShowToast($"已在 GP8 打开: {_selectedSong.Title}");
                return;
            }
        }

        try
        {
            bool copied = false;
            for (int i = 0; i < 5; i++)
            {
                try
                {
                    var fileList = new StringCollection { validPdf };
                    var dataObj = new DataObject();
                    dataObj.SetFileDropList(fileList);
                    dataObj.SetText(validPdf);
                    Clipboard.SetDataObject(dataObj, true);
                    copied = true;
                    break;
                }
                catch
                {
                    Thread.Sleep(80);
                }
            }

            txtSendTabletLabel.Text = copied ? "已复制" : "复制失败";
            pathCopyIcon.Data = Geometry.Parse("M 2,7 L 6,11 L 13,3");
            pathCopyIcon.Stroke = new SolidColorBrush(Color.FromRgb(16, 185, 129));
            ShowToast("已复制 PDF");
        }
        catch (Exception)
        {
            ShowToast("复制失败", isSuccess: false);
        }
    }

    private void ClearStage()
    {
        _selectedSong = null;
        txtStageTitle.Text = "";
        bdStageVersion.Visibility = Visibility.Collapsed;
        txtStageArtist.Text = "";
        txtStageTelemetry.Text = "";
        txtStageLevel.Text = "";
        txtStageTierPill.Text = "";
        bdStageTierPill.Background = new SolidColorBrush(Color.FromRgb(179, 172, 188));
        txtBestScore.Text = "--";
        txtScoreSub.Text = "";
        txtBestGrade.Text = "";
        _stageSong = null;
        _coverPath = null;
        _backdropPath = null;
        imgStageCover.Source = null;
        imgStageBackdrop.Source = null;
        imgStageBackdropAmbient.Source = null;
        txtSendTabletLabel.Text = "复制PDF谱面";
        UpdateFavoriteUI(null);
    }

    private void LstSongs_PreviewKeyDown(object sender, KeyEventArgs e)
    {
        if (e.Key == Key.Delete)
        {
            _ = DeleteCurrentSelectedSongAsync();
            e.Handled = true;
        }
    }

    private void MenuDeleteSong_Click(object sender, RoutedEventArgs e)
    {
        _ = DeleteCurrentSelectedSongAsync();
    }

    private void BtnDeleteSelectedSong_Click(object sender, RoutedEventArgs e)
    {
        _ = DeleteCurrentSelectedSongAsync();
    }

    private async Task DeleteCurrentSelectedSongAsync()
    {
        if (_selectedSong == null || DialogOpen) return;
        var song = _selectedSong;

        if (!await ConfirmSongAsync(song, "删除乐曲", "删除")) return;

        string songTitle = song.Title;
        string songId = song.Id;
        string? folderName = song.FolderName;

        bool ok = await _dbService.DeleteSongAsync(songId, folderName);
        if (ok)
        {
            var toRemove = _allSongs.FirstOrDefault(s => s.Id == songId);
            if (toRemove != null) _allSongs.Remove(toRemove);

            var toRemoveF = _filteredSongs.FirstOrDefault(s => s.Id == songId);
            if (toRemoveF != null) _filteredSongs.Remove(toRemoveF);

            lstSongs.ItemsSource = null;
            lstSongs.ItemsSource = _filteredSongs;

            if (_filteredSongs.Count > 0)
            {
                var nextSong = _filteredSongs[0];
                _selectedSong = nextSong;
                lstSongs.SelectedItem = nextSong;
                UpdateStage(nextSong);
            }
            else
            {
                ClearStage();
            }

            ShowToast($"已删除: {songTitle}");
        }
        else
        {
            ShowToast("删除失败", isSuccess: false);
        }
    }

    private static string GetGuitarProExecutablePath() => GuitarProNativeExporter.GetGuitarProExecutablePath();

    private void BtnStartPractice_Click(object sender, RoutedEventArgs e)
    {
        if (_isPracticing)
        {
            // User manually clicked the active practice button to end and settle practice session
            EndPracticeSession(userRequested: true);
            return;
        }

        if (_selectedSong == null || string.IsNullOrEmpty(_selectedSong.GpPath))
        {
            ShowToast("无可用乐谱", isSuccess: false);
            return;
        }

        if (!File.Exists(_selectedSong.GpPath))
        {
            ShowToast("乐谱文件丢失", isSuccess: false);
            return;
        }

        LaunchPractice(ActiveGpPath(_selectedSong), isCheck: false);
    }

    // Opens a score in Guitar Pro and starts timing a practice session (practice file or 核对版).
    private void LaunchPractice(string gpPath, bool isCheck)
    {
        if (_selectedSong == null) return;
        try
        {
            string gpExe = GetGuitarProExecutablePath();
            Process? proc = null;
            string fullPath = Path.GetFullPath(gpPath);
            _practiceIsCheck = isCheck;
            _checkGpPath = isCheck ? fullPath : null;
            _checkWriteTimeAtStart = isCheck ? File.GetLastWriteTime(fullPath) : DateTime.MinValue;
            if (!string.IsNullOrEmpty(gpExe) && File.Exists(gpExe))
            {
                var psi = new ProcessStartInfo
                {
                    FileName = gpExe,
                    UseShellExecute = false,
                    WorkingDirectory = Path.GetDirectoryName(gpExe) ?? ""
                };
                psi.ArgumentList.Add("--open");
                psi.ArgumentList.Add(fullPath);
                proc = Process.Start(psi);
            }
            else
            {
                var psi = new ProcessStartInfo
                {
                    FileName = fullPath,
                    UseShellExecute = true
                };
                proc = Process.Start(psi);
            }

            if (proc == null)
            {
                var procs = Process.GetProcessesByName("GuitarPro");
                if (procs.Length > 0)
                {
                    proc = procs[0];
                }
            }

            _gpProcess = proc;
            _isPracticing = true;
            _practicingSong = _selectedSong;
            _practiceStartTime = DateTime.Now;
            _practicePollCounter = 0;

            StartPracticeTimer();
            UpdatePracticeButtonUI(true, TimeSpan.Zero);
            ShowToast(isCheck ? $"核对版: {_practicingSong.Title}" : $"已打开: {_practicingSong.Title}");
        }
        catch (Exception ex)
        {
            ShowToast($"启动失败: {ex.Message}", isSuccess: false);
        }
    }

    private void StartPracticeTimer()
    {
        if (_practiceTimer == null)
        {
            _practiceTimer = new DispatcherTimer
            {
                Interval = TimeSpan.FromSeconds(1)
            };
            _practiceTimer.Tick += PracticeTimer_Tick;
        }
        _practiceTimer.Start();
    }

    private void PracticeTimer_Tick(object? sender, EventArgs e)
    {
        if (!_isPracticing || _practiceStartTime == null) return;

        var elapsed = DateTime.Now - _practiceStartTime.Value;
        UpdatePracticeButtonUI(true, elapsed);

        _practicePollCounter++;
        // Check if GP8 process exited every 2 seconds
        if (_practicePollCounter % 2 == 0)
        {
            bool gpAlive = false;
            try
            {
                if (_gpProcess != null && !_gpProcess.HasExited)
                {
                    gpAlive = true;
                }
                else
                {
                    var procs = Process.GetProcessesByName("GuitarPro");
                    if (procs.Length > 0)
                    {
                        gpAlive = true;
                        foreach (var p in procs) p.Dispose();
                    }
                }
            }
            catch { }

            if (!gpAlive)
            {
                // Guitar Pro 8 has exited in Windows! Settle practice session automatically!
                EndPracticeSession(userRequested: false);
            }
        }
    }

    private void EndPracticeSession(bool userRequested)
    {
        if (!_isPracticing || _practiceStartTime == null || _practicingSong == null) return;

        _practiceTimer?.Stop();
        _isPracticing = false;

        var elapsed = DateTime.Now - _practiceStartTime.Value;
        double totalSec = elapsed.TotalSeconds;
        var song = _practicingSong;

        // Reset button UI to STAGE START
        UpdatePracticeButtonUI(false, TimeSpan.Zero);

        if (totalSec >= 10.0)
        {
            // Record actual GP8 practice duration into SQLite
            _ = Task.Run(async () =>
            {
                try
                {
                    await _dbService.RecordPracticeSessionAsync(song.Id, song.Title, song.Artist, totalSec);
                }
                catch { }
            });

            string durStr = FormatPracticeDuration(totalSec);
            ShowToast($"练习时长: {durStr}");
        }
        else
        {
            ShowToast("练习未满10秒");
        }

        _practicingSong = null;
        _practiceStartTime = null;
        _gpProcess = null;

        bool wasCheck = _practiceIsCheck;
        string? checkPath = _checkGpPath;
        _practiceIsCheck = false;
        _checkGpPath = null;
        UpdateCheckButton(_selectedSong);
        SyncCheckVersionIfSaved(wasCheck, checkPath, _checkWriteTimeAtStart);
    }

    private void UpdatePracticeButtonUI(bool isPracticing, TimeSpan elapsed)
    {
        try
        {
            if (isPracticing)
            {
                // Active practice mode: deep vibrant crimson gradient with live counting timer
                var activeGrad = new LinearGradientBrush(Color.FromRgb(98, 162, 255), Color.FromRgb(62, 139, 255), 90.0);
                btnStartPractice.Background = activeGrad;
                btnStartPractice.BorderBrush = new SolidColorBrush(Color.FromRgb(30, 96, 204));
                pathPlayIcon.Data = Geometry.Parse("M 3,3 L 11,3 L 11,11 L 3,11 Z"); // Clean Stop square
                pathPlayIcon.Fill = Brushes.White;
                txtPlayLabel.Text = $"{(_practiceIsCheck ? "核对中" : "练习中")} {(int)elapsed.TotalMinutes:D2}:{elapsed.Seconds:D2}";
                btnCheckVersion.IsEnabled = false;
            }
            else
            {
                btnStartPractice.Background = (Brush)FindResource("PinkVividGradient");
                btnStartPractice.BorderBrush = (Brush)FindResource("PinkLipBrush");
                pathPlayIcon.Data = Geometry.Parse("M 2,1 L 14,8 L 2,15 Z"); // Play triangle
                pathPlayIcon.Fill = Brushes.White;
                txtPlayLabel.Text = "开始练习";
            }
        }
        catch { }
    }

    private string FormatPracticeDuration(double totalSec)
    {
        int sec = (int)Math.Round(totalSec);
        if (sec < 60) return $"{sec}秒";
        int m = sec / 60;
        int s = sec % 60;
        if (s == 0) return $"{m}分钟";
        return $"{m}分{s:D2}秒";
    }

    private void MainWindow_Closing(object? sender, System.ComponentModel.CancelEventArgs e)
    {
        if (_isPracticing && _practiceStartTime != null && _practicingSong != null)
        {
            var elapsed = (DateTime.Now - _practiceStartTime.Value).TotalSeconds;
            if (elapsed >= 10.0)
            {
                try
                {
                    _dbService.RecordPracticeSessionAsync(_practicingSong.Id, _practicingSong.Title, _practicingSong.Artist, elapsed).GetAwaiter().GetResult();
                }
                catch { }
            }
        }
    }

    private void UpdateDifficultyTierUI(string tier)
    {
        var tierUpper = tier.ToUpperInvariant();
        txtStageTierPill.Text = tierUpper;
        bdStageTierPill.Background = new SolidColorBrush(TierColorOf(tierUpper));
    }

    private static Color TierColorOf(string tier) => tier.ToUpperInvariant() switch
    {
        "EASY" => Color.FromRgb(62, 139, 255),
        "NORMAL" => Color.FromRgb(52, 194, 116),
        "HARD" => Color.FromRgb(255, 174, 26),
        "EXPERT" => Color.FromRgb(255, 64, 88),
        "SPECIAL" => Color.FromRgb(224, 75, 214),
        _ => Color.FromRgb(255, 51, 119)
    };

    internal Views.EvaluationWindow? DebugEvalSheet { get; private set; }

    private void CardScore_MouseDown(object sender, MouseButtonEventArgs e)
    {
        if (_selectedSong == null) return;
        var song = _selectedSong;
        var evalWin = new Views.EvaluationWindow(song);
        DebugEvalSheet = evalWin;
        evalWin.ScoreUpdated += updatedScore =>
        {
            song.BestScore = updatedScore.OverallScore;
            song.BestGrade = updatedScore.Grade;
            if (_selectedSong == song) UpdateStage(song);
        };
        evalWin.ResultReady += result => ShowEvaluationResult(song, result);
        ShowSheet(evalWin, addClose: true, subscribe: h => evalWin.RequestClose += h);
    }

    private void BtnCalendar_Click(object sender, RoutedEventArgs e)
    {
        var calWin = new Views.PracticeCalendarWindow();
        ShowSheet(calWin, addClose: true, subscribe: h => calWin.RequestClose += h);
    }

    private void BtnAddSong_Click(object sender, RoutedEventArgs e) => OpenAddSong();

    private Views.AddSongWindow OpenAddSong()
    {
        var addSongWin = new Views.AddSongWindow();
        // the import may finish after the sheet was dismissed, so refresh on the event, not on close
        addSongWin.SongAdded += OnSongAdded;
        ShowSheet(addSongWin, addClose: false, subscribe: h => addSongWin.RequestClose += h);
        return addSongWin;
    }

    private async void OnSongAdded(string newTitle)
    {
        _currentCategory = "ALL";
        _currentTierFilter = "ALL";
        UpdateCategoryButtonsStyle();
        txtSearch.Text = "";
        await LoadDataAsync();
        if (!string.IsNullOrEmpty(newTitle))
        {
            var match = _allSongs.FirstOrDefault(s =>
                (s.Title != null && s.Title.Contains(newTitle, StringComparison.OrdinalIgnoreCase)) ||
                (s.FolderName != null && s.FolderName.Contains(newTitle, StringComparison.OrdinalIgnoreCase)));
            txtSearch.Text = newTitle;
            if (match != null)
            {
                lstSongs.SelectedItem = match;
                lstSongs.ScrollIntoView(match);
            }
        }
        ShowToast($"已导入: {newTitle}", isSuccess: true);
    }
}
