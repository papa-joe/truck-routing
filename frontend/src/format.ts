import type { SeverityName } from "./types";

export function formatDuration(minutes: number): string {
  const total = Math.round(minutes);
  const hours = Math.floor(total / 60);
  const mins = total % 60;
  return hours > 0 ? `${hours}h ${mins.toString().padStart(2, "0")}m` : `${mins}m`;
}

export function formatClock(iso: string, timeZone?: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "—";
  return new Intl.DateTimeFormat("en-US", {
    hour: "numeric",
    minute: "2-digit",
    timeZone,
  }).format(date);
}

export function formatDayClock(iso: string, timeZone?: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "—";
  return new Intl.DateTimeFormat("en-US", {
    weekday: "short",
    hour: "numeric",
    minute: "2-digit",
    timeZone,
  }).format(date);
}

export function formatMiles(miles: number): string {
  return `${Math.round(miles).toLocaleString()} mi`;
}

export function formatRisk(value: number): string {
  return value.toFixed(2);
}

export const SEVERITY_LABEL: Record<SeverityName, string> = {
  low: "Low",
  moderate: "Moderate",
  high: "High",
  severe: "Severe",
  no_travel: "No travel",
  unknown: "No data",
};

export function toLocalInputValue(date: Date): string {
  const offset = date.getTimezoneOffset();
  return new Date(date.getTime() - offset * 60_000).toISOString().slice(0, 16);
}

export function nextLocalHour(hoursAhead: number): string {
  const when = new Date(Date.now() + hoursAhead * 3_600_000);
  when.setMinutes(0, 0, 0);
  return toLocalInputValue(when);
}
