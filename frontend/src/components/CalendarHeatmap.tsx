import React, { useEffect, useState } from 'react';
import { PracticeStats } from '../types';
import { Flame, Clock, Calendar as CalendarIcon, Trophy, History } from 'lucide-react';

export const CalendarHeatmap: React.FC = () => {
  const [stats, setStats] = useState<PracticeStats | null>(null);
  const [heatmap, setHeatmap] = useState<Record<string, { minutes: number; sessions: number }>>({});
  const [recent, setRecent] = useState<any[]>([]);

  useEffect(() => {
    fetch('/api/practice/stats')
      .then((r) => r.json())
      .then((d) => setStats(d))
      .catch(console.error);

    fetch('/api/practice/heatmap')
      .then((r) => r.json())
      .then((d) => setHeatmap(d))
      .catch(console.error);

    fetch('/api/practice/recent')
      .then((r) => r.json())
      .then((d) => setRecent(d))
      .catch(console.error);
  }, []);

  // Generate past 16 weeks (112 days) grid
  const days = [];
  const today = new Date();
  for (let i = 111; i >= 0; i--) {
    const d = new Date(today);
    d.setDate(d.getDate() - i);
    const dateStr = d.toISOString().split('T')[0];
    const data = heatmap[dateStr] || { minutes: 0, sessions: 0 };
    days.push({
      date: dateStr,
      dayOfWeek: d.getDay(),
      minutes: data.minutes,
      sessions: data.sessions,
    });
  }

  const getColor = (mins: number) => {
    if (mins <= 0) return 'bg-slate-100 border-slate-200';
    if (mins < 15) return 'bg-pink-100 border-pink-200';
    if (mins < 30) return 'bg-pink-300 border-pink-300';
    if (mins < 60) return 'bg-[#ff2d75] border-[#ff2d75] shadow-sm';
    return 'bg-gradient-to-tr from-[#ff2d75] to-amber-400 border-amber-300 shadow-md';
  };

  return (
    <div className="space-y-6">
      {/* 4 Overview Stat Cards */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        <div className="p-5 rounded-2xl bg-white border border-slate-200 flex items-center gap-4 shadow-sm">
          <div className="p-3.5 rounded-2xl bg-pink-50 text-pink-600 border border-pink-200">
            <Clock className="w-7 h-7" />
          </div>
          <div>
            <span className="text-xs font-bold uppercase tracking-wider text-slate-400">
              累计练习时长
            </span>
            <p className="text-2xl font-black text-slate-900 font-mono">
              {stats?.total_hours || 0} <span className="text-sm font-normal text-slate-500">小时</span>
            </p>
          </div>
        </div>

        <div className="p-5 rounded-2xl bg-white border border-slate-200 flex items-center gap-4 shadow-sm">
          <div className="p-3.5 rounded-2xl bg-amber-50 text-amber-600 border border-amber-200">
            <Flame className="w-7 h-7" />
          </div>
          <div>
            <span className="text-xs font-bold uppercase tracking-wider text-slate-400">
              连续练琴天数
            </span>
            <p className="text-2xl font-black text-amber-600 font-mono">
              {stats?.streak_days || 0} <span className="text-sm font-normal text-slate-500">天</span>
            </p>
          </div>
        </div>

        <div className="p-5 rounded-2xl bg-white border border-slate-200 flex items-center gap-4 shadow-sm">
          <div className="p-3.5 rounded-2xl bg-cyan-50 text-cyan-600 border border-cyan-200">
            <CalendarIcon className="w-7 h-7" />
          </div>
          <div>
            <span className="text-xs font-bold uppercase tracking-wider text-slate-400">
              今日练习专注
            </span>
            <p className="text-2xl font-black text-cyan-600 font-mono">
              {stats?.today_minutes || 0} <span className="text-sm font-normal text-slate-500">分钟</span>
            </p>
          </div>
        </div>

        <div className="p-5 rounded-2xl bg-white border border-slate-200 flex items-center gap-4 shadow-sm">
          <div className="p-3.5 rounded-2xl bg-purple-50 text-purple-600 border border-purple-200">
            <Trophy className="w-7 h-7" />
          </div>
          <div>
            <span className="text-xs font-bold uppercase tracking-wider text-slate-400">
              总打卡场次
            </span>
            <p className="text-2xl font-black text-purple-600 font-mono">
              {stats?.total_sessions || 0} <span className="text-sm font-normal text-slate-500">次</span>
            </p>
          </div>
        </div>
      </div>

      {/* Heatmap Grid Panel */}
      <div className="p-6 rounded-3xl bg-white border border-slate-200 shadow-sm">
        <div className="flex items-center justify-between mb-4">
          <div className="flex items-center gap-2">
            <Flame className="w-5 h-5 text-[#ff2d75]" />
            <h3 className="text-base font-black text-slate-900">练琴打卡热力图</h3>
          </div>
          <div className="flex items-center gap-2 text-xs text-slate-500 font-medium">
            <span>休止</span>
            <div className="w-3 h-3 rounded-sm bg-slate-100 border border-slate-200" />
            <div className="w-3 h-3 rounded-sm bg-pink-100 border border-pink-200" />
            <div className="w-3 h-3 rounded-sm bg-pink-300 border border-pink-300" />
            <div className="w-3 h-3 rounded-sm bg-[#ff2d75] border-[#ff2d75]" />
            <div className="w-3 h-3 rounded-sm bg-amber-400 border border-amber-300" />
            <span>高负荷 (60m+)</span>
          </div>
        </div>

        <div className="overflow-x-auto pb-2">
          <div className="grid grid-rows-7 grid-flow-col gap-1.5 min-w-[700px]">
            {days.map((d, i) => (
              <div
                key={i}
                title={`${d.date}: 练习 ${d.minutes} 分钟 (${d.sessions} 次)`}
                className={`w-4 h-4 rounded-sm border transition-all duration-200 hover:scale-125 cursor-pointer ${getColor(
                  d.minutes
                )}`}
              />
            ))}
          </div>
        </div>
      </div>

      {/* Bottom 2 Columns: Top Songs & Recent History */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
        {/* Top Played Songs */}
        <div className="p-6 rounded-3xl bg-white border border-slate-200 shadow-sm">
          <div className="flex items-center gap-2 mb-4">
            <Trophy className="w-5 h-5 text-amber-500" />
            <h4 className="text-base font-black text-slate-900">攻克榜单</h4>
          </div>

          {stats?.top_songs && stats.top_songs.length > 0 ? (
            <div className="space-y-3">
              {stats.top_songs.map((song, idx) => (
                <div
                  key={idx}
                  className="flex items-center justify-between p-3 rounded-xl bg-slate-50 border border-slate-200"
                >
                  <div className="flex items-center gap-3">
                    <span className="text-lg font-black text-[#ff2d75] font-mono w-5">
                      #{idx + 1}
                    </span>
                    <div>
                      <p className="text-sm font-black text-slate-900 line-clamp-1">{song.title}</p>
                      <span className="text-xs text-slate-500">{song.artist}</span>
                    </div>
                  </div>
                  <div className="text-right">
                    <span className="text-sm font-black text-amber-600 font-mono">
                      {song.minutes} 分钟
                    </span>
                    <p className="text-[11px] text-slate-400 font-medium">{song.plays} 次练琴</p>
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <p className="text-xs text-slate-400 py-8 text-center">暂无打卡数据，拿起贝斯开始第一次练习吧！</p>
          )}
        </div>

        {/* Recent Practice Sessions */}
        <div className="p-6 rounded-3xl bg-white border border-slate-200 shadow-sm">
          <div className="flex items-center gap-2 mb-4">
            <History className="w-5 h-5 text-cyan-600" />
            <h4 className="text-base font-black text-slate-900">最近练琴历史</h4>
          </div>

          {recent.length > 0 ? (
            <div className="space-y-2.5 max-h-[300px] overflow-y-auto pr-1">
              {recent.map((s, idx) => (
                <div
                  key={idx}
                  className="flex items-center justify-between p-2.5 rounded-xl bg-slate-50 border border-slate-200 text-xs"
                >
                  <div>
                    <span className="font-bold text-slate-800">{s.song_title}</span>
                    <p className="text-[11px] text-slate-400 mt-0.5">{s.start_time.replace('T', ' ').slice(0, 16)}</p>
                  </div>
                  <span className="font-mono font-bold text-[#ff2d75] bg-pink-50 px-2 py-1 rounded-lg border border-pink-200">
                    {s.duration_minutes} 分钟
                  </span>
                </div>
              ))}
            </div>
          ) : (
            <p className="text-xs text-slate-400 py-8 text-center">暂无历史记录</p>
          )}
        </div>
      </div>
    </div>
  );
};
