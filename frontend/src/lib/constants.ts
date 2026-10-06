export const BOROUGHS = ['All', 'Manhattan', 'Brooklyn', 'Queens', 'Bronx', 'Staten Island'] as const;
export const PRIORITY_TIERS = ['All', 'High', 'Medium', 'Low'] as const;

/** Tier marker colours on the map (identical in both themes). */
export const TIER_MAP_COLORS = { High: '#f06f51', Medium: '#eeb64b', Low: '#6fa6a0' } as const;

export const TIER_BADGE = { High: 'badge-high', Medium: 'badge-medium', Low: 'badge-low' } as const;

export const DISPATCH_STATUS = {
  recommended: { label: 'Repair', long: 'Recommended for repair', badge: 'badge-ok' },
  deferred: { label: 'Deferred', long: 'Deferred', badge: 'badge-neutral' },
  not_scored: { label: 'Not scored', long: 'Not scored', badge: 'badge-medium' },
} as const;

export const MAP_CENTER: [number, number] = [-73.98, 40.72];
export const MAP_DEFAULT_ZOOM = 10.5;
