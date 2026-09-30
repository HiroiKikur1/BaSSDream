import React, { useState, useRef, useEffect } from 'react';
import {
  Upload,
  X,
  CheckCircle,
  AlertCircle,
  Award,
  Volume2,
  Clock,
  Zap,
  RotateCcw,
  Mic,
  Square,
  Sparkles,
  Radio,
} from 'lucide-react';
import type { Song } from '../types';

interface EvaluationModalProps {
  song: Song;
  onClose: () => void;
  onScoreUpdated?: (score: number, grade: string) => void;
}

export const EvaluationModal: React.FC<EvaluationModalProps> = ({ song, onClose, onScoreUpdated }) => {
  const [tabMode, setTabMode] = useState<'record' | 'upload'>('record');
  const [file, setFile] = useState<File | null>(null);
  const [analyzing, setAnalyzing] = useState(false);
  const [result, setResult] = useState<any | null>(null);
  const [error, setError] = useState<string | null>(null);

  // Audio Recording State
  const [isRecording, setIsRecording] = useState(false);
  const [recordSeconds, setRecordSeconds] = useState(0);
  const [audioLevel, setAudioLevel] = useState(0);

  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const audioChunksRef = useRef<Blob[]>([]);
  const recordTimerRef = useRef<number | null>(null);
  const audioContextRef = useRef<AudioContext | null>(null);
  const analyserRef = useRef<AnalyserNode | null>(null);
  const animFrameRef = useRef<number | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && !isRecording) onClose();
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => {
      window.removeEventListener('keydown', handleKeyDown);
      stopRecordingCleanup();
    };
  }, [onClose, isRecording]);

  const stopRecordingCleanup = () => {
    if (recordTimerRef.current) clearInterval(recordTimerRef.current);
    if (animFrameRef.current) cancelAnimationFrame(animFrameRef.current);
    if (audioContextRef.current && audioContextRef.current.state !== 'closed') {
      audioContextRef.current.close().catch(() => {});
    }
  };

  const handleStartRecording = async () => {
    setError(null);
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          echoCancellation: false,
          noiseSuppression: false,
          autoGainControl: false,
        },
      });

      const AudioCtx = window.AudioContext || (window as any).webkitAudioContext;
      const ctx = new AudioCtx();
      audioContextRef.current = ctx;

      const source = ctx.createMediaStreamSource(stream);
      const analyser = ctx.createAnalyser();
      analyser.fftSize = 256;
      source.connect(analyser);
      analyserRef.current = analyser;

      // VU meter animation
      const dataArray = new Uint8Array(analyser.frequencyBinCount);
      const updateLevel = () => {
        if (analyserRef.current) {
          analyserRef.current.getByteFrequencyData(dataArray);
          const avg = dataArray.reduce((acc, v) => acc + v, 0) / dataArray.length;
          setAudioLevel(Math.min(100, Math.round((avg / 128) * 100)));
          animFrameRef.current = requestAnimationFrame(updateLevel);
        }
      };
      updateLevel();

      const mediaRecorder = new MediaRecorder(stream);
      mediaRecorderRef.current = mediaRecorder;
      audioChunksRef.current = [];

      mediaRecorder.ondataavailable = (e) => {
        if (e.data.size > 0) {
          audioChunksRef.current.push(e.data);
        }
      };

      mediaRecorder.onstop = async () => {
        stream.getTracks().forEach((t) => t.stop());
        stopRecordingCleanup();

        const audioBlob = new Blob(audioChunksRef.current, { type: 'audio/wav' });
        const recordedFile = new File([audioBlob], `direct_record_${Date.now()}.wav`, {
          type: 'audio/wav',
        });
        await uploadAndEvaluate(recordedFile);
      };

      mediaRecorder.start(250);
      setIsRecording(true);
      setRecordSeconds(0);
      recordTimerRef.current = window.setInterval(() => {
        setRecordSeconds((s) => s + 1);
      }, 1000);
    } catch (err: any) {
      console.error(err);
      setError('无法获取声卡或麦克风权限: ' + (err.message || '请在浏览器设置中允许音频输入'));
    }
  };

  const handleStopRecording = () => {
    if (mediaRecorderRef.current && isRecording) {
      mediaRecorderRef.current.stop();
      setIsRecording(false);
    }
  };

  const uploadAndEvaluate = async (audioFile: File) => {
    setAnalyzing(true);
    setError(null);
    const formData = new FormData();
    formData.append('song_id', song.id);
    formData.append('audio', audioFile);

    try {
      const res = await fetch('/api/practice/evaluate', {
        method: 'POST',
        body: formData,
      });
      const data = await res.json();
      if (!res.ok || !data.success) {
        throw new Error(data.error || `HTTP ${res.status}`);
      }
      setResult(data);
      onScoreUpdated?.(data.overall_score, data.grade);
    } catch (err: any) {
      console.error(err);
      setError(err.message || '评测分析失败，请重试');
    } finally {
      setAnalyzing(false);
    }
  };

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    if (e.target.files && e.target.files[0]) {
      setFile(e.target.files[0]);
      setError(null);
    }
  };

  const handleUploadSubmit = () => {
    if (!file) {
      setError('请先选择练习录音文件');
      return;
    }
    uploadAndEvaluate(file);
  };

  const getComboBadgeStyle = (combo: string) => {
    switch (combo) {
      case 'ALL PERFECT':
        return 'bg-gradient-to-r from-pink-500 via-purple-500 to-cyan-400 text-white shadow-lg shadow-purple-500/30 border-white ring-2 ring-pink-300';
      case 'FULL COMBO':
        return 'bg-gradient-to-r from-amber-400 via-yellow-300 to-amber-500 text-amber-950 shadow-md shadow-amber-500/30 border-yellow-200 ring-2 ring-amber-300';
      case 'CLEARED':
        return 'bg-gradient-to-r from-[#ff2d75] to-[#ff4081] text-white shadow-md shadow-pink-500/20';
      default:
        return 'bg-slate-200 text-slate-700';
    }
  };

  return (
    <div
      onClick={() => {
        if (!isRecording) onClose();
      }}
      className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-slate-900/60 backdrop-blur-sm animate-fade-in select-none cursor-pointer"
    >
      <div
        onClick={(e) => e.stopPropagation()}
        className="relative w-full max-w-2xl rounded-3xl bg-white border-2 border-pink-200/90 shadow-2xl shadow-pink-500/10 overflow-hidden flex flex-col max-h-[92vh] text-slate-900 cursor-default"
      >
        {/* Header Bar */}
        <div className="flex items-center justify-between px-6 py-4 bg-slate-50/80 border-b border-slate-100">
          <div className="flex items-center gap-2.5">
            <span className="w-2.5 h-2.5 rounded-full bg-[#ff2d75] shadow-sm shadow-pink-500/60 animate-pulse" />
            <span className="text-sm font-black tracking-wider text-slate-900 uppercase">
              贝斯实弹声学评测
            </span>
          </div>
          <button
            onClick={onClose}
            className="p-1.5 rounded-full text-slate-400 hover:text-slate-700 hover:bg-slate-100 transition-colors"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Content Body */}
        <div className="p-6 overflow-y-auto space-y-5 custom-scrollbar">
          {/* Song Badge Header */}
          <div className="flex items-center justify-between p-3.5 rounded-2xl bg-slate-50 border border-slate-200">
            <div>
              <span className="text-[10px] font-bold text-pink-600 tracking-wider uppercase block">目标曲目</span>
              <h3 className="text-base font-black text-slate-900 line-clamp-1">{song.title}</h3>
              <p className="text-xs text-slate-500 font-medium">{song.artist}</p>
            </div>
            <div className="text-right">
              <span className="text-[10px] font-mono text-slate-400 block">BASE TEMPO</span>
              <span className="text-sm font-mono font-black text-slate-800">{song.tempo || 120} BPM</span>
            </div>
          </div>

          {!result ? (
            <div className="space-y-4">
              {/* Tab Selector: Direct Record vs Upload */}
              <div className="grid grid-cols-2 p-1 rounded-2xl bg-slate-100 border border-slate-200">
                <button
                  onClick={() => setTabMode('record')}
                  disabled={isRecording}
                  className={`py-2 rounded-xl text-xs font-bold transition-all flex items-center justify-center gap-2 ${
                    tabMode === 'record'
                      ? 'bg-white text-slate-900 shadow-sm font-black'
                      : 'text-slate-500 hover:text-slate-800'
                  }`}
                >
                  <Radio className="w-4 h-4 text-[#ff2d75]" />
                  <span>声卡 / 麦克风直录</span>
                </button>
                <button
                  onClick={() => setTabMode('upload')}
                  disabled={isRecording}
                  className={`py-2 rounded-xl text-sm font-bold transition-all flex items-center justify-center gap-2 ${
                    tabMode === 'upload'
                      ? 'bg-white text-slate-900 shadow-sm font-black'
                      : 'text-slate-500 hover:text-slate-800'
                  }`}
                >
                  <Upload className="w-4 h-4 text-slate-600" />
                  <span>上传已有音频文件</span>
                </button>
              </div>

              {/* Tab 1: Direct Recording */}
              {tabMode === 'record' ? (
                <div className="p-6 rounded-2xl border-2 border-slate-200 bg-slate-50/50 flex flex-col items-center justify-center text-center space-y-4">
                  {isRecording ? (
                    <div className="space-y-4 w-full flex flex-col items-center">
                      <div className="flex items-center gap-2 text-rose-500 animate-pulse">
                        <span className="w-3 h-3 rounded-full bg-rose-500" />
                        <span className="text-xs font-bold font-mono tracking-wider uppercase">录音采集中...</span>
                      </div>

                      <div className="text-3xl font-mono font-black text-slate-900">
                        {Math.floor(recordSeconds / 60)
                          .toString()
                          .padStart(2, '0')}
                        :
                        {(recordSeconds % 60).toString().padStart(2, '0')}
                      </div>

                      {/* VU Meter */}
                      <div className="w-48 h-2.5 rounded-full bg-slate-200 overflow-hidden shadow-inner">
                        <div
                          className="h-full bg-gradient-to-r from-emerald-400 via-yellow-400 to-rose-500 transition-all duration-75"
                          style={{ width: `${audioLevel}%` }}
                        />
                      </div>

                      <button
                        onClick={handleStopRecording}
                        className="px-6 py-2.5 rounded-full bg-rose-600 hover:bg-rose-700 text-white font-bold text-xs flex items-center gap-2 shadow-lg shadow-rose-600/30 hover:scale-105 active:scale-95 transition-all"
                      >
                        <Square className="w-4 h-4 fill-white" />
                        <span>停止录音并评分</span>
                      </button>
                    </div>
                  ) : (
                    <div className="space-y-3">
                      <div className="w-14 h-14 rounded-2xl bg-pink-50 flex items-center justify-center text-[#ff2d75] border border-pink-200 shadow-sm mx-auto">
                        <Mic className="w-7 h-7" />
                      </div>
                      <div>
                        <h4 className="text-base font-black text-slate-900">声卡 / 麦克风</h4>
                      </div>
                      <button
                        onClick={handleStartRecording}
                        disabled={analyzing}
                        className="px-6 py-2.5 rounded-full bg-gradient-to-r from-[#ff2d75] to-[#ff4081] hover:brightness-105 text-white font-bold text-xs shadow-lg shadow-pink-500/25 hover:scale-105 active:scale-95 transition-all flex items-center gap-2 mx-auto"
                      >
                        <Mic className="w-4 h-4" />
                        <span>{analyzing ? '正在声学分析中...' : '开始声卡直录'}</span>
                      </button>
                    </div>
                  )}
                </div>
              ) : (
                /* Tab 2: File Upload */
                <div
                  onClick={() => fileInputRef.current?.click()}
                  className="border-2 border-dashed border-slate-300 hover:border-pink-400 rounded-2xl p-6 text-center cursor-pointer transition-colors bg-slate-50/50 hover:bg-pink-50/20 flex flex-col items-center justify-center gap-2"
                >
                  <input
                    ref={fileInputRef}
                    type="file"
                    accept="audio/*"
                    onChange={handleFileChange}
                    className="hidden"
                  />
                  <div className="w-10 h-10 rounded-xl bg-pink-100/60 flex items-center justify-center text-[#ff2d75]">
                    <Upload className="w-5 h-5" />
                  </div>
                  <div>
                    <span className="text-sm font-bold text-slate-800">
                      {file ? file.name : '选择音频文件'}
                    </span>
                  </div>
                  {file && (
                    <button
                      onClick={(e) => {
                        e.stopPropagation();
                        handleUploadSubmit();
                      }}
                      disabled={analyzing}
                      className="mt-2 px-5 py-2 rounded-full bg-[#ff2d75] text-white text-xs font-bold shadow-md shadow-pink-500/25 hover:scale-105 active:scale-95 transition-all"
                    >
                      {analyzing ? '正在声学评测中...' : '开始评测'}
                    </button>
                  )}
                </div>
              )}

              {error && (
                <div className="flex items-center gap-2 p-3 rounded-xl bg-rose-50 border border-rose-200 text-rose-600 text-xs">
                  <AlertCircle className="w-4 h-4 shrink-0" />
                  <span>{error}</span>
                </div>
              )}
            </div>
          ) : (
            /* Results View */
            <div className="space-y-4 animate-fade-in">
              {/* Score & Combo Badge Header */}
              <div className="p-5 rounded-3xl bg-gradient-to-br from-slate-900 to-slate-800 text-white shadow-xl relative overflow-hidden flex items-center justify-between">
                <div className="space-y-1">
                  <div className="flex items-center gap-2">
                    <span className="text-xs font-bold text-slate-400 tracking-wider uppercase">OVERALL SCORE</span>
                    {result.combo_badge && (
                      <span
                        className={`px-2.5 py-0.5 rounded-full text-[10px] font-black tracking-wide border shadow-sm ${getComboBadgeStyle(
                          result.combo_badge
                        )}`}
                      >
                        {result.combo_badge}
                      </span>
                    )}
                  </div>
                  <div className="flex items-baseline gap-2">
                    <span className="text-5xl font-black font-mono tracking-tight text-white">
                      {result.overall_score}
                    </span>
                    <span className="text-xs font-mono text-pink-400 font-bold">/ 100 PTS</span>
                  </div>
                </div>

                <div className="flex flex-col items-center">
                  <div className="w-16 h-16 rounded-2xl bg-gradient-to-tr from-[#ff2d75] to-purple-500 p-0.5 shadow-lg shadow-pink-500/30">
                    <div className="w-full h-full rounded-2xl bg-slate-900 flex items-center justify-center">
                      <span className="text-3xl font-black font-mono text-transparent bg-clip-text bg-gradient-to-r from-pink-400 to-amber-300">
                        {result.grade}
                      </span>
                    </div>
                  </div>
                  <span className="text-[9px] font-mono text-slate-400 mt-1 uppercase">STAGE RANK</span>
                </div>
              </div>

              {/* 4 Professional Dimensions */}
              <div className="grid grid-cols-2 gap-2.5">
                {Object.entries(result.dimensions || {}).map(([key, dim]: [string, any]) => (
                  <div key={key} className="p-3.5 rounded-2xl bg-white border border-slate-200 shadow-xs space-y-1">
                    <div className="flex items-center justify-between text-sm">
                      <span className="font-bold text-slate-700">{dim.label}</span>
                      <span className="font-mono font-black text-pink-600">{dim.score}分</span>
                    </div>
                    <div className="w-full h-2 rounded-full bg-slate-100 overflow-hidden">
                      <div
                        className="h-full bg-gradient-to-r from-pink-500 to-[#ff2d75] rounded-full"
                        style={{ width: `${dim.score}%` }}
                      />
                    </div>
                  </div>
                ))}
              </div>

              {/* Coach Comment */}
              {result.coach_comment && (
                <div className="p-3.5 rounded-2xl bg-pink-50/70 border border-pink-200 text-slate-700 text-sm leading-relaxed space-y-1">
                  <span className="text-xs font-bold text-pink-600 uppercase tracking-wider block font-mono">
                    评测反馈
                  </span>
                  <p>{result.coach_comment}</p>
                </div>
              )}

              {/* Re-eval Button */}
              <button
                onClick={() => {
                  setResult(null);
                  setFile(null);
                }}
                className="w-full py-2.5 rounded-full bg-slate-100 hover:bg-slate-200 text-slate-700 font-bold text-xs flex items-center justify-center gap-1.5 transition-colors"
              >
                <RotateCcw className="w-3.5 h-3.5" />
                <span>重新录音评测</span>
              </button>
            </div>
          )}
        </div>
      </div>
    </div>
  );
};
