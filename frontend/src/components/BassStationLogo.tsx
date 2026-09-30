import React from 'react';

interface BassStationLogoProps {
  className?: string;
  size?: number;
}

export const BassStationLogo: React.FC<BassStationLogoProps> = ({ className = 'w-9 h-9', size }) => {
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      viewBox="0 0 120 120"
      width={size}
      height={size}
      className={className}
      fill="none"
    >
      <defs>
        <linearGradient id="bsOrbStarGrad" x1="0%" y1="0%" x2="100%" y2="100%">
          <stop offset="0%" stopColor="#ff528c" />
          <stop offset="45%" stopColor="#ff2d75" />
          <stop offset="100%" stopColor="#b80043" />
        </linearGradient>

        <linearGradient id="bsOrbGoldGrad" x1="0%" y1="0%" x2="100%" y2="100%">
          <stop offset="0%" stopColor="#fff176" />
          <stop offset="50%" stopColor="#ffd54f" />
          <stop offset="100%" stopColor="#ffb300" />
        </linearGradient>

        <linearGradient id="bsOrbBeamGrad" x1="0%" y1="0%" x2="100%" y2="100%">
          <stop offset="0%" stopColor="#38bdf8" />
          <stop offset="50%" stopColor="#a855f7" />
          <stop offset="100%" stopColor="#ff2d75" />
        </linearGradient>

        <filter id="bsOrbGlow" x="-25%" y="-25%" width="150%" height="150%">
          <feDropShadow dx="0" dy="3" stdDeviation="4" floodColor="#ff2d75" floodOpacity="0.45" />
        </filter>
      </defs>

      <g filter="url(#bsOrbGlow)">
        {/* Back Orbital Groove Arc */}
        <path
          d="M 22,64 C 18,48 34,32 58,26 C 82,20 102,26 106,42"
          stroke="url(#bsOrbBeamGrad)"
          strokeWidth="4.5"
          strokeLinecap="round"
          opacity="0.5"
        />

        {/* Main BanG Dream! Faceted Stage Prism Star */}
        <g transform="translate(60, 58) rotate(-8)">
          <polygon
            points="0,-48 13,-15 48,-12 22,14 31,48 0,30 -31,48 -22,14 -48,-12 -13,-15"
            fill="url(#bsOrbStarGrad)"
            stroke="#ffffff"
            strokeWidth="2"
            strokeLinejoin="round"
          />

          <polygon points="0,-48 0,0 48,-12" fill="#ffffff" opacity="0.35" />
          <polygon points="48,-12 0,0 22,14" fill="#000000" opacity="0.15" />
          <polygon points="22,14 0,0 31,48" fill="#ffffff" opacity="0.25" />
          <polygon points="31,48 0,0 0,30" fill="#000000" opacity="0.2" />
          <polygon points="0,30 0,0 -31,48" fill="#ffffff" opacity="0.2" />
          <polygon points="-31,48 0,0 -22,14" fill="#000000" opacity="0.25" />
          <polygon points="-22,14 0,0 -48,-12" fill="#ffffff" opacity="0.3" />
          <polygon points="-48,-12 0,0 -13,-15" fill="#000000" opacity="0.1" />
          <polygon points="-13,-15 0,0 0,-48" fill="#ffffff" opacity="0.45" />

          <polygon
            points="0,-22 6,-7 22,-5 10,6 14,22 0,14 -14,22 -10,6 -22,-5 -6,-7"
            fill="url(#bsOrbGoldGrad)"
            opacity="0.9"
          />
          <circle cx="0" cy="0" r="4.5" fill="#ffffff" />
        </g>

        {/* Front Orbital Low-End Groove Wave */}
        <path
          d="M 106,42 C 110,58 96,80 66,92 C 40,102 18,96 14,80"
          stroke="url(#bsOrbBeamGrad)"
          strokeWidth="5"
          strokeLinecap="round"
        />

        {/* Twin Twinkling Starlight Satellites */}
        <g transform="translate(105, 38)">
          <polygon points="0,-9 2,-2 9,0 2,2 0,9 -2,2 -9,0 -2,-2" fill="#ffffff" />
          <circle cx="0" cy="0" r="1.8" fill="#ff2d75" />
        </g>

        <g transform="translate(15, 82) scale(0.75)">
          <polygon points="0,-9 2,-2 9,0 2,2 0,9 -2,2 -9,0 -2,-2" fill="#ffffff" />
          <circle cx="0" cy="0" r="1.8" fill="#38bdf8" />
        </g>
      </g>
    </svg>
  );
};
