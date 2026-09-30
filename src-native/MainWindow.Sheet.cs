using System;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Input;
using System.Windows.Media;
using System.Windows.Media.Animation;

namespace BassStation;

// Hosts a view's content inside the main window (no separate OS window); click outside to return.
public partial class MainWindow
{
    private Action? _sheetClosed;

    private void ShowSheet(Window view, bool addClose, Action<Action> subscribe, Action? onClosed = null)
    {
        var content = (UIElement)view.Content;
        view.Content = null;
        var panel = new Grid();
        panel.RowDefinitions.Add(new RowDefinition { Height = new GridLength(1, GridUnitType.Star) });
        panel.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
        panel.Children.Add(content);
        if (addClose)
        {
            var close = new Button { Content = "关闭", Style = (Style)FindResource("GbpFlat"), Width = 220, Height = 52, FontSize = 20, Margin = new Thickness(0, 0, 0, 18) };
            close.Click += (_, _) => CloseSheet();
            Grid.SetRow(close, 1);
            panel.Children.Add(close);
        }
        sheetHost.Background = view.Background ?? Brushes.White;
        sheetHost.Width = Math.Min(view.Width, ActualWidth - 60);
        sheetHost.Height = Math.Min(view.Height + (addClose ? 30 : 0), ActualHeight - 80);
        sheetHost.Child = panel;
        _sheetClosed = onClosed;
        subscribe(CloseSheet);

        sheetOverlay.Visibility = Visibility.Visible;
        sheetHost.RenderTransformOrigin = new Point(0.5, 0.5);
        var ease = new BackEase { EasingMode = EasingMode.EaseOut, Amplitude = 0.3 };
        sheetScale.BeginAnimation(ScaleTransform.ScaleXProperty, new DoubleAnimation(0.92, 1, TimeSpan.FromMilliseconds(200)) { EasingFunction = ease });
        sheetScale.BeginAnimation(ScaleTransform.ScaleYProperty, new DoubleAnimation(0.92, 1, TimeSpan.FromMilliseconds(200)) { EasingFunction = ease });
        sheetOverlay.BeginAnimation(OpacityProperty, new DoubleAnimation(0, 1, TimeSpan.FromMilliseconds(160)));
    }

    private void CloseSheet()
    {
        if (sheetOverlay.Visibility != Visibility.Visible) return;
        var anim = new DoubleAnimation(1, 0, TimeSpan.FromMilliseconds(140));
        anim.Completed += (_, _) =>
        {
            sheetOverlay.Visibility = Visibility.Collapsed;
            sheetHost.Child = null;
            var cb = _sheetClosed;
            _sheetClosed = null;
            cb?.Invoke();
        };
        sheetOverlay.BeginAnimation(OpacityProperty, anim);
    }

    private void SheetScrim_MouseDown(object sender, MouseButtonEventArgs e) => CloseSheet();

    internal Views.EvaluationResultView? DebugResultView { get; private set; }

    // ---------------------------------------------------------------- score view (in-app tab player / editor)

    private Views.ScoreView? _scoreView;
    internal Views.ScoreView? DebugScoreView => _scoreView;
    internal Models.SongModel? DebugSelectedSong => _selectedSong;

    private void BtnScore_Click(object sender, RoutedEventArgs e)
    {
        if (_selectedSong != null) OpenScoreView(_selectedSong);
    }

    internal void OpenScoreView(Models.SongModel song)
    {
        if (_scoreView != null || string.IsNullOrEmpty(song.GpPath)) return;
        string gp = ActiveGpPath(song);
        // scores without embedded audio play from their aligned '[伴奏].gp'
        string derived = System.IO.Path.Combine(System.IO.Path.GetDirectoryName(gp) ?? "",
                                                System.IO.Path.GetFileNameWithoutExtension(gp) + " [伴奏].gp");
        if (System.IO.File.Exists(derived)) gp = derived;
        if (!System.IO.File.Exists(gp))
        {
            ShowToast("乐谱文件丢失", isSuccess: false);
            return;
        }
        // the other arrangement in the same folder (4/5 strings) is a score of its own
        var probe = new Models.SongModel
        {
            Id = song.Id, Title = song.Title, Artist = song.Artist, FolderName = song.FolderName, Version = song.Version,
            Tier = song.Tier, Level = song.Level, Tempo = song.Tempo
        };
        if (!string.Equals(ActiveGpPath(song), song.GpPath, StringComparison.OrdinalIgnoreCase)) probe.Id = song.Id + "_alt";
        var view = new Views.ScoreView(probe, gp);
        _scoreView = view;
        view.RequestClose += () =>
        {
            rootGrid.Children.Remove(view);
            _scoreView = null;
            Focus();
        };
        rootGrid.Children.Insert(rootGrid.Children.IndexOf(fxLayer), view);
    }

    // Full-window result screen after an evaluation; its "返回" leads back to the main screen.
    private void ShowEvaluationResult(Models.SongModel song, Models.PerformanceScoreDetailModel score)
    {
        CloseSheet();
        var view = new Views.EvaluationResultView(song, score);
        DebugResultView = view;
        view.RequestClose += () =>
        {
            var fade = new DoubleAnimation(1, 0, TimeSpan.FromMilliseconds(160));
            fade.Completed += (_, _) => rootGrid.Children.Remove(view);
            view.BeginAnimation(OpacityProperty, fade);
            if (DebugResultView == view) DebugResultView = null;
        };
        // above the sheet, below the click effects and toast
        rootGrid.Children.Insert(rootGrid.Children.IndexOf(fxLayer), view);
    }

    // Debug render: result screen from a saved report file (its "summary" block) for the selected song.
    internal void DebugShowResultFromReport(string reportPath)
    {
        if (_selectedSong == null) return;
        var m = Models.PerformanceScoreDetailModel.FromReport(_selectedSong.Id, reportPath);
        if (m == null) return;
        m.PrevBest = _selectedSong.BestScore;
        m.IsNewBest = true;
        ShowEvaluationResult(_selectedSong, m);
    }

    // borderless window: drag from any empty area, own caption buttons
    // Only the header strip moves the window, and only once the mouse really travels: calling DragMove on
    // every press entered the modal move loop on each rapid click (animations hitched) and swallowed the
    // drag gesture that draws the emblem trail everywhere else.
    private const double HeaderHeight = 92;
    private Point? _dragStart;

    private void Root_DragMove(object sender, MouseButtonEventArgs e)
    {
        var p = e.GetPosition(rootGrid);
        _dragStart = e.OriginalSource is FrameworkElement fe && (fe is Panel || fe is Border || fe is Image || fe is System.Windows.Shapes.Rectangle)
                     && p.Y < HeaderHeight && filterOverlay.Visibility != Visibility.Visible && sheetOverlay.Visibility != Visibility.Visible
                     && dialogOverlay.Visibility != Visibility.Visible
            ? p : null;
    }

    private void Root_MouseMove(object sender, MouseEventArgs e)
    {
        if (_dragStart is not Point start) return;
        if (e.LeftButton != MouseButtonState.Pressed) { _dragStart = null; return; }
        var d = e.GetPosition(rootGrid) - start;
        if (Math.Abs(d.X) < SystemParameters.MinimumHorizontalDragDistance && Math.Abs(d.Y) < SystemParameters.MinimumVerticalDragDistance) return;
        _dragStart = null;
        try { DragMove(); } catch { }
    }

    private void BtnMinimize_Click(object sender, RoutedEventArgs e) => WindowState = WindowState.Minimized;
    private void BtnClose_Click(object sender, RoutedEventArgs e) => Close();
}
