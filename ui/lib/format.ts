const dateFormatter = new Intl.DateTimeFormat("en-AU", {
  day: "numeric",
  month: "short",
  year: "numeric",
  timeZone: "Australia/Melbourne",
});

const dateTimeFormatter = new Intl.DateTimeFormat("en-AU", {
  day: "numeric",
  month: "short",
  year: "numeric",
  hour: "numeric",
  minute: "2-digit",
  timeZone: "Australia/Melbourne",
  timeZoneName: "short",
});

export function formatDate(value: string): string {
  return dateFormatter.format(new Date(`${value}T12:00:00+10:00`));
}

export function formatDateTime(value: string): string {
  return dateTimeFormatter.format(new Date(value));
}

export function formatHours(value: string): string {
  return `${Number(value).toLocaleString("en-AU", { maximumFractionDigits: 2 })} hrs`;
}

export function formatDays(hours: string, hoursPerDay: number): string {
  return `${(Number(hours) / hoursPerDay).toLocaleString("en-AU", {
    maximumFractionDigits: 1,
  })} days`;
}

export function sentenceCase(value: string): string {
  return value
    .toLowerCase()
    .replaceAll("_", " ")
    .replace(/^./, (character) => character.toUpperCase());
}
