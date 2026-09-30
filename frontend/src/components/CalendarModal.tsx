import React from 'react';
import { CalendarHeatmap } from './CalendarHeatmap';
import { X, Calendar as CalendarIcon } from 'lucide-react';

interface CalendarModalProps {
  onClose: () => void;
}

export const CalendarModal: React.FC<CalendarModalProps> = ({ onClose }) => {
  React.useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [onClose]);

  return (
    <div
      onClick={onClose}
      className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-slate-900/60 backdrop-blur-sm animate-fadeIn cursor-pointer"
    >
      <div
        className="relative w-full max-w-4xl rounded-3xl bg-white border-2 border-pink-200/90 shadow-2xl shadow-pink-500/10 text-slate-900 overflow-hidden flex flex-col max-h-[90vh] cursor-default"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-slate-100 bg-slate-50/80">
          <div className="flex items-center gap-2.5">
            <CalendarIcon className="w-5 h-5 text-[#ff2d75]" />
            <div>
              <h3 className="text-base font-black text-slate-900">练琴打卡日历</h3>
            </div>
          </div>
          <button
            onClick={onClose}
            className="p-1.5 rounded-full text-slate-400 hover:text-slate-700 hover:bg-slate-100 transition-colors"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Body */}
        <div className="p-6 overflow-y-auto flex-1">
          <CalendarHeatmap />
        </div>
      </div>
    </div>
  );
};
