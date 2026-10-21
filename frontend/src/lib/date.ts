/**
 * Timezone-aware date and time formatting utilities.
 *
 * Ensures ISO-8601 strings lacking UTC designators from SQLite/Python
 * are normalized to UTC before parsing, preventing incorrect local timezone offsets.
 */

/**
 * Safely parse a date/time string from the backend into a valid Date object.
 * Normalizes ISO strings that omit UTC timezone markers (Z or offset)
 * so they are never erroneously interpreted as local browser time.
 */
export function parseServerDate(dateStr: string | null | undefined): Date {
  if (!dateStr) return new Date();
  let str = String(dateStr).trim();
  if (str.includes(" ") && !str.includes("T")) {
    str = str.replace(" ", "T");
  }
  // If no timezone suffix (no Z, and no +XX:XX or -XX:XX at the end)
  if (!str.endsWith("Z") && !/[+-]\d{2}(:\d{2})?$/.test(str)) {
    str = `${str}Z`;
  }
  const d = new Date(str);
  return isNaN(d.getTime()) ? new Date(dateStr) : d;
}

/**
 * Format relative time (e.g. "Just now", "2m ago", "1h ago", "Yesterday", "Oct 18")
 * with accurate timezone-aware diff calculation.
 */
export function formatRelativeTime(dateStr: string | null | undefined): string {
  if (!dateStr) return "";
  try {
    const date = parseServerDate(dateStr);
    const now = new Date();
    const diffMs = now.getTime() - date.getTime();

    // Tolerate slight server/client clock drift (< 60 seconds into future)
    if (diffMs < 0 && diffMs > -60000) {
      return "Just now";
    }

    const diffSec = Math.floor(diffMs / 1000);
    const diffMin = Math.floor(diffSec / 60);
    const diffHr = Math.floor(diffMin / 60);
    const diffDays = Math.floor(diffHr / 24);

    if (diffSec < 60) return "Just now";
    if (diffMin < 60) return `${diffMin}m ago`;
    if (diffHr < 24) return `${diffHr}h ago`;
    if (diffDays === 1) return "Yesterday";
    if (diffDays < 7) return `${diffDays}d ago`;
    return date.toLocaleDateString([], { month: "short", day: "numeric" });
  } catch {
    return String(dateStr);
  }
}

/**
 * Format a complete date-time string in user's local timezone.
 * e.g. "Oct 18, 2026, 3:15:20 PM"
 */
export function formatDateTime(dateStr: string | null | undefined): string {
  if (!dateStr) return "";
  try {
    const date = parseServerDate(dateStr);
    return date.toLocaleString([], {
      month: "short",
      day: "numeric",
      year: "numeric",
      hour: "numeric",
      minute: "2-digit",
      second: "2-digit",
    });
  } catch {
    return String(dateStr);
  }
}

/**
 * Format date only in user's local timezone.
 * e.g. "Oct 18, 2026"
 */
export function formatDate(dateStr: string | null | undefined): string {
  if (!dateStr) return "";
  try {
    const date = parseServerDate(dateStr);
    return date.toLocaleDateString([], {
      month: "short",
      day: "numeric",
      year: "numeric",
    });
  } catch {
    return String(dateStr);
  }
}

/**
 * Format time only in user's local timezone.
 * e.g. "3:15 PM"
 */
export function formatTime(dateStr: string | null | undefined): string {
  if (!dateStr) return "";
  try {
    const date = parseServerDate(dateStr);
    return date.toLocaleTimeString([], {
      hour: "numeric",
      minute: "2-digit",
    });
  } catch {
    return String(dateStr);
  }
}

