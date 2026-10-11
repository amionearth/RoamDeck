import React from "react";
import {
  Radar,
  RadarChart,
  PolarGrid,
  PolarAngleAxis,
  PolarRadiusAxis,
  ResponsiveContainer,
} from "recharts";

interface StyleRadarProps {
  data: Array<{ subject: string; value: number; fullMark: number }>;
}

export const StyleRadar: React.FC<StyleRadarProps> = ({ data }) => {
  return (
    <div className="w-full h-64 flex items-center justify-center">
      <ResponsiveContainer width="100%" height="100%">
        <RadarChart cx="50%" cy="50%" outerRadius="75%" data={data}>
          <PolarGrid stroke="#EAF0EF" />
          <PolarAngleAxis
            dataKey="subject"
            tick={{ fill: "#6B7C84", fontSize: 12, fontWeight: 500 }}
          />
          <PolarRadiusAxis angle={30} domain={[0, 100]} stroke="#EAF0EF" tick={false} />
          <Radar
            name="Style"
            dataKey="value"
            stroke="#1E7F85"
            fill="#1E7F85"
            fillOpacity={0.45}
          />
        </RadarChart>
      </ResponsiveContainer>
    </div>
  );
};
