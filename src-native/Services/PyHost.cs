using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Text;
using System.Text.Json;
using System.Threading;
using System.Threading.Tasks;

namespace BassStation.Services;

/// <summary>
/// Runs the ML backend scripts (score_format, eval_session, performance_evaluator) in resident Python workers
/// (backend/worker.py), so numpy / librosa / torch load once per session instead of on every call. Up to
/// <see cref="MaxWorkers"/> run side by side (an evaluation doesn't block a tempo change); a request that
/// times out kills its worker. When no worker can start, the script runs as a one-off process as before.
/// </summary>
public static class PyHost
{
    private const int MaxWorkers = 3;
    private static readonly SemaphoreSlim Slots = new(MaxWorkers);
    private static readonly object Gate = new();
    private static readonly Stack<Worker> Idle = new();
    private static readonly List<Worker> All = new();
    private static int _nextId;

    /// <summary>Starts one worker in the background (called at launch, during the boot animation).</summary>
    public static void Warm() => Task.Run(() =>
    {
        var w = Worker.Start();
        if (w != null) lock (Gate) Idle.Push(w);
    });

    /// <summary>Stops every worker (application exit). Workers also exit by themselves when their stdin closes.</summary>
    public static void Shutdown()
    {
        lock (Gate)
        {
            foreach (var w in All.ToArray()) w.Kill();
            All.Clear();
            Idle.Clear();
        }
    }

    /// <summary>Runs <c>python &lt;script&gt; &lt;args&gt;</c> and returns the JSON object on its last stdout line.</summary>
    public static JsonElement? Run(string script, TimeSpan timeout, params string[] args)
    {
        string? stdout = RunText(script, timeout, args);
        return stdout == null ? null : LastJson(stdout);
    }

    public static Task<JsonElement?> RunAsync(string script, TimeSpan timeout, params string[] args) =>
        Task.Run(() => Run(script, timeout, args));

    public static JsonElement? LastJson(string stdout)
    {
        string? line = stdout.Split('\n').Select(l => l.Trim()).LastOrDefault(l => l.StartsWith('{'));
        if (line == null) return null;
        try
        {
            using var doc = JsonDocument.Parse(line);
            return doc.RootElement.Clone();
        }
        catch (JsonException) { return null; }
    }

    private static string? RunText(string script, TimeSpan timeout, string[] args)
    {
        Slots.Wait();
        Worker? w = null;
        try
        {
            lock (Gate) if (Idle.Count > 0) w = Idle.Pop();
            w ??= Worker.Start();
            if (w == null) return RunOnce(script, timeout, args);
            string? res = w.Call(Interlocked.Increment(ref _nextId), script, args, timeout);
            if (res == null) { w.Kill(); w = null; }
            return res;
        }
        finally
        {
            if (w != null) lock (Gate) Idle.Push(w);
            Slots.Release();
        }
    }

    /// <summary>The old way: a fresh interpreter for one call.</summary>
    private static string? RunOnce(string script, TimeSpan timeout, string[] args)
    {
        var psi = Psi(AppPaths.Script(script));
        foreach (var a in args) psi.ArgumentList.Add(a);
        try
        {
            using var p = Process.Start(psi);
            if (p == null) return null;
            p.ErrorDataReceived += (_, _) => { };
            p.BeginErrorReadLine();
            var outTask = p.StandardOutput.ReadToEndAsync();
            if (!p.WaitForExit((int)timeout.TotalMilliseconds)) { try { p.Kill(true); } catch { } return null; }
            return outTask.Result;
        }
        catch (Exception ex) { Debug.WriteLine(ex); return null; }
    }

    private static ProcessStartInfo Psi(string script)
    {
        var psi = new ProcessStartInfo
        {
            FileName = AppPaths.PythonMl,
            UseShellExecute = false,
            RedirectStandardInput = true,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
            CreateNoWindow = true,
            StandardOutputEncoding = Encoding.UTF8,
            StandardErrorEncoding = Encoding.UTF8,
            WorkingDirectory = AppPaths.Backend,
        };
        psi.ArgumentList.Add(script);
        psi.EnvironmentVariables["PYTHONUTF8"] = "1";
        psi.EnvironmentVariables["PYTHONIOENCODING"] = "utf-8";
        return psi;
    }

    private sealed class Worker
    {
        private readonly Process _p;

        private Worker(Process p) { _p = p; }

        public static Worker? Start()
        {
            string script = AppPaths.Script("worker.py");
            if (!File.Exists(script)) return null;
            try
            {
                var psi = Psi(script);
                psi.StandardInputEncoding = new UTF8Encoding(false);
                var p = Process.Start(psi);
                if (p == null) return null;
                p.ErrorDataReceived += (_, e) => { if (e.Data != null) Debug.WriteLine("[py] " + e.Data); };
                p.BeginErrorReadLine();
                var ready = p.StandardOutput.ReadLineAsync();
                if (!ready.Wait(TimeSpan.FromSeconds(30)) || ready.Result == null || !ready.Result.Contains("ready"))
                {
                    try { p.Kill(true); } catch { }
                    return null;
                }
                var w = new Worker(p);
                lock (Gate) All.Add(w);
                return w;
            }
            catch (Exception ex) { Debug.WriteLine(ex); return null; }
        }

        /// <summary>Captured stdout of the script, or null on timeout / a dead worker.</summary>
        public string? Call(int id, string script, string[] args, TimeSpan timeout)
        {
            if (_p.HasExited) return null;
            try
            {
                _p.StandardInput.WriteLine(JsonSerializer.Serialize(new { id, script, args }));
                _p.StandardInput.Flush();
                var deadline = DateTime.UtcNow + timeout;
                while (true)
                {
                    var left = deadline - DateTime.UtcNow;
                    if (left <= TimeSpan.Zero) return null;
                    var line = _p.StandardOutput.ReadLineAsync();
                    if (!line.Wait(left) || line.Result == null) return null;
                    using var doc = JsonDocument.Parse(line.Result);
                    var r = doc.RootElement;
                    if (r.TryGetProperty("id", out var rid) && rid.ValueKind == JsonValueKind.Number && rid.GetInt32() == id)
                        return r.TryGetProperty("out", out var o) ? o.GetString() ?? "" : "";
                }
            }
            catch (Exception ex) when (ex is IOException or JsonException or InvalidOperationException or AggregateException)
            {
                Debug.WriteLine(ex);
                return null;
            }
        }

        public void Kill()
        {
            try { if (!_p.HasExited) _p.Kill(true); } catch { }
            lock (Gate) All.Remove(this);
        }
    }
}
