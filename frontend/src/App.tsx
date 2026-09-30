import React, { useState, useEffect, useRef } from 'react';
import { Song, ActiveSession } from './types';
import { PracticeOverlay } from './components/PracticeOverlay';
import { AddSongModal } from './components/AddSongModal';
import { CalendarModal } from './components/CalendarModal';
import { EvaluationModal } from './components/EvaluationModal';
import { TitleBadge } from './components/TitleBadge';
import { BassStationLogo } from './components/BassStationLogo';
import {
  Search,
  Calendar,
  Sparkles,
  Guitar,
  Play,
  Copy,
  Check,
  Plus,
  Star,
  RefreshCw,
  Clock,
} from 'lucide-react';

export const BAND_COLORS: Record<string, { main: string; bg: string; border: string; text: string }> = {
  'Roselia': { main: '#5b21b6', bg: '#f5f3ff', border: '#ddd6fe', text: '#5b21b6' },
  'Morfonica': { main: '#0284c7', bg: '#f0f9ff', border: '#bae6fd', text: '#0284c7' },
  'Ave Mujica': { main: '#881337', bg: '#fff1f2', border: '#fecdd3', text: '#881337' },
  'MyGO!!!!!': { main: '#0e7490', bg: '#ecfeff', border: '#a5f3fc', text: '#0e7490' },
  "Poppin'Party": { main: '#db2777', bg: '#fdf2f8', border: '#fbcfe8', text: '#db2777' },
  'RAISE A SUILEN': { main: '#059669', bg: '#ecfdf5', border: '#a7f3d0', text: '#059669' },
  'Afterglow': { main: '#b91c1c', bg: '#fef2f2', border: '#fecaca', text: '#b91c1c' },
  'Pastel*Palettes': { main: '#10b981', bg: '#ecfdf5', border: '#a7f3d0', text: '#10b981' },
  'Hello, Happy World!': { main: '#f59e0b', bg: '#fffbeb', border: '#fde68a', text: '#d97706' },
};

const getBandTheme = (artist?: string) => {
  if (!artist) return { main: '#ff2d75', bg: '#fff1f5', border: '#fecdd6', text: '#ff2d75' };
  for (const [key, col] of Object.entries(BAND_COLORS)) {
    if (artist.includes(key)) return col;
  }
  return { main: '#ff2d75', bg: '#fff1f5', border: '#fecdd6', text: '#ff2d75' };
};

const formatDuration = (seconds?: number) => {
  if (!seconds) return '0:00';
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return `${m}:${s < 10 ? '0' : ''}${s}`;
};

const CATEGORIES = [
  { id: 'ALL', label: '所有' },
  { id: 'Roselia', label: 'Roselia' },
  { id: 'Morfonica', label: 'Morfonica' },
  { id: 'Ave Mujica', label: 'Ave Mujica' },
  { id: 'MyGO!!!!!', label: 'MyGO!!!!!' },
  { id: "Poppin'Party", label: "Poppin'Party" },
  { id: 'RAISE A SUILEN', label: 'RAISE A SUILEN' },
  { id: 'Afterglow', label: 'Afterglow' },
  { id: '5STRINGS', label: '5弦专区' },
  { id: 'OTHER', label: '其他乐队/个人' },
];

export function App() {
  const [songs, setSongs] = useState<Song[]>([]);
  const [loading, setLoading] = useState(true);

  // Filters & Navigation
  const [selectedCategory, setSelectedCategory] = useState('ALL');
  const [searchQuery, setSearchQuery] = useState('');
  const [tierFilter, setTierFilter] = useState<'ALL' | 'EASY' | 'NORMAL' | 'HARD' | 'EXPERT' | 'SPECIAL'>('ALL');
  const [sortOrder, setSortOrder] = useState<'LEVEL' | 'TITLE' | 'BPM'>('LEVEL');

  // Gamification
  const [masteryMap, setMasteryMap] = useState<Record<string, any>>({});

  // Selected Song
  const [selectedSong, setSelectedSong] = useState<Song | null>(null);

  // Modals
  const [showAddSong, setShowAddSong] = useState(false);
  const [showCalendarModal, setShowCalendarModal] = useState(false);
  const [showEvalModal, setShowEvalModal] = useState(false);
  const [copied, setCopied] = useState(false);
  const [toastMessage, setToastMessage] = useState<string | null>(null);

  // Practice Monitor
  const [activeSession, setActiveSession] = useState<ActiveSession | null>(null);
  const [lastCompleted, setLastCompleted] = useState<any | null>(null);
  const dismissedCompletedIdsRef = useRef<Set<string>>(new Set());

  const fetchSongs = () => {
    setLoading(true);
    fetch('/api/songs')
      .then((res) => res.json())
      .then((data) => {
        const loadedSongs: Song[] = data.songs || [];
        setSongs(loadedSongs);
        if (loadedSongs.length > 0) {
          const sorted = [...loadedSongs].sort((a, b) => (b.level || 0) - (a.level || 0));
          if (!selectedSong || !loadedSongs.some((s) => s.id === selectedSong.id)) {
            setSelectedSong(sorted[0]);
          }
        }
        setLoading(false);
      })
      .catch((err) => {
        console.error(err);
        setLoading(false);
      });
  };

  const checkPracticeStatus = () => {
    fetch('/api/practice/status')
      .then((res) => res.json())
      .then((data) => {
        if (data.is_practicing) {
          setActiveSession(data.active_session);
        } else {
          setActiveSession(null);
          if (data.last_completed) {
            const key = String(data.last_completed.id || data.last_completed.completed_at || data.last_completed.start_time);
            if (!dismissedCompletedIdsRef.current.has(key)) {
              dismissedCompletedIdsRef.current.add(key);
              setLastCompleted(data.last_completed);
            }
          }
        }
      })
      .catch(console.error);
  };

  const fetchMastery = () => {
    fetch('/api/gamification/mastery')
      .then((res) => res.json())
      .then((data) => setMasteryMap(data || {}))
      .catch(() => {});
  };

  useEffect(() => {
    fetchSongs();
    fetchMastery();
    checkPracticeStatus();
    const interval = setInterval(checkPracticeStatus, 3000);
    return () => clearInterval(interval);
  }, []);

  const showToast = (msg: string) => {
    setToastMessage(msg);
    setTimeout(() => setToastMessage(null), 3000);
  };

  const handleStartPractice = async () => {
    if (!selectedSong) return;
    try {
      const res = await fetch('/api/practice/start', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ song_id: selectedSong.id }),
      });
      const data = await res.json();
      if (data.is_practicing) {
        setActiveSession(data.active_session);
        showToast('练习计时已启动');
      }
    } catch (err) {
      console.error(err);
      showToast('启动练习失败');
    }
  };

  const handleStopPractice = async () => {
    try {
      const res = await fetch('/api/practice/stop', { method: 'POST' });
      const data = await res.json();
      setActiveSession(null);
      if (data.session) {
        setLastCompleted(data.session);
      }
    } catch (err) {
      console.error(err);
    }
  };

  const handleCopyClipboard = async () => {
    if (!selectedSong) return;
    try {
      const res = await fetch('/api/export/clipboard', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ song_id: selectedSong.id }),
      });
      const data = await res.json();
      if (data.success) {
        setCopied(true);
        setTimeout(() => setCopied(false), 2000);
        showToast('已复制 PDF 谱面到剪贴板，可直接粘贴发送至平板');
      } else {
        showToast(`复制失败: ${data.detail || '未找到乐谱文件'}`);
      }
    } catch (err) {
      showToast('调用剪贴板失败');
    }
  };

  const [aligning, setAligning] = useState(false);
  const [currentOffsetMs, setCurrentOffsetMs] = useState<number | null>(null);

  useEffect(() => {
    if (!selectedSong || !selectedSong.has_backing_track) {
      setCurrentOffsetMs(null);
      return;
    }
    fetch(`/api/audio/offset/${selectedSong.id}`)
      .then((res) => res.json())
      .then((data) => {
        if (data.success) {
          setCurrentOffsetMs(data.current_offset_ms);
        }
      })
      .catch(() => {});
  }, [selectedSong?.id]);

  const handleAutoAlignAudio = async () => {
    if (!selectedSong) return;
    setAligning(true);
    try {
      const res = await fetch('/api/audio/auto-align', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ song_id: selectedSong.id }),
      });
      const data = await res.json();
      if (data.success) {
        setCurrentOffsetMs(data.current_offset_ms);
        showToast(`伴奏对齐完成 (偏移: ${data.current_offset_ms}ms)`);
      } else {
        showToast(`自动对齐失败: ${data.error || '未知错误'}`);
      }
    } catch (err: any) {
      showToast(`对齐请求失败: ${err.message || '网络错误'}`);
    } finally {
      setAligning(false);
    }
  };

  const handleAdjustOffset = async (deltaMs: number) => {
    if (!selectedSong) return;
    try {
      const res = await fetch('/api/audio/adjust-offset', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ song_id: selectedSong.id, delta_ms: deltaMs }),
      });
      const data = await res.json();
      if (data.success) {
        setCurrentOffsetMs(data.current_offset_ms);
        showToast(`对齐偏移已更新: ${data.current_offset_ms}ms`);
      } else {
        showToast(`微调失败: ${data.error || '未能更新谱面'}`);
      }
    } catch (err: any) {
      showToast(`微调失败: ${err.message || '网络请求错误'}`);
    }
  };

  // Filter and Sort
  const filteredSongs = songs.filter((s) => {
    if (!s) return false;
    if (searchQuery) {
      const q = searchQuery.toLowerCase().trim();
      const match =
        (s.title || '').toLowerCase().includes(q) ||
        (s.artist || '').toLowerCase().includes(q) ||
        (s.franchise || '').toLowerCase().includes(q) ||
        (Array.isArray(s.tags) && s.tags.some((t) => (t || '').toLowerCase().includes(q)));
      if (!match) return false;
    }

    if (selectedCategory === '5STRINGS') {
      if (!s.is_5string) return false;
    } else if (selectedCategory === 'OTHER') {
      const mainBands = [
        'roselia', 'morfonica', 'ave mujica', 'mygo', "poppin'party",
        'raise a suilen', 'ras', 'afterglow', 'pastel', 'hello happy world'
      ];
      const artistLower = (s.artist || '').toLowerCase();
      if (mainBands.some((b) => artistLower.includes(b))) {
        return false;
      }
    } else if (selectedCategory !== 'ALL') {
      if (!(s.artist || '').toLowerCase().includes(selectedCategory.toLowerCase())) {
        return false;
      }
    }

    if (tierFilter !== 'ALL' && s.tier !== tierFilter) {
      return false;
    }

    return true;
  });

  filteredSongs.sort((a, b) => {
    if (sortOrder === 'LEVEL') return (b.level || 0) - (a.level || 0);
    if (sortOrder === 'BPM') return (b.tempo || 0) - (a.tempo || 0);
    return (a.title || '').localeCompare(b.title || '');
  });

  const activeTheme = getBandTheme(selectedSong?.artist);

  return (
    <div className="h-screen w-screen bg-[#f1f3f9] text-slate-900 flex flex-col select-none overflow-hidden font-sans">
      {/* Anime Stage Atmospheric Glow */}
      <div className="fixed inset-0 pointer-events-none z-0">
        <div className="absolute -top-32 left-1/4 w-[600px] h-[600px] bg-pink-200/20 rounded-full blur-[140px]" />
        <div className="absolute top-1/3 -right-32 w-[550px] h-[550px] bg-sky-200/20 rounded-full blur-[140px]" />
        <div className="absolute -bottom-32 left-1/3 w-[600px] h-[600px] bg-purple-200/20 rounded-full blur-[140px]" />
        <div
          className="absolute inset-0 opacity-[0.025]"
          style={{
            backgroundImage:
              'repeating-linear-gradient(45deg, #000, #000 1px, transparent 1px, transparent 20px)',
          }}
        />
      </div>

      {/* Toast Notification */}
      {toastMessage && (
        <div className="fixed top-4 right-6 z-50 flex items-center gap-2.5 px-4 py-2 rounded-full bg-slate-900 text-white font-bold text-xs shadow-2xl border border-white/20 animate-slideDown">
          <Sparkles className="w-3.5 h-3.5 text-[#ff2d75]" />
          <span>{toastMessage}</span>
        </div>
      )}

      {/* ================= TOP NAVIGATION BAR ================= */}
      <header className="relative z-20 h-16 bg-white/95 backdrop-blur-md border-b border-slate-200 px-6 flex items-center justify-between shadow-xs">
        {/* Left: Brand Identity & Sort Toggle */}
        <div className="flex items-center gap-5">
          <div className="flex items-center gap-3 group cursor-default">
            <BassStationLogo className="w-10 h-10 drop-shadow-sm group-hover:scale-105 transition-transform" />
            <div className="flex items-center gap-2">
              <span className="text-xl font-black tracking-tight text-slate-900 font-display">
                BASS<span className="text-[#ff2d75]">STATION</span>
              </span>
              <span className="px-2 py-0.5 rounded-full bg-pink-50 text-[#ff2d75] border border-pink-200 font-mono font-black text-xs tracking-wider">
                2.0
              </span>
            </div>
          </div>

          <div className="h-6 w-px bg-slate-200" />

          {/* Toggle Sort Button */}
          <button
            onClick={() => {
              if (sortOrder === 'LEVEL') setSortOrder('BPM');
              else if (sortOrder === 'BPM') setSortOrder('TITLE');
              else setSortOrder('LEVEL');
            }}
            className="flex items-center gap-2 px-4 py-1.5 rounded-full bg-slate-50 hover:bg-pink-50/70 border border-slate-200 hover:border-pink-300 text-sm font-bold text-slate-700 hover:text-pink-600 transition-all cursor-pointer"
          >
            <RefreshCw className="w-3.5 h-3.5 text-pink-500" />
            <span>排序: {sortOrder === 'LEVEL' ? '难度定数 ▼' : sortOrder === 'BPM' ? '速度 BPM ▼' : '曲名 A-Z'}</span>
          </button>
        </div>

        {/* Right Controls: Search, Calendar, Title Badge, Add Song */}
        <div className="flex items-center gap-3">
          {/* Search Box */}
          <div className="relative w-56 lg:w-64">
            <Search className="absolute left-3.5 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-400" />
            <input
              type="text"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              placeholder="搜索曲名、乐队..."
              className="w-full pl-10 pr-4 py-1.5 rounded-full bg-slate-100 border border-slate-200 text-sm text-slate-900 placeholder:text-slate-400 focus:outline-none focus:bg-white focus:border-pink-500 focus:ring-2 focus:ring-pink-200 transition-all font-medium"
            />
          </div>

          {/* Practice Calendar */}
          <button
            onClick={() => setShowCalendarModal(true)}
            className="flex items-center gap-2 px-3.5 py-1.5 rounded-full bg-white border border-slate-200 text-sm font-bold text-slate-700 hover:text-pink-600 hover:border-pink-300 shadow-xs transition-all cursor-pointer"
            title="查看练琴打卡日历"
          >
            <Calendar className="w-4 h-4 text-slate-500" />
            <span>练琴日历</span>
          </button>

          {/* BanG Dream! Titles & Badges */}
          <TitleBadge />

          {/* Add Song Button */}
          <button
            onClick={() => setShowAddSong(true)}
            className="flex items-center gap-2 px-4.5 py-1.5 rounded-full bg-gradient-to-r from-[#ff2d75] to-[#ff4081] hover:brightness-105 text-white font-black text-sm shadow-md shadow-pink-500/20 transition-all hover:scale-105 active:scale-95 cursor-pointer"
          >
            <Plus className="w-4 h-4" />
            <span>新增曲目</span>
          </button>
        </div>
      </header>

      {/* ================= MAIN THREE-COLUMN STAGE ================= */}
      <div className="relative z-10 flex-1 flex overflow-hidden">
        {/* 1. LEFT: BAND SELECTOR DRAWER */}
        <aside className="w-32 bg-white/95 border-r border-slate-200 flex flex-col py-3 overflow-y-auto space-y-1.5 shrink-0 px-2.5 shadow-xs">
          {CATEGORIES.map((cat) => {
            const isActive = selectedCategory === cat.id;
            return (
              <button
                key={cat.id}
                onClick={() => setSelectedCategory(cat.id)}
                className={`relative w-full py-2.5 px-3 rounded-2xl text-left transition-all flex items-center justify-between cursor-pointer font-black text-sm ${
                  isActive
                    ? 'bg-[#ff2d75] text-white shadow-md shadow-pink-500/25'
                    : 'bg-white hover:bg-slate-50 text-slate-700 border border-slate-200/80 shadow-xs'
                }`}
              >
                <span className="truncate">{cat.label}</span>
                {isActive && <div className="w-1.5 h-4 bg-white rounded-full shrink-0" />}
              </button>
            );
          })}
        </aside>

        {/* 2. CENTER: ARCADE SONG SELECTION DECK */}
        <section className="w-96 lg:w-[430px] bg-[#f8fafd] border-r border-slate-200 flex flex-col overflow-hidden shrink-0">
          <div className="px-5 py-3 border-b border-slate-200/80 flex items-center justify-between text-sm font-bold text-slate-700 bg-white/80">
            <span>曲目列表 ({filteredSongs.length})</span>
            <span className="text-pink-600 font-mono text-xs font-black">Lv.6 ~ 31</span>
          </div>

          <div className="flex-1 overflow-y-auto p-2.5 space-y-2">
            {loading ? (
              <div className="flex flex-col items-center justify-center py-20 text-slate-400 space-y-2">
                <RefreshCw className="w-6 h-6 animate-spin text-[#ff2d75]" />
                <span className="text-sm font-bold font-mono">LOADING STAGE...</span>
              </div>
            ) : filteredSongs.length > 0 ? (
              filteredSongs.map((song) => {
                const isSelected = selectedSong?.id === song.id;
                const isPracticingThis = activeSession?.song_id === song.id;

                return (
                  <div
                    key={song.id}
                    onClick={() => setSelectedSong(song)}
                    className={`relative w-full rounded-2xl p-3 cursor-pointer transition-all duration-150 flex items-center justify-between overflow-hidden ${
                      isSelected
                        ? 'bg-gradient-to-r from-[#ff2d75] to-[#ff4081] text-white shadow-lg shadow-pink-500/30'
                        : 'bg-white hover:bg-pink-50/40 text-slate-800 border border-slate-200/80 shadow-xs'
                    } ${isPracticingThis ? 'ring-2 ring-amber-400 animate-pulse' : ''}`}
                  >
                    {/* Left Album Cover Thumbnail & Info */}
                    <div className="flex items-center gap-3 min-w-0">
                      <div className="relative w-14 h-14 rounded-xl overflow-hidden shrink-0 border border-black/10 shadow-xs bg-slate-100">
                        <img
                          src={`/api/cover/${song.id}`}
                          alt={song.title}
                          className="w-full h-full object-cover"
                          loading="lazy"
                        />
                        {song.has_backing_track && (
                          <div
                            className="absolute bottom-0 right-0 w-3 h-3 bg-emerald-500 rounded-tl-sm shadow-xs"
                            title="伴奏就绪"
                          />
                        )}
                      </div>

                      <div className="min-w-0">
                        <div className="flex items-center gap-1.5">
                          <h4
                            className={`text-base font-bold truncate max-w-[175px] leading-tight ${
                              isSelected ? 'text-white font-black' : 'text-slate-900'
                            }`}
                          >
                            {song.title}
                          </h4>
                          {song.is_5string && (
                            <span
                              className={`px-1.5 py-0.5 rounded text-xs font-black shrink-0 ${
                                isSelected ? 'bg-amber-400 text-amber-950' : 'bg-amber-500 text-white'
                              }`}
                            >
                              5弦
                            </span>
                          )}
                          {masteryMap[song.id]?.mastery_level && (
                            <span
                              className={`px-1.5 py-0.5 rounded text-xs font-black shrink-0 ${
                                masteryMap[song.id].mastery_level === '精通'
                                  ? 'bg-amber-400 text-amber-950 ring-1 ring-amber-300'
                                  : isSelected
                                  ? 'bg-white/20 text-white'
                                  : 'bg-pink-500 text-white'
                              }`}
                            >
                              {masteryMap[song.id].mastery_level}
                            </span>
                          )}
                        </div>
                        <p
                          className={`text-sm truncate max-w-[175px] mt-0.5 font-medium ${
                            isSelected ? 'text-pink-100' : 'text-slate-500'
                          }`}
                        >
                          {song.artist}
                        </p>
                        <p
                          className={`text-xs font-mono mt-0.5 ${
                            isSelected ? 'text-pink-200' : 'text-slate-400'
                          }`}
                        >
                          {formatDuration(song.duration)} · BPM {Math.round(song.tempo)}
                        </p>
                      </div>
                    </div>

                    {/* Right Fixed Level Display & Rank */}
                    <div className="flex flex-col items-end shrink-0 pl-2">
                      <div className="flex items-center gap-1.5 mb-0.5">
                        {song.best_grade && (
                          <span
                            className={`px-2 py-0.5 rounded text-xs font-black font-mono border ${
                              isSelected
                                ? 'bg-white/20 text-white border-white/40'
                                : 'bg-pink-50 text-pink-700 border-pink-200'
                            }`}
                          >
                            {song.best_grade}
                          </span>
                        )}
                        <span
                          className={`text-base font-mono font-black ${
                            isSelected ? 'text-white' : 'text-[#ff2d75]'
                          }`}
                        >
                          Lv.{song.level}
                        </span>
                      </div>
                      <span
                        className={`text-xs font-bold uppercase font-mono tracking-wider ${
                          isSelected ? 'text-pink-200' : 'text-slate-400'
                        }`}
                      >
                        {song.tier}
                      </span>
                    </div>
                  </div>
                );
              })
            ) : (
              <div className="text-center py-20 text-slate-400 text-sm font-bold font-mono">
                NO TRACK FOUND
              </div>
            )}
          </div>
        </section>

        {/* 3. RIGHT: BANG DREAM! OPEN LIVE STAGE */}
        <main className="flex-1 bg-[#f4f6fb] p-6 lg:p-8 overflow-y-auto flex flex-col justify-between">
          {selectedSong ? (
            <div className="max-w-5xl mx-auto w-full flex-1 flex flex-col justify-between space-y-6">
              {/* Franchise Pill Top-Right */}
              <div className="flex justify-end">
                <span className="px-3.5 py-1 rounded-full bg-white border border-slate-200/80 text-xs font-mono font-bold text-slate-600 shadow-xs uppercase tracking-wider">
                  {selectedSong.franchise || 'BANG DREAM!'}
                </span>
              </div>

              {/* UPPER MAIN ROW: Left Album Jacket & Title + Right Floating Status Cards */}
              <div className="grid grid-cols-1 md:grid-cols-2 gap-8 lg:gap-12 items-center">
                {/* Left Column: Big Album Jacket + Centered Title & Artist */}
                <div className="flex flex-col items-center">
                  <div className="relative w-64 h-64 sm:w-72 sm:h-72 lg:w-80 lg:h-80 rounded-3xl overflow-hidden shadow-2xl border-4 border-white bg-slate-100 group">
                    <img
                      src={`/api/cover/${selectedSong.id}`}
                      alt={selectedSong.title}
                      className="w-full h-full object-cover group-hover:scale-105 transition-transform duration-500"
                    />
                    <div className="absolute top-3 right-3 px-3 py-1 rounded-full bg-slate-900/80 backdrop-blur-md text-white font-mono text-xs font-black uppercase tracking-wider shadow-md">
                      {selectedSong.tier}
                    </div>
                  </div>

                  <h1 className="text-3xl lg:text-4xl font-black text-slate-900 tracking-tight text-center mt-5 line-clamp-1">
                    {selectedSong.title}
                  </h1>

                  <p className="text-base font-bold text-[#ff2d75] text-center mt-1.5">
                    {selectedSong.artist}
                    {selectedSong.franchise ? ` · ${selectedSong.franchise}` : ''}
                  </p>

                  <div className="flex items-center gap-2 pt-1.5 text-sm text-slate-500 font-mono font-bold">
                    <span>BPM {Math.round(selectedSong.tempo)}</span>
                    <span>·</span>
                    <span>{selectedSong.is_5string ? '5弦 [BEADG]' : '4弦 [EADG]'}</span>
                    <span>·</span>
                    <span>{formatDuration(selectedSong.duration)}</span>
                  </div>
                </div>

                {/* Right Column: Floating Clean Cards */}
                <div className="space-y-4 flex flex-col justify-center">
                  {/* Card 1: 演奏评测成绩 (Clean, NO decorative icon) */}
                  <div
                    onClick={() => setShowEvalModal(true)}
                    className="p-5 rounded-2xl bg-white border border-slate-200/80 shadow-xs hover:shadow-md transition-all cursor-pointer group"
                  >
                    <div className="flex items-center justify-between pb-2.5 border-b border-slate-100">
                      <span className="text-sm font-bold text-slate-800">
                        演奏评测成绩
                      </span>
                      <span className="text-sm font-bold text-[#ff2d75] group-hover:underline">
                        上传/录音打分 ➔
                      </span>
                    </div>
                    <div className="flex items-center justify-between pt-3">
                      <div className="flex items-baseline gap-2">
                        <span className="text-3xl lg:text-4xl font-mono font-black text-slate-900">
                          {selectedSong.best_score != null && selectedSong.best_score > 0
                            ? selectedSong.best_score.toFixed(1)
                            : '--'}
                        </span>
                        <span className="text-sm text-slate-400 font-bold">
                          {selectedSong.best_score != null && selectedSong.best_score > 0 ? '分' : '暂未评测'}
                        </span>
                      </div>
                      {selectedSong.best_grade ? (
                        <div
                          className={`px-3.5 py-1 rounded-xl text-sm font-black font-mono border shadow-xs ${
                            selectedSong.best_grade === 'SS'
                              ? 'bg-amber-100 text-amber-700 border-amber-300'
                              : selectedSong.best_grade === 'S'
                              ? 'bg-pink-100 text-pink-700 border-pink-300'
                              : 'bg-purple-100 text-purple-700 border-purple-300'
                          }`}
                        >
                          RANK {selectedSong.best_grade}
                        </div>
                      ) : (
                        <div className="px-3 py-1 rounded-xl bg-slate-100 text-slate-400 text-xs font-mono font-bold">
                          NO RANK
                        </div>
                      )}
                    </div>
                  </div>

                  {/* Card 2: 乐曲定数 (RATING) */}
                  <div className="p-5 rounded-2xl bg-white border border-slate-200/80 shadow-xs flex items-center justify-between">
                    <span className="text-sm font-bold text-slate-700 font-mono">
                      乐曲定数 (RATING)
                    </span>
                    <div className="flex items-center gap-3">
                      <span className="px-3 py-1 rounded-full bg-[#ff2d75] text-white text-xs font-black font-mono tracking-wider shadow-xs">
                        {selectedSong.tier}
                      </span>
                      <span className="text-4xl font-black font-mono text-[#ff2d75]">
                        {selectedSong.level}
                      </span>
                    </div>
                  </div>

                  {/* Feature Tags below Rating */}
                  <div className="flex flex-wrap gap-2">
                    {(Array.isArray(selectedSong.tags) ? selectedSong.tags : []).map((tag, idx) => (
                      <span
                        key={idx}
                        className="px-3.5 py-1 rounded-full bg-white text-slate-700 border border-slate-200/80 text-sm font-bold shadow-xs"
                      >
                        #{tag}
                      </span>
                    ))}
                  </div>

                  {/* Card 3: 发送至平板 (Concise, NO parenthetical) */}
                  <button
                    onClick={handleCopyClipboard}
                    className="w-full p-4 rounded-2xl bg-white hover:bg-pink-50/50 border border-slate-200/80 hover:border-pink-300 text-slate-800 text-sm font-bold flex items-center justify-between transition-all shadow-xs group cursor-pointer"
                  >
                    <div className="flex items-center gap-2.5">
                      {copied ? (
                        <>
                          <Check className="w-4 h-4 text-emerald-500" />
                          <span className="text-emerald-600 font-bold">已复制配套 PDF</span>
                        </>
                      ) : (
                        <>
                          <Copy className="w-4 h-4 text-[#ff2d75] group-hover:scale-110 transition-transform" />
                          <span>发送至平板</span>
                        </>
                      )}
                    </div>
                    <span className="text-xs px-3 py-1 rounded-full bg-pink-100 text-pink-700 font-bold font-mono">
                      PDF 就绪
                    </span>
                  </button>

                  {/* Card 4: 伴奏节拍对齐 (NO headphone icon, NO sparkle icon) */}
                  {selectedSong.has_backing_track && (
                    <div className="p-5 rounded-2xl bg-white border border-slate-200/80 shadow-xs space-y-3">
                      <div className="flex items-center justify-between">
                        <span className="text-sm font-bold text-slate-800">
                          伴奏节拍对齐
                        </span>
                        <button
                          onClick={handleAutoAlignAudio}
                          disabled={aligning}
                          className="px-3.5 py-1.5 rounded-full bg-gradient-to-r from-[#ff2d75] to-[#ff4081] text-white text-xs font-bold shadow-xs hover:brightness-105 active:scale-95 disabled:opacity-50 transition-all cursor-pointer"
                        >
                          {aligning ? '正在对齐...' : '自动对齐伴奏'}
                        </button>
                      </div>

                      <div className="flex items-center justify-between px-3.5 py-2 rounded-xl bg-slate-50 border border-slate-100 text-sm font-mono">
                        <span className="text-slate-600 font-bold">当前对齐偏移:</span>
                        <span className="font-bold text-[#ff2d75] text-base">
                          {currentOffsetMs !== null ? `${currentOffsetMs > 0 ? '+' : ''}${currentOffsetMs} ms` : '--'}
                        </span>
                      </div>

                      <div className="grid grid-cols-6 gap-2">
                        {[
                          { label: '-50ms', delta: -50 },
                          { label: '-10ms', delta: -10 },
                          { label: '+10ms', delta: 10 },
                          { label: '+50ms', delta: 50 },
                          { label: '-1拍', delta: -Math.round(60000.0 / (selectedSong.tempo || 120)), isBeat: true },
                          { label: '+1拍', delta: Math.round(60000.0 / (selectedSong.tempo || 120)), isBeat: true },
                        ].map((btn, idx) => (
                          <button
                            key={idx}
                            onClick={() => handleAdjustOffset(btn.delta)}
                            className={`py-1.5 rounded-lg text-xs font-mono font-bold transition-colors cursor-pointer border ${
                              btn.isBeat
                                ? 'bg-pink-50 border-pink-200 text-[#ff2d75] hover:bg-pink-100'
                                : 'bg-slate-50 border-slate-200/80 text-slate-700 hover:bg-slate-100'
                            }`}
                          >
                            {btn.label}
                          </button>
                        ))}
                      </div>
                    </div>
                  )}
                </div>
              </div>

              {/* LOWER ROW: 5 Difficulty Discs + Big Practice Button */}
              <div className="space-y-4 pt-2">
                {/* 5 Difficulty Discs */}
                <div className="flex items-center justify-center gap-3.5">
                  {[
                    { key: 'EASY', label: 'EASY', color: 'from-sky-400 to-blue-500', activeRing: 'ring-sky-300' },
                    { key: 'NORMAL', label: 'NORMAL', color: 'from-emerald-400 to-green-500', activeRing: 'ring-emerald-300' },
                    { key: 'HARD', label: 'HARD', color: 'from-amber-400 to-yellow-500', activeRing: 'ring-amber-300' },
                    { key: 'EXPERT', label: 'EXPERT', color: 'from-rose-500 to-pink-600', activeRing: 'ring-rose-300' },
                    { key: 'SPECIAL', label: 'SPECIAL', color: 'from-purple-500 to-fuchsia-600', activeRing: 'ring-purple-300' },
                  ].map((disc) => {
                    const isCurrentSongTier = selectedSong.tier === disc.key;
                    const isFilterActive = tierFilter === disc.key;
                    return (
                      <button
                        key={disc.key}
                        onClick={() => {
                          setTierFilter(tierFilter === disc.key ? 'ALL' : (disc.key as any));
                        }}
                        className={`w-16 h-16 rounded-full flex flex-col items-center justify-center transition-all duration-200 cursor-pointer ${
                          isCurrentSongTier
                            ? `bg-gradient-to-tr ${disc.color} text-white shadow-lg shadow-purple-500/30 scale-105 ring-4 ${disc.activeRing}`
                            : isFilterActive
                            ? `bg-gradient-to-tr ${disc.color} text-white ring-2 ring-slate-400`
                            : 'bg-white text-slate-400 hover:text-slate-600 border border-slate-200/80 shadow-xs'
                        }`}
                        title={`筛选 ${disc.label}`}
                      >
                        <Star
                          className={`w-4 h-4 ${
                            isCurrentSongTier || isFilterActive ? 'fill-white text-white' : 'fill-slate-300 text-slate-300'
                          }`}
                        />
                        <span className="text-[10px] font-black tracking-wider uppercase font-mono mt-0.5">
                          {disc.label}
                        </span>
                      </button>
                    );
                  })}
                </div>

                {/* Primary Action Button: STRICTLY '▶ 进入练习' */}
                <button
                  onClick={handleStartPractice}
                  className="w-full max-w-xl mx-auto py-4 rounded-2xl bg-gradient-to-r from-[#ff2d75] to-[#ff3b7c] hover:brightness-105 text-white font-black text-xl tracking-wider shadow-xl shadow-pink-500/35 flex items-center justify-center gap-3 transition-all transform hover:scale-[1.01] active:scale-98 cursor-pointer"
                >
                  <Play className="w-6 h-6 fill-current" />
                  <span>进入练习</span>
                </button>
              </div>
            </div>
          ) : (
            <div className="flex flex-col items-center justify-center h-full text-slate-400 space-y-2">
              <Guitar className="w-12 h-12 text-pink-400/30" />
              <p className="text-sm font-bold text-slate-500 font-mono">PLEASE SELECT A SONG</p>
            </div>
          )}
        </main>
      </div>

      {/* Floating Active Practice Timer Bar */}
      <PracticeOverlay
        activeSession={activeSession}
        lastCompleted={lastCompleted}
        onStopPractice={handleStopPractice}
        onCloseCompleted={() => {
          if (lastCompleted) {
            const key = String(lastCompleted.id || lastCompleted.completed_at || lastCompleted.start_time);
            dismissedCompletedIdsRef.current.add(key);
          }
          setLastCompleted(null);
          fetch('/api/practice/clear-completed', { method: 'POST' }).catch(() => {});
        }}
      />

      {/* Unified Add Song Modal */}
      {showAddSong && (
        <AddSongModal
          onClose={() => setShowAddSong(false)}
          onSuccess={() => {
            fetchSongs();
            setShowAddSong(false);
          }}
        />
      )}

      {/* Evaluation Modal */}
      {showEvalModal && selectedSong && (
        <EvaluationModal
          song={selectedSong}
          onClose={() => {
            setShowEvalModal(false);
            fetchSongs();
            fetchMastery();
          }}
          onScoreUpdated={() => {
            fetchSongs();
            fetchMastery();
          }}
        />
      )}

      {/* Activity Heatmap Modal */}
      {showCalendarModal && (
        <CalendarModal onClose={() => setShowCalendarModal(false)} />
      )}
    </div>
  );
}

export default App;
