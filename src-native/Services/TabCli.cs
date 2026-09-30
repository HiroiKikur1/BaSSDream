using System;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Text.Json;

namespace BassStation.Services;

/// <summary>Runs backend/tab_cli.py actions (backend Python) and reads the JSON result line.</summary>
public static class TabCli
{
    public const string CliPath = @"E:\BassStation\backend\tab_cli.py";

    public record Result(bool Ok, JsonElement Root, string Error);

    // stderr is drained concurrently (the CLI routes all its logging there; an unread pipe fills up
    // and deadlocks long imports), arguments are passed verbatim.
    public static Result Run(TimeSpan timeout, params string[] args)
    {
        if (!File.Exists(CliPath)) return new Result(false, default, "tab_cli.py 缺失");
        var psi = new ProcessStartInfo
        {
            FileName = "python",
            RedirectStandardOutput = true,
            RedirectStandardError = true,
            UseShellExecute = false,
            CreateNoWindow = true,
            StandardOutputEncoding = System.Text.Encoding.UTF8,
            StandardErrorEncoding = System.Text.Encoding.UTF8
        };
        psi.ArgumentList.Add(CliPath);
        foreach (var a in args) psi.ArgumentList.Add(a ?? "");
        psi.EnvironmentVariables["PYTHONUTF8"] = "1";
        psi.EnvironmentVariables["PYTHONIOENCODING"] = "utf-8";

        using var proc = Process.Start(psi);
        if (proc == null) return new Result(false, default, "无法启动 Python");
        var stdoutTask = proc.StandardOutput.ReadToEndAsync();
        var stderrTask = proc.StandardError.ReadToEndAsync();
        if (!proc.WaitForExit((int)timeout.TotalMilliseconds))
        {
            try { proc.Kill(entireProcessTree: true); } catch { }
            return new Result(false, default, "超时");
        }
        string stdout = stdoutTask.Result;
        _ = stderrTask.Result;

        // the result is the last JSON line on stdout
        foreach (var line in stdout.Split('\n', StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries).Reverse())
        {
            if (!line.StartsWith('{')) continue;
            try
            {
                using var doc = JsonDocument.Parse(line);
                var root = doc.RootElement.Clone();
                bool ok = Str(root, "status") == "success";
                string err = Str(root, "message") is { Length: > 0 } m ? m : Str(root, "error");
                return new Result(ok, root, err);
            }
            catch (JsonException) { }
        }
        return new Result(false, default, "无返回");
    }

    public static string Str(JsonElement el, string name, string fallback = "") =>
        el.ValueKind == JsonValueKind.Object && el.TryGetProperty(name, out var v)
            ? v.ValueKind switch { JsonValueKind.String => v.GetString() ?? fallback, JsonValueKind.Number => v.GetRawText(), _ => fallback }
            : fallback;
}
