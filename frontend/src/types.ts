export interface RadarScores {
  speed: number;
  stamina: number;
  rhythm: number;
  agility: number;
  technique: number;
}

export interface Song {
  id: string;
  folder_name: string;
  title: string;
  artist: string;
  franchise: string;
  gp_path: string;
  pdf_path: string;
  tempo: number;
  duration: number;
  measures: number;
  notes_count: number;
  is_5string: boolean;
  tuning: string;
  has_backing_track: boolean;
  audio_path: string;
  cover_url: string;
  level: number;
  level_exact: number;
  tier: 'NORMAL' | 'HARD' | 'EXPERT' | 'SPECIAL';
  tier_levels?: {
    EASY: number;
    NORMAL: number;
    HARD: number;
    EXPERT: number;
    SPECIAL: number;
  };
  radar: RadarScores;
  tags: string[];
  peak_nps: number;
  backing_info?: {
    has_backing_track: boolean;
    is_embedded: boolean;
    local_audio_files: string[];
    primary_audio_path: string;
  };
  best_score?: number | null;
  best_grade?: string | null;
}

export interface PracticeStats {
  total_hours: number;
  total_minutes: number;
  total_sessions: number;
  today_minutes: number;
  streak_days: number;
  top_songs: Array<{
    title: string;
    artist: string;
    minutes: number;
    plays: number;
  }>;
}

export interface ActiveSession {
  song_id: string;
  song_title: string;
  artist: string;
  level: number;
  tier: string;
  start_time: string;
  elapsed_seconds: number;
  elapsed_minutes: number;
}
