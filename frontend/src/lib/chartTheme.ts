import type React from 'react';
import type { Theme } from '../context/ThemeContext';

/** Chart colours per theme (SVG attributes cannot read CSS variables reliably). */
export function chartColors(theme: Theme) {
  const dark = theme === 'dark';
  return {
    grid: dark ? '#1b3329' : '#e3e8e1',
    axis: dark ? '#91a69a' : '#536661',
    primary: dark ? '#21e58a' : '#183f36',
    secondary: dark ? '#6b8c7c' : '#94a3b8',
    accent: dark ? '#36d9c0' : '#2563eb',
    band: dark ? '#3a5a8f' : '#bfdbfe',
    // Operations series (validated categorical trio, light/dark): dispatch/capacity, demand, backlog/repaired.
    dispatch: dark ? '#5b8def' : '#2563eb',
    demand: dark ? '#d9622a' : '#c2410c',
    backlog: dark ? '#14a38b' : '#0d9488',
    zero: dark ? '#f0f7f2' : '#132c27',
    tooltip: {
      background: dark ? 'rgba(9, 24, 18, 0.92)' : '#ffffff',
      border: `1px solid ${dark ? 'rgba(33, 229, 138, 0.35)' : '#d8ded7'}`,
      borderRadius: 10,
      fontSize: 12,
      color: dark ? '#f0f7f2' : '#132c27',
      boxShadow: dark ? '0 8px 28px rgba(0, 0, 0, 0.55), 0 0 18px -6px rgba(33, 229, 138, 0.4)' : undefined,
      backdropFilter: 'blur(8px)',
    } as React.CSSProperties,
  };
}
