import React, { useState } from 'react';
import { Search, Download, Upload, Sliders, X, FileMusic, CheckCircle2, AlertCircle } from 'lucide-react';

interface GetSongModalProps {
  onClose: () => void;
  onRefreshSongs: () => void;
}

export const GetSongModal: React.FC<GetSongModalProps> = ({ onClose, onRefreshSongs }) => {
  const [activeTab, setActiveTab] = useState<'search' | 'upload' | 'uvr'>('search');
  const [keyword, setKeyword] = useState('');
  const [searchResults, setSearchResults] = useState<any[]>([]);
  const [isSearching, setIsSearching] = useState(false);
  const [uploadStatus, setUploadStatus] = useState<string>('');
  const [isUploading, setIsUploading] = useState(false);

  const handleSearch = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!keyword.trim()) return;
    setIsSearching(true);
    try {
      const res = await fetch('/api/get-song/search', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ keyword }),
      });
      const data = await res.json();
      setSearchResults(data);
    } catch (err) {
      console.error(err);
    } finally {
      setIsSearching(false);
    }
  };

  const handleFileUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = e.target.files;
    if (!files || files.length === 0) return;

    const file = files[0];
    setIsUploading(true);
    setUploadStatus('正在上传并自动解密/解析音频元数据...');

    const formData = new FormData();
    formData.append('file', file);

    try {
      const res = await fetch('/api/audio/upload', {
        method: 'POST',
        body: formData,
      });
      const data = await res.json();
      if (data.status === 'decrypted') {
        setUploadStatus(`已解密: ${data.result.output_path}`);
      } else {
        setUploadStatus(`已保存: ${data.path}`);
      }
      onRefreshSongs();
    } catch (err) {
      setUploadStatus('处理失败');
    } finally {
      setIsUploading(false);
    }
  };

  const handleLaunchUVR = async () => {
    try {
      await fetch('/api/tools/launch-uvr', { method: 'POST' });
    } catch (err) {
      console.error(err);
    }
  };

  return (
    <div
      onClick={onClose}
      className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/75 backdrop-blur-sm animate-fade-in"
    >
      <div
        onClick={(e) => e.stopPropagation()}
        className="relative w-full max-w-2xl rounded-3xl bg-[#12162d] border border-[#2c3666] shadow-2xl overflow-hidden flex flex-col max-h-[90vh]"
      >
        {/* Header */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-[#232a52] bg-[#171c38]">
          <div className="flex items-center gap-2">
            <span className="text-base font-black text-white">曲谱导入与下载</span>
          </div>
          <button
            onClick={onClose}
            className="p-1.5 rounded-full text-slate-400 hover:text-white hover:bg-white/10 transition-colors"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Tab Buttons */}
        <div className="flex border-b border-[#232a52] bg-[#141830]">
          <button
            onClick={() => setActiveTab('search')}
            className={`flex-1 py-3 text-xs font-bold transition-colors flex items-center justify-center gap-2 border-b-2 ${
              activeTab === 'search'
                ? 'border-pink-500 text-pink-400 bg-pink-500/10'
                : 'border-transparent text-slate-400 hover:text-slate-200'
            }`}
          >
            <Search className="w-4 h-4" />
            <span>网络检索曲谱</span>
          </button>
          <button
            onClick={() => setActiveTab('upload')}
            className={`flex-1 py-3 text-xs font-bold transition-colors flex items-center justify-center gap-2 border-b-2 ${
              activeTab === 'upload'
                ? 'border-pink-500 text-pink-400 bg-pink-500/10'
                : 'border-transparent text-slate-400 hover:text-slate-200'
            }`}
          >
            <Upload className="w-4 h-4" />
            <span>导入本地音源 (.ncm / .mp3)</span>
          </button>
        </div>

        {/* Body */}
        <div className="p-6 overflow-y-auto space-y-6">
          {activeTab === 'search' && (
            <div className="space-y-4">
              <form onSubmit={handleSearch} className="flex gap-2">
                <input
                  type="text"
                  placeholder="输入曲名..."
                  value={keyword}
                  onChange={(e) => setKeyword(e.target.value)}
                  className="flex-1 px-4 py-2.5 rounded-xl bg-[#1a2040] border border-[#2b3566] text-white text-sm focus:outline-none focus:border-pink-500 transition-colors placeholder:text-slate-500"
                />
                <button
                  type="submit"
                  disabled={isSearching}
                  className="px-5 py-2.5 rounded-xl bg-pink-600 hover:bg-pink-500 text-white text-sm font-bold shadow-lg shadow-pink-600/30 transition-all disabled:opacity-50 flex items-center gap-2"
                >
                  <Search className="w-4 h-4" />
                  <span>{isSearching ? '检索中...' : '搜索'}</span>
                </button>
              </form>

              {searchResults.length > 0 ? (
                <div className="space-y-2 max-h-80 overflow-y-auto pr-1">
                  {searchResults.map((item, idx) => (
                    <div
                      key={idx}
                      className="p-3.5 rounded-xl bg-[#191f3d] border border-[#293361] flex items-center justify-between"
                    >
                      <div>
                        <h4 className="text-sm font-bold text-white">{item.title}</h4>
                        <span className="text-xs text-indigo-300">
                          {item.artist} · 来源: {item.source}
                        </span>
                      </div>
                      <span className="text-xs px-2.5 py-1 rounded-full bg-emerald-950/80 text-emerald-300 border border-emerald-500/40 font-semibold">
                        含贝斯音轨
                      </span>
                    </div>
                  ))}
                </div>
              ) : null}
            </div>
          )}

          {activeTab === 'upload' && (
            <div className="space-y-4">
              <label className="flex flex-col items-center justify-center p-8 rounded-2xl border-2 border-dashed border-[#2f3b75] hover:border-pink-500 bg-[#161b36] cursor-pointer transition-colors group">
                <FileMusic className="w-12 h-12 text-pink-400 group-hover:scale-110 transition-transform mb-2" />
                <span className="text-sm font-bold text-white">选择或拖入音频 (.ncm / .mp3 / .flac)</span>
                <input
                  type="file"
                  accept=".ncm,.mp3,.flac,.wav"
                  onChange={handleFileUpload}
                  className="hidden"
                />
              </label>

              {uploadStatus && (
                <div className="p-3 rounded-xl bg-[#1a2142] border border-[#2b376b] text-xs flex items-center gap-2 text-indigo-200">
                  <CheckCircle2 className="w-4 h-4 text-emerald-400 shrink-0" />
                  <span>{uploadStatus}</span>
                </div>
              )}
            </div>
          )}

          {activeTab === 'uvr' && (
            <div className="space-y-4">
              <div className="p-4 rounded-2xl bg-[#171d38] border border-[#2b366e]">
                <h4 className="text-sm font-bold text-white mb-1">
                  E 盘已就绪的 Ultimate Vocal Remover 5
                </h4>
                <p className="text-xs text-slate-400 leading-relaxed">
                  UVR5 已从 C 盘完整迁移至 <code className="text-pink-300">E:\BassStation\tools\Ultimate Vocal Remover</code>，模型（Demucs / MDX-Net / VR Arch）完备。
                  你可以用它一键提取纯净贝斯音轨，或剥离去除贝斯生成 <strong className="text-yellow-300">Minus-One 纯伴奏</strong>！
                </p>
              </div>

              <button
                onClick={handleLaunchUVR}
                className="w-full py-3 rounded-xl bg-gradient-to-r from-pink-600 to-purple-600 hover:from-pink-500 hover:to-purple-500 text-white font-bold text-sm shadow-lg shadow-pink-600/30 flex items-center justify-center gap-2 transition-all"
              >
                <Sliders className="w-4 h-4" />
                <span>立即启动本地 UVR5 分离工作站</span>
              </button>
            </div>
          )}
        </div>
      </div>
    </div>
  );
};
