using System;
using System.Collections.Generic;
using System.IO;
using System.Threading.Tasks;
using NAudio.CoreAudioApi;
using NAudio.Wave;

namespace BassStation.Services;

/// <summary>
/// One evaluation take: plays the session file (count-in + backing + metronome) on the default output
/// while recording the chosen input. The recording starts first; RecordOffset is how much input had been
/// captured when playback started, so recording time r corresponds to session time r - RecordOffset
/// (plus the output device latency, which the evaluator's aligner absorbs).
/// </summary>
public sealed class TakeRecorder : IDisposable
{
    private const string DeviceFile = @"E:\BassStation\cache\eval_session\input_device.txt";

    private WasapiCapture? _capture;
    private WaveFileWriter? _writer;
    private WasapiOut? _output;
    private WaveFileReader? _reader;
    private readonly object _lock = new();
    private long _capturedBytes;
    private bool _finishing;
    private bool _cancelled;

    public string OutputPath { get; }
    public double RecordOffset { get; private set; }
    public TimeSpan Position => _reader?.CurrentTime ?? TimeSpan.Zero;
    public TimeSpan Duration => _reader?.TotalTime ?? TimeSpan.Zero;

    /// <summary>Raised once on the capture thread when the take has been written (not raised after Cancel).</summary>
    public event Action<TakeRecorder>? Completed;

    public TakeRecorder(string outputPath) => OutputPath = outputPath;

    public static List<(string Id, string Name)> InputDevices()
    {
        var list = new List<(string, string)>();
        using var en = new MMDeviceEnumerator();
        foreach (var d in en.EnumerateAudioEndPoints(DataFlow.Capture, DeviceState.Active))
            list.Add((d.ID, d.FriendlyName));
        return list;
    }

    public static string? SavedDeviceId
    {
        get
        {
            try { return File.Exists(DeviceFile) ? File.ReadAllText(DeviceFile).Trim() : null; }
            catch { return null; }
        }
        set
        {
            try
            {
                Directory.CreateDirectory(Path.GetDirectoryName(DeviceFile)!);
                File.WriteAllText(DeviceFile, value ?? "");
            }
            catch { }
        }
    }

    private static MMDevice ResolveInput(string? id)
    {
        using var en = new MMDeviceEnumerator();
        if (!string.IsNullOrEmpty(id))
        {
            try
            {
                var d = en.GetDevice(id);
                if (d.State == DeviceState.Active) return d;
            }
            catch { }
        }
        return en.GetDefaultAudioEndpoint(DataFlow.Capture, Role.Multimedia);
    }

    public async Task StartAsync(string sessionWav, string? inputId)
    {
        _capture = new WasapiCapture(ResolveInput(inputId), true, 20);
        _writer = new WaveFileWriter(OutputPath, _capture.WaveFormat);
        _capture.DataAvailable += (_, a) =>
        {
            lock (_lock)
            {
                _writer?.Write(a.Buffer, 0, a.BytesRecorded);
                _capturedBytes += a.BytesRecorded;
            }
        };
        _capture.RecordingStopped += (_, _) =>
        {
            lock (_lock)
            {
                _writer?.Dispose();
                _writer = null;
            }
            _capture?.Dispose();
            if (!_cancelled) Completed?.Invoke(this);
        };
        _capture.StartRecording();
        await Task.Delay(400);          // let the input stream settle before the count-in

        _reader = new WaveFileReader(sessionWav);
        // play in the device's own mix format (Windows' automatic 44.1 -> 48 kHz conversion produced noise)
        using var en = new MMDeviceEnumerator();
        var dev = en.GetDefaultAudioEndpoint(DataFlow.Render, Role.Multimedia);
        var mix = dev.AudioClient.MixFormat;
        ISampleProvider src = _reader.ToSampleProvider();
        if (mix.SampleRate != src.WaveFormat.SampleRate)
            src = new NAudio.Wave.SampleProviders.WdlResamplingSampleProvider(src, mix.SampleRate);
        if (mix.Channels > src.WaveFormat.Channels)
            src = new NAudio.Wave.SampleProviders.MultiplexingSampleProvider(new[] { src }, mix.Channels);
        _output = new WasapiOut(dev, AudioClientShareMode.Shared, true, 60);
        _output.Init(src);
        _output.PlaybackStopped += (_, _) => FinishAfterTail();
        lock (_lock)
        {
            RecordOffset = (double)_capturedBytes / _capture.WaveFormat.AverageBytesPerSecond;
            _output.Play();
        }
    }

    private async void FinishAfterTail()
    {
        if (_finishing) return;
        _finishing = true;
        await Task.Delay(_cancelled ? 0 : 600);     // keep the last note's ring-out
        try { _capture?.StopRecording(); } catch { }
    }

    /// <summary>Ends the take early; the part played so far is still evaluated.</summary>
    public void Stop()
    {
        if (_output != null && _output.PlaybackState != PlaybackState.Stopped) _output.Stop();
        else FinishAfterTail();
    }

    /// <summary>Stops without raising Completed (window closed mid-take).</summary>
    public void Cancel()
    {
        _cancelled = true;
        Stop();
    }

    public void Dispose()
    {
        _cancelled = true;
        try { _output?.Stop(); } catch { }
        _output?.Dispose();
        _reader?.Dispose();
        try { _capture?.StopRecording(); } catch { }
    }
}
