import maplibregl, {
  type CircleLayerSpecification,
  type ExpressionSpecification,
  type GeoJSONSource,
  type HeatmapLayerSpecification,
  type LineLayerSpecification,
} from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import { useEffect, useRef, useState } from "react";
import type { Feature, FeatureCollection, LineString, Point } from "geojson";
import { formatClock, formatDuration } from "../format";
import type { Plan, Position, Route, SeverityName } from "../types";

const STYLE_URL =
  (import.meta.env.VITE_MAP_STYLE_URL as string | undefined) ??
  "https://tiles.openfreemap.org/styles/positron";

const SEVERITY_COLORS: Record<SeverityName, string> = {
  low: "#15803d",
  moderate: "#ca8a04",
  high: "#ea580c",
  severe: "#dc2626",
  no_travel: "#7f1d1d",
  unknown: "#64748b",
};

/** Style expressions are deeply typed tuples; a single narrow cast keeps the
 *  layer definitions readable without spreading `any` through the file. */
const expr = (value: unknown): ExpressionSpecification =>
  value as ExpressionSpecification;

function emptyCollection(): FeatureCollection {
  return { type: "FeatureCollection", features: [] };
}

function haversineMiles(a: Position, b: Position): number {
  const toRad = Math.PI / 180;
  const dLat = (b[1] - a[1]) * toRad;
  const dLon = (b[0] - a[0]) * toRad;
  const h =
    Math.sin(dLat / 2) ** 2 +
    Math.cos(a[1] * toRad) * Math.cos(b[1] * toRad) * Math.sin(dLon / 2) ** 2;
  return 2 * 3958.8 * Math.asin(Math.min(1, Math.sqrt(h)));
}

function routesToFeatures(routes: Route[], selectedId: string | null): FeatureCollection<LineString> {
  return {
    type: "FeatureCollection",
    features: routes.map((route) => ({
      type: "Feature",
      properties: {
        routeId: route.id,
        selected: route.id === selectedId ? 1 : 0,
      },
      geometry: { type: "LineString", coordinates: route.geometry },
    })),
  };
}

/** Colour the selected route by the risk of the checkpoint that governs each
 *  stretch, merging neighbouring points so the source stays small. */
function ribbonFeatures(route: Route | undefined): FeatureCollection<LineString> {
  if (!route || route.geometry.length < 2) {
    return { type: "FeatureCollection", features: [] };
  }

  const checkpoints = route.checkpoints;
  if (checkpoints.length === 0) {
    return { type: "FeatureCollection", features: [] };
  }

  const features: Feature<LineString>[] = [];
  let run: Position[] = [];
  let runSeverity: SeverityName | null = null;
  let distance = 0;
  let checkpointIndex = 0;

  const first = route.geometry[0];
  if (!first) return { type: "FeatureCollection", features: [] };

  for (let index = 1; index < route.geometry.length; index += 1) {
    const previous = route.geometry[index - 1];
    const current = route.geometry[index];
    if (!previous || !current) continue;

    while (
      checkpointIndex + 1 < checkpoints.length &&
      (checkpoints[checkpointIndex + 1]?.distanceMiles ?? Number.POSITIVE_INFINITY) <= distance
    ) {
      checkpointIndex += 1;
    }
    const severity = checkpoints[checkpointIndex]?.severity ?? "unknown";

    if (severity !== runSeverity) {
      if (run.length > 1 && runSeverity) {
        features.push({
          type: "Feature",
          properties: { severity: runSeverity },
          geometry: { type: "LineString", coordinates: [...run] },
        });
      }
      run = [previous];
      runSeverity = severity;
    }
    run.push(current);
    distance += haversineMiles(previous, current);
  }

  if (run.length > 1 && runSeverity) {
    features.push({
      type: "Feature",
      properties: { severity: runSeverity },
      geometry: { type: "LineString", coordinates: run },
    });
  }

  return { type: "FeatureCollection", features };
}

function checkpointFeatures(route: Route | undefined): FeatureCollection<Point> {
  if (!route) return { type: "FeatureCollection", features: [] };
  return {
    type: "FeatureCollection",
    features: route.checkpoints.map((checkpoint) => ({
      type: "Feature",
      properties: {
        severity: checkpoint.severity,
        windMph: checkpoint.windMph,
        rainInHr: checkpoint.rainInHr,
        snowInHr: checkpoint.snowInHr,
        eta: checkpoint.eta,
        distanceMiles: checkpoint.distanceMiles,
        hasData: checkpoint.hasData ? 1 : 0,
      },
      geometry: { type: "Point", coordinates: checkpoint.position },
    })),
  };
}

const HEAT_WEIGHT = [0.25, 0.45, 0.68, 0.88, 1, 0];

function heatFeatures(plan: Plan, hour: number): FeatureCollection<Point> {
  const features: Feature<Point>[] = [];
  for (const cell of plan.heatmap.cells) {
    const value = cell.values[hour];
    if (value === undefined || value >= 5) continue;
    features.push({
      type: "Feature",
      properties: { weight: HEAT_WEIGHT[value] ?? 0 },
      geometry: { type: "Point", coordinates: cell.position },
    });
  }
  return { type: "FeatureCollection", features };
}

function boundsOf(routes: Route[]): maplibregl.LngLatBounds | null {
  const bounds = new maplibregl.LngLatBounds();
  let seen = false;
  for (const route of routes) {
    for (const coordinate of route.geometry) {
      bounds.extend(coordinate as [number, number]);
      seen = true;
    }
  }
  return seen ? bounds : null;
}

function popupElement(properties: Record<string, unknown>): HTMLElement {
  const wrapper = document.createElement("div");
  wrapper.className = "popup";

  const distance = Number(properties.distanceMiles ?? 0);
  const title = document.createElement("strong");
  title.textContent = `${Math.round(distance)} mi`;
  wrapper.appendChild(title);

  const time = document.createElement("div");
  time.className = "popup__time";
  time.textContent = formatClock(String(properties.eta ?? ""));
  wrapper.appendChild(time);

  const rows: Array<[string, string]> = [
    ["Severity", String(properties.severity ?? "unknown").replace("_", " ")],
    ["Wind", properties.windMph === null ? "no data" : `${properties.windMph} mph`],
    ["Rain", properties.rainInHr === null ? "no data" : `${properties.rainInHr} in/hr`],
    ["Snow", properties.snowInHr === null ? "no data" : `${properties.snowInHr} in/hr`],
  ];

  const list = document.createElement("dl");
  for (const [label, value] of rows) {
    const dt = document.createElement("dt");
    dt.textContent = label;
    const dd = document.createElement("dd");
    dd.textContent = value;
    list.append(dt, dd);
  }
  wrapper.appendChild(list);
  return wrapper;
}

interface TripMapProps {
  plan: Plan | null;
  selectedRouteId: string | null;
  hour: number;
  heatmapEnabled: boolean;
  onSelectRoute: (id: string) => void;
}

export function TripMap({
  plan,
  selectedRouteId,
  hour,
  heatmapEnabled,
  onSelectRoute,
}: TripMapProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<maplibregl.Map | null>(null);
  const fittedPlan = useRef<Plan | null>(null);
  const selectRoute = useRef(onSelectRoute);
  const [ready, setReady] = useState(false);

  selectRoute.current = onSelectRoute;

  useEffect(() => {
    if (!containerRef.current) return;
    let cancelled = false;

    const map = new maplibregl.Map({
      container: containerRef.current,
      style: STYLE_URL,
      center: [-98.5, 39.5],
      zoom: 3.6,
      attributionControl: false,
    });
    mapRef.current = map;

    map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");
    map.addControl(
      new maplibregl.AttributionControl({
        compact: true,
        customAttribution: "openrouteservice · Open-Meteo",
      }),
      "bottom-right",
    );

    map.on("load", () => {
      if (cancelled) return;

      map.addSource("heat", { type: "geojson", data: emptyCollection() });
      map.addSource("ribbon", { type: "geojson", data: emptyCollection() });
      map.addSource("routes", { type: "geojson", data: emptyCollection() });
      map.addSource("checkpoints", { type: "geojson", data: emptyCollection() });

      const heatPaint: HeatmapLayerSpecification["paint"] = {
        "heatmap-weight": expr(["interpolate", ["linear"], ["get", "weight"], 0, 0, 1, 1]),
        "heatmap-intensity": expr(["interpolate", ["linear"], ["zoom"], 4, 0.7, 9, 1.1]),
        "heatmap-radius": expr(["interpolate", ["linear"], ["zoom"], 4, 10, 6, 16, 9, 26, 12, 38]),
        "heatmap-opacity": 0.62,
        "heatmap-color": expr([
          "interpolate",
          ["linear"],
          ["heatmap-density"],
          0,
          "rgba(21,128,61,0)",
          0.10,
          "rgba(34,197,94,0.45)",
          0.35,
          "#eab308",
          0.58,
          "#f97316",
          0.8,
          "#dc2626",
          1,
          "#7f1d1d",
        ]),
      };
      map.addLayer({ id: "heat", type: "heatmap", source: "heat", paint: heatPaint });

      const ribbonPaint: LineLayerSpecification["paint"] = {
        "line-color": expr([
          "match",
          ["get", "severity"],
          "low",
          SEVERITY_COLORS.low,
          "moderate",
          SEVERITY_COLORS.moderate,
          "high",
          SEVERITY_COLORS.high,
          "severe",
          SEVERITY_COLORS.severe,
          "no_travel",
          SEVERITY_COLORS.no_travel,
          SEVERITY_COLORS.unknown,
        ]),
        "line-width": expr(["interpolate", ["linear"], ["zoom"], 4, 5, 8, 9, 12, 12]),
        "line-opacity": 0.55,
        "line-blur": 1.2,
      };
      map.addLayer({ id: "ribbon", type: "line", source: "ribbon", paint: ribbonPaint });

      const casingPaint: LineLayerSpecification["paint"] = {
        "line-color": "#ffffff",
        "line-width": expr([
          "interpolate",
          ["linear"],
          ["zoom"],
          4,
          ["case", ["==", ["get", "selected"], 1], 6, 4],
          10,
          ["case", ["==", ["get", "selected"], 1], 9, 6],
        ]),
        "line-opacity": 0.85,
      };
      map.addLayer({ id: "route-casing", type: "line", source: "routes", paint: casingPaint });

      const routePaint: LineLayerSpecification["paint"] = {
        "line-color": expr([
          "case",
          ["==", ["get", "selected"], 1],
          "#0f172a",
          "#94a3b8",
        ]),
        "line-width": expr([
          "interpolate",
          ["linear"],
          ["zoom"],
          4,
          ["case", ["==", ["get", "selected"], 1], 3, 1.8],
          10,
          ["case", ["==", ["get", "selected"], 1], 5, 3],
        ]),
        "line-opacity": expr(["case", ["==", ["get", "selected"], 1], 1, 0.75]),
      };
      map.addLayer({ id: "route-lines", type: "line", source: "routes", paint: routePaint });

      const checkpointPaint: CircleLayerSpecification["paint"] = {
        "circle-radius": expr(["interpolate", ["linear"], ["zoom"], 4, 3, 8, 6, 12, 8]),
        "circle-color": expr([
          "match",
          ["get", "severity"],
          "low",
          SEVERITY_COLORS.low,
          "moderate",
          SEVERITY_COLORS.moderate,
          "high",
          SEVERITY_COLORS.high,
          "severe",
          SEVERITY_COLORS.severe,
          "no_travel",
          SEVERITY_COLORS.no_travel,
          SEVERITY_COLORS.unknown,
        ]),
        "circle-stroke-width": 1.5,
        "circle-stroke-color": "#ffffff",
        "circle-opacity": 0.95,
      };
      map.addLayer({
        id: "checkpoints",
        type: "circle",
        source: "checkpoints",
        paint: checkpointPaint,
      });

      map.on("click", "route-lines", (event) => {
        const feature = event.features?.[0];
        const id = feature?.properties?.["routeId"];
        if (typeof id === "string") selectRoute.current(id);
      });

      map.on("mouseenter", "route-lines", () => {
        map.getCanvas().style.cursor = "pointer";
      });
      map.on("mouseleave", "route-lines", () => {
        map.getCanvas().style.cursor = "";
      });

      const popup = new maplibregl.Popup({ closeButton: true, maxWidth: "240px" });
      map.on("click", "checkpoints", (event) => {
        const feature = event.features?.[0];
        if (!feature) return;
        const properties = (feature.properties ?? {}) as Record<string, unknown>;
        const geometry = feature.geometry;
        if (geometry.type !== "Point") return;
        popup
          .setLngLat(geometry.coordinates as [number, number])
          .setDOMContent(popupElement(properties))
          .addTo(map);
      });

      setReady(true);
    });

    return () => {
      cancelled = true;
      setReady(false);
      map.remove();
      mapRef.current = null;
    };
  }, []);

  useEffect(() => {
    const map = mapRef.current;
    if (!ready || !map || !plan) return;

    const routeSource = map.getSource<GeoJSONSource>("routes");
    const ribbonSource = map.getSource<GeoJSONSource>("ribbon");
    const checkpointSource = map.getSource<GeoJSONSource>("checkpoints");
    if (!routeSource || !ribbonSource || !checkpointSource) return;

    const selected =
      plan.routes.find((route) => route.id === selectedRouteId) ??
      plan.routes.find((route) => route.isRecommended) ??
      plan.routes[0];

    routeSource.setData(routesToFeatures(plan.routes, selected?.id ?? null));
    ribbonSource.setData(ribbonFeatures(selected));
    checkpointSource.setData(checkpointFeatures(selected));

    if (fittedPlan.current !== plan) {
      fittedPlan.current = plan;
      const bounds = boundsOf(plan.routes);
      if (bounds) {
        // The sidebar overlays the map on wide screens, and the heatmap panel
        // covers the bottom on narrow ones.
        const narrow = map.getContainer().clientWidth < 720;
        map.fitBounds(bounds, {
          padding: narrow
            ? { top: 84, bottom: 124, left: 34, right: 34 }
            : { top: 60, bottom: 90, left: 40, right: 340 },
          maxZoom: 11,
        });
      }
    }
  }, [ready, plan, selectedRouteId]);

  useEffect(() => {
    const map = mapRef.current;
    if (!ready || !map) return;
    map.setLayoutProperty("heat", "visibility", heatmapEnabled ? "visible" : "none");
  }, [ready, heatmapEnabled]);

  useEffect(() => {
    const map = mapRef.current;
    if (!ready || !map || !plan) return;
    const heatSource = map.getSource<GeoJSONSource>("heat");
    if (!heatSource) return;
    heatSource.setData(heatFeatures(plan, hour));
  }, [ready, plan, hour]);

  return <div className="map" ref={containerRef} />;
}

export function tripSummaryLine(plan: Plan): string {
  const recommended = plan.routes.find((route) => route.isRecommended);
  if (!recommended) return "";
  return `${recommended.label} · ${formatDuration(recommended.durationMinutes)} · ${Math.round(
    recommended.distanceMiles,
  )} mi`;
}
