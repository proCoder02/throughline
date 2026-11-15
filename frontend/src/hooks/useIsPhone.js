import { useEffect, useState } from 'react';

// Matches theme.css's single responsive breakpoint (the whole app swaps to
// the phone layout below this width) and Composer.jsx's identical constant
// for the attach menu -- one number, so "phone" means the same viewport
// everywhere this app makes a phone-vs-desktop decision.
export const PHONE_BREAKPOINT_PX = 860;

/// Reactive (tracks window resize/rotation), unlike a one-off
/// window.innerWidth read -- needed anywhere the choice has to stay correct
/// for as long as the component stays mounted (e.g. App.jsx deciding
/// IconRail vs. the phone drawer shell), not just at the moment of a click.
export function useIsPhone() {
  const [isPhone, setIsPhone] = useState(() => window.innerWidth < PHONE_BREAKPOINT_PX);
  useEffect(() => {
    const mql = window.matchMedia(`(max-width: ${PHONE_BREAKPOINT_PX - 1}px)`);
    const onChange = () => setIsPhone(mql.matches);
    onChange();
    mql.addEventListener('change', onChange);
    return () => mql.removeEventListener('change', onChange);
  }, []);
  return isPhone;
}
