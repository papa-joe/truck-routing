import { formatClock, formatDuration, formatMiles, formatRisk } from "../format";
import type { Route, SeverityName } from "../types";
import { SEVERITY_META } from "../types";

interface RouteCardsProps {
  routes: Route[];
  selectedId: string;
  onSelect: (id: string) => void;
}

const BAR_ORDER: SeverityName[] = [
  "low",
  "moderate",
  "high",
  "severe",
  "no_travel",
  "unknown",
];

function barSegments(route: Route) {
  const summary = route.summary;
  const miles: Record<SeverityName, number> = {
    low: summary.lowMiles,
    moderate: summary.moderateMiles,
    high: summary.highMiles,
    // severeMiles includes no-travel miles by design; split them for display.
    severe: Math.max(0, summary.severeMiles - summary.noTravelMiles),
    no_travel: summary.noTravelMiles,
    unknown: summary.unknownMiles,
  };
  return BAR_ORDER.filter((key) => miles[key] > 0).map((key) => ({
    key,
    miles: miles[key],
    percent: summary.totalMiles > 0 ? (miles[key] / summary.totalMiles) * 100 : 0,
  }));
}

export function RouteCards({ routes, selectedId, onSelect }: RouteCardsProps) {
  return (
    <div className="route-cards">
      <div className="route-cards__head">
        <h2>Routes</h2>
        <span>{routes.length} option{routes.length === 1 ? "" : "s"}</span>
      </div>

      {routes.map((route) => {
        const segments = barSegments(route);
        const selected = route.id === selectedId;
        return (
          <button
            key={route.id}
            type="button"
            className={selected ? "route-card is-selected" : "route-card"}
            onClick={() => onSelect(route.id)}
            aria-pressed={selected}
          >
            <div className="route-card__top">
              <span className={route.isRecommended ? "rank rank--best" : "rank"}>
                {route.isRecommended ? "Best" : `#${route.rank}`}
              </span>
              <span className="route-card__label">{route.label}</span>
              {route.summary.noTravelMiles > 0 && (
                <span className="badge badge--danger">Do not travel</span>
              )}
            </div>

            <div className="route-card__stats">
              <strong>{formatDuration(route.durationMinutes)}</strong>
              <span>{formatMiles(route.distanceMiles)}</span>
              <span>arrive {formatClock(route.arrivalAt)}</span>
            </div>

            <div className="risk-bar" role="img" aria-label="Miles by risk level">
              {segments.length === 0 && <span className="risk-bar__empty" />}
              {segments.map((segment) => (
                <span
                  key={segment.key}
                  className="risk-bar__segment"
                  style={{
                    width: `${segment.percent}%`,
                    background: SEVERITY_META[segment.key].color,
                  }}
                  title={`${SEVERITY_META[segment.key].label}: ${Math.round(segment.miles)} mi`}
                />
              ))}
            </div>

            <dl className="route-card__risk">
              <div>
                <dt>Severe</dt>
                <dd>{Math.round(route.summary.severeMiles)} mi</dd>
              </div>
              <div>
                <dt>High</dt>
                <dd>{Math.round(route.summary.highMiles)} mi</dd>
              </div>
              <div>
                <dt title="Miles with no forecast; never treated as safe">No data</dt>
                <dd>{Math.round(route.summary.unknownMiles)} mi</dd>
              </div>
              <div>
                <dt title="Average risk per mile, 0 = low, 4 = no travel">Avg</dt>
                <dd>{formatRisk(route.summary.avgRisk)}</dd>
              </div>
            </dl>

            {route.warnings.length > 0 && (
              <ul className="route-card__warnings">
                {route.warnings.map((warning) => (
                  <li key={warning}>{warning}</li>
                ))}
              </ul>
            )}
          </button>
        );
      })}

      <p className="route-cards__legend">
        Ranked by fewest severe miles, then fewest high miles, then lowest average risk, then
        shortest travel time.
      </p>
    </div>
  );
}
