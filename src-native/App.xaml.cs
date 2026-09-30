using System;
using System.IO;
using System.Linq;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Media;
using System.Windows.Media.Imaging;

namespace BassStation;

public partial class App : Application
{
    protected override async void OnStartup(StartupEventArgs e)
    {
        base.OnStartup(e);

        // no dashed keyboard-focus rectangles (they appear after pressing Alt)
        EventManager.RegisterClassHandler(typeof(FrameworkElement), FrameworkElement.LoadedEvent,
            new RoutedEventHandler((s, _) => { if (s is FrameworkElement fe) fe.FocusVisualStyle = null; }));

        AppDomain.CurrentDomain.UnhandledException += (s, args) =>
        {
            try
            {
                File.WriteAllText("crash.log", args.ExceptionObject.ToString());
            }
            catch { }
        };

        DispatcherUnhandledException += (s, args) =>
        {
            try
            {
                File.WriteAllText("crash_dispatcher.log", args.Exception.ToString());
            }
            catch { }
        };

        static string CleanPath(string[] args, int index, string fallback) =>
            args.Length > index ? args[index].Trim().Trim('"', '\'') : fallback;

        var mainWindow = new MainWindow();

        if (e.Args.Length > 0 && e.Args[0] == "--perf-clicks")
        {
            string outPath = CleanPath(e.Args, 1, "perf.txt");
            mainWindow.Show();
            await Task.Delay(2500);
            string report = await mainWindow.DebugPerfClicks(TimeSpan.FromSeconds(6), e.Args.Length > 2 ? e.Args[2] : "songs");
            File.WriteAllText(outPath, report);
            Shutdown(0);
            return;
        }

        if (e.Args.Length > 0 && (e.Args[0] == "--render-filter" || e.Args[0] == "--render-fx" || e.Args[0].StartsWith("--render-sheet-")))
        {
            string outPath = CleanPath(e.Args, 1, "render.png");
            mainWindow.Show();
            await Task.Delay(2000);
            if (e.Args[0] == "--render-filter") mainWindow.DebugOpenFilter();
            else if (e.Args[0].StartsWith("--render-sheet-"))
            {
                // optional: BASSDREAM_SONG selects the song first
                if (Environment.GetEnvironmentVariable("BASSDREAM_SONG") is { Length: > 0 } pick) mainWindow.SelectSongByTitle(pick);
                mainWindow.DebugOpenSheet(e.Args[0].Substring(15));
            }
            else mainWindow.DebugBurst();
            await Task.Delay(e.Args[0] == "--render-fx" ? 180 : 1200);
            // BASSDREAM_MENU=section: also render the eval sheet's practice-range menu to <out>.menu.png
            if (Environment.GetEnvironmentVariable("BASSDREAM_MENU") == "section" && mainWindow.DebugEvalSheet != null)
            {
                var menu = await mainWindow.DebugEvalSheet.DebugOpenSectionMenuAsync();
                await Task.Delay(400);
                var rtbM = new RenderTargetBitmap((int)menu.ActualWidth + 12, (int)menu.ActualHeight + 12, 96, 96, PixelFormats.Pbgra32);
                rtbM.Render(menu);
                var encM = new PngBitmapEncoder();
                encM.Frames.Add(BitmapFrame.Create(rtbM));
                using (var stM = File.Create(outPath + ".menu.png")) encM.Save(stM);
            }
            mainWindow.UpdateLayout();
            var rtb2 = new RenderTargetBitmap((int)((FrameworkElement)mainWindow.Content).ActualWidth, (int)((FrameworkElement)mainWindow.Content).ActualHeight, 96, 96, PixelFormats.Pbgra32);
            rtb2.Render((System.Windows.Media.Visual)mainWindow.Content);
            var enc2 = new PngBitmapEncoder();
            enc2.Frames.Add(BitmapFrame.Create(rtb2));
            using (var st = File.Create(outPath)) enc2.Save(st);
            Shutdown(0);
            return;
        }

        if (e.Args.Length > 0 && e.Args[0] == "--render-test")
        {
            string outPath = CleanPath(e.Args, 1, "render_test.png");
            mainWindow.Show();

            // Wait for DB data to load and UI to settle
            await Task.Delay(2000);
            mainWindow.UpdateLayout();

            int w = (int)((FrameworkElement)mainWindow.Content).ActualWidth;
            int h = (int)((FrameworkElement)mainWindow.Content).ActualHeight;
            if (w <= 0) w = 1400;
            if (h <= 0) h = 820;

            var rtb = new RenderTargetBitmap(w, h, 96, 96, PixelFormats.Pbgra32);
            rtb.Render((System.Windows.Media.Visual)mainWindow.Content);

            var encoder = new PngBitmapEncoder();
            encoder.Frames.Add(BitmapFrame.Create(rtb));
            using (var stream = File.Create(outPath))
            {
                encoder.Save(stream);
            }

            Shutdown(0);
            return;
        }

        // Real in-app take: --debug-take out.png <seconds> <song title>
        if (e.Args.Length > 3 && e.Args[0] == "--debug-take")
        {
            string outPath = CleanPath(e.Args, 1, "debug_take.png");
            mainWindow.Show();
            await Task.Delay(1500);
            mainWindow.SelectSongByTitle(string.Join(" ", e.Args.Skip(3)).Trim());
            await Task.Delay(300);
            mainWindow.DebugOpenSheet("eval");
            await Task.Delay(800);
            string? take = mainWindow.DebugEvalSheet == null ? null
                : await mainWindow.DebugEvalSheet.DebugTakeAsync(double.Parse(e.Args[2], System.Globalization.CultureInfo.InvariantCulture));
            File.WriteAllText(outPath + ".txt", take ?? "no take");
            await Task.Delay(800);
            mainWindow.UpdateLayout();
            var contentT = (FrameworkElement)mainWindow.Content;
            var rtbT = new RenderTargetBitmap((int)contentT.ActualWidth, (int)contentT.ActualHeight, 96, 96, PixelFormats.Pbgra32);
            rtbT.Render(contentT);
            var encT = new PngBitmapEncoder();
            encT.Frames.Add(BitmapFrame.Create(rtbT));
            using (var st = File.Create(outPath)) encT.Save(st);
            Shutdown(0);
            return;
        }

        // Result screen from a saved report: --render-result out.png <report.json> <song title> [--report [scroll]]
        if (e.Args.Length > 3 && e.Args[0] == "--render-result")
        {
            string outPath = CleanPath(e.Args, 1, "render_result.png");
            var rest = e.Args.Skip(3).ToList();
            int ri = rest.IndexOf("--report");
            double scroll = 0;
            bool report = ri >= 0;
            if (report)
            {
                if (ri + 1 < rest.Count && double.TryParse(rest[ri + 1], System.Globalization.NumberStyles.Float, System.Globalization.CultureInfo.InvariantCulture, out var sc)) { scroll = sc; rest.RemoveAt(ri + 1); }
                rest.RemoveAt(ri);
            }
            mainWindow.Show();
            await Task.Delay(1500);
            mainWindow.SelectSongByTitle(string.Join(" ", rest).Trim());
            await Task.Delay(300);
            mainWindow.DebugShowResultFromReport(e.Args[2]);
            await Task.Delay(900);
            if (report && mainWindow.DebugResultView != null)
            {
                mainWindow.DebugResultView.ShowReport();
                await Task.Delay(500);
                mainWindow.DebugResultView.DebugScroll(scroll);
                await Task.Delay(400);
                // BASSDREAM_REPLAY="<bar index>|mine|orig|ab|<loopback wav>": bar replay through the real output
                if (Environment.GetEnvironmentVariable("BASSDREAM_REPLAY") is { Length: > 0 } rp)
                {
                    var parts = rp.Split('|');
                    await mainWindow.DebugResultView.DebugReplayAsync(int.Parse(parts[0]), parts[1], parts[2]);
                }
            }
            mainWindow.UpdateLayout();
            var content = (FrameworkElement)mainWindow.Content;
            var rtbR = new RenderTargetBitmap((int)content.ActualWidth, (int)content.ActualHeight, 96, 96, PixelFormats.Pbgra32);
            rtbR.Render(content);
            var encR = new PngBitmapEncoder();
            encR.Frames.Add(BitmapFrame.Create(rtbR));
            using (var st = File.Create(outPath)) encR.Save(st);
            Shutdown(0);
            return;
        }

        // Real evaluation through the WPF entry: --render-eval-run out.png <audio> <song title>
        if (e.Args.Length > 3 && e.Args[0] == "--render-eval-run")
        {
            string outPath = CleanPath(e.Args, 1, "render_eval_run.png");
            mainWindow.Show();
            await Task.Delay(2000);
            mainWindow.SelectSongByTitle(string.Join(" ", e.Args.Skip(3)).Trim());
            await Task.Delay(500);
            mainWindow.DebugOpenSheet("eval");
            await Task.Delay(800);
            if (mainWindow.DebugEvalSheet != null)
                await mainWindow.DebugEvalSheet.DebugEvaluateAsync(e.Args[2], Environment.GetEnvironmentVariable("BASSDREAM_SECTION"),
                    double.TryParse(Environment.GetEnvironmentVariable("BASSDREAM_RATE"), System.Globalization.NumberStyles.Float,
                                    System.Globalization.CultureInfo.InvariantCulture, out var dr) ? dr : 1.0);
            await Task.Delay(600);
            mainWindow.UpdateLayout();
            var rtbE = new RenderTargetBitmap((int)((FrameworkElement)mainWindow.Content).ActualWidth, (int)((FrameworkElement)mainWindow.Content).ActualHeight, 96, 96, PixelFormats.Pbgra32);
            rtbE.Render((System.Windows.Media.Visual)mainWindow.Content);
            var encE = new PngBitmapEncoder();
            encE.Frames.Add(BitmapFrame.Create(rtbE));
            using (var st = File.Create(outPath)) encE.Save(st);
            Shutdown(0);
            return;
        }

        // Score view: --render-score out.png <song title> [bar beat string [play seconds]]
        if (e.Args.Length > 2 && e.Args[0] == "--render-score")
        {
            string outPath = CleanPath(e.Args, 1, "render_score.png");
            mainWindow.Show();
            await Task.Delay(2000);
            mainWindow.SelectSongByTitle(e.Args[2]);
            await Task.Delay(400);
            mainWindow.OpenScoreView(mainWindow.DebugSelectedSong!);
            int Arg(int i, int d) => e.Args.Length > i && int.TryParse(e.Args[i], out int v) ? v : d;
            // BASSDREAM_MIX="<wav>|rate|from|seconds|vb,vbass,vsynth,vclick": offline mix instead of a selection
            if (Environment.GetEnvironmentVariable("BASSDREAM_MIX") is { Length: > 0 } mix && mainWindow.DebugScoreView != null)
            {
                var m = mix.Split('|');
                double D(string x) => double.Parse(x, System.Globalization.CultureInfo.InvariantCulture);
                await mainWindow.DebugScoreView.DebugRenderAsync(m[0], D(m[1]), D(m[2]), D(m[3]), m[4].Split(',').Select(D).ToArray());
            }
            else if (mainWindow.DebugScoreView != null)
                await mainWindow.DebugScoreView.DebugAsync(Arg(3, 0), Arg(4, 0), Arg(5, 0), Arg(6, 0));
            await Task.Delay(800);
            mainWindow.UpdateLayout();
            var rtbS = new RenderTargetBitmap((int)((FrameworkElement)mainWindow.Content).ActualWidth, (int)((FrameworkElement)mainWindow.Content).ActualHeight, 96, 96, PixelFormats.Pbgra32);
            rtbS.Render((System.Windows.Media.Visual)mainWindow.Content);
            var encS = new PngBitmapEncoder();
            encS.Frames.Add(BitmapFrame.Create(rtbS));
            using (var st = File.Create(outPath)) encS.Save(st);
            Shutdown(0);
            return;
        }

        if (e.Args.Length > 0 && e.Args[0] == "--render-song")
        {
            string outPath = CleanPath(e.Args, 1, "render_song.png");
            string targetTitle = e.Args.Length > 2 ? string.Join(" ", e.Args.Skip(2)).Trim().Trim('"', '\'') : "";
            mainWindow.Show();

            await Task.Delay(2000);
            if (!string.IsNullOrEmpty(targetTitle))
            {
                mainWindow.SelectSongByTitle(targetTitle);
            }
            await Task.Delay(800);
            mainWindow.UpdateLayout();

            int w = (int)((FrameworkElement)mainWindow.Content).ActualWidth;
            int h = (int)((FrameworkElement)mainWindow.Content).ActualHeight;
            if (w <= 0) w = 1400;
            if (h <= 0) h = 820;

            var rtb = new RenderTargetBitmap(w, h, 96, 96, PixelFormats.Pbgra32);
            rtb.Render((System.Windows.Media.Visual)mainWindow.Content);

            var encoder = new PngBitmapEncoder();
            encoder.Frames.Add(BitmapFrame.Create(rtb));
            using (var stream = File.Create(outPath))
            {
                encoder.Save(stream);
            }

            Shutdown(0);
            return;
        }

        if (e.Args.Length > 0 && e.Args[0] == "--render-category")
        {
            string outPath = CleanPath(e.Args, 1, "render_category.png");
            string targetCat = e.Args.Length > 2 ? e.Args[2].Trim().Trim('"', '\'') : "Roselia";
            mainWindow.Show();

            await Task.Delay(2000);
            mainWindow.SelectCategory(targetCat);
            await Task.Delay(800);
            mainWindow.UpdateLayout();

            int w = (int)((FrameworkElement)mainWindow.Content).ActualWidth;
            int h = (int)((FrameworkElement)mainWindow.Content).ActualHeight;
            if (w <= 0) w = 1400;
            if (h <= 0) h = 820;

            var rtb = new RenderTargetBitmap(w, h, 96, 96, PixelFormats.Pbgra32);
            rtb.Render((System.Windows.Media.Visual)mainWindow.Content);

            var encoder = new PngBitmapEncoder();
            encoder.Frames.Add(BitmapFrame.Create(rtb));
            using (var stream = File.Create(outPath))
            {
                encoder.Save(stream);
            }

            Shutdown(0);
            return;
        }

        if (e.Args.Length > 0 && e.Args[0] == "--render-search")
        {
            string outPath = e.Args.Length > 1 ? e.Args[1] : "render_search.png";
            string searchWord = e.Args.Length > 2 ? e.Args[2] : "又三郎";
            mainWindow.Show();

            await Task.Delay(2000);
            mainWindow.txtSearch.Text = searchWord;
            await Task.Delay(800);
            mainWindow.UpdateLayout();

            int w = (int)((FrameworkElement)mainWindow.Content).ActualWidth;
            int h = (int)((FrameworkElement)mainWindow.Content).ActualHeight;
            if (w <= 0) w = 1440;
            if (h <= 0) h = 900;

            var rtb = new RenderTargetBitmap(w, h, 96, 96, PixelFormats.Pbgra32);
            rtb.Render((System.Windows.Media.Visual)mainWindow.Content);

            var encoder = new PngBitmapEncoder();
            encoder.Frames.Add(BitmapFrame.Create(rtb));
            using (var stream = File.Create(outPath))
            {
                encoder.Save(stream);
            }

            Shutdown(0);
            return;
        }

        if (e.Args.Length > 0 && e.Args[0] == "--render-calendar")
        {
            string outPath = e.Args.Length > 1 ? e.Args[1] : "render_calendar.png";
            var calWin = new Views.PracticeCalendarWindow
            {
                Width = 920,
                Height = 650
            };
            calWin.Show();

            await Task.Delay(1500);
            calWin.UpdateLayout();

            int w = (int)calWin.ActualWidth;
            int h = (int)calWin.ActualHeight;
            if (w <= 0) w = 920;
            if (h <= 0) h = 650;

            var rtb = new RenderTargetBitmap(w, h, 96, 96, PixelFormats.Pbgra32);
            rtb.Render(calWin);

            var encoder = new PngBitmapEncoder();
            encoder.Frames.Add(BitmapFrame.Create(rtb));
            using (var stream = File.Create(outPath))
            {
                encoder.Save(stream);
            }

            Shutdown(0);
            return;
        }

        if (e.Args.Length > 0 && e.Args[0] == "--render-eval")
        {
            string outPath = e.Args.Length > 1 ? e.Args[1] : "render_eval.png";
            var db = new Services.DatabaseService();
            var songs = await db.LoadSongsAsync();
            var song = songs.FirstOrDefault(s => s.BestScore.HasValue && s.BestScore > 0)
                       ?? songs.FirstOrDefault()
                       ?? new Models.SongModel { Id = "test_song", Title = "Ether", Artist = "Ave Mujica", Tier = "SPECIAL", Level = 30 };

            var evalWin = new Views.EvaluationWindow(song)
            {
                Width = 820,
                Height = 660
            };
            evalWin.Show();

            await Task.Delay(1500);
            evalWin.UpdateLayout();

            int w = (int)evalWin.ActualWidth;
            int h = (int)evalWin.ActualHeight;
            if (w <= 0) w = 820;
            if (h <= 0) h = 660;

            var rtb = new RenderTargetBitmap(w, h, 96, 96, PixelFormats.Pbgra32);
            rtb.Render(evalWin);

            var encoder = new PngBitmapEncoder();
            encoder.Frames.Add(BitmapFrame.Create(rtb));
            using (var stream = File.Create(outPath))
            {
                encoder.Save(stream);
            }

            Shutdown(0);
            return;
        }

        if (e.Args.Length > 0 && e.Args[0] == "--render-add-song")
        {
            string outPath = CleanPath(e.Args, 1, "render_add_song.png");
            string searchWord = e.Args.Length > 2 ? e.Args[2].Trim().Trim('"', '\'') : "";
            var addWin = new Views.AddSongWindow
            {
                Width = 840,
                Height = 620
            };
            addWin.Show();

            await Task.Delay(800);
            if (!string.IsNullOrEmpty(searchWord))
            {
                addWin.txtQuery.Text = searchWord;
                addWin.btnSearch.RaiseEvent(new RoutedEventArgs(System.Windows.Controls.Primitives.ButtonBase.ClickEvent));
                // Wait for background search to complete
                for (int i = 0; i < 25; i++)
                {
                    await Task.Delay(500);
                    if (addWin.scrollResults.Visibility == Visibility.Visible)
                        break;
                }
                await Task.Delay(600);
            }
            else
            {
                await Task.Delay(500);
            }

            addWin.UpdateLayout();

            int w = (int)addWin.ActualWidth;
            int h = (int)addWin.ActualHeight;
            if (w <= 0) w = 840;
            if (h <= 0) h = 620;

            var rtb = new RenderTargetBitmap(w, h, 96, 96, PixelFormats.Pbgra32);
            rtb.Render(addWin);

            var encoder = new PngBitmapEncoder();
            encoder.Frames.Add(BitmapFrame.Create(rtb));
            using (var stream = File.Create(outPath))
            {
                encoder.Save(stream);
            }

            Shutdown(0);
            return;
        }

        mainWindow.Show();
    }
}
