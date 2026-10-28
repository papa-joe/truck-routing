import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, requestPlan } from "./api";
import { HeatmapPanel } from "./components/HeatmapPanel";
import { PlanForm } from "./components/PlanForm";
import { RouteCards } from "./components/RouteCards";
import { TripMap } from "./components/TripMap";
import { formatClock, formatDuration } from "./format";
import { SEVERITY_META } from "./types";
import type { Plan, PlanRequest, SeverityName } from "./types";

const LEGEND: SeverityName[] = ["low", "moderate", "high", "severe", "no_travel", "unknown"];

export function App() {
  const [plan, setPlan] = useState<Plan | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selectedRouteId, setSelectedRouteId] = useState<string | null>(null);
  const [hour, setHour] = useState(0);
  const [heatmapEnabled, setHeatmapEnabled] = useState(true);
  const lastRequest = useRef<PlanRequest | null>(null);

  // The trip panel is a bottom sheet below 960px and a static column above it.
  // It always starts closed so the map is the first thing on screen.
  const [sheetOpen, setSheetOpen] = useState(false);
  const sheetRef = useRef<HTMLElement>(null);
  const toggleRef = useRef<HTMLButtonElement>(null);

  const closeSheet = useCallback(() => {
    setSheetOpen(false);
    toggleRef.current?.focus();
  }, []);

  useEffect(() => {
    if (!sheetOpen) return;

    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") closeSheet();
    }

    const previousOverflow = document.documentElement.style.overflow;
    document.documentElement.style.overflow = "hidden";
    document.addEventListener("keydown", onKeyDown);
    // Wait for the sheet to become visible, otherwise focus() is ignored.
    const frame = requestAnimationFrame(() => sheetRef.current?.focus());

    return () => {
      cancelAnimationFrame(frame);
      document.removeEventListener("keydown", onKeyDown);
      document.documentElement.style.overflow = previousOverflow;
    };
  }, [sheetOpen, closeSheet]);

  const runPlan = useCallback(async (request: PlanRequest) => {
    lastRequest.current = request;
    setLoading(true);
    setError(null);
    try {
      const result = await requestPlan(request);
      setPlan(result);
      setSelectedRouteId(result.recommendedRouteId);
      const startHour = result.heatmap.tripWindow?.startHour ?? 0;
      const lastHour = Math.max(0, result.heatmap.hours.length - 1);
      setHour(Math.min(lastHour, Math.max(0, Math.round(startHour))));
      setHeatmapEnabled(true);
      setSheetOpen(false);
    } catch (caught) {
      setError(
        caught instanceof ApiError
          ? caught.message
          : "Could not plan this trip. Check your connection and try again.",
      );
    } finally {
      setLoading(false);
    }
  }, []);

  const recommended = plan?.routes.find((route) => route.isRecommended) ?? null;

  return (
    <div className={sheetOpen ? "app app--sheet" : "app"}>
      <header className="topbar">
        <div className="brand">
          <span className="brand__mark" aria-hidden="true" />
          <div>
            <h1>Spotter</h1>
            <p>Pick the truck route the weather will actually allow.</p>
          </div>
        </div>
        {recommended && (
          <div className="topbar__summary">
            <strong>{recommended.label}</strong>
            <span>
              {formatDuration(recommended.durationMinutes)} · arrive{" "}
              {formatClock(recommended.arrivalAt)}
            </span>
          </div>
        )}
        <button
          ref={toggleRef}
          type="button"
          className="sheet-toggle"
          aria-expanded={sheetOpen}
          aria-controls="trip-panel"
          onClick={() => setSheetOpen((open) => !open)}
        >
          {plan ? `Routes (${plan.routes.length})` : "Plan trip"}
          <svg className="sheet-toggle__chevron" viewBox="0 0 12 12" aria-hidden="true">
            <path
              d="M2.5 7.5 6 4l3.5 3.5"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.6"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          </svg>
        </button>
      </header>

      {sheetOpen && (
        <button
          type="button"
          className="sheet-backdrop"
          aria-label="Close trip panel"
          tabIndex={-1}
          onClick={closeSheet}
        />
      )}

      <div className="layout">
        <aside
          className={sheetOpen ? "sidebar sidebar--open" : "sidebar"}
          id="trip-panel"
          ref={sheetRef}
          tabIndex={-1}
          aria-label="Trip plan"
        >
          <div className="sheet-head">
            <h2>Trip plan</h2>
            <button type="button" className="sheet-close" aria-label="Close" onClick={closeSheet}>
              <svg viewBox="0 0 12 12" aria-hidden="true">
                <path
                  d="M3 3l6 6M9 3l-6 6"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="1.6"
                  strokeLinecap="round"
                />
              </svg>
            </button>
          </div>

          <PlanForm loading={loading} onPlan={runPlan} />

          {error && (
            <div className="alert" role="alert">
              <p>{error}</p>
              <button
                type="button"
                className="ghost-button"
                onClick={() => {
                  if (lastRequest.current) void runPlan(lastRequest.current);
                }}
              >
                Try again
              </button>
            </div>
          )}

          {plan && plan.notices.length > 0 && (
            <ul className="notices">
              {plan.notices.map((notice) => (
                <li key={notice}>{notice}</li>
              ))}
            </ul>
          )}

          {plan && (
            <RouteCards
              routes={plan.routes}
              selectedId={selectedRouteId ?? plan.recommendedRouteId}
              onSelect={setSelectedRouteId}
            />
          )}

          {!plan && !loading && (
            <p className="empty">
              Enter a trip to compare three truck routes by the weather each one meets along the
              way.
            </p>
          )}

          {loading && (
            <div className="loading" aria-live="polite">
              <span className="loading__bar" />
              <p>Routing the truck, then sampling the forecast at every checkpoint…</p>
            </div>
          )}

          <footer className="sidebar__foot">
            {plan?.attribution.map((line) => (
              <span key={line}>{line}</span>
            ))}
          </footer>
        </aside>

        <main className="map-shell">
          <TripMap
            plan={plan}
            selectedRouteId={selectedRouteId}
            hour={hour}
            heatmapEnabled={heatmapEnabled}
            onSelectRoute={setSelectedRouteId}
          />

          {!plan && !loading && !sheetOpen && (
            <div className="map-cta">
              <h2>No trip planned yet</h2>
              <p>Compare three truck routes by the weather each one meets along the way.</p>
              <button type="button" className="primary-button" onClick={() => setSheetOpen(true)}>
                Plan a trip
              </button>
            </div>
          )}

          {/* The sheet covers the legend on mobile, so drop it while open. */}
          {!sheetOpen && (
            <div className="legend" aria-label="Risk legend">
              {LEGEND.map((severity) => (
                <span key={severity}>
                  <i style={{ background: SEVERITY_META[severity].color }} />
                  {SEVERITY_META[severity].label}
                </span>
              ))}
            </div>
          )}

          {plan && (
            <HeatmapPanel
              heatmap={plan.heatmap}
              hour={hour}
              enabled={heatmapEnabled}
              onHourChange={setHour}
              onToggle={setHeatmapEnabled}
            />
          )}
        </main>
      </div>
    </div>
  );
}
