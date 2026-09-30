using System;
using NAudio.Wave;
using SoundTouch;

namespace BassStation.Services;

/// <summary>
/// Real-time tempo change without pitch change (SoundTouch WSOLA), pulled like any sample provider; replaces the
/// ffmpeg-rendered copy per tempo. Settings were chosen by measuring onset timing on a click track (44.1 kHz):
/// sequence 30 ms / seek window 12 ms keeps onsets within a few ms; the remaining systematic lead is
/// removed by <see cref="LeadSeconds"/>.
/// </summary>
public sealed class StretchProvider : ISampleProvider
{
    private readonly ISampleProvider _src;
    private readonly SoundTouchProcessor _st = new();
    private readonly int _ch;
    private readonly float[] _in = new float[4096 * 2];
    private bool _flushed;

    public double Rate { get; }

    public StretchProvider(ISampleProvider src, double rate)
    {
        _src = src;
        _ch = src.WaveFormat.Channels;
        Rate = rate;
        _st.SampleRate = src.WaveFormat.SampleRate;
        _st.Channels = _ch;
        _st.SetSetting(SettingId.SequenceDurationMs, 30);
        _st.SetSetting(SettingId.SeekWindowDurationMs, 12);
        _st.SetSetting(SettingId.OverlapDurationMs, 8);
        _st.Tempo = rate;
    }

    public WaveFormat WaveFormat => _src.WaveFormat;

    private bool Bypass => Math.Abs(Rate - 1) < 1e-6;

    /// <summary>
    /// How early (seconds of output) the stretched audio sounds relative to exact scaling, measured:
    /// ≈ 22 ms × (1/rate − 1) when slower, ≈ 15 ms × (1/rate − 1) when faster. Reading the source that much
    /// earlier (× rate, in source seconds) lines it up with the tab synth and the metronome.
    /// </summary>
    public static double LeadSeconds(double rate) =>
        Math.Abs(rate - 1) < 1e-6 ? 0 : (rate < 1 ? .022 : .015) * (1 / rate - 1);

    /// <summary>Drop buffered audio (the source was repositioned).</summary>
    public void Reset()
    {
        _st.Clear();
        _flushed = false;
    }

    public int Read(float[] buffer, int offset, int count)
    {
        if (Bypass) return _src.Read(buffer, offset, count);
        int frames = count / _ch;
        while (_st.AvailableSamples < frames && !_flushed)
        {
            int got = _src.Read(_in, 0, _in.Length);
            if (got == 0)
            {
                _st.Flush();
                _flushed = true;
                break;
            }
            _st.PutSamples(_in.AsSpan(0, got), got / _ch);
        }
        return _st.ReceiveSamples(buffer.AsSpan(offset, frames * _ch), frames) * _ch;
    }
}
