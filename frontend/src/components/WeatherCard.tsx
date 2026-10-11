import React from "react";
import { Cloud, Sun, CloudRain, Wind, Droplets } from "lucide-react";

interface WeatherData {
  location: string;
  temperature: number;
  humidity: number;
  weather_code: number;
  wind_speed: number;
  hourly: Array<{ time: string; temp: number; code: number }>;
}

export const WeatherCard: React.FC<{ weather: WeatherData | null; isLoading?: boolean }> = ({
  weather,
  isLoading,
}) => {
  if (isLoading || !weather) {
    return (
      <div className="glass-teal p-6 rounded-card border border-white/30 h-44 flex items-center justify-center animate-pulse">
        <span className="text-sm font-sans text-teal-950/70">Gathering coastal weather...</span>
      </div>
    );
  }

  const getWeatherIcon = (code: number) => {
    if (code >= 51) return <CloudRain className="w-4 h-4 text-amber-300" />;
    if (code >= 1) return <Cloud className="w-4 h-4 text-white/90" />;
    return <Sun className="w-4 h-4 text-amber-300" />;
  };

  const getConditionText = (code: number) => {
    if (code >= 51) return "Passing rain";
    if (code >= 3) return "Overcast";
    if (code >= 1) return "Partly cloudy";
    return "Sunny breeze";
  };

  return (
    <div className="glass-teal p-6 rounded-card border border-white/40 shadow-sm flex flex-col justify-between relative overflow-hidden group">
      {/* Soft ambient inner sheen */}
      <div className="absolute -top-12 -right-12 w-32 h-32 rounded-full bg-white/20 blur-xl pointer-events-none" />

      <div className="flex items-start justify-between relative z-10">
        <div>
          <span className="text-xs font-sans font-medium text-teal-900/80 block">
            {weather.location || "Coastal Forecast"}
          </span>
          <div className="flex items-baseline gap-2 mt-1">
            <span className="font-serif text-5xl font-normal text-teal-950 tracking-tight">
              {Math.round(weather.temperature)}°
            </span>
            <span className="text-sm text-teal-900/90 font-sans font-medium">
              {getConditionText(weather.weather_code)}
            </span>
          </div>
        </div>

        <div className="flex items-center gap-3 text-xs text-teal-900/80 bg-white/30 backdrop-blur-md px-2.5 py-1 rounded-full border border-white/30">
          <div className="flex items-center gap-1">
            <Droplets className="w-3.5 h-3.5 text-teal-700" />
            <span>{weather.humidity}%</span>
          </div>
          <div className="flex items-center gap-1">
            <Wind className="w-3.5 h-3.5 text-teal-700" />
            <span>{Math.round(weather.wind_speed)} km/h</span>
          </div>
        </div>
      </div>

      {/* Hourly forecast row: 5 items */}
      <div className="mt-4 pt-3 border-t border-teal-900/15 flex items-center justify-between text-center overflow-x-auto gap-2 relative z-10">
        {weather.hourly?.slice(0, 5).map((h, idx) => (
          <div key={idx} className="flex flex-col items-center min-w-[42px]">
            <span className="text-[11px] text-teal-900/70 font-sans font-medium">{h.time}</span>
            <div className="my-1.5">{getWeatherIcon(h.code)}</div>
            <span className="text-xs font-semibold text-teal-950">{Math.round(h.temp)}°</span>
          </div>
        ))}
      </div>
    </div>
  );
};
