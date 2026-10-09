import React from 'react';
import { NavLink } from 'react-router-dom';
import { LayoutDashboard, Map, Lightbulb, ListOrdered, FlaskConical, PlayCircle, Microscope } from 'lucide-react';
import { useProfile } from '../../context/ProfileContext';

const NAV_SECTIONS = [
  {
    title: null,
    items: [
      { path: '/', label: 'Overview', icon: LayoutDashboard, end: true },
      { path: '/map', label: 'City Map', icon: Map, end: false },
      { path: '/outages', label: 'Outages', icon: Lightbulb, end: false },
      { path: '/priority', label: 'Dispatch Plan', icon: ListOrdered, end: false },
    ],
  },
  {
    title: 'Evidence',
    items: [
      { path: '/causal', label: 'Legacy DiD Summary', icon: FlaskConical, end: false },
    ],
  },
  {
    title: 'Operations',
    items: [
      { path: '/replay', label: 'City Replay', icon: PlayCircle, end: false },
    ],
  },
];

export const Sidebar: React.FC = () => {
  const { synthetic } = useProfile();
  const sections = synthetic
    ? [
        { title: null, items: [{ path: '/synthetic', label: 'Synthetic Analysis', icon: Microscope, end: false }, ...NAV_SECTIONS[0].items.map((i) => (i.path === '/' ? { ...i, path: '/overview', label: 'Dispatch Overview' } : i))] },
        NAV_SECTIONS[1],
      ]
    : NAV_SECTIONS;
  return (
  <aside className="flex w-14 shrink-0 flex-col border-r border-signal/10 bg-night-deep/95 text-slate-300 backdrop-blur xl:w-60" aria-label="Primary">
    <nav className="flex flex-col gap-0.5 p-2 xl:p-3">
      {sections.map((section) => (
        <React.Fragment key={section.title ?? 'main'}>
          {section.title && (
            <p className="mt-3 hidden px-3 pb-1 text-[10px] font-bold uppercase tracking-wider text-slate-500 xl:block">{section.title}</p>
          )}
          {section.title && <span aria-hidden className="mx-2 my-2 block h-px bg-white/10 xl:hidden" />}
          {section.items.map(({ path, label, icon: Icon, end }) => (
        <NavLink
          key={path}
          to={path}
          end={end}
          title={label}
          className={({ isActive }) =>
            `group relative flex h-9 items-center gap-3 rounded-lg px-2.5 text-[13px] font-semibold transition-all duration-200 xl:px-3 ${
              isActive ? 'bg-signal/10 text-white shadow-[inset_0_0_0_1px_rgb(var(--c-signal)/0.28),0_0_22px_-6px_rgb(var(--c-signal)/0.55)]' : 'text-slate-400 hover:translate-x-0.5 hover:bg-signal/5 hover:text-slate-100'
            }`
          }
        >
          {({ isActive }) => (
            <>
              <span
                aria-hidden
                className={`absolute -left-1 top-1/2 h-5 w-[3px] -translate-y-1/2 rounded-full bg-signal shadow-[0_0_10px_rgb(var(--c-signal))] transition-all duration-200 ${isActive ? 'scale-y-100 opacity-100' : 'scale-y-50 opacity-0'}`}
              />
              <Icon size={16} strokeWidth={2} className={`shrink-0 transition-colors duration-150 ${isActive ? 'text-signal' : 'text-slate-500 group-hover:text-slate-300'}`} />
              <span className="hidden xl:inline">{label}</span>
            </>
          )}
        </NavLink>
          ))}
        </React.Fragment>
      ))}
    </nav>

    <p className="mt-auto hidden px-5 pb-4 text-[11px] leading-snug text-slate-400 xl:block">
      Recommendations support, but do not replace, dispatch judgement.
    </p>
  </aside>
  );
};
