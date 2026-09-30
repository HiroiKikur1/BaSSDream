import React, { useState } from 'react';
import { Song } from '../types';
import { Play, Copy, Check, Music2, Headphones, Activity } from 'lucide-react';

interface SongCardProps {
  song: Song;
  onSelect: (song: Song) => void;
  onStartPractice: (song: Song) => void;
  onCopyClipboard: (song: Song) => void;
  isPracticingThis: boolean;
}

const BAND_COLORS: Record<string, { bg: string; text: string; border: string }> = {
  'Roselia': { bg: 'bg-purple-950/80', text: 'text-purple-300', border: 'border-purple-500/40' },
  'Morfonica': { bg: 'bg-sky-950/80', text: 'text-sky-300', border: 'border-sky-500/40' },
  'Ave Mujica': { bg: 'bg-rose-950/80', text: 'text-rose-300', border: 'border-rose-500/40' },
  'MyGO!!!!!': { bg: 'bg-cyan-950/80', text: 'text-cyan-300', border: 'border-cyan-500/40' },
  "Poppin'Party": { bg: 'bg-pink-950/80', text: 'text-pink-300', border: 'border-pink-500/40' },
  'RAISE A SUILEN': { bg: 'bg-emerald-950/80', text: 'text-emerald-300', border: 'border-emerald-500/40' },
  'Afterglow': { bg: 'bg-red-950/80', text: 'text-red-300', border: 'border-red-500/40' },
};

export const SongCard: React.FC<SongCardProps> = ({
  song,
  onSelect,
  onStartPractice,
  onCopyClipboard,
  isPracticingThis,
}) => {
  const [copied, setCopied] = useState(false);

  const bandStyle = BAND_COLORS[song.artist] || {
    bg: 'bg-slate-900/80',
    text: 'text-indigo-300',
    border: 'border-indigo-500/30',
  };

  const tierColors = {
    SPECIAL: 'from-amber-400 via-rose-500 to-purple-600 text-white shadow-rose-500/30',
    EXPERT: 'from-rose-600 to-pink-500 text-white shadow-pink-500/30',
    HARD: 'from-amber-500 to-yellow-400 text-slate-950 font-bold shadow-amber-500/30',
    NORMAL: 'from-blue-600 to-cyan-500 text-white shadow-cyan-500/30',
  }[song.tier] || 'from-rose-600 to-pink-500 text-white';

  const formatDuration = (sec: number) => {
    const m = Math.floor(sec / 60);
    const s = Math.floor(sec % 60);
    return `${m}:${s < 10 ? '0' : ''}${s}`;
  };

  const handleCopy = (e: React.MouseEvent) => {
    e.stopPropagation();
    onCopyClipboard(song);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  const handlePlay = (e: React.MouseEvent) => {
    e.stopPropagation();
    onStartPractice(song);
  };

  return (
    <div
      onClick={() => onSelect(song)}
      className={`group relative flex flex-col rounded-2xl bg-gradient-to-b from-[#161a33]/90 to-[#0e1124]/90 p-4 backdrop-blur-md border border-[#27305c]/60 hover:border-pink-500/60 transition-all duration-300 hover:shadow-xl hover:shadow-pink-500/10 cursor-pointer overflow-hidden ${
        isPracticingThis ? 'ring-2 ring-pink-500 shadow-lg shadow-pink-500/30' : ''
      }`}
    >
      {/* Glow highlight */}
      <div className="absolute -top-16 -right-16 w-32 h-32 bg-pink-500/10 rounded-full blur-2xl group-hover:bg-pink-500/25 transition-all duration-500" />

      {/* Top Banner: Cover & Badges */}
      <div className="relative aspect-video w-full rounded-xl overflow-hidden bg-[#1c2242] mb-3 flex items-center justify-center border border-[#2b3566]">
        {/* Cover image via API or Band themed art */}
        <img
          src={`/api/cover/${song.id}`}
          alt={song.title}
          className="w-full h-full object-cover transition-transform duration-500 group-hover:scale-105"
          onError={(e) => {
            // Fallback gradient cover
            (e.target as HTMLElement).style.display = 'none';
          }}
        />

        {/* Fallback art if no cover */}
        <div className="absolute inset-0 flex flex-col items-center justify-center bg-gradient-to-br from-[#1b2042] to-[#11142e] text-slate-400 p-2 text-center pointer-events-none">
          <Music2 className="w-10 h-10 mb-1 text-pink-400/60" />
          <span className="text-xs font-semibold text-slate-300 line-clamp-1">{song.artist}</span>
        </div>

        {/* Difficulty Badge (BanG Dream! style) */}
        <div className="absolute top-2 right-2">
          <div
            className={`px-3 py-1 rounded-full text-xs font-black tracking-wider uppercase bg-gradient-to-r shadow-md ${tierColors}`}
          >
            {song.tier} {song.level}
          </div>
        </div>

        {/* 5弦 / 4弦 Badge */}
        <div className="absolute top-2 left-2">
          <span
            className={`px-2 py-0.5 rounded-md text-[11px] font-bold tracking-tight border ${
              song.is_5string
                ? 'bg-amber-950/80 text-amber-300 border-amber-500/50'
                : 'bg-blue-950/80 text-blue-300 border-blue-500/50'
            }`}
          >
            {song.is_5string ? '5弦 BEADG' : '4弦 EADG'}
          </span>
        </div>

        {/* Backing track audio badge */}
        {song.has_backing_track && (
          <div className="absolute bottom-2 left-2 flex items-center gap-1 px-2 py-0.5 rounded-full bg-emerald-950/80 border border-emerald-500/50 text-emerald-300 text-[10px] font-semibold">
            <Headphones className="w-3 h-3" />
            <span>伴奏已就绪</span>
          </div>
        )}
      </div>

      {/* Title & Artist */}
      <div className="flex-1 mb-2">
        <h3 className="text-base font-bold text-slate-100 group-hover:text-pink-400 transition-colors line-clamp-1">
          {song.title}
        </h3>
        <div className="flex items-center gap-2 mt-1">
          <span
            className={`text-xs px-2 py-0.5 rounded-full border ${bandStyle.bg} ${bandStyle.text} ${bandStyle.border} font-medium`}
          >
            {song.artist}
          </span>
          <span className="text-xs text-slate-400 font-mono">
            BPM {Math.round(song.tempo)}
          </span>
          <span className="text-xs text-slate-500 font-mono">
            {formatDuration(song.duration)}
          </span>
        </div>
      </div>

      {/* Feature tags */}
      <div className="flex flex-wrap gap-1 mb-3">
        {song.tags.slice(0, 3).map((tag, idx) => (
          <span
            key={idx}
            className="text-[10px] px-1.5 py-0.5 rounded bg-[#1e2547] text-indigo-200 border border-[#313c72]"
          >
            #{tag}
          </span>
        ))}
      </div>

      {/* Action Buttons */}
      <div className="grid grid-cols-2 gap-2 mt-auto pt-2 border-t border-[#232b52]">
        <button
          onClick={handlePlay}
          className={`flex items-center justify-center gap-1.5 py-2 px-3 rounded-xl font-bold text-xs transition-all ${
            isPracticingThis
              ? 'bg-rose-600 text-white shadow-lg shadow-rose-600/40 animate-pulse'
              : 'bg-gradient-to-r from-pink-600 to-purple-600 hover:from-pink-500 hover:to-purple-500 text-white shadow-md shadow-pink-600/20'
          }`}
        >
          <Play className="w-3.5 h-3.5 fill-current" />
          <span>{isPracticingThis ? '练习中...' : '开始练习'}</span>
        </button>

        <button
          onClick={handleCopy}
          className="flex items-center justify-center gap-1.5 py-2 px-2.5 rounded-xl font-medium text-xs bg-[#1a2142] hover:bg-[#252f5e] text-slate-200 border border-[#323f78] transition-colors"
        >
          {copied ? (
            <>
              <Check className="w-3.5 h-3.5 text-emerald-400" />
              <span className="text-emerald-400 font-bold">已复制!</span>
            </>
          ) : (
            <>
              <Copy className="w-3.5 h-3.5 text-indigo-300" />
              <span>发给平板</span>
            </>
          )}
        </button>
      </div>
    </div>
  );
};
