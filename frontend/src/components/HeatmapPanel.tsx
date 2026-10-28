import { useEffect, useState } from "react";
import { formatClock, formatDayClock } from "../format";
import type { Heatmap } from "../types";

interface HeatmapPanelProps {
  heatmap: Heatmap;
  hour: number;
  enabled: boolean;
  onHourChange: (hour: number) => void;
  onToggle: (enabled: boolean) => void;
}

export function HeatmapPanel({
  heatmap,
  hour,
  enabled,
  onHourChange,
  onToggle,
}: HeatmapPanelProps) {
  const [playing, setPlaying] = useState(false);
  const lastHour = heatmap.hours.length - 1;

  useEffect(() => {
    if (!playing) return;
    const timer = window.setInterval(() => {
      onHourChange(hour >= lastHour ? 0 : hour + 1);
    }, 900);
    return () => window.clearInterval(timer);
  }, [playing, hour, lastHour, onHourChange]);

  useEffect(() => {
    if (hour > lastHour) onHourChange(lastHour);
  }, [hour, lastHour, onHourChange]);

  const activeHour = Math.min(hour, lastHour);
  const label = heatmap.hours[activeHour];
  const window_ = heatmap.tripWindow;

  return (
    <section className={enabled ? "heatmap" : "heatmap heatmap--off"} aria-label="Forecast heatmap">
      <header className="heatmap__head">
        <div>
          <h2>Corridor forecast</h2>
          <p className="heatmap__time">
            {label ? formatDayClock(label) : "—"}
            <span>+{activeHour}h</span>
          </p>
        </div>
        <label className="switch">
          <input
            type="checkbox"
            checked={enabled}
            onChange={(event) => onToggle(event.target.checked)}
          />
          <span>Show</span>
        </label>
      </header>

      <div className="heatmap__track">
        <input
          type="range"
          min={0}
          max={lastHour}
          value={activeHour}
          step={1}
          aria-label="Forecast hour"
          disabled={!enabled}
          onChange={(event) => onHourChange(Number(event.target.value))}
        />
        {window_ && (
          <div
            className="heatmap__trip"
            title="Hours the truck is on the road"
            style={{
              left: `${(window_.startHour / lastHour) * 100}%`,
              width: `${Math.max(1, ((window_.endHour - window_.startHour) / lastHour) * 100)}%`,
            }}
          />
        )}
      </div>

      <div className="heatmap__foot">
        <button
          type="button"
          className="ghost-button"
          onClick={() => setPlaying((value) => !value)}
          disabled={!enabled}
        >
          {playing ? "Pause" : "Play 48h"}
        </button>
        <span className="heatmap__note">
          {window_
            ? `Truck on road ${formatClock(heatmap.hours[Math.round(window_.startHour)] ?? heatmap.hours[0] ?? "")}–${formatClock(
                heatmap.hours[Math.round(window_.endHour)] ?? heatmap.hours[lastHour] ?? "",
              )}`
            : "Trip is outside the 48 hour window"}
        </span>
      </div>
    </section>
  );
}
