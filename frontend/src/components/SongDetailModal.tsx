import React, { useState } from 'react';
import { Song } from '../types';
import { RadarChart } from './RadarChart';
import { X, Play, Copy, Check, ExternalLink, Headphones, Sparkles, Sliders } from 'lucide-react';

interface SongDetailModalProps {
  song: Song | null;
  onClose: () => void;
  onStartPractice: (song: Song) => void;
  onCopyClipboard: (song: Song) => void;
  onLaunchUVR: () => void;
}

export const SongDetailModal: React.FC<SongDetailModalProps> = ({
  song,
  onClose,
  onStartPractice,
  onCopyClipboard,
  onLaunchUVR,
}) => {
  const [copied, setCopied] = useState(false);

  if (!song) return null;

  const handleCopy = () => {
    onCopyClipboard(song);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  const tierGradients = {
    SPECIAL: 'from-amber-400 via-rose-500 to-purple-600',
    EXPERT: 'from-rose-600 to-pink-500',
    HARD: 'from-amber-500 to-yellow-400',
    NORMAL: 'from-blue-600 to-cyan-500',
  }[song.tier] || 'from-rose-600 to-pink-500';

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/80 backdrop-blur-md animate-fadeIn">
      <div
        className="relative w-full max-w-3xl rounded-3xl bg-[#121629] border border-[#2b3566] shadow-2xl overflow-hidden text-slate-100 max-h-[90vh] flex flex-col"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header bar */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-[#222a52] bg-[#161b33]">
          <div className="flex items-center gap-3">
            <span
              className={`px-3 py-1 rounded-full text-xs font-black tracking-wider uppercase bg-gradient-to-r text-white shadow-md ${tierGradients}`}
            >
              {song.tier} {song.level}
            </span>
            <span className="text-sm font-semibold text-pink-400">{song.artist}</span>
            <span className="text-xs text-slate-400">• {song.tuning}</span>
          </div>
          <button
            onClick={onClose}
            className="p-1.5 rounded-full text-slate-400 hover:text-white hover:bg-white/10 transition-colors"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Content Body */}
        <div className="p-6 overflow-y-auto space-y-6 flex-1">
          <div className="grid grid-cols-1 md:grid-cols-2 gap-6 items-center">
            {/* Left Column: Cover & Quick Stats */}
            <div className="flex flex-col items-center">
              <div className="relative w-full aspect-video rounded-2xl overflow-hidden bg-[#1a2040] border border-[#2b3566] shadow-lg mb-4">
                <img
                  src={`/api/cover/${song.id}`}
                  alt={song.title}
                  className="w-full h-full object-cover"
                  onError={(e) => {
                    (e.target as HTMLElement).style.display = 'none';
                  }}
                />
                <div className="absolute inset-0 flex flex-col items-center justify-center bg-gradient-to-br from-[#1d2347] to-[#10142e] text-slate-400 pointer-events-none p-4 text-center">
                  <span className="text-2xl font-black text-pink-400 mb-1">{song.artist}</span>
                  <span className="text-sm text-slate-300">{song.franchise}</span>
                </div>
              </div>

              <h2 className="text-xl font-black text-white text-center mb-1">{song.title}</h2>
              <p className="text-sm text-indigo-300 font-medium text-center mb-3">
                {song.artist} · {song.franchise}
              </p>

              {/* Tag Pills */}
              <div className="flex flex-wrap justify-center gap-1.5 mb-2">
                {song.tags.map((tag, idx) => (
                  <span
                    key={idx}
                    className="text-xs px-2.5 py-1 rounded-lg bg-[#1f274d] text-pink-300 border border-pink-500/30 font-semibold"
                  >
                    #{tag}
                  </span>
                ))}
              </div>
            </div>

            {/* Right Column: 5-Axis Radar Chart */}
            <div className="flex flex-col items-center justify-center bg-[#171c36] p-4 rounded-2xl border border-[#273059]">
              <div className="flex items-center gap-2 mb-2">
                <Sparkles className="w-4 h-4 text-pink-400" />
                <span className="text-xs font-bold uppercase tracking-wider text-slate-300">
                  演奏机能
                </span>
              </div>
              <RadarChart scores={song.radar} size={210} />

              {/* Numeric breakdown */}
              <div className="grid grid-cols-2 gap-2 w-full mt-4 text-xs font-mono">
                <div className="flex justify-between px-3 py-1.5 bg-[#101326] rounded-lg border border-[#202747]">
                  <span className="text-slate-400">瞬时 NPS</span>
                  <span className="text-pink-400 font-bold">{song.peak_nps}/s</span>
                </div>
                <div className="flex justify-between px-3 py-1.5 bg-[#101326] rounded-lg border border-[#202747]">
                  <span className="text-slate-400">物量</span>
                  <span className="text-cyan-400 font-bold">{song.notes_count} 音</span>
                </div>
                <div className="flex justify-between px-3 py-1.5 bg-[#101326] rounded-lg border border-[#202747]">
                  <span className="text-slate-400">速度</span>
                  <span className="text-yellow-400 font-bold">{Math.round(song.tempo)} BPM</span>
                </div>
                <div className="flex justify-between px-3 py-1.5 bg-[#101326] rounded-lg border border-[#202747]">
                  <span className="text-slate-400">小节</span>
                  <span className="text-purple-400 font-bold">{song.measures} Bars</span>
                </div>
              </div>
            </div>
          </div>

          {/* Backing Track Status */}
          <div className="p-4 rounded-2xl bg-[#171d38] border border-[#293466] flex flex-col md:flex-row items-center justify-between gap-4">
            <div className="flex items-center gap-3">
              <div className="p-3 rounded-xl bg-pink-600/20 text-pink-400 border border-pink-500/30">
                <Headphones className="w-6 h-6" />
              </div>
              <div>
                <h4 className="text-sm font-bold text-white">伴奏</h4>
                <p className="text-xs text-slate-400 mt-0.5">
                  {song.has_backing_track ? '无贝斯伴奏已就绪' : '未绑定伴奏'}
                </p>
              </div>
            </div>

            <button
              onClick={onLaunchUVR}
              className="flex items-center gap-1.5 px-3 py-2 rounded-xl text-xs font-semibold bg-[#222b52] hover:bg-[#2e3a6e] text-indigo-200 border border-[#3b4987] transition-colors whitespace-nowrap"
            >
              <Sliders className="w-4 h-4 text-pink-400" />
              <span>UVR5</span>
            </button>
          </div>
        </div>

        {/* Footer Actions */}
        <div className="p-6 border-t border-[#222a52] bg-[#14182e] flex flex-wrap items-center justify-end gap-3">
          {song.pdf_path && (
            <a
              href={`/api/export/pdf/${song.id}`}
              target="_blank"
              rel="noreferrer"
              className="flex items-center gap-2 px-4 py-2.5 rounded-xl text-xs font-semibold bg-[#1d2447] hover:bg-[#283263] text-indigo-300 border border-[#333f75] transition-colors"
            >
              <ExternalLink className="w-4 h-4" />
              <span>全屏看谱</span>
            </a>
          )}

          <button
            onClick={handleCopy}
            className="flex items-center gap-2 px-4 py-2.5 rounded-xl text-xs font-bold bg-[#1d2447] hover:bg-[#283263] text-slate-100 border border-[#37447d] transition-colors"
          >
            {copied ? (
              <>
                <Check className="w-4 h-4 text-emerald-400" />
                <span className="text-emerald-400">已复制</span>
              </>
            ) : (
              <>
                <Copy className="w-4 h-4 text-pink-400" />
                <span>发给平板</span>
              </>
            )}
          </button>

          <button
            onClick={() => {
              onStartPractice(song);
              onClose();
            }}
            className="flex items-center gap-2 px-6 py-2.5 rounded-xl text-sm font-bold bg-gradient-to-r from-pink-600 to-purple-600 hover:from-pink-500 hover:to-purple-500 text-white shadow-lg shadow-pink-600/30 transition-all scale-100 hover:scale-105"
          >
            <Play className="w-4 h-4 fill-current" />
            <span>开始练习</span>
          </button>
        </div>
      </div>
    </div>
  );
};
