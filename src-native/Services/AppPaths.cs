using System;
using System.Collections.Generic;
using System.IO;
using System.Text.Json;

namespace BassStation.Services;

/// <summary>
/// Machine paths, shared with the backend (backend/paths.py). Everything derives from the project root — the folder
/// that holds backend\tab_cli.py, found upward from the exe — and bassdream.json in that root overrides single
/// entries (see bassdream.example.json).
/// </summary>
public static class AppPaths
{
    private static readonly Dictionary<string, string> Cfg = LoadConfig();

    public static string Root { get; } = Get("root") ?? FindRoot();
    public static string Backend => Path.Combine(Root, "backend");
    public static string Tabs => Get("tabs") ?? Path.Combine(Root, "tabs");
    public static string Cache => Get("cache") ?? Path.Combine(Root, "cache");
    public static string Assets => Path.Combine(Root, "assets");
    public static string Db => Get("db") ?? Path.Combine(Backend, "data.db");
    public static string Covers => Path.Combine(Cache, "covers");

    /// <summary>Interpreter for the light backend scripts (tab_cli, scanner).</summary>
    public static string Python => Get("python") ?? "python";

    /// <summary>Interpreter with torch / librosa (evaluation, score audio, transcription).</summary>
    public static string PythonMl
    {
        get
        {
            if (Get("python_ml") is { } p) return p;
            string d = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
                                    @"Programs\Python\Python311\python.exe");
            return File.Exists(d) ? d : "python";
        }
    }

    public static string Script(string name) => Path.Combine(Backend, name);
    public static string CachePath(params string[] parts) => Path.Combine(Cache, Path.Combine(parts));

    /// <summary>Guitar Pro executable from the config, or null (callers fall back to the registry / install folders).</summary>
    public static string? GuitarPro => Get("guitar_pro");

    private static string? Get(string key) => Cfg.TryGetValue(key, out var v) && !string.IsNullOrWhiteSpace(v) ? v : null;

    private static string FindRoot()
    {
        for (var d = new DirectoryInfo(AppContext.BaseDirectory); d != null; d = d.Parent)
            if (File.Exists(Path.Combine(d.FullName, "backend", "tab_cli.py"))) return d.FullName;
        return @"E:\BassStation";
    }

    private static Dictionary<string, string> LoadConfig()
    {
        var map = new Dictionary<string, string>();
        string? path = Environment.GetEnvironmentVariable("BASSDREAM_CONFIG");
        if (path == null)
            for (var d = new DirectoryInfo(AppContext.BaseDirectory); d != null && path == null; d = d.Parent)
                if (File.Exists(Path.Combine(d.FullName, "bassdream.json"))) path = Path.Combine(d.FullName, "bassdream.json");
        if (path == null || !File.Exists(path)) return map;
        try
        {
            using var doc = JsonDocument.Parse(File.ReadAllText(path));
            foreach (var p in doc.RootElement.EnumerateObject())
                if (p.Value.ValueKind == JsonValueKind.String) map[p.Name] = p.Value.GetString()!;
        }
        catch (Exception ex) when (ex is IOException or JsonException) { }
        return map;
    }
}
