using System;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Input;
using System.Windows.Media;
using System.Windows.Media.Animation;
using BassStation.Models;

namespace BassStation;

// In-window confirmation dialog (GBP style) replacing the system MessageBox.
public partial class MainWindow
{
    private TaskCompletionSource<bool>? _dialogTcs;

    private bool DialogOpen => _dialogTcs != null;

    private Task<bool> ConfirmSongAsync(SongModel song, string title, string okText)
    {
        _dialogTcs?.TrySetResult(false);
        _dialogTcs = new TaskCompletionSource<bool>();

        txtDialogTitle.Text = title;
        btnDialogOk.Content = okText;
        txtDialogSong.Text = song.Title;
        txtDialogArtist.Text = song.Artist;
        // reuse the jacket already decoded for the stage when it is this song
        imgDialogCover.Source = song == _stageSong ? imgStageCover.Source : null;

        dialogOverlay.Visibility = Visibility.Visible;
        var ease = new BackEase { EasingMode = EasingMode.EaseOut, Amplitude = 0.35 };
        dialogScale.BeginAnimation(ScaleTransform.ScaleXProperty, new DoubleAnimation(0.88, 1, TimeSpan.FromMilliseconds(220)) { EasingFunction = ease });
        dialogScale.BeginAnimation(ScaleTransform.ScaleYProperty, new DoubleAnimation(0.88, 1, TimeSpan.FromMilliseconds(220)) { EasingFunction = ease });
        dialogOverlay.BeginAnimation(OpacityProperty, new DoubleAnimation(0, 1, TimeSpan.FromMilliseconds(160)));
        dialogOverlay.Focus();
        return _dialogTcs.Task;
    }

    private void CloseDialog(bool result)
    {
        var tcs = _dialogTcs;
        if (tcs == null) return;
        _dialogTcs = null;
        var fade = new DoubleAnimation(1, 0, TimeSpan.FromMilliseconds(130));
        fade.Completed += (_, _) =>
        {
            if (_dialogTcs == null) dialogOverlay.Visibility = Visibility.Collapsed;
        };
        dialogOverlay.BeginAnimation(OpacityProperty, fade);
        tcs.TrySetResult(result);
        lstSongs.Focus();
    }

    private void DialogOk_Click(object sender, RoutedEventArgs e) => CloseDialog(true);
    private void DialogCancel_Click(object sender, RoutedEventArgs e) => CloseDialog(false);
    private void DialogScrim_MouseDown(object sender, MouseButtonEventArgs e) => CloseDialog(false);

    private void Dialog_KeyDown(object sender, KeyEventArgs e)
    {
        if (e.Key == Key.Escape) { CloseDialog(false); e.Handled = true; }
        else if (e.Key == Key.Enter) { CloseDialog(true); e.Handled = true; }
    }
}
