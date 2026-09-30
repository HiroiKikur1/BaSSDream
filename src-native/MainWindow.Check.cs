using System;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Text.Json;
using System.Threading.Tasks;
using System.Windows;
using BassStation.Models;

using BassStation.Services;

namespace BassStation;

// 核对版: an AI transcription's companion GP whose audio is the song with the separated bass boosted.
// Opening it is a practice session; if the user saved it, the score is synced back into the practice file.
public partial class MainWindow
{
    private const string CheckTag = "[核对版]";
    private bool _practiceIsCheck;
    private string? _checkGpPath;
    private DateTime _checkWriteTimeAtStart;

    private static string? FindCheckVersion(SongModel? song)
    {
        if (song == null || string.IsNullOrEmpty(song.GpPath) || !File.Exists(song.GpPath)) return null;
        string dir = Path.GetDirectoryName(song.GpPath) ?? "";
        string exact = Path.Combine(dir, $"{Path.GetFileNameWithoutExtension(song.GpPath)} {CheckTag}.gp");
        if (File.Exists(exact)) return exact;
        var cands = Directory.GetFiles(dir, $"*{CheckTag}.gp");
        return cands.Length == 1 ? cands[0] : null;
    }

    // 4弦 / 5弦: one list entry per song version. The other arrangement is either a sibling song (own folder, own
    // level/stats) or a file in the same folder (sibling "…4st/5st…" file, or the AI "[4弦版].gp" of a 5-string transcription).
    private const string FourTag = "[4弦版]";
    private static readonly string PrefsPath = AppPaths.CachePath(@"ui_prefs.json");
    private readonly Dictionary<string, bool> _fivePick = new();   // per song (GroupKey or Id): last chosen arrangement
    private bool _defaultFive = LoadDefaultFive();                 // last arrangement chosen anywhere

    private static bool LoadDefaultFive()
    {
        try
        {
            using var doc = JsonDocument.Parse(File.ReadAllText(PrefsPath));
            return doc.RootElement.TryGetProperty("five", out var v) && v.GetBoolean();
        }
        catch { return true; }
    }

    private void SaveDefaultFive()
    {
        try { File.WriteAllText(PrefsPath, JsonSerializer.Serialize(new { five = _defaultFive })); } catch { }
    }

    private static string PairKey(SongModel s) => s.Sibling != null && s.GroupKey.Length > 0 ? s.GroupKey : s.Id;

    // "[4弦版]" files created after the last library scan are not in AltGpPath yet
    private static string AltPathOf(SongModel s)
    {
        if (s.AltGpPath.Length > 0 && File.Exists(s.AltGpPath)) return s.AltGpPath;
        if (string.IsNullOrEmpty(s.GpPath)) return "";
        string p = Path.Combine(Path.GetDirectoryName(s.GpPath) ?? "", $"{Path.GetFileNameWithoutExtension(s.GpPath)} {FourTag}.gp");
        return File.Exists(p) ? p : "";
    }

    private bool WantFive(SongModel s)
    {
        if (_fivePick.TryGetValue(PairKey(s), out bool five)) return five;
        if (_currentCategory == "5STRINGS" && string.IsNullOrWhiteSpace(txtSearch.Text)) return true;
        return _defaultFive;
    }

    private static bool HasFive(SongModel s) =>
        s.Is5String || s.Sibling?.Is5String == true || (s.AltGpPath.Length > 0 && !s.Is5String);

    // a linked pair shows only the arrangement currently wanted
    private bool IsListed(SongModel s) => s.Sibling == null || WantFive(s) == s.Is5String;

    private static void LinkStringPairs(List<SongModel> songs)
    {
        foreach (var s in songs) s.Sibling = null;
        foreach (var g in songs.Where(s => s.GroupKey.Length > 0).GroupBy(s => s.GroupKey))
        {
            var five = g.FirstOrDefault(s => s.Is5String);
            var four = g.FirstOrDefault(s => !s.Is5String);
            if (five == null || four == null) continue;
            five.Sibling = four;
            four.Sibling = five;
        }
    }

    // score file to open for the song on stage
    private string ActiveGpPath(SongModel s)
    {
        if (s.Sibling != null) return s.GpPath;
        string alt = AltPathOf(s);
        return alt.Length > 0 && WantFive(s) != s.Is5String ? alt : s.GpPath;
    }

    private bool UpdateStringSwitch(SongModel song)
    {
        bool dual = song.Sibling != null || AltPathOf(song).Length > 0;
        stringSwitch.Visibility = dual ? Visibility.Visible : Visibility.Collapsed;
        if (dual) PaintStringSwitch(song.Sibling != null ? song.Is5String : WantFive(song));
        return dual;
    }

    private void PaintStringSwitch(bool five)
    {
        var pink = new System.Windows.Media.SolidColorBrush(System.Windows.Media.Color.FromRgb(0xFF, 0x3B, 0x72));
        var dim = new System.Windows.Media.SolidColorBrush(System.Windows.Media.Color.FromRgb(0xC9, 0xC4, 0xD3));
        pill4.Background = five ? System.Windows.Media.Brushes.Transparent : pink;
        pill5.Background = five ? pink : System.Windows.Media.Brushes.Transparent;
        txtPill4.Foreground = five ? dim : System.Windows.Media.Brushes.White;
        txtPill5.Foreground = five ? System.Windows.Media.Brushes.White : dim;
    }

    private void Pill4_Click(object sender, System.Windows.Input.MouseButtonEventArgs e) => PickStrings(false);

    private void Pill5_Click(object sender, System.Windows.Input.MouseButtonEventArgs e) => PickStrings(true);

    private void PickStrings(bool five)
    {
        if (_selectedSong == null || _isPracticing) return;
        var song = _selectedSong;
        _fivePick[PairKey(song)] = five;
        if (_defaultFive != five)
        {
            _defaultFive = five;
            SaveDefaultFive();
        }
        if (song.Sibling != null && song.Is5String != five)
        {
            // swap the list entry to the other arrangement; stage follows with its own level / stats
            _selectedSong = song.Sibling;
            ApplyFilterAndSort();
            lstSongs.ScrollIntoView(_selectedSong);
            return;
        }
        PaintStringSwitch(five);
    }

    private void UpdateCheckButton(SongModel? song)
    {
        btnCheckVersion.Visibility = FindCheckVersion(song) != null ? Visibility.Visible : Visibility.Collapsed;
        btnCheckVersion.IsEnabled = !_isPracticing;
    }

    private void BtnCheckVersion_Click(object sender, RoutedEventArgs e)
    {
        if (_isPracticing) return;
        string? path = FindCheckVersion(_selectedSong);
        if (path == null)
        {
            UpdateCheckButton(_selectedSong);
            return;
        }
        LaunchPractice(path, isCheck: true);
    }

    private void SyncCheckVersionIfSaved(bool wasCheck, string? checkPath, DateTime writeTimeAtStart)
    {
        if (!wasCheck || checkPath == null || !File.Exists(checkPath)) return;
        if (File.GetLastWriteTime(checkPath) <= writeTimeAtStart) return;
        _ = Task.Run(() =>
        {
            string err = "";
            bool ok = false;
            try
            {
                var psi = new ProcessStartInfo
                {
                    FileName = AppPaths.Python,
                    RedirectStandardOutput = true,
                    RedirectStandardError = true,
                    UseShellExecute = false,
                    CreateNoWindow = true,
                    StandardOutputEncoding = System.Text.Encoding.UTF8
                };
                psi.ArgumentList.Add(AppPaths.Script("tab_cli.py"));
                psi.ArgumentList.Add("sync-check");
                psi.ArgumentList.Add(checkPath);
                psi.EnvironmentVariables["PYTHONUTF8"] = "1";
                psi.EnvironmentVariables["PYTHONIOENCODING"] = "utf-8";
                using var proc = Process.Start(psi);
                if (proc != null)
                {
                    var stderrTask = proc.StandardError.ReadToEndAsync();
                    string stdout = proc.StandardOutput.ReadToEnd();
                    proc.WaitForExit(120000);
                    _ = stderrTask.Result;
                    string? line = stdout.Split('\n').Select(l => l.Trim()).LastOrDefault(l => l.StartsWith('{'));
                    if (line != null)
                    {
                        using var doc = JsonDocument.Parse(line);
                        ok = doc.RootElement.TryGetProperty("ok", out var o) && o.GetBoolean();
                        if (!ok && doc.RootElement.TryGetProperty("error", out var er)) err = er.GetString() ?? "";
                    }
                }
            }
            catch (Exception ex) { err = ex.Message; }
            Dispatcher.Invoke(() => ShowToast(ok ? "已同步到正式版" : $"同步失败{(err.Length > 0 ? ": " + err : "")}", ok));
        });
    }
}
