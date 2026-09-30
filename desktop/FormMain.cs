using System;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Net.Http;
using System.Threading.Tasks;
using System.Windows.Forms;
using Microsoft.Web.WebView2.Core;
using Microsoft.Web.WebView2.WinForms;

namespace BassStation;

public class FormMain : Form
{
    private readonly WebView2 _webView;
    private readonly Label _lblStatus;
    private readonly Panel _pnlLoading;
    private Process? _backendProcess;
    private bool _backendStartedByUs = false;
    private const string AppUrl = "http://localhost:8080";
    private const string HealthUrl = "http://localhost:8080/api/songs";

    public FormMain()
    {
        // Window Configuration
        Text = "BassStation 2.0 - J-Rock & Low-End Stage";
        Width = 1440;
        Height = 900;
        MinimumSize = new Size(1200, 750);
        StartPosition = FormStartPosition.CenterScreen;
        BackColor = Color.FromArgb(244, 246, 251);

        // Try load Icon
        try
        {
            var icoPath = Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "BassStation.ico");
            if (File.Exists(icoPath))
            {
                Icon = new Icon(icoPath);
            }
        }
        catch
        {
            // Ignore icon failure
        }

        // Loading Overlay
        _pnlLoading = new Panel
        {
            Dock = DockStyle.Fill,
            BackColor = Color.FromArgb(244, 246, 251),
        };

        _lblStatus = new Label
        {
            Text = "正在启动 BassStation 2.0 原生音频核心...",
            Font = new Font("Segoe UI", 12, FontStyle.Bold),
            ForeColor = Color.FromArgb(255, 45, 117),
            AutoSize = false,
            TextAlign = ContentAlignment.MiddleCenter,
            Dock = DockStyle.Fill
        };
        _pnlLoading.Controls.Add(_lblStatus);
        Controls.Add(_pnlLoading);

        // WebView2 Setup
        _webView = new WebView2
        {
            Dock = DockStyle.Fill,
            Visible = false
        };
        Controls.Add(_webView);

        Shown += async (s, e) => await InitializeAppAsync();
        FormClosing += FormMain_FormClosing;
    }

    private async Task InitializeAppAsync()
    {
        try
        {
            _lblStatus.Text = "检查本地音轨工作站引擎...";
            await EnsureBackendRunningAsync();

            _lblStatus.Text = "加载硬件加速渲染引擎 (WebView2)...";
            
            // Set User Data Folder for WebView2 cache
            var appData = Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData);
            var userDataFolder = Path.Combine(appData, "BassStation", "WebView2Data");
            Directory.CreateDirectory(userDataFolder);

            var env = await CoreWebView2Environment.CreateAsync(null, userDataFolder);
            await _webView.EnsureCoreWebView2Async(env);

            // Configure WebView2 Settings for clean native desktop app feel
            _webView.CoreWebView2.Settings.IsStatusBarEnabled = false;
            _webView.CoreWebView2.Settings.AreDefaultContextMenusEnabled = false;
            _webView.CoreWebView2.Settings.AreDevToolsEnabled = false;
            _webView.CoreWebView2.Settings.IsZoomControlEnabled = false;

            // Handle Navigation Completed
            _webView.NavigationCompleted += (s, e) =>
            {
                if (e.IsSuccess)
                {
                    _pnlLoading.Visible = false;
                    _webView.Visible = true;
                    _webView.Focus();
                }
                else
                {
                    _lblStatus.Text = "加载失败，请重试。";
                }
            };

            _lblStatus.Text = "进入舞台...";
            _webView.CoreWebView2.Navigate(AppUrl);
        }
        catch (Exception ex)
        {
            _lblStatus.Text = $"启动异常: {ex.Message}";
            MessageBox.Show($"启动失败: {ex.Message}", "BassStation 错误", MessageBoxButtons.OK, MessageBoxIcon.Error);
        }
    }

    private async Task EnsureBackendRunningAsync()
    {
        using var client = new HttpClient { Timeout = TimeSpan.FromSeconds(1) };
        for (int i = 0; i < 3; i++)
        {
            try
            {
                var res = await client.GetAsync(HealthUrl);
                if (res.IsSuccessStatusCode)
                {
                    return; // Already running!
                }
            }
            catch
            {
                // Continue
            }
        }

        // Start backend silently
        _lblStatus.Text = "正在静默启动 Python 后端服务...";
        StartBackendProcess();

        // Wait up to 15 seconds for backend to respond
        for (int i = 0; i < 45; i++)
        {
            await Task.Delay(350);
            try
            {
                var res = await client.GetAsync(HealthUrl);
                if (res.IsSuccessStatusCode)
                {
                    return;
                }
            }
            catch
            {
                // Still spinning up
            }
        }

        throw new TimeoutException("后端服务启动超时，请检查 app.py 日志。");
    }

    private void StartBackendProcess()
    {
        var rootDir = Path.GetFullPath(Path.Combine(AppDomain.CurrentDomain.BaseDirectory, @"..\..\..\..\backend"));
        if (!Directory.Exists(rootDir))
        {
            rootDir = @"E:\BassStation\backend";
        }

        var appPy = Path.Combine(rootDir, "app.py");
        if (!File.Exists(appPy))
        {
            throw new FileNotFoundException($"找不到后端 app.py: {appPy}");
        }

        var pythonw = @"C:\Python314\pythonw.exe";
        if (!File.Exists(pythonw))
        {
            pythonw = "pythonw.exe";
        }

        var psi = new ProcessStartInfo
        {
            FileName = pythonw,
            Arguments = $"\"{appPy}\"",
            WorkingDirectory = rootDir,
            CreateNoWindow = true,
            UseShellExecute = false
        };

        _backendProcess = Process.Start(psi);
        _backendStartedByUs = true;
    }

    private void FormMain_FormClosing(object? sender, FormClosingEventArgs e)
    {
        // If we launched the backend, cleanly terminate it
        if (_backendStartedByUs && _backendProcess != null && !_backendProcess.HasExited)
        {
            try
            {
                _backendProcess.Kill();
                _backendProcess.Dispose();
            }
            catch
            {
                // Ignore
            }
        }
    }
}
