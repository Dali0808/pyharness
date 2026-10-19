import type { CSSProperties } from 'react';

const paths = {
  plus: 'M12 5v14M5 12h14',
  chat: 'M21 11.5a8.4 8.4 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.4 8.4 0 0 1-3.8-.9L3 21l1.9-5.7a8.4 8.4 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.4 8.4 0 0 1 3.8-.9h.5a8.5 8.5 0 0 1 8 8z',
  folder: 'M3 7a2 2 0 0 1 2-2h5l2 2h7a2 2 0 0 1 2 2v10H3z',
  chevron: 'm9 5 7 7-7 7',
  down: 'm6 9 6 6 6-6',
  search: 'm21 21-5-5M18 10a8 8 0 1 1-16 0 8 8 0 0 1 16 0',
  settings: 'M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8M9 3l-1 3-3 1-2 3 2 2-1 3 3 3 3-1 2 2 3-2 3 1 2-3-1-3 2-2-2-3-3-1-1-3z',
  panel: 'M14 4v16M4 4h16v16H4z',
  file: 'M14 3H5v18h14V8zm0 0v5h5M8 12h8M8 16h6',
  terminal: 'm4 6 5 5-5 5m8 1h8',
  check: 'm5 12 4 4L19 6',
  arrow: 'M12 19V5m-6 6 6-6 6 6',
  stop: 'M6 6h12v12H6z',
  close: 'm6 6 12 12M6 18 18 6',
  shield: 'm12 3 8 4v6c0 4-8 8-8 8s-8-4-8-8V7zm-4 9 3 3 5-6',
  branch: 'M6 6v12m0-6c10 0 12-2 12-6M8 4a2 2 0 1 1-4 0 2 2 0 0 1 4 0m12 0a2 2 0 1 1-4 0 2 2 0 0 1 4 0M8 20a2 2 0 1 1-4 0 2 2 0 0 1 4 0',
  clock: 'M12 7v5l3 2M22 12a10 10 0 1 1-20 0 10 10 0 0 1 20 0',
  external: 'M14 3h7v7m0-7L10 14M10 3H3v18h18v-7',
  spark: 'm12 3 2.5 6.5L21 12l-6.5 2.5L12 21l-2.5-6.5L3 12l6.5-2.5z',
};

export function Icon({ name, size = 18, style }: { name: keyof typeof paths; size?: number; style?: CSSProperties }) {
  return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.65" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" style={style}><path d={paths[name]} /></svg>;
}

export function Brand({ small = false }: { small?: boolean }) {
  return <span className={`brand-mark${small ? ' small' : ''}`} aria-hidden="true"><svg viewBox="0 0 52 52" fill="none"><g stroke="currentColor" strokeWidth="1.4" strokeLinecap="round">{Array.from({ length: 24 }, (_, index) => {
    const angle = -Math.PI / 2 + index * Math.PI / 12;
    return <line key={index} x1={26 + Math.cos(angle) * 10.4} y1={26 + Math.sin(angle) * 10.4} x2={26 + Math.cos(angle) * 22.6} y2={26 + Math.sin(angle) * 22.6} />;
  })}</g><circle cx="26" cy="26" r="7.4" fill="currentColor" /></svg></span>;
}
