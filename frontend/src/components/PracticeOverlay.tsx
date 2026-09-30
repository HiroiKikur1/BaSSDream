import React, { useEffect, useState } from 'react';
import { ActiveSession } from '../types';
import { Timer, Square, Award, Flame, CheckCircle2 } from 'lucide-react';

interface PracticeOverlayProps {
  activeSession: ActiveSession | null;
  lastCompleted: any | null;
  onStopPractice: () => void;
  onCloseCompleted: () => void;
}

export const PracticeOverlay: React.FC<PracticeOverlayProps> = ({
  activeSession,
  lastCompleted,
  onStopPractice,
  onCloseCompleted,
}) => {
  const [seconds, setSeconds] = useState(0);

  useEffect(() => {
    if (!activeSession) {
      setSeconds(0);
      return;
    }
    setSeconds(Math.floor(activeSession.elapsed_seconds || 0));

    const interval = setInterval(() => {
      setSeconds((prev) => prev + 1);
    }, 1000);

    return () => clearInterval(interval);
  }, [activeSession]);

  const formatTime = (totalSec: number) => {
    const mins = Math.floor(totalSec / 60);
    const secs = totalSec % 60;
    return `${mins < 10 ? '0' : ''}${mins}:${secs < 10 ? '0' : ''}${secs}`;
  };

  return (
    <>
      {/* Floating Active Practice Bar */}
      {activeSession && (
        <div className="fixed bottom-6 left-1/2 -translate-x-1/2 z-50 flex items-center gap-4 px-6 py-3 rounded-full bg-white/95 border-2 border-pink-200/90 shadow-xl shadow-pink-500/15 backdrop-blur-xl animate-bounce-short">
          <div className="flex items-center gap-2">
            <span className="relative flex h-3 w-3">
              <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-pink-400 opacity-75"></span>
              <span className="relative inline-flex rounded-full h-3 w-3 bg-[#ff2d75]"></span>
            </span>
            <span className="text-xs font-bold tracking-wider uppercase text-pink-600">
              正在练习
            </span>
          </div>

          <div className="h-4 w-px bg-slate-200" />

          <div className="flex items-center gap-2">
            <span className="text-sm font-black text-slate-900 max-w-[200px] truncate">
              {activeSession.song_title}
            </span>
            <span className="text-xs px-2 py-0.5 rounded-full bg-pink-50 text-pink-600 font-bold border border-pink-200">
              Lv.{activeSession.level}
            </span>
          </div>

          <div className="h-4 w-px bg-slate-200" />

          <div className="flex items-center gap-1.5 font-mono text-lg font-black text-slate-900">
            <Timer className="w-5 h-5 text-amber-500 animate-pulse" />
            <span>{formatTime(seconds)}</span>
          </div>

          <button
            onClick={onStopPractice}
            className="flex items-center gap-1.5 px-3.5 py-1.5 rounded-full bg-rose-500 hover:bg-rose-600 text-white text-xs font-bold shadow-md shadow-rose-500/25 transition-all ml-2"
          >
            <Square className="w-3.5 h-3.5 fill-current" />
            <span>结束</span>
          </button>
        </div>
      )}

      {/* Practice Cleared Modal */}
      {lastCompleted && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-slate-900/60 backdrop-blur-sm animate-fadeIn">
          <div className="relative w-full max-w-md rounded-3xl bg-white border-2 border-pink-200/90 p-6 text-center shadow-2xl shadow-pink-500/15 overflow-hidden">
            <div className="relative flex justify-center mb-3">
              <div className="p-4 rounded-full bg-gradient-to-tr from-[#ff2d75] to-amber-400 text-white shadow-lg shadow-pink-500/25">
                <Award className="w-12 h-12" />
              </div>
            </div>

            <h2 className="text-2xl font-black text-[#ff2d75] tracking-wider uppercase mb-1">
              STAGE CLEARED!
            </h2>
            <p className="text-xs font-bold text-slate-400 uppercase tracking-widest mb-4">
              本次练琴记录已结算并入库
            </p>

            <div className="bg-slate-50 rounded-2xl p-4 border border-slate-200 mb-6 space-y-3 text-left">
              <div>
                <span className="text-xs text-slate-400">练习曲目</span>
                <p className="text-base font-black text-slate-900 line-clamp-1">{lastCompleted.song_title}</p>
              </div>

              <div className="grid grid-cols-2 gap-3 pt-2 border-t border-slate-200">
                <div>
                  <span className="text-xs text-slate-400">专注练习时长</span>
                  <p className="text-xl font-black text-amber-600 font-mono">
                    {lastCompleted.duration_minutes} <span className="text-xs font-normal text-slate-500">分钟</span>
                  </p>
                </div>
                <div>
                  <span className="text-xs text-slate-400">难度等级</span>
                  <p className="text-xl font-black text-[#ff2d75] font-mono">
                    Lv.{lastCompleted.level || 25}
                  </p>
                </div>
              </div>

              <div className="flex items-center gap-2 pt-2 border-t border-slate-200 text-xs text-emerald-600 font-bold">
                <CheckCircle2 className="w-4 h-4 text-emerald-500" />
                <span>日历打卡热力图已点亮！连续练习天数 +1</span>
              </div>
            </div>

            <button
              onClick={onCloseCompleted}
              className="w-full py-3 rounded-xl bg-gradient-to-r from-[#ff2d75] to-[#ff4081] hover:brightness-105 text-white font-black text-sm shadow-md shadow-pink-500/25 transition-all"
            >
              太棒了，继续练习！
            </button>
          </div>
        </div>
      )}
    </>
  );
};
