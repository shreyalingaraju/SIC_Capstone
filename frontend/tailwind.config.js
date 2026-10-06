/** @type {import('tailwindcss').Config} */
// All theme-dependent colours are CSS variables (see src/styles/globals.css) so the
// light and night themes are switched centrally by the data-theme attribute on <html>.
const c = (name) => `rgb(var(--c-${name}) / <alpha-value>)`;

export default {
  content: ["./index.html", "./src/**/*.{js,ts,jsx,tsx}"],
  theme: {
    extend: {
      colors: {
        ink: { DEFAULT: c('ink'), soft: c('ink-soft') },
        paper: c('paper'),
        surface: { DEFAULT: c('surface'), strong: c('surface-strong') },
        line: { DEFAULT: c('line'), soft: c('line-soft') },
        night: { DEFAULT: c('night'), deep: c('night-deep'), light: c('night-light') },
        signal: { DEFAULT: c('signal'), deep: c('signal-deep') },
        brand: c('brand'),
        priority: {
          high: '#f06f51',
          medium: '#eeb64b',
          low: '#6fa6a0',
          repaired: '#94a3b8',
        },
      },
      fontFamily: {
        sans: ['Manrope', 'system-ui', 'sans-serif'],
        mono: ['"DM Mono"', 'ui-monospace', 'monospace'],
      },
      borderRadius: { card: '10px' },
      boxShadow: {
        subtle: 'var(--shadow-subtle)',
        elevated: 'var(--shadow-elevated)',
        modal: 'var(--shadow-modal)',
      },
      keyframes: {
        'fade-up': { from: { opacity: '0', transform: 'translateY(4px)' }, to: { opacity: '1', transform: 'none' } },
        'fade-in': { from: { opacity: '0' }, to: { opacity: '1' } },
        'pop-in': { from: { opacity: '0', transform: 'translateY(-4px) scale(0.98)' }, to: { opacity: '1', transform: 'none' } },
      },
      animation: {
        'fade-up': 'fade-up 180ms ease-out both',
        'fade-in': 'fade-in 150ms ease-out both',
        'pop-in': 'pop-in 140ms ease-out both',
      },
    },
  },
  plugins: [],
};
