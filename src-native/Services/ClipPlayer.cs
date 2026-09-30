using System;
using System.Collections.Generic;
using NAudio.CoreAudioApi;
using NAudio.Wave;
using NAudio.Wave.SampleProviders;

namespace BassStation.Services;

/// <summary>
/// Plays short segments of audio files one after another (bar replay: my take, then the original bass),
/// converted to the output device's own mix format.
/// </summary>
public sealed class ClipPlayer : IDisposable
{
    public record Clip(string Path, double Start, double End);

    private WasapiOut? _out;
    private readonly List<IDisposable> _open = new();

    public event Action? Finished;
    public bool IsPlaying => _out?.PlaybackState == PlaybackState.Playing;

    public void Play(IReadOnlyList<Clip> clips, double gapSeconds = 0.35)
    {
        Stop();
        using var en = new MMDeviceEnumerator();
        var dev = en.GetDefaultAudioEndpoint(DataFlow.Render, Role.Multimedia);
        var mix = dev.AudioClient.MixFormat;
        var parts = new List<ISampleProvider>();
        foreach (var c in clips)
        {
            WaveStream reader = c.Path.EndsWith(".wav", StringComparison.OrdinalIgnoreCase)
                ? new WaveFileReader(c.Path) : new MediaFoundationReader(c.Path);
            _open.Add(reader);
            reader.CurrentTime = TimeSpan.FromSeconds(Math.Max(0, c.Start));
            ISampleProvider sp = reader.ToSampleProvider();
            if (sp.WaveFormat.Channels == 1) sp = new MonoToStereoSampleProvider(sp);
            else if (sp.WaveFormat.Channels > 2) sp = new MultiplexingSampleProvider(new[] { sp }, 2);
            sp = new OffsetSampleProvider(sp) { Take = TimeSpan.FromSeconds(Math.Max(0.05, c.End - Math.Max(0, c.Start))) };
            var fade = new FadeInOutSampleProvider(sp, true);
            fade.BeginFadeIn(15);
            sp = fade;
            if (sp.WaveFormat.SampleRate != mix.SampleRate) sp = new WdlResamplingSampleProvider(sp, mix.SampleRate);
            if (parts.Count > 0) parts.Add(new SilenceProviderEx(sp.WaveFormat, gapSeconds));
            parts.Add(sp);
        }
        if (parts.Count == 0) return;
        ISampleProvider chain = new ConcatenatingSampleProvider(parts);
        if (mix.Channels > 2) chain = new MultiplexingSampleProvider(new[] { chain }, mix.Channels);
        _out = new WasapiOut(dev, AudioClientShareMode.Shared, true, 80);
        _out.PlaybackStopped += (_, _) => Finished?.Invoke();
        _out.Init(chain);
        _out.Play();
    }

    public void Stop()
    {
        try { _out?.Stop(); } catch { }
        _out?.Dispose();
        _out = null;
        foreach (var d in _open) d.Dispose();
        _open.Clear();
    }

    public void Dispose() => Stop();

    /// <summary>Silence of a fixed length in a given format (the gap between two clips).</summary>
    private sealed class SilenceProviderEx : ISampleProvider
    {
        private long _left;
        public WaveFormat WaveFormat { get; }

        public SilenceProviderEx(WaveFormat format, double seconds)
        {
            WaveFormat = format;
            _left = (long)(seconds * format.SampleRate) * format.Channels;
        }

        public int Read(float[] buffer, int offset, int count)
        {
            int n = (int)Math.Min(count, _left);
            Array.Clear(buffer, offset, n);
            _left -= n;
            return n;
        }
    }
}
