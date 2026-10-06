import React, { createContext, useContext } from 'react';
import { createPortal } from 'react-dom';

/** The header exposes a slot; each page portals only the controls relevant to it. */
export const HeaderSlotContext = createContext<HTMLElement | null>(null);

export const HeaderControls: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const slot = useContext(HeaderSlotContext);
  return slot ? createPortal(children, slot) : null;
};
