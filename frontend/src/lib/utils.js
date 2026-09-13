import { clsx } from 'clsx';
import { twMerge } from 'tailwind-merge';

// Standard shadcn/ui helper -- every generated component imports this from
// @/lib/utils to merge conditional classNames without Tailwind class
// conflicts (e.g. two different `px-*` utilities colliding).
export function cn(...inputs) {
  return twMerge(clsx(inputs));
}
