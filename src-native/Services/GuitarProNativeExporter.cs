using System;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Threading.Tasks;

namespace BassStation.Services;

public static class GuitarProNativeExporter
{
    public static string GetGuitarProExecutablePath()
    {
        if (AppPaths.GuitarPro is { } configured && File.Exists(configured)) return configured;
        try
        {
            using var key = Microsoft.Win32.Registry.ClassesRoot.OpenSubKey(@"Guitar Pro 8.AssocFile.gp\shell\open\command");
            if (key != null)
            {
                string cmd = key.GetValue("")?.ToString() ?? "";
                int firstQ = cmd.IndexOf('"');
                int secondQ = firstQ >= 0 ? cmd.IndexOf('"', firstQ + 1) : -1;
                if (firstQ >= 0 && secondQ > firstQ)
                {
                    string path = cmd.Substring(firstQ + 1, secondQ - firstQ - 1);
                    if (File.Exists(path)) return path;
                }
            }
        }
        catch { }

        string[] candidates = {
            @"C:\Program Files\Arobas Music\Guitar Pro 8\GuitarPro.exe",
            @"C:\Program Files (x86)\Arobas Music\Guitar Pro 8\GuitarPro.exe",
            @"C:\Program Files\Arobas Music\Guitar Pro 7\GuitarPro.exe"
        };
        foreach (var p in candidates)
        {
            if (File.Exists(p)) return p;
        }
        return "";
    }

    public static async Task<string?> EnsureOrExportPdfAsync(string gpPath)
    {
        if (string.IsNullOrEmpty(gpPath) || !File.Exists(gpPath)) return null;

        string dir = Path.GetDirectoryName(gpPath) ?? "";
        if (!Directory.Exists(dir)) return null;

        // 1. Check if an authentic companion PDF (>=30KB) already exists in the folder
        var existingPdfs = Directory.GetFiles(dir, "*.pdf")
            .Where(p => new FileInfo(p).Length >= 30000)
            .OrderByDescending(p => File.GetLastWriteTime(p))
            .ToArray();

        if (existingPdfs.Length > 0)
        {
            return existingPdfs[0];
        }

        // 2. Auto-engrave authentic vector PDF using Verovio backend
        string? generatedPdf = await Task.Run(() =>
        {
            var r = TabCli.Run(TimeSpan.FromSeconds(60), "ensure-pdf", "", "", "", gpPath);
            return r.Ok ? TabCli.Str(r.Root, "pdf_path") : null;
        });

        if (!string.IsNullOrEmpty(generatedPdf) && File.Exists(generatedPdf))
        {
            return generatedPdf;
        }

        // 3. Fallback: Open score directly in Guitar Pro 8 for native viewing
        string gpExe = GetGuitarProExecutablePath();
        if (string.IsNullOrEmpty(gpExe)) return null;

        await Task.Run(() =>
        {
            try
            {
                Process.Start(new ProcessStartInfo
                {
                    FileName = gpExe,
                    Arguments = $"--open \"{gpPath}\"",
                    UseShellExecute = true
                });
            }
            catch { }
        });

        return null;
    }
}
