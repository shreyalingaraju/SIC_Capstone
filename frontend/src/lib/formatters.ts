const dash = '–';

export function formatScore(val: number | undefined | null): string {
  if (val === undefined || val === null || isNaN(val)) return dash;
  return val.toFixed(1);
}

export function formatImpact(val: number | undefined | null): string {
  if (val === undefined || val === null || isNaN(val)) return dash;
  return val >= 100 ? val.toLocaleString('en-US', { maximumFractionDigits: 1 }) : val.toFixed(1);
}

export function formatPct(val: number | undefined | null): string {
  if (val === undefined || val === null || isNaN(val)) return dash;
  const prefix = val > 0 ? '+' : '';
  return val > 999 ? `${prefix}${val.toLocaleString('en-US', { maximumFractionDigits: 0 })}%` : `${prefix}${val.toFixed(1)}%`;
}

export function formatNumber(val: number | undefined | null): string {
  if (val === undefined || val === null || isNaN(val)) return dash;
  return val.toLocaleString('en-US');
}

export function formatDays(val: number | undefined | null): string {
  if (val === undefined || val === null || isNaN(val)) return dash;
  return `${val.toLocaleString('en-US', { maximumFractionDigits: 1 })} d`;
}

export function formatDate(dateStr: string | undefined | null): string {
  if (!dateStr) return dash;
  const d = new Date(dateStr.replace(' ', 'T'));
  if (isNaN(d.getTime())) return dateStr.split(' ')[0] || dateStr;
  return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
}
