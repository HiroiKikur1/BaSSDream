import React, { useEffect, useState } from 'react';
import { createPortal } from 'react-dom';
import { Check, X, Lock } from 'lucide-react';

export interface BadgeItem {
  id: string;
  name: string;
  rarity: 'rainbow' | 'gold' | 'silver';
  description: string;
  franchise: string;
  is_unlocked: boolean;
  unlocked_at?: string;
  is_equipped: boolean;
}

export const TitleBadge: React.FC = () => {
  const [badges, setBadges] = useState<BadgeItem[]>([]);
  const [equipped, setEquipped] = useState<BadgeItem | null>(null);
  const [modalOpen, setModalOpen] = useState(false);
  const [equipping, setEquipping] = useState(false);

  const fetchBadges = () => {
    fetch('/api/gamification/badges')
      .then((res) => res.json())
      .then((data) => {
        if (data.badges) setBadges(data.badges);
        if (data.equipped) setEquipped(data.equipped);
      })
      .catch(() => {});
  };

  useEffect(() => {
    fetchBadges();
  }, []);

  useEffect(() => {
    if (!modalOpen) return;
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setModalOpen(false);
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [modalOpen]);

  const handleEquip = async (badgeId: string) => {
    setEquipping(true);
    try {
      const res = await fetch('/api/gamification/equip', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ badge_id: badgeId }),
      });
      const data = await res.json();
      if (data.success) {
        fetchBadges();
      }
    } finally {
      setEquipping(false);
    }
  };

  const getBadgeStyle = (rarity: string) => {
    switch (rarity) {
      case 'rainbow':
        return 'bg-gradient-to-r from-pink-500 via-purple-500 to-cyan-500 text-white shadow-md shadow-purple-500/25 border-white/60';
      case 'gold':
        return 'bg-gradient-to-r from-amber-500 via-yellow-400 to-amber-600 text-amber-950 shadow-md shadow-amber-500/20 border-yellow-200';
      default:
        return 'bg-gradient-to-r from-slate-200 via-slate-100 to-slate-300 text-slate-800 shadow-sm border-white';
    }
  };

  return (
    <>
      {/* Header Badge */}
      <button
        onClick={() => setModalOpen(true)}
        className="flex items-center gap-2 px-3.5 py-1.5 rounded-full bg-white border-2 border-slate-200 hover:border-pink-400 shadow-sm hover:scale-105 active:scale-95 transition-all group cursor-pointer"
      >
        <svg viewBox="0 0 24 24" className="w-4 h-4 text-[#ff2d75] fill-current group-hover:rotate-12 transition-transform">
          <polygon points="12,2 14.8,8.2 21.5,9.2 16.5,14 17.8,21 12,17.6 6.2,21 7.5,14 2.5,9.2 9.2,8.2" />
        </svg>
        <div
          className={`px-2.5 py-0.5 rounded-full text-xs font-black tracking-wide border ${getBadgeStyle(
            equipped?.rarity || 'silver'
          )}`}
        >
          {equipped?.name || '新人贝斯手'}
        </div>
      </button>

      {/* Fullscreen Portal Modal (Mounted directly to document.body to avoid backdrop-filter clipping) */}
      {modalOpen &&
        createPortal(
          <div
            onClick={() => setModalOpen(false)}
            className="fixed inset-0 z-[9999] flex items-center justify-center p-4 sm:p-6 bg-slate-950/60 backdrop-blur-md animate-fade-in cursor-pointer"
          >
            <div
              onClick={(e) => e.stopPropagation()}
              className="bg-white rounded-3xl p-6 sm:p-7 w-full max-w-3xl shadow-2xl border-2 border-slate-200/90 flex flex-col max-h-[90vh] text-left cursor-default animate-scaleIn"
            >
              {/* Header */}
              <div className="flex items-center justify-between border-b border-slate-100 pb-3 shrink-0">
                <div className="flex items-center gap-3">
                  <h3 className="text-lg font-black text-slate-900 tracking-tight">BanG Dream! 称号成就馆</h3>
                  <span className="px-2.5 py-0.5 rounded-full bg-pink-50 text-pink-600 border border-pink-200 text-xs font-bold">
                    {badges.filter((b) => b.is_unlocked).length} / {badges.length}
                  </span>
                </div>
                <button
                  onClick={() => setModalOpen(false)}
                  className="p-2 rounded-xl hover:bg-slate-100 text-slate-400 hover:text-slate-700 transition-colors cursor-pointer"
                  title="关闭"
                >
                  <X className="w-5 h-5" />
                </button>
              </div>

              {/* 2-Column Badges Grid */}
              <div className="overflow-y-auto p-1 pb-4 pr-1.5 flex-1 grid grid-cols-1 sm:grid-cols-2 gap-3 custom-scrollbar">
                {badges.map((b) => (
                  <div
                    key={b.id}
                    className={`p-3.5 rounded-2xl border-2 transition-all flex flex-col justify-between gap-2.5 ${
                      b.is_equipped
                        ? 'bg-pink-50/70 border-pink-400 shadow-sm ring-2 ring-pink-300/40'
                        : b.is_unlocked
                        ? 'bg-white border-slate-200 hover:border-pink-300 hover:shadow-sm'
                        : 'bg-slate-50/70 border-slate-200/60 opacity-60'
                    }`}
                  >
                    <div className="space-y-2">
                      <div className="flex items-center justify-between gap-2">
                        <div
                          className={`px-3.5 py-1 rounded-full text-sm font-black tracking-wide border shadow-xs ${getBadgeStyle(
                            b.rarity
                          )}`}
                        >
                          {b.name}
                        </div>
                      </div>
                      <p className="text-sm text-slate-700 leading-relaxed font-medium">{b.description}</p>
                    </div>

                    <div className="pt-2.5 border-t border-slate-100 flex items-center justify-end">
                      {b.is_equipped ? (
                        <div className="px-4 py-1.5 rounded-full bg-[#ff2d75] text-white text-sm font-bold flex items-center gap-1.5 shadow-sm">
                          <Check className="w-4 h-4" />
                          <span>已佩戴</span>
                        </div>
                      ) : b.is_unlocked ? (
                        <button
                          onClick={() => handleEquip(b.id)}
                          disabled={equipping}
                          className="px-4 py-1.5 rounded-full bg-pink-50 hover:bg-[#ff2d75] text-pink-600 hover:text-white text-sm font-bold transition-all border border-pink-200 hover:border-[#ff2d75] shadow-xs cursor-pointer active:scale-95"
                        >
                          佩戴
                        </button>
                      ) : (
                        <div className="px-4 py-1.5 rounded-full bg-slate-100 text-slate-400 text-sm font-medium flex items-center gap-1.5">
                          <Lock className="w-3.5 h-3.5" />
                          <span>未解锁</span>
                        </div>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            </div>
          </div>,
          document.body
        )}
    </>
  );
};
