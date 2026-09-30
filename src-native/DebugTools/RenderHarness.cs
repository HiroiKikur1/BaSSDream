using System;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Media;
using System.Windows.Media.Imaging;

namespace BassStation.DebugTools;

/// <summary>
/// Command-line entry points that drive the real UI and save a PNG (no user input needed), used to check
/// layouts, the evaluation flow and the score view from scripts. Each returns true when it handled the arguments;
/// the app then shuts down.
///
///   --perf-clicks out.txt [songs|...]            click-effect frame timings
///   --render-test | --render-song | --render-category | --render-search  out.png [arg]
///   --render-filter | --render-fx | --render-sheet-&lt;name&gt; out.png     (BASSDREAM_SONG, BASSDREAM_MENU=section)
///   --debug-take out.png &lt;seconds&gt; &lt;song title&gt;                real in-app take
///   --render-result out.png &lt;report.json&gt; &lt;song title&gt; [--report [scroll]]   (BASSDREAM_REPLAY)
///   --render-eval-run out.png &lt;audio&gt; &lt;song title&gt;               (BASSDREAM_SECTION, BASSDREAM_RATE)
///   --render-score out.png &lt;song title&gt; [bar beat string [play seconds]]   (BASSDREAM_MIX)
///   --render-boot out.png &lt;seconds&gt;                           one frame of the boot animation
///   --render-calendar | --render-eval | --render-add-song out.png [query]
/// </summary>
internal static class RenderHarness
{
    public static async Task<bool> TryRunAsync(MainWindow main, string[] a)
    {
        if (a.Length == 0 || !a[0].StartsWith("--", StringComparison.Ordinal)) return false;
        string cmd = a[0];
        string Out(string fallback) => a.Length > 1 ? a[1].Trim().Trim('"', '\'') : fallback;
        string Rest(int from) => string.Join(" ", a.Skip(from)).Trim().Trim('"', '\'');
        double Env(string name, double d) =>
            double.TryParse(Environment.GetEnvironmentVariable(name), NumberStyles.Float, CultureInfo.InvariantCulture, out var v) ? v : d;
        FrameworkElement Content() => (FrameworkElement)main.Content;

        switch (cmd)
        {
            case "--perf-clicks":
                main.Show();
                await Task.Delay(2500);
                File.WriteAllText(Out("perf.txt"), await main.DebugPerfClicks(TimeSpan.FromSeconds(6), a.Length > 2 ? a[2] : "songs"));
                return true;

            case "--render-filter":
            case "--render-fx":
            case var s when s.StartsWith("--render-sheet-", StringComparison.Ordinal):
            {
                string outPath = Out("render.png");
                main.Show();
                await Task.Delay(2000);
                if (cmd == "--render-filter") main.DebugOpenFilter();
                else if (cmd == "--render-fx") main.DebugBurst();
                else
                {
                    if (Environment.GetEnvironmentVariable("BASSDREAM_SONG") is { Length: > 0 } pick) main.SelectSongByTitle(pick);
                    main.DebugOpenSheet(cmd.Substring(15));
                }
                await Task.Delay(cmd == "--render-fx" ? 180 : 1200);
                // BASSDREAM_MENU=section: also the eval sheet's practice-range menu, to <out>.menu.png
                if (Environment.GetEnvironmentVariable("BASSDREAM_MENU") == "section" && main.DebugEvalSheet != null)
                {
                    var menu = await main.DebugEvalSheet.DebugOpenSectionMenuAsync();
                    await Task.Delay(400);
                    Snap(menu, menu.ActualWidth + 12, menu.ActualHeight + 12, outPath + ".menu.png");
                }
                Snap(Content(), outPath);
                return true;
            }

            case "--render-boot":                          // --render-boot out.png <seconds>
                main.PlayBoot(a.Length > 2 ? double.Parse(a[2], CultureInfo.InvariantCulture) : 0);
                main.Show();
                await Task.Delay(2000);
                Snap(Content(), Out("render_boot.png"));
                return true;

            case "--render-test":
                main.Show();
                await Task.Delay(2000);
                Snap(Content(), Out("render_test.png"));
                return true;

            case "--render-song":
                main.Show();
                await Task.Delay(2000);
                if (a.Length > 2) main.SelectSongByTitle(Rest(2));
                await Task.Delay(800);
                Snap(Content(), Out("render_song.png"));
                return true;

            case "--render-category":
                main.Show();
                await Task.Delay(2000);
                main.SelectCategory(a.Length > 2 ? a[2].Trim().Trim('"', '\'') : "Roselia");
                await Task.Delay(800);
                Snap(Content(), Out("render_category.png"));
                return true;

            case "--render-search":
                main.Show();
                await Task.Delay(2000);
                main.txtSearch.Text = a.Length > 2 ? a[2] : "又三郎";
                await Task.Delay(800);
                Snap(Content(), Out("render_search.png"));
                return true;

            case "--debug-take" when a.Length > 3:
            {
                string outPath = Out("debug_take.png");
                main.Show();
                await Task.Delay(1500);
                main.SelectSongByTitle(Rest(3));
                await Task.Delay(300);
                main.DebugOpenSheet("eval");
                await Task.Delay(800);
                string? take = main.DebugEvalSheet == null ? null
                    : await main.DebugEvalSheet.DebugTakeAsync(double.Parse(a[2], CultureInfo.InvariantCulture));
                File.WriteAllText(outPath + ".txt", take ?? "no take");
                await Task.Delay(800);
                Snap(Content(), outPath);
                return true;
            }

            case "--render-result" when a.Length > 3:
            {
                var rest = a.Skip(3).ToList();
                int ri = rest.IndexOf("--report");
                double scroll = 0;
                bool report = ri >= 0;
                if (report)
                {
                    if (ri + 1 < rest.Count && double.TryParse(rest[ri + 1], NumberStyles.Float, CultureInfo.InvariantCulture, out var sc)) { scroll = sc; rest.RemoveAt(ri + 1); }
                    rest.RemoveAt(ri);
                }
                main.Show();
                await Task.Delay(1500);
                main.SelectSongByTitle(string.Join(" ", rest).Trim());
                await Task.Delay(300);
                main.DebugShowResultFromReport(a[2]);
                await Task.Delay(900);
                if (report && main.DebugResultView != null)
                {
                    main.DebugResultView.ShowReport();
                    await Task.Delay(500);
                    main.DebugResultView.DebugScroll(scroll);
                    await Task.Delay(400);
                    // BASSDREAM_REPLAY="<bar index>|mine|orig|ab|<loopback wav>": bar replay through the real output
                    if (Environment.GetEnvironmentVariable("BASSDREAM_REPLAY") is { Length: > 0 } rp)
                    {
                        var parts = rp.Split('|');
                        await main.DebugResultView.DebugReplayAsync(int.Parse(parts[0]), parts[1], parts[2]);
                    }
                }
                Snap(Content(), Out("render_result.png"));
                return true;
            }

            case "--render-eval-run" when a.Length > 3:
                main.Show();
                await Task.Delay(2000);
                main.SelectSongByTitle(Rest(3));
                await Task.Delay(500);
                main.DebugOpenSheet("eval");
                await Task.Delay(800);
                if (main.DebugEvalSheet != null)
                    await main.DebugEvalSheet.DebugEvaluateAsync(a[2], Environment.GetEnvironmentVariable("BASSDREAM_SECTION"), Env("BASSDREAM_RATE", 1.0));
                await Task.Delay(600);
                Snap(Content(), Out("render_eval_run.png"));
                return true;

            case "--render-score" when a.Length > 2:
            {
                main.Show();
                await Task.Delay(2000);
                main.SelectSongByTitle(a[2]);
                await Task.Delay(400);
                main.OpenScoreView(main.DebugSelectedSong!);
                int Arg(int i) => a.Length > i && int.TryParse(a[i], out int v) ? v : 0;
                // BASSDREAM_MIX="<wav>|rate|from|seconds|vb,vbass,vsynth,vclick": offline mix instead of a selection
                if (Environment.GetEnvironmentVariable("BASSDREAM_MIX") is { Length: > 0 } mix && main.DebugScoreView != null)
                {
                    var m = mix.Split('|');
                    double D(string x) => double.Parse(x, CultureInfo.InvariantCulture);
                    await main.DebugScoreView.DebugRenderAsync(m[0], D(m[1]), D(m[2]), D(m[3]), m[4].Split(',').Select(D).ToArray());
                }
                else if (main.DebugScoreView != null)
                    await main.DebugScoreView.DebugAsync(Arg(3), Arg(4), Arg(5), Arg(6));
                await Task.Delay(800);
                Snap(Content(), Out("render_score.png"));
                return true;
            }

            case "--render-calendar":
            {
                var w = new Views.PracticeCalendarWindow { Width = 920, Height = 650 };
                w.Show();
                await Task.Delay(1500);
                Snap(w, Out("render_calendar.png"));
                return true;
            }

            case "--render-eval":
            {
                var songs = await new Services.DatabaseService().LoadSongsAsync();
                var song = songs.FirstOrDefault(x => x.BestScore > 0) ?? songs.FirstOrDefault()
                           ?? new Models.SongModel { Id = "test_song", Title = "Ether", Artist = "Ave Mujica", Tier = "SPECIAL", Level = 30 };
                var w = new Views.EvaluationWindow(song) { Width = 820, Height = 660 };
                w.Show();
                await Task.Delay(1500);
                Snap(w, Out("render_eval.png"));
                return true;
            }

            case "--render-add-song":
            {
                var w = new Views.AddSongWindow { Width = 840, Height = 620 };
                w.Show();
                await Task.Delay(800);
                string query = a.Length > 2 ? a[2].Trim().Trim('"', '\'') : "";
                if (query.Length > 0)
                {
                    w.txtQuery.Text = query;
                    w.btnSearch.RaiseEvent(new RoutedEventArgs(System.Windows.Controls.Primitives.ButtonBase.ClickEvent));
                    for (int i = 0; i < 25 && w.scrollResults.Visibility != Visibility.Visible; i++) await Task.Delay(500);
                    await Task.Delay(600);
                }
                else await Task.Delay(500);
                Snap(w, Out("render_add_song.png"));
                return true;
            }
        }
        return false;
    }

    private static void Snap(FrameworkElement v, string path) => Snap(v, v.ActualWidth, v.ActualHeight, path);

    private static void Snap(FrameworkElement v, double w, double h, string path)
    {
        v.UpdateLayout();
        var rtb = new RenderTargetBitmap(Math.Max(1, (int)w), Math.Max(1, (int)h), 96, 96, PixelFormats.Pbgra32);
        rtb.Render(v);
        var enc = new PngBitmapEncoder();
        enc.Frames.Add(BitmapFrame.Create(rtb));
        using var st = File.Create(path);
        enc.Save(st);
    }
}
