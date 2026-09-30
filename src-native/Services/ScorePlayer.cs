using System;
using System.Collections.Generic;
using NAudio.CoreAudioApi;
using NAudio.Wave;
using BassStation.Models;

namespace BassStation.Services;

/// <summary>
/// Plays a score with its tracks mixed live: bass-less backing and the original bass (the 1.00 wav files, tempo
/// changed in real time by <see cref="StretchProvider"/>), a tab synth and a metronome (both generated from the
/// score at any tempo). Session time s plays recording time s * Rate.
/// </summary>
public sealed class ScorePlayer : IDisposable
{
    public enum Track { Backing, Bass, Synth, Click }

    private const int Sr = 44100;
    private const int LatencyMs = 80;

    private readonly Mixer _mixer;
    private WasapiOut? _out;

    public ScorePlayer(BassScore score)
    {
        _mixer = new Mixer(score);
    }

    public bool IsPlaying => _out?.PlaybackState == PlaybackState.Playing;
    public double Rate => _mixer.Rate;
    public double Duration => _mixer.DurationRec;

    /// <summary>Recording time heard right now (output latency taken off while playing).</summary>
    public double Position => Math.Max(0, (_mixer.Frame / (double)Sr - (IsPlaying ? LatencyMs / 1000.0 : 0)) * _mixer.Rate);

    public void SetVolume(Track t, double v) => _mixer.Volume[(int)t] = (float)Math.Clamp(v, 0, 1.5);
    public double GetVolume(Track t) => _mixer.Volume[(int)t];
    public bool HasTrack(Track t) => t switch { Track.Backing => _mixer.HasBacking, Track.Bass => _mixer.HasBass, _ => true };

    /// <summary>Tracks at their original tempo (null = absent), played at <paramref name="rate"/>.</summary>
    public void Load(string? backingWav, string? bassWav, double rate)
    {
        bool was = IsPlaying;
        double at = Position;
        Pause();
        _mixer.SetSources(backingWav, bassWav, rate);
        Seek(at);
        if (was) Play();
    }

    public void Seek(double recTime) => _mixer.Seek(recTime);

    /// <summary>Tempo change, instant: the audio is stretched while it plays.</summary>
    public void SetRate(double rate) => _mixer.SetRate(rate);

    /// <summary>Backing / bass alignment (ms): the audio heard at score time t is the recording at t + offset.</summary>
    public double AudioOffsetMs
    {
        get => _mixer.OffsetSec * 1000.0;
        set => _mixer.SetOffset(value / 1000.0);
    }

    /// <summary>Loop between two recording times; null clears.</summary>
    public void SetLoop(double? a, double? b) => _mixer.SetLoop(a, b);

    /// <summary>The score changed (edit): the synth and metronome follow at once.</summary>
    public void Refresh() => _mixer.RebuildEvents();

    public void Play()
    {
        if (_out == null)
        {
            // feed the device its own mix format: letting Windows convert 44.1 kHz float to a 48 kHz device
            // (AUTOCONVERTPCM) came out as loud broadband noise on the user's machine
            using var en = new MMDeviceEnumerator();
            var dev = en.GetDefaultAudioEndpoint(DataFlow.Render, Role.Multimedia);
            var mix = dev.AudioClient.MixFormat;
            ISampleProvider src = _mixer;
            if (mix.SampleRate != Sr) src = new NAudio.Wave.SampleProviders.WdlResamplingSampleProvider(src, mix.SampleRate);
            if (mix.Channels > 2) src = new NAudio.Wave.SampleProviders.MultiplexingSampleProvider(new[] { src }, mix.Channels);
            _out = new WasapiOut(dev, AudioClientShareMode.Shared, true, LatencyMs);
            _out.Init(src);
        }
        _out.Play();
    }

    public void Pause() => _out?.Pause();

    /// <summary>Debug: renders the mix offline (no output device) from a recording time into a wav.</summary>
    internal void DebugRender(string path, double fromRec, double seconds)
    {
        Seek(fromRec);
        using var w = new WaveFileWriter(path, _mixer.WaveFormat);
        var buf = new float[4410 * 2];
        int blocks = (int)(seconds * Sr / 4410);
        for (int i = 0; i < blocks; i++)
        {
            _mixer.Read(buf, 0, buf.Length);
            w.WriteSamples(buf, 0, buf.Length);
        }
    }

    /// <summary>Debug: plays through the real output while recording the system loopback; returns the output format.</summary>
    internal async System.Threading.Tasks.Task<string> DebugLoopbackAsync(string path, double fromRec, double seconds)
    {
        var cap = new WasapiLoopbackCapture();
        var w = new WaveFileWriter(path, cap.WaveFormat);
        var done = new System.Threading.Tasks.TaskCompletionSource<bool>();
        cap.DataAvailable += (_, a) => w.Write(a.Buffer, 0, a.BytesRecorded);
        cap.RecordingStopped += (_, _) => { w.Dispose(); cap.Dispose(); done.TrySetResult(true); };
        Seek(fromRec);
        cap.StartRecording();
        Play();
        await System.Threading.Tasks.Task.Delay(TimeSpan.FromSeconds(seconds));
        Pause();
        cap.StopRecording();
        await done.Task;
        return $"out {_out?.OutputWaveFormat} | loopback {cap.WaveFormat}";
    }

    public void Dispose()
    {
        try { _out?.Stop(); } catch { }
        _out?.Dispose();
        _mixer.Dispose();
    }

    // ------------------------------------------------------------------------ mixer

    private sealed class Mixer : ISampleProvider, IDisposable
    {
        private readonly BassScore _score;
        private readonly object _lock = new();
        private WaveFileReader? _backing, _bass;
        private StretchProvider? _backingSp, _bassSp;
        private float[] _tmp = new float[8192];
        private List<(double T, double Dur, int Midi, bool Dead)> _notes = new();
        private List<(double T, bool Accent)> _clicks = new();
        private int _nextNote, _nextClick;
        private readonly List<Voice> _voices = new();
        private readonly List<ClickVoice> _clickVoices = new();
        private double? _loopA, _loopB;
        public double OffsetSec { get; private set; }

        /// <summary>Recording time (s) the audio tracks are read from at a session frame.</summary>
        private double ReadTime(long frame) => frame / (double)Sr * Rate + OffsetSec - StretchProvider.LeadSeconds(Rate) * Rate;

        public WaveFormat WaveFormat { get; } = WaveFormat.CreateIeeeFloatWaveFormat(Sr, 2);
        public readonly float[] Volume = { 0.9f, 0.9f, 0.0f, 0.0f };
        public double Rate { get; private set; } = 1.0;
        public long Frame;                                      // session frames rendered so far
        public bool HasBacking => _backing != null;
        public bool HasBass => _bass != null;
        public double DurationRec { get; private set; }

        public Mixer(BassScore score)
        {
            _score = score;
            RebuildEvents();
        }

        public void RebuildEvents()
        {
            var notes = _score.SoundingNotes();
            var clicks = new List<(double, bool)>();
            foreach (var b in _score.Bars)
            {
                int beats = b.Den == 8 && b.Num % 3 == 0 && b.Num >= 6 ? b.Num / 3 : b.Num;
                for (int i = 0; i < beats; i++) clicks.Add((b.T0 + (b.T1 - b.T0) * i / beats, i == 0));
            }
            lock (_lock)
            {
                _notes = notes;
                _clicks = clicks;
                DurationRec = Math.Max(_score.Bars.Count > 0 ? _score.Bars[^1].T1 : 0, DurationRec);
                ResetCursors(Frame / (double)Sr * Rate);
            }
        }

        public void SetSources(string? backingWav, string? bassWav, double rate)
        {
            lock (_lock)
            {
                _backing?.Dispose();
                _bass?.Dispose();
                _backing = backingWav != null ? new WaveFileReader(backingWav) : null;
                _bass = bassWav != null ? new WaveFileReader(bassWav) : null;
                Rate = rate;
                Wrap();
                if (_backing != null) DurationRec = Math.Max(DurationRec, _backing.TotalTime.TotalSeconds);
            }
        }

        private void Wrap()
        {
            _backingSp = _backing == null ? null : new StretchProvider(Stereo(_backing.ToSampleProvider()), Rate);
            _bassSp = _bass == null ? null : new StretchProvider(Stereo(_bass.ToSampleProvider()), Rate);
        }

        public void SetRate(double rate)
        {
            lock (_lock)
            {
                double rec = Frame / (double)Sr * Rate;
                Rate = rate;
                Wrap();
                Frame = (long)(rec / Rate * Sr);
                SeekReader(_backing, _backingSp);
                SeekReader(_bass, _bassSp);
                ResetCursors(rec);
            }
        }

        private static ISampleProvider Stereo(ISampleProvider sp) =>
            sp.WaveFormat.Channels == 1 ? new NAudio.Wave.SampleProviders.MonoToStereoSampleProvider(sp) : sp;

        public void Seek(double recTime)
        {
            lock (_lock)
            {
                recTime = Math.Clamp(recTime, 0, Math.Max(0, DurationRec));
                Frame = (long)(recTime / Rate * Sr);
                SeekReader(_backing, _backingSp);
                SeekReader(_bass, _bassSp);
                ResetCursors(recTime);
            }
        }

        public void SetOffset(double sec)
        {
            lock (_lock)
            {
                OffsetSec = Math.Clamp(sec, -2.0, 2.0);
                SeekReader(_backing, _backingSp);
                SeekReader(_bass, _bassSp);
            }
        }

        public void SetLoop(double? a, double? b)
        {
            lock (_lock)
            {
                (_loopA, _loopB) = a.HasValue && b.HasValue && b > a ? (a, b) : (null, null);
            }
        }

        private void SeekReader(WaveFileReader? r, StretchProvider? st)
        {
            if (r == null) return;
            long pos = (long)Math.Max(0, ReadTime(Frame) * r.WaveFormat.SampleRate) * r.WaveFormat.BlockAlign;
            r.Position = Math.Min(pos, r.Length);
            st?.Reset();
        }

        private void ResetCursors(double recTime)
        {
            _voices.Clear();
            _clickVoices.Clear();
            _nextNote = LowerBound(_notes, recTime);
            _nextClick = 0;
            while (_nextClick < _clicks.Count && _clicks[_nextClick].T < recTime) _nextClick++;
        }

        private static int LowerBound(List<(double T, double Dur, int Midi, bool Dead)> l, double t)
        {
            int lo = 0, hi = l.Count;
            while (lo < hi) { int m = (lo + hi) / 2; if (l[m].T < t) lo = m + 1; else hi = m; }
            return lo;
        }

        public int Read(float[] buffer, int offset, int count)
        {
            lock (_lock)
            {
                int frames = count / 2;
                if (_loopB.HasValue && (Frame + frames) / (double)Sr * Rate >= _loopB.Value)
                {
                    // render up to the loop end, jump back, fill the rest
                    int head = Math.Max(0, (int)(_loopB.Value / Rate * Sr - Frame));
                    RenderBlock(buffer, offset, head);
                    double a = _loopA!.Value;
                    Frame = (long)(a / Rate * Sr);
                    SeekReader(_backing, _backingSp);
                    SeekReader(_bass, _bassSp);
                    ResetCursors(a);
                    RenderBlock(buffer, offset + head * 2, frames - head);
                }
                else
                {
                    RenderBlock(buffer, offset, frames);
                }
                return count;
            }
        }

        private void RenderBlock(float[] buf, int off, int frames)
        {
            if (frames <= 0) return;
            int n = frames * 2;
            Array.Clear(buf, off, n);
            if (_tmp.Length < n) _tmp = new float[n];
            // before the audio starts (negative offset) the tracks are silent, then read on in step
            double rt = ReadTime(Frame);
            int skip = rt < 0 ? (int)Math.Min(frames, Math.Ceiling(-rt / Rate * Sr)) : 0;
            AddReader(_backingSp, Volume[0], buf, off + 2 * skip, n - 2 * skip);
            AddReader(_bassSp, Volume[1], buf, off + 2 * skip, n - 2 * skip);
            RenderSynth(buf, off, frames);
            Frame += frames;
        }

        private void AddReader(ISampleProvider? sp, float vol, float[] buf, int off, int n)
        {
            if (sp == null) return;
            int got = sp.Read(_tmp, 0, n);                         // keep reading while muted, to stay in step
            if (vol <= 0) return;
            for (int i = 0; i < got; i++) buf[off + i] += _tmp[i] * vol;
        }

        private void RenderSynth(float[] buf, int off, int frames)
        {
            double t0 = Frame / (double)Sr * Rate;
            double t1 = (Frame + frames) / (double)Sr * Rate;
            while (_nextNote < _notes.Count && _notes[_nextNote].T < t1)
            {
                var nt = _notes[_nextNote++];
                long start = (long)(nt.T / Rate * Sr);
                _voices.Add(new Voice(nt.Midi, nt.Dead, start, (long)(nt.Dur / Rate * Sr)));
            }
            while (_nextClick < _clicks.Count && _clicks[_nextClick].T < t1)
            {
                var c = _clicks[_nextClick++];
                _clickVoices.Add(new ClickVoice(c.Accent, (long)(c.T / Rate * Sr)));
            }
            float vs = Volume[2], vc = Volume[3];
            for (int i = 0; i < frames; i++)
            {
                long f = Frame + i;
                float s = 0;
                if (vs > 0) foreach (var v in _voices) s += v.Sample(f) * vs;
                if (vc > 0) foreach (var c in _clickVoices) s += c.Sample(f) * vc;
                buf[off + 2 * i] += s;
                buf[off + 2 * i + 1] += s;
            }
            long end = Frame + frames;
            _voices.RemoveAll(v => v.Done(end));
            _clickVoices.RemoveAll(c => c.Done(end));
        }

        public void Dispose()
        {
            lock (_lock)
            {
                _backing?.Dispose();
                _bass?.Dispose();
            }
        }
    }

    /// <summary>Plucked bass: a few decaying harmonics over a weak fundamental, short release at the note end.</summary>
    private sealed class Voice
    {
        private static readonly double[] Amp = { 0.45, 1.0, 0.55, 0.35, 0.22, 0.14 };
        private readonly double _f;
        private readonly bool _dead;
        private readonly long _start, _len;
        private readonly Random _rng = new(7);

        public Voice(int midi, bool dead, long start, long len)
        {
            _f = 440.0 * Math.Pow(2, (midi - 69) / 12.0);
            _dead = dead;
            _start = start;
            _len = Math.Max(len, Sr / 40);
        }

        public bool Done(long frame) => frame > _start + _len + Sr / 30;

        public float Sample(long frame)
        {
            long k = frame - _start;
            if (k < 0) return 0;
            double t = k / (double)Sr;
            double env = Math.Min(1.0, k / (0.004 * Sr));
            long rel = k - _len;
            if (rel > 0) env *= Math.Max(0, 1 - rel / (0.03 * Sr));
            if (_dead) return (float)((_rng.NextDouble() - 0.5) * 0.25 * Math.Exp(-t / 0.012) * env);
            double s = 0;
            for (int h = 0; h < Amp.Length; h++)
            {
                double fh = _f * (h + 1);
                if (fh > 5000) break;
                s += Amp[h] * Math.Exp(-t * (1.8 + 1.4 * h)) * Math.Sin(2 * Math.PI * fh * t);
            }
            return (float)(0.16 * s * env);
        }
    }

    /// <summary>Metronome tick: short sine burst above the bass register (as in the evaluation sessions).</summary>
    private sealed class ClickVoice
    {
        private readonly double _f;
        private readonly float _gain;
        private readonly long _start;

        public ClickVoice(bool accent, long start)
        {
            _f = accent ? 3400 : 2800;
            _gain = accent ? 0.32f : 0.25f;
            _start = start;
        }

        public bool Done(long frame) => frame > _start + Sr / 20;

        public float Sample(long frame)
        {
            long k = frame - _start;
            if (k < 0 || k > Sr / 20) return 0;
            double t = k / (double)Sr;
            return (float)(_gain * Math.Exp(-t / 0.012) * Math.Sin(2 * Math.PI * _f * t));
        }
    }
}
