import type { IndexSchedule } from "./api";

/** The "How often should DeepFind index?" choices, in the order they appear in the dropdown. */
export const SCHEDULES: { id: IndexSchedule; label: string; hint: string }[] = [
  { id: "realtime", label: "As files change", hint: "New and edited files become searchable within seconds." },
  { id: "15m", label: "Every 15 minutes", hint: "Reads everything that changed, once every 15 minutes." },
  { id: "30m", label: "Every 30 minutes", hint: "Reads everything that changed, once every 30 minutes." },
  { id: "1h", label: "Every hour", hint: "Reads everything that changed, once an hour." },
  { id: "6h", label: "Every 6 hours", hint: "Reads everything that changed, four times a day." },
  { id: "24h", label: "Once a day", hint: "Reads everything that changed, once every 24 hours." },
  { id: "manual", label: "Only when I click “Index now”", hint: "Nothing is read until you ask. Good for saving battery." },
];

export const scheduleOf = (id?: IndexSchedule) => SCHEDULES.find((s) => s.id === id) ?? SCHEDULES[0];

/** "just now", "5 min ago", "3 h ago", "2 days ago" for an epoch-seconds time. */
export function fmtWhen(epochSec?: number | null): string {
  if (!epochSec) return "never";
  const s = Math.max(0, Date.now() / 1000 - epochSec);
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  if (s < 86400) return `${Math.round(s / 3600)} h ago`;
  const d = Math.round(s / 86400);
  return d === 1 ? "yesterday" : `${d} days ago`;
}

/** "any moment", "in 12 min", "in 3 h" for an epoch-seconds time in the future. */
export function fmtIn(epochSec?: number | null): string {
  if (!epochSec) return "";
  const s = epochSec - Date.now() / 1000;
  if (s <= 30) return "any moment";
  if (s < 3600) return `in ${Math.max(1, Math.round(s / 60))} min`;
  if (s < 86400) return `in ${Math.round(s / 3600)} h`;
  return `in ${Math.round(s / 86400)} days`;
}