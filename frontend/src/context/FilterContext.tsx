import React, { createContext, useContext, useMemo, useState } from 'react';

export type DecisionFilter = 'All' | 'recommended' | 'deferred';

interface FilterContextType {
  borough: string;
  setBorough: (b: string) => void;
  priorityTier: string;
  setPriorityTier: (t: string) => void;
  decision: DecisionFilter;
  setDecision: (d: DecisionFilter) => void;
  resetFilters: () => void;
}

const FilterContext = createContext<FilterContextType | undefined>(undefined);

export const FilterProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [borough, setBorough] = useState<string>('All');
  const [priorityTier, setPriorityTier] = useState<string>('All');
  const [decision, setDecision] = useState<DecisionFilter>('All');

  const value = useMemo(
    () => ({
      borough,
      setBorough,
      priorityTier,
      setPriorityTier,
      decision,
      setDecision,
      resetFilters: () => {
        setBorough('All');
        setPriorityTier('All');
        setDecision('All');
      },
    }),
    [borough, priorityTier, decision]
  );

  return <FilterContext.Provider value={value}>{children}</FilterContext.Provider>;
};

export function useGlobalFilters() {
  const context = useContext(FilterContext);
  if (!context) throw new Error('useGlobalFilters must be used within a FilterProvider');
  return context;
}
