import React, { useState } from 'react';
import { Search, Download, Music2, Wand2, X, CheckCircle2, AlertCircle, FileMusic, ArrowRight, Loader2 } from 'lucide-react';

interface AddSongModalProps {
  onClose: () => void;
  onSuccess: () => void;
}

export const AddSongModal: React.FC<AddSongModalProps> = ({ onClose, onSuccess }) => {
  const [songTitle, setSongTitle] = useState('');
  const [step, setStep] = useState<'input' | 'tab_results' | 'audio_results' | 'success'>('input');
  const [loading, setLoading] = useState(false);
  const [tabResults, setTabResults] = useState<any[]>([]);
  const [audioResults, setAudioResults] = useState<any[]>([]);
  const [statusMsg, setStatusMsg] = useState('');
  const [progressData, setProgressData] = useState<{ percent: number; step: string; etaText: string } | null>(null);
  const [completedSong, setCompletedSong] = useState<any>(null);

  React.useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [onClose]);

  // Step 1: Search for existing tabs
  const handleSearch = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!songTitle.trim()) return;

    setLoading(true);
    setStatusMsg('检索中...');
    try {
      const res = await fetch('/api/add-song/search-tabs', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ title: songTitle }),
      });
      const tabs = await res.json();
      setTabResults(tabs || []);
      setStep('tab_results');
    } catch (err) {
      console.error(err);
      setStatusMsg('未检索到曲谱');
    } finally {
      setLoading(false);
    }
  };

  // Step 2A: Confirm tab download
  const handleConfirmTab = async (tab: any) => {
    setLoading(true);
    setStatusMsg(`下载中: ${tab.title}`);
    try {
      const res = await fetch('/api/add-song/confirm-tab', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          title: tab.title,
          artist: tab.artist,
          franchise: 'BanG Dream! 经典曲目',
          download_url: tab.download_url,
        }),
      });
      if (!res.ok) {
        const errData = await res.json().catch(() => ({}));
        throw new Error(errData.detail || `HTTP ${res.status}`);
      }
      const data = await res.json();
      if (data.status !== 'success' || !data.song) {
        throw new Error(data.message || '导入失败');
      }
      setCompletedSong(data.song);
      setStatusMsg('');
      setStep('success');
      onSuccess();
    } catch (err: any) {
      console.error(err);
      setStatusMsg(`导入失败`);
    } finally {
      setLoading(false);
    }
  };

  // Step 2B: Search audio for AI transcription
  const handleSwitchToAudio = async () => {
    setLoading(true);
    setStatusMsg('检索音源中...');
    try {
      const res = await fetch('/api/add-song/search-audio', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ title: songTitle }),
      });
      if (!res.ok) {
        throw new Error(`HTTP ${res.status}`);
      }
      const audios = await res.json();
      setAudioResults(audios || []);
      setStatusMsg('');
      setStep('audio_results');
    } catch (err: any) {
      console.error(err);
      setStatusMsg(`检索音源失败: ${err.message || '请重试'}`);
    } finally {
      setLoading(false);
    }
  };

  // Step 3: Confirm audio transcription
  const handleConfirmAudio = async (audio: any) => {
    setLoading(true);
    setProgressData({ percent: 15, step: '下载音源', etaText: '' });
    setStatusMsg('');

    const pollTimer = setInterval(async () => {
      try {
        const progRes = await fetch('/api/add-song/transcribe-status');
        if (progRes.ok) {
          const prog = await progRes.json();
          if (prog && prog.status === 'running') {
            setProgressData({
              percent: prog.percent || 0,
              step: prog.step || '',
              etaText: prog.eta_text || '',
            });
          }
        }
      } catch {
        // Ignore polling network blips
      }
    }, 600);

    try {
      const res = await fetch('/api/add-song/transcribe-from-audio', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          title: audio.title,
          artist: audio.artist,
          audio_id: audio.audio_id,
          album: audio.album,
        }),
      });
      if (!res.ok) {
        const errData = await res.json().catch(() => ({}));
        throw new Error(errData.detail || `HTTP ${res.status}`);
      }
      const data = await res.json();
      if (data.status !== 'success' || !data.song) {
        throw new Error(data.message || '生成失败');
      }
      setCompletedSong(data.song);
      setStatusMsg('');
      setStep('success');
      onSuccess();
    } catch (err: any) {
      console.error(err);
      setStatusMsg('生成失败');
    } finally {
      clearInterval(pollTimer);
      setProgressData(null);
      setLoading(false);
    }
  };

  // Universal Drag and Drop (.gp, .gp5, .gpx, .pdf, .ncm, .mp3, etc.)
  const handleFileUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = e.target.files;
    if (!files || files.length === 0) return;
    const file = files[0];

    setLoading(true);
    const fname = file.name;
    const isScore = /\.(gp|gp5|gpx|pdf)$/i.test(fname);

    if (isScore) {
      setStatusMsg(`导入中: ${fname}`);
      const baseTitle = fname.replace(/\.(gp|gp5|gpx|pdf)$/i, '').replace(/^\[BASS TAB\]\s*/i, '').trim();
      const formData = new FormData();
      formData.append('file', file);
      formData.append('title', baseTitle || '未命名曲目');
      formData.append('artist', 'BanG Dream! 经典曲目');
      formData.append('franchise', 'BanG Dream!');

      try {
        const res = await fetch('/api/add-song/upload-local', {
          method: 'POST',
          body: formData,
        });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();
        setCompletedSong({ title: baseTitle, artist: 'BanG Dream!' });
        setStatusMsg('');
        setStep('success');
        onSuccess();
      } catch (err: any) {
        console.error(err);
        setStatusMsg('导入失败');
      } finally {
        setLoading(false);
      }
    } else {
      setLoading(true);
      setProgressData({ percent: 10, step: '上传音频', etaText: '' });
      setStatusMsg('');
      const baseTitle = fname.replace(/\.(mp3|flac|wav|ncm|m4a)$/i, '').trim();
      const formData = new FormData();
      formData.append('file', file);
      formData.append('title', baseTitle || '未命名曲目');
      formData.append('artist', 'BanG Dream! 经典曲目');
      formData.append('franchise', 'BanG Dream!');

      const pollTimer = setInterval(async () => {
        try {
          const progRes = await fetch('/api/add-song/transcribe-status');
          if (progRes.ok) {
            const prog = await progRes.json();
            if (prog && prog.status === 'running') {
              setProgressData({
                percent: prog.percent || 0,
                step: prog.step || '',
                etaText: prog.eta_text || '',
              });
            }
          }
        } catch {}
      }, 600);

      try {
        const res = await fetch('/api/add-song/transcribe-local-audio', {
          method: 'POST',
          body: formData,
        });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();
        setCompletedSong(data.song || { title: baseTitle, artist: 'BanG Dream!' });
        setStatusMsg('');
        setStep('success');
        onSuccess();
      } catch (err) {
        setStatusMsg('生成失败');
      } finally {
        clearInterval(pollTimer);
        setProgressData(null);
        setLoading(false);
      }
    }
  };

  return (
    <div
      onClick={onClose}
      className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-slate-900/60 backdrop-blur-sm animate-fadeIn cursor-pointer"
    >
      <div
        className="relative w-full max-w-xl rounded-3xl bg-white border-2 border-pink-200/90 shadow-2xl shadow-pink-500/10 text-slate-900 overflow-hidden flex flex-col max-h-[85vh] cursor-default"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-slate-100 bg-slate-50/80">
          <div className="flex items-center gap-2.5">
            <div className="p-2 rounded-xl bg-gradient-to-tr from-[#ff2d75] to-[#ff4081] text-white shadow-md shadow-pink-500/25">
              <Download className="w-5 h-5" />
            </div>
            <div>
              <h3 className="text-base font-black text-slate-900 tracking-wide">新增曲目</h3>
            </div>
          </div>
          <button
            onClick={onClose}
            className="p-1.5 rounded-full text-slate-400 hover:text-slate-700 hover:bg-slate-100 transition-colors"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Content Body */}
        <div className="p-6 overflow-y-auto flex-1 space-y-4">
          {/* STEP 1: Search Form */}
          {step === 'input' && (
            <div className="space-y-4">
              <form onSubmit={handleSearch} className="space-y-3">
                <label className="text-xs font-bold text-slate-700 block">
                  曲目名称
                </label>
                <div className="relative">
                  <Search className="absolute left-4 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-400" />
                  <input
                    type="text"
                    value={songTitle}
                    onChange={(e) => setSongTitle(e.target.value)}
                    placeholder="输入曲名或乐队名"
                    className="w-full pl-11 pr-4 py-3 rounded-2xl bg-slate-50 border border-slate-200 text-sm text-slate-900 placeholder:text-slate-400 focus:outline-none focus:bg-white focus:border-pink-500 focus:ring-2 focus:ring-pink-200 transition-all"
                    autoFocus
                  />
                </div>
                <button
                  type="submit"
                  disabled={loading || !songTitle.trim()}
                  className="w-full py-3 rounded-xl bg-gradient-to-r from-[#ff2d75] to-[#ff4081] hover:brightness-105 text-white font-black text-sm shadow-md shadow-pink-500/25 flex items-center justify-center gap-2 transition-all disabled:opacity-50"
                >
                  {loading ? (
                    <>
                      <Loader2 className="w-4 h-4 animate-spin" />
                      <span>检索中...</span>
                    </>
                  ) : (
                    <>
                      <Search className="w-4 h-4" />
                      <span>检索曲谱</span>
                    </>
                  )}
                </button>
              </form>

              <div className="relative flex py-2 items-center">
                <div className="flex-grow border-t border-slate-200"></div>
                <span className="flex-shrink mx-4 text-[11px] text-slate-400 font-bold uppercase">
                  本地文件
                </span>
                <div className="flex-grow border-t border-slate-200"></div>
              </div>

              {/* Universal Drag Zone */}
              <label className="flex flex-col items-center justify-center p-6 rounded-2xl border-2 border-dashed border-slate-200 hover:border-pink-400 bg-slate-50/70 hover:bg-pink-50/30 cursor-pointer transition-colors group">
                <FileMusic className="w-8 h-8 text-pink-500 group-hover:scale-110 transition-transform mb-1.5" />
                <span className="text-xs font-bold text-slate-800">拖入或选择文件 (.gp / .pdf / 音频)</span>
                <input
                  type="file"
                  accept=".gp,.gp5,.gpx,.pdf,.ncm,.mp3,.flac,.wav"
                  onChange={handleFileUpload}
                  className="hidden"
                />
              </label>
            </div>
          )}

          {/* STEP 2A: Found Tab Results */}
          {step === 'tab_results' && (
            <div className="space-y-4 animate-fadeIn">
              <div className="flex items-center justify-between">
                <span className="text-xs font-bold text-slate-700">
                  曲谱结果
                </span>
                <button
                  onClick={() => setStep('input')}
                  className="text-xs font-bold text-pink-600 hover:underline"
                >
                  重新搜索
                </button>
              </div>

              <div className="space-y-2.5 max-h-[300px] overflow-y-auto pr-1">
                {tabResults.map((tab, idx) => (
                  <div
                    key={idx}
                    className="flex items-center justify-between p-3.5 rounded-2xl bg-slate-50 border border-slate-200 hover:border-pink-400 transition-all"
                  >
                    <div>
                      <h4 className="text-sm font-bold text-slate-900">{tab.title}</h4>
                      <p className="text-xs text-slate-500">
                        {tab.artist} · <span className="text-slate-400">{tab.source}</span>
                      </p>
                    </div>

                    <button
                      onClick={() => handleConfirmTab(tab)}
                      disabled={loading}
                      className="flex items-center gap-1.5 px-3.5 py-2 rounded-xl bg-gradient-to-r from-[#ff2d75] to-[#ff4081] hover:brightness-105 text-white font-bold text-xs shadow-md shadow-pink-500/25 transition-all"
                    >
                      <Download className="w-3.5 h-3.5" />
                      <span>导入</span>
                    </button>
                  </div>
                ))}
              </div>

              {/* Fallback to Audio AI Transcription */}
              <div className="pt-2 border-t border-slate-100 flex items-center justify-between">
                <span className="text-xs text-slate-500">未找到乐谱？</span>
                <button
                  onClick={handleSwitchToAudio}
                  disabled={loading}
                  className="flex items-center gap-1.5 px-3 py-1.5 rounded-xl bg-slate-100 hover:bg-pink-50 text-pink-600 border border-pink-200 text-xs font-bold transition-all"
                >
                  <Wand2 className="w-3.5 h-3.5 text-pink-500" />
                  <span>原声扒谱</span>
                </button>
              </div>
            </div>
          )}

          {/* STEP 2B: Audio Source Results for AI Transcription */}
          {step === 'audio_results' && (
            <div className="space-y-4 animate-fadeIn">
              <div className="flex items-center justify-between">
                <span className="text-xs font-bold text-slate-700">
                  选择版本
                </span>
                <button
                  onClick={() => setStep('input')}
                  className="text-xs font-bold text-pink-600 hover:underline"
                >
                  返回
                </button>
              </div>

              <div className="space-y-2.5 max-h-[300px] overflow-y-auto pr-1">
                {audioResults.map((audio, idx) => (
                  <div
                    key={idx}
                    className="flex items-center justify-between p-3.5 rounded-2xl bg-slate-50 border border-slate-200 hover:border-pink-400 transition-all"
                  >
                    <div>
                      <h4 className="text-sm font-bold text-slate-900">{audio.title}</h4>
                      <p className="text-xs text-slate-500">
                        {audio.artist} · <span className="text-slate-400">{audio.album}</span>
                      </p>
                    </div>

                    <button
                      onClick={() => handleConfirmAudio(audio)}
                      disabled={loading}
                      className="flex items-center gap-1.5 px-3.5 py-2 rounded-xl bg-gradient-to-r from-[#ff2d75] to-amber-500 hover:brightness-105 text-white font-bold text-xs shadow-md shadow-pink-500/25 transition-all"
                    >
                      <Wand2 className="w-3.5 h-3.5" />
                      <span>生成曲谱</span>
                    </button>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* STEP 3: SUCCESS CARD */}
          {step === 'success' && (
            <div className="text-center py-6 space-y-4 animate-fadeIn">
              <div className="flex justify-center">
                <div className="p-4 rounded-full bg-emerald-50 text-emerald-600 border border-emerald-200 shadow-lg">
                  <CheckCircle2 className="w-12 h-12" />
                </div>
              </div>
              <div>
                <h3 className="text-lg font-black text-slate-900">已就绪</h3>
              </div>
              <button
                onClick={onClose}
                className="px-6 py-2.5 rounded-xl bg-gradient-to-r from-[#ff2d75] to-[#ff4081] text-white font-black text-xs shadow-md shadow-pink-500/25"
              >
                开始练习
              </button>
            </div>
          )}

          {progressData && (
            <div className="p-4 rounded-2xl bg-slate-50 border border-pink-200/80 shadow-sm space-y-2.5">
              <div className="flex items-center justify-between text-xs">
                <div className="flex items-center gap-2 font-bold text-slate-800">
                  <Loader2 className="w-3.5 h-3.5 animate-spin text-pink-600 shrink-0" />
                  <span>{progressData.step || '处理中'}</span>
                  <span className="text-pink-600">{progressData.percent}%</span>
                </div>
                {progressData.etaText && (
                  <span className="text-[11px] font-medium text-slate-500 bg-white px-2 py-0.5 rounded-full border border-slate-200/60 shadow-xs">
                    {progressData.etaText}
                  </span>
                )}
              </div>
              <div className="w-full h-2 bg-slate-200/80 rounded-full overflow-hidden">
                <div
                  className="h-full bg-gradient-to-r from-[#ff2d75] to-amber-500 rounded-full transition-all duration-500 ease-out"
                  style={{ width: `${Math.min(100, Math.max(0, progressData.percent))}%` }}
                />
              </div>
            </div>
          )}

          {statusMsg && !progressData && (
            <div className="p-3 rounded-xl bg-pink-50 border border-pink-200 text-xs text-pink-700 flex items-center gap-2">
              <Loader2 className="w-4 h-4 animate-spin text-pink-600 shrink-0" />
              <span>{statusMsg}</span>
            </div>
          )}
        </div>
      </div>
    </div>
  );
};
