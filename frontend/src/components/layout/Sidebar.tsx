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
  <aside className="flex w-14 shrink-0 flex-col border-r border-night-light/40 bg-night-deep text-slate-300 xl:w-60" aria-label="Primary">
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
            `group relative flex h-9 items-center gap-3 rounded-md px-2.5 text-[13px] font-semibold transition-colors duration-150 xl:px-3 ${
              isActive ? 'bg-white/10 text-white' : 'text-slate-400 hover:bg-white/5 hover:text-slate-100'
            }`
          }
        >
          {({ isActive }) => (
            <>
              <span
                aria-hidden
                className={`absolute left-0 top-1/2 h-4 w-0.5 -translate-y-1/2 rounded-full bg-signal transition-opacity duration-150 ${isActive ? 'opacity-100' : 'opacity-0'}`}
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
