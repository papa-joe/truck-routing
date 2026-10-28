export type SeverityName =
  | "low"
  | "moderate"
  | "high"
  | "severe"
  | "no_travel"
  | "unknown";

export interface GeoPoint {
  lat: number;
  lon: number;
  label?: string;
}

export interface Place {
  label: string;
  lat: number;
  lon: number;
  kind: string;
}

export type Position = [number, number];

export interface Checkpoint {
  distanceMiles: number;
  position: Position;
  eta: string;
  elapsedMinutes: number;
  windMph: number | null;
  rainInHr: number | null;
  snowInHr: number | null;
  severity: SeverityName;
  hasData: boolean;
}

export interface RouteSummary {
  totalMiles: number;
  lowMiles: number;
  moderateMiles: number;
  highMiles: number;
  severeMiles: number;
  noTravelMiles: number;
  unknownMiles: number;
  avgRisk: number;
}

export interface Route {
  id: string;
  label: string;
  rank: number;
  isRecommended: boolean;
  distanceMiles: number;
  durationMinutes: number;
  arrivalAt: string;
  intervalMiles: number;
  geometry: Position[];
  checkpoints: Checkpoint[];
  summary: RouteSummary;
  warnings: string[];
}

export interface HeatmapCell {
  position: Position;
  /** One severity index per hour: 0 low … 4 no travel, 5 unknown. */
  values: number[];
}

export interface Heatmap {
  hours: string[];
  cells: HeatmapCell[];
  tripWindow: { startHour: number; endHour: number } | null;
  generatedAt: string;
  sampleMiles: number;
}

export interface Plan {
  origin: { lat: number; lon: number };
  destination: { lat: number; lon: number };
  departureAt: string;
  loadLbs: number;
  intervalMiles: number;
  recommendedRouteId: string;
  routes: Route[];
  heatmap: Heatmap;
  notices: string[];
  attribution: string[];
}

export interface PlanRequest {
  origin: GeoPoint;
  destination: GeoPoint;
  departureAt: string;
  loadLbs: number;
  intervalMiles: number;
}

export const SEVERITY_ORDER: SeverityName[] = [
  "low",
  "moderate",
  "high",
  "severe",
  "no_travel",
  "unknown",
];

export const SEVERITY_META: Record<
  SeverityName,
  { label: string; color: string; short: string }
> = {
  low: { label: "Low", color: "#15803d", short: "Low" },
  moderate: { label: "Moderate", color: "#ca8a04", short: "Mod" },
  high: { label: "High", color: "#ea580c", short: "High" },
  severe: { label: "Severe", color: "#dc2626", short: "Sev" },
  no_travel: { label: "No travel", color: "#7f1d1d", short: "Stop" },
  unknown: { label: "No data", color: "#64748b", short: "N/A" },
};
