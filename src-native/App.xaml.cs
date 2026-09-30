using System;
using System.IO;
using System.Windows;

namespace BassStation;

public partial class App : Application
{
    protected override async void OnStartup(StartupEventArgs e)
    {
        base.OnStartup(e);
        Resources["RoundFont"] = Views.ScoreCanvas.Round;

        // no dashed keyboard-focus rectangles (they appear after pressing Alt)
        EventManager.RegisterClassHandler(typeof(FrameworkElement), FrameworkElement.LoadedEvent,
            new RoutedEventHandler((s, _) => { if (s is FrameworkElement fe) fe.FocusVisualStyle = null; }));

        AppDomain.CurrentDomain.UnhandledException += (_, args) =>
        {
            try { File.WriteAllText("crash.log", args.ExceptionObject.ToString()); } catch { }
        };
        DispatcherUnhandledException += (_, args) =>
        {
            try { File.WriteAllText("crash_dispatcher.log", args.Exception.ToString()); } catch { }
        };

        Exit += (_, _) => Services.PyHost.Shutdown();
        var mainWindow = new MainWindow();
        if (await DebugTools.RenderHarness.TryRunAsync(mainWindow, e.Args))
        {
            Shutdown(0);
            return;
        }
        Services.PyHost.Warm();
        mainWindow.Show();
    }
}
