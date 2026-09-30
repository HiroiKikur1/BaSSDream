import React from 'react';
import { RadarScores } from '../types';

interface RadarChartProps {
  scores: RadarScores;
  size?: number;
}

export const RadarChart: React.FC<RadarChartProps> = ({ scores, size = 220 }) => {
  const center = size / 2;
  const radius = size * 0.36;

  const axes = [
    { key: 'speed', label: '速度', score: scores?.speed || 20 },
    { key: 'agility', label: '位移', score: scores?.agility || 20 },
    { key: 'technique', label: '技法', score: scores?.technique || 20 },
    { key: 'rhythm', label: '律动', score: scores?.rhythm || 20 },
    { key: 'stamina', label: '耐力', score: scores?.stamina || 20 },
  ];

  const totalAxes = axes.length;
  const angleStep = (Math.PI * 2) / totalAxes;

  const getCoords = (index: number, value: number) => {
    const angle = index * angleStep - Math.PI / 2;
    const r = (value / 100) * radius;
    return {
      x: center + r * Math.cos(angle),
      y: center + r * Math.sin(angle),
    };
  };

  const dataPoints = axes
    .map((axis, i) => {
      const { x, y } = getCoords(i, axis.score);
      return `${x},${y}`;
    })
    .join(' ');

  const rings = [0.25, 0.5, 0.75, 1.0];

  return (
    <div className="relative flex flex-col items-center">
      <svg width={size} height={size} className="overflow-visible">
        <defs>
          <linearGradient id="radar-gradient" x1="0%" y1="0%" x2="100%" y2="100%">
            <stop offset="0%" stopColor="#ff2d75" stopOpacity="0.75" />
            <stop offset="100%" stopColor="#8a2be2" stopOpacity="0.6" />
          </linearGradient>
        </defs>

        {rings.map((factor, idx) => {
          const ringPoints = axes
            .map((_, i) => {
              const angle = i * angleStep - Math.PI / 2;
              const r = radius * factor;
              return `${center + r * Math.cos(angle)},${center + r * Math.sin(angle)}`;
            })
            .join(' ');
          return (
            <polygon
              key={idx}
              points={ringPoints}
              fill="none"
              stroke="#e2e8f0"
              strokeWidth="1.2"
              strokeDasharray={idx === rings.length - 1 ? 'none' : '3 3'}
            />
          );
        })}

        {axes.map((_, i) => {
          const end = getCoords(i, 100);
          return (
            <line
              key={i}
              x1={center}
              y1={center}
              x2={end.x}
              y2={end.y}
              stroke="#cbd5e1"
              strokeWidth="1"
            />
          );
        })}

        <polygon
          points={dataPoints}
          fill="url(#radar-gradient)"
          stroke="#ff2d75"
          strokeWidth="2.5"
        />

        {axes.map((axis, i) => {
          const { x, y } = getCoords(i, axis.score);
          return (
            <circle
              key={i}
              cx={x}
              cy={y}
              r="4"
              fill="#00b4d8"
              stroke="#ffffff"
              strokeWidth="1.5"
            />
          );
        })}

        {axes.map((axis, i) => {
          const angle = i * angleStep - Math.PI / 2;
          const labelRadius = radius + 20;
          const lx = center + labelRadius * Math.cos(angle);
          const ly = center + labelRadius * Math.sin(angle);
          return (
            <text
              key={i}
              x={lx}
              y={ly}
              textAnchor="middle"
              dominantBaseline="middle"
              fill="#334155"
              fontSize="11"
              fontWeight="800"
              className="select-none"
            >
              {axis.label} {axis.score}
            </text>
          );
        })}
      </svg>
    </div>
  );
};
