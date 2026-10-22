export function formatProgramHours(hours: number): string {
  const total = Math.max(0, Math.round(hours));
  const lastTwo = total % 100;
  const last = total % 10;
  const word =
    last === 1 && lastTwo !== 11
      ? 'час'
      : last >= 2 && last <= 4 && (lastTwo < 12 || lastTwo > 14)
        ? 'часа'
        : 'часов';

  return `${total} ${word}`;
}
