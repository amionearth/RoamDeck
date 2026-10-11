import React from "react";

interface RingGaugeProps {
  current: number;
  total: number;
  label: string;
  color?: string;
  size?: number;
}

export const RingGauge: React.FC<RingGaugeProps> = ({
  current,
  total,
  label,
  color = "#1E7F85",
  size = 110,
}) => {
  const strokeWidth = 8;
  const radius = (size - strokeWidth) / 2;
  const circumference = 2 * Math.PI * radius;
  const pct = Math.min(100, Math.max(0, (current / Math.max(1, total)) * 100));
  const offset = circumference - (pct / 100) * circumference;

  return (
    <div className="flex flex-col items-center justify-center p-3">
      <div className="relative" style={{ width: size, height: size }}>
        <svg width={size} height={size} className="transform -rotate-90">
          {/* Background circle */}
          <circle
            cx={size / 2}
            cy={size / 2}
            r={radius}
            stroke="#EAF0EF"
            strokeWidth={strokeWidth}
            fill="transparent"
          />
          {/* Progress circle */}
          <circle
            cx={size / 2}
            cy={size / 2}
            r={radius}
            stroke={color}
            strokeWidth={strokeWidth}
            fill="transparent"
            strokeDasharray={circumference}
            strokeDashoffset={offset}
            strokeLinecap="round"
            className="transition-all duration-1000 ease-out"
          />
        </svg>
        <div className="absolute inset-0 flex flex-col items-center justify-center">
          <span className="text-base font-bold font-mono text-ink">
            {current}<span className="text-mist text-xs font-normal">/{total}</span>
          </span>
        </div>
      </div>
      <span className="text-xs font-medium text-mist mt-2 text-center">{label}</span>
    </div>
  );
};
