"""BassNet: monophonic bass transcription + beat tracking.

Inputs  : bass-stem CQT (264 bins, 3/semitone from A0) and mix log-mel (128), 86.13 fps.
Outputs : frame pitch class (0 = rest, 1..48 = MIDI 23..70), onset, dead-note, beat, downbeat logits.
"""
import torch
import torch.nn as nn
import torch.nn.functional as F

BINS_PER_SEMI = 3
MIDI_LO = 21
PITCH_LO, PITCH_HI = 23, 70
N_PITCH = PITCH_HI - PITCH_LO + 1
FUND_START = (PITCH_LO - MIDI_LO) * BINS_PER_SEMI          # 6
FUND_BINS = N_PITCH * BINS_PER_SEMI                         # 144
# technique outputs (per note onset): slide arrival (legato/shift slide target), slide out down, slide out up,
# slide in, hammer-on/pull-off arrival, slap, pop
TECH_NAMES = ["slide_arr", "slide_out_down", "slide_out_up", "slide_in", "hopo_arr", "slap", "pop"]
# harmonic shifts in bins: 1/2, 1, 2, 3, 4, 5, 6 x f0
HARM_SHIFTS = [-36, 0, 36, 57, 72, 84, 93]


def harmonic_stack(cqt: torch.Tensor) -> torch.Tensor:
    """cqt (B, F, T) -> (B, H, FUND_BINS, T) aligned on the fundamental."""
    B, Fq, T = cqt.shape
    pad = F.pad(cqt, (0, 0, 64, 128))
    chans = []
    for s in HARM_SHIFTS:
        a = 64 + FUND_START + s
        chans.append(pad[:, a:a + FUND_BINS, :])
    return torch.stack(chans, dim=1)


class ConvBlock(nn.Module):
    def __init__(self, cin, cout, kf, kt, df=1):
        super().__init__()
        self.conv = nn.Conv2d(cin, cout, (kf, kt), padding=((kf // 2) * df, kt // 2), dilation=(df, 1))
        self.bn = nn.BatchNorm2d(cout)

    def forward(self, x):
        return F.gelu(self.bn(self.conv(x)))


class BassNet(nn.Module):
    def __init__(self, n_mel=128, hidden=256, stem_branch=False, tech=0, mert=0):
        super().__init__()
        self.stem_branch = stem_branch
        self.tech = tech
        self.mert = mert
        H = len(HARM_SHIFTS)
        self.cqt_norm = nn.BatchNorm2d(H)
        self.convs = nn.Sequential(
            ConvBlock(H, 32, 5, 5),
            ConvBlock(32, 32, 3, 3),
            ConvBlock(32, 48, 3, 3, df=3),     # semitone-spaced context
            ConvBlock(48, 48, 3, 3, df=12),    # octave-spaced context
        )
        self.pool = nn.MaxPool2d((BINS_PER_SEMI, 1))
        self.pitch_proj = nn.Conv1d(48 * N_PITCH, hidden, 1)
        self.mel = nn.Sequential(
            nn.Conv1d(n_mel, 128, 5, padding=2), nn.BatchNorm1d(128), nn.GELU(),
            nn.Conv1d(128, 128, 5, padding=2), nn.BatchNorm1d(128), nn.GELU(),
        )
        extra = 0
        if stem_branch:
            # broadband transient view of the bass stem (re-plucks of the same pitch live here)
            self.stem = nn.Sequential(
                nn.Conv1d(264, 128, 3, padding=1), nn.BatchNorm1d(128), nn.GELU(),
                nn.Conv1d(128, 96, 3, padding=1), nn.BatchNorm1d(96), nn.GELU(),
            )
            extra = 96 + 96
        if mert:
            # foundation-model features (MERT-v1-330M, PCA) resampled to the CQT frame rate
            self.mert_proj = nn.Sequential(
                nn.Conv1d(mert, 256, 1), nn.BatchNorm1d(256), nn.GELU(),
                nn.Conv1d(256, 128, 3, padding=1), nn.BatchNorm1d(128), nn.GELU(),
            )
            extra += 128
        self.gru = nn.GRU(hidden + 128 + extra, hidden, num_layers=2, batch_first=True, bidirectional=True, dropout=0.2)
        # per-pitch local features also go straight to the pitch head (keeps pitch resolution sharp)
        self.local = nn.Conv1d(48, 16, 1)
        self.frame_head = nn.Linear(2 * hidden, 1 + N_PITCH)
        self.frame_local = nn.Sequential(nn.GELU(), nn.Conv1d(16, 1, 1))
        self.aux_head = nn.Linear(2 * hidden, 4)        # onset, dead, beat, downbeat
        if tech:
            self.tech_head = nn.Linear(2 * hidden, tech)

    def forward(self, cqt, mel, mert_feat=None):
        x = harmonic_stack(cqt)                          # B,H,144,T
        x = self.cqt_norm(x)
        x = self.convs(x)                                # B,48,144,T
        x = self.pool(x)                                 # B,48,48,T
        B, C, P, T = x.shape
        loc = self.local(x.permute(0, 2, 1, 3).reshape(B * P, C, T)).reshape(B, P, 16, T)
        z = self.pitch_proj(x.reshape(B, C * P, T))      # B,hidden,T
        m = self.mel(mel)
        feats = [z, m]
        if self.stem_branch:
            st = self.stem(cqt)
            flux = F.relu(st[:, :, 1:] - st[:, :, :-1])
            feats += [st, F.pad(flux, (1, 0))]
        if self.mert:
            if mert_feat is None:
                mert_feat = torch.zeros(cqt.shape[0], self.mert, cqt.shape[2], device=cqt.device, dtype=cqt.dtype)
            feats.append(self.mert_proj(mert_feat))
        h, _ = self.gru(torch.cat(feats, 1).transpose(1, 2))   # B,T,2h
        glob_logits = self.frame_head(h)                 # B,T,49
        per_pitch = self.frame_local(loc.reshape(B * P, 16, T)).reshape(B, P, T)
        frame = torch.cat([glob_logits[..., :1], glob_logits[..., 1:] + per_pitch.transpose(1, 2)], -1)
        aux = self.aux_head(h)
        if self.tech:
            return frame, aux[..., 0], aux[..., 1], aux[..., 2], aux[..., 3], self.tech_head(h)
        return frame, aux[..., 0], aux[..., 1], aux[..., 2], aux[..., 3]


def load_checkpoint(path, dev):
    """Builds the right architecture for a saved checkpoint."""
    sd = torch.load(path, map_location=dev)
    m = BassNet(**sd.get("arch", {})).to(dev)
    m.load_state_dict(sd["model"])
    m.eval()
    return m
