import type React from 'react';
import type { Theme } from '../context/ThemeContext';

/** Chart colours per theme (SVG attributes cannot read CSS variables reliably). */
export function chartColors(theme: Theme) {
  const dark = theme === 'dark';
  return {
    grid: dark ? '#233a32' : '#e3e8e1',
    axis: dark ? '#96aaa2' : '#536661',
    primary: dark ? '#c9f45b' : '#183f36',
    secondary: dark ? '#7d8f88' : '#94a3b8',
    accent: dark ? '#7fb0ff' : '#2563eb',
    band: dark ? '#3a5a8f' : '#bfdbfe',
    tooltip: {
      background: dark ? '#14251f' : '#ffffff',
      border: `1px solid ${dark ? '#243831' : '#d8ded7'}`,
      borderRadius: 8,
      fontSize: 12,
      color: dark ? '#e6eee9' : '#132c27',
    } as React.CSSProperties,
  };
}
