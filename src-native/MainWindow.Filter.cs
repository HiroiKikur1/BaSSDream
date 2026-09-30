using System;
using System.Collections.Generic;
using System.Linq;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Controls.Primitives;
using System.Windows.Input;
using System.Windows.Media;
using System.Windows.Media.Animation;
using BassStation.Models;

namespace BassStation;

// In-window filter panel (band / difficulty / level range) and favourites.
public partial class MainWindow
{
    private static readonly (string Key, string Label, string Match)[] Bands =
    {
        ("ALL", "全部", ""),
        ("popipa", "Poppin'Party", "poppin"),
        ("afterglow", "Afterglow", "afterglow"),
        ("pasupare", "Pastel*Palettes", "pastel"),
        ("roselia", "Roselia", "roselia"),
        ("hhw", "Hello, Happy World!", "ハロー|hello, happy|hello happy"),
        ("morfonica", "Morfonica", "morfonica"),
        ("ras", "RAISE A SUILEN", "raise a suilen|ras_|ras "),
        ("mygo", "MyGO!!!!!", "mygo"),
        ("avemujica", "Ave Mujica", "ave mujica|avemujica"),
        ("ikka", "一家Dumb Rock!", "一家dumb|ikka dumb|dumb rock"),
        ("millsage", "millsage", "millsage"),
        ("other", "其他", "")
    };

    private readonly HashSet<string> _bandFilter = new();
    private int _levelMin = 1, _levelMax = 32;
    private const int LevelLo = 1, LevelHi = 32;
    private readonly Dictionary<string, ToggleButton> _bandTiles = new();

    private static bool IsClassical(SongModel x) =>
        (x.Franchise ?? "").Contains("古典") || (x.FolderName ?? "").Contains("古典") ||
        (x.Artist ?? "").Contains("Bach", StringComparison.OrdinalIgnoreCase);

    private static string BandKeyOf(SongModel s)
    {
        string a = ((s.Artist ?? "") + " " + (s.FolderName ?? "")).ToLowerInvariant();
        foreach (var b in Bands.Skip(1).Take(Bands.Length - 2))
            if (b.Match.Split('|').Any(m => m.Length > 0 && a.Contains(m.ToLowerInvariant()))) return b.Key;
        return "other";
    }

    private void InitFilterPanel()
    {
        gridBands.Children.Clear();
        foreach (var (key, label, _) in Bands)
        {
            var tile = new ToggleButton { Style = (Style)FindResource("BandTile"), Tag = key, Content = BuildBandLabel(key, label) };
            tile.Click += BandTile_Click;
            _bandTiles[key] = tile;
            gridBands.Children.Add(tile);
        }
        SyncBandTiles();
    }

    private UIElement BuildBandLabel(string key, string label)
    {
        var logo = key is "ALL" or "other" ? null : LogoImage(key);
        if (logo != null)
            return new Image { Source = logo, Height = 46, Margin = new Thickness(8, 4, 8, 4), Stretch = Stretch.Uniform };
        return new TextBlock { Text = label, FontSize = 16, FontWeight = FontWeights.Bold, TextAlignment = TextAlignment.Center,
                               TextWrapping = TextWrapping.Wrap, Margin = new Thickness(6, 0, 6, 0) };
    }

    private void SyncBandTiles()
    {
        foreach (var (key, tile) in _bandTiles)
        {
            tile.IsChecked = key == "ALL" ? _bandFilter.Count == 0 : _bandFilter.Contains(key);
            if (tile.Content is TextBlock tb)
                tb.Foreground = tile.IsChecked == true ? Brushes.White : new SolidColorBrush(Color.FromRgb(74, 71, 80));
        }
        dotFilterActive.Visibility = (_bandFilter.Count > 0 || _currentTierFilter != "ALL" || _levelMin > LevelLo || _levelMax < LevelHi)
            ? Visibility.Visible : Visibility.Collapsed;
    }

    private IEnumerable<SongModel> ApplyPanelFilters(IEnumerable<SongModel> list)
    {
        if (_bandFilter.Count > 0) list = list.Where(x => _bandFilter.Contains(BandKeyOf(x)));
        return list.Where(x => x.Level >= _levelMin && x.Level <= _levelMax);
    }

    private void BandTile_Click(object sender, RoutedEventArgs e)
    {
        if (sender is not ToggleButton t || t.Tag is not string key) return;
        if (key == "ALL") _bandFilter.Clear();
        else if (!_bandFilter.Add(key)) _bandFilter.Remove(key);
        SyncBandTiles();
        ApplyFilterAndSort();
    }

    private void TierRadio_Checked(object sender, RoutedEventArgs e)
    {
        if (sender is RadioButton rb && rb.Tag is string tier && IsLoaded)
        {
            _currentTierFilter = tier;
            SyncBandTiles();
            ApplyFilterAndSort();
        }
    }

    private void BtnFilter_Click(object sender, RoutedEventArgs e)
    {
        filterOverlay.Visibility = Visibility.Visible;
        var ease = new CubicEase { EasingMode = EasingMode.EaseOut };
        filterSlide.BeginAnimation(TranslateTransform.XProperty, new DoubleAnimation(filterPanel.Width, 0, TimeSpan.FromMilliseconds(220)) { EasingFunction = ease });
        filterScrim.BeginAnimation(OpacityProperty, new DoubleAnimation(0, 1, TimeSpan.FromMilliseconds(180)));
        LayoutRange();
    }

    private void CloseFilter()
    {
        var ease = new CubicEase { EasingMode = EasingMode.EaseIn };
        var anim = new DoubleAnimation(0, filterPanel.Width, TimeSpan.FromMilliseconds(180)) { EasingFunction = ease };
        anim.Completed += (_, _) => filterOverlay.Visibility = Visibility.Collapsed;
        filterSlide.BeginAnimation(TranslateTransform.XProperty, anim);
        filterScrim.BeginAnimation(OpacityProperty, new DoubleAnimation(1, 0, TimeSpan.FromMilliseconds(180)));
    }

    private void FilterScrim_MouseDown(object sender, MouseButtonEventArgs e) => CloseFilter();
    private void FilterClose_Click(object sender, RoutedEventArgs e) => CloseFilter();

    private void FilterReset_Click(object sender, RoutedEventArgs e)
    {
        _bandFilter.Clear();
        _levelMin = LevelLo;
        _levelMax = LevelHi;
        _currentTierFilter = "ALL";
        foreach (var rb in pnlTierRadios.Children.OfType<RadioButton>()) rb.IsChecked = (string)rb.Tag == "ALL";
        LayoutRange();
        SyncBandTiles();
        ApplyFilterAndSort();
    }

    // ---- level range slider ----
    private double RangeWidth => Math.Max(10, rangeCanvas.ActualWidth - thumbMin.Width);
    private double XOf(int level) => (level - LevelLo) / (double)(LevelHi - LevelLo) * RangeWidth;
    private int LevelOf(double x) => (int)Math.Round(LevelLo + Math.Clamp(x / RangeWidth, 0, 1) * (LevelHi - LevelLo));

    private void LayoutRange()
    {
        if (rangeCanvas.ActualWidth <= 0) return;
        double half = thumbMin.Width / 2;
        rangeTrack.Width = rangeCanvas.ActualWidth - thumbMin.Width;
        Canvas.SetLeft(rangeTrack, half);
        Canvas.SetLeft(thumbMin, XOf(_levelMin));
        Canvas.SetLeft(thumbMax, XOf(_levelMax));
        Canvas.SetLeft(rangeFill, XOf(_levelMin) + half);
        rangeFill.Width = Math.Max(0, XOf(_levelMax) - XOf(_levelMin));
        Panel.SetZIndex(thumbMin, 2);
        Panel.SetZIndex(thumbMax, 2);
        txtLevelMin.Text = _levelMin.ToString();
        txtLevelMax.Text = _levelMax.ToString();
    }

    private void RangeCanvas_SizeChanged(object sender, SizeChangedEventArgs e) => LayoutRange();

    private void ThumbMin_DragDelta(object sender, DragDeltaEventArgs e)
    {
        int v = LevelOf(Canvas.GetLeft(thumbMin) + e.HorizontalChange);
        v = Math.Min(v, _levelMax);
        if (v == _levelMin) return;
        _levelMin = v;
        LayoutRange();
        SyncBandTiles();
        ApplyFilterAndSort();
    }

    private void ThumbMax_DragDelta(object sender, DragDeltaEventArgs e)
    {
        int v = LevelOf(Canvas.GetLeft(thumbMax) + e.HorizontalChange);
        v = Math.Max(v, _levelMin);
        if (v == _levelMax) return;
        _levelMax = v;
        LayoutRange();
        SyncBandTiles();
        ApplyFilterAndSort();
    }

    // ---- favourites ----
    private void UpdateFavoriteUI(SongModel? song)
    {
        var slots = song?.FavSlots ?? new HashSet<int>();
        favSlot1.IsChecked = slots.Contains(1);
        favSlot2.IsChecked = slots.Contains(2);
        favSlot3.IsChecked = slots.Contains(3);
    }

    private async void FavSlot_Click(object sender, RoutedEventArgs e)
    {
        if (_selectedSong == null || sender is not ToggleButton t || !int.TryParse(t.Tag?.ToString(), out int slot)) return;
        bool on = t.IsChecked == true;
        var song = _selectedSong;
        var set = new HashSet<int>(song.FavSlots);
        if (on) set.Add(slot); else set.Remove(slot);
        song.FavSlots = set;
        await _dbService.SetFavoriteAsync(song.Id, slot, on);
        if (_currentCategory == "FAV" && !song.IsFavorite) ApplyFilterAndSort();
    }

    private void BtnRandom_Click(object sender, RoutedEventArgs e)
    {
        if (_filteredSongs.Count == 0) return;
        var pick = _filteredSongs[Random.Shared.Next(_filteredSongs.Count)];
        lstSongs.SelectedItem = pick;
        lstSongs.ScrollIntoView(pick);
    }
}
