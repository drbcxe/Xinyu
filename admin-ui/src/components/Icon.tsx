import type { CSSProperties } from 'react'

const paths = {
  chat: <><path d="M20 11a8 8 0 0 1-8 8H5l-3 3V11a9 9 0 1 1 18 0Z" /><path d="M7 9h8M7 13h5" /></>,
  heart: <path d="M20.8 4.6a5.5 5.5 0 0 0-7.8 0L12 5.7l-1.1-1.1a5.5 5.5 0 0 0-7.8 7.8L12 21l8.8-8.6a5.5 5.5 0 0 0 0-7.8Z" />,
  calendar: <><rect x="3" y="5" width="18" height="16" rx="3" /><path d="M7 3v4M17 3v4M3 11h18M8 15h3M8 18h6" /></>,
  bell: <><path d="M18 8a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9M10 21h4" /></>,
  upload: <><path d="M12 16V3m-4 4 4-4 4 4M4 15v5a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-5" /></>,
  send: <><path d="M12 19V5m-6 6 6-6 6 6" /></>,
  user: <><circle cx="12" cy="8" r="4" /><path d="M4 21v-2a8 8 0 0 1 16 0v2" /></>,
  grid: <><rect x="3" y="3" width="7" height="7" rx="2" /><rect x="14" y="3" width="7" height="7" rx="2" /><rect x="3" y="14" width="7" height="7" rx="2" /><rect x="14" y="14" width="7" height="7" rx="2" /></>,
  plus: <path d="M12 5v14M5 12h14" />,
  arrow: <path d="M5 12h14m-6-6 6 6-6 6" />,
  chevron: <path d="m9 5 7 7-7 7" />,
  spark: <><path d="m12 3 2.5 6.5L21 12l-6.5 2.5L12 21l-2.5-6.5L3 12l6.5-2.5Z" /><path d="m20 2 .5 1.5L22 4l-1.5.5L20 6l-.5-1.5L18 4l1.5-.5Z" /></>,
  folder: <><path d="M3 8V6a2 2 0 0 1 2-2h4l3 3h7a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2Z" /><path d="M3 10h18" /></>,
  file: <><path d="M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9Z" /><path d="M14 3v6h6M8 13h8M8 17h5" /></>,
  chart: <><path d="M4 4v16h16M8 15v-4M12 15V7M16 15v-6" /></>,
  memory: <><rect x="5" y="5" width="14" height="14" rx="4" /><path d="M9 2v3M15 2v3M9 19v3M15 19v3M2 9h3M2 15h3M19 9h3M19 15h3M10 10h4v4h-4z" /></>,
  settings: <><path d="M4 7h16M4 17h16" /><circle cx="9" cy="7" r="3" /><circle cx="15" cy="17" r="3" /></>,
  search: <><circle cx="10.5" cy="10.5" r="6.5" /><path d="m16 16 4.5 4.5" /></>,
  refresh: <><path d="M20 7v5h-5M4 17v-5h5M6.1 7a7 7 0 0 1 11.5-1L20 9M4 15l2.4 3A7 7 0 0 0 17.9 17" /></>,
  shield: <><path d="m12 3 8 3v6c0 5-8 9-8 9s-8-4-8-9V6Z" /><path d="m8.5 12 2.5 2.5 4.5-5" /></>,
  command: <><path d="M8 8h8v8H8zM8 8H5a3 3 0 1 1 3-3v3Zm8 0V5a3 3 0 1 1 3 3h-3ZM8 16v3a3 3 0 1 1-3-3h3Zm8 0h3a3 3 0 1 1-3 3v-3Z" /></>,
}

export type IconName = keyof typeof paths

export default function Icon({ name, className, style }: { name: IconName; className?: string; style?: CSSProperties }) {
  return <svg className={className} style={style} viewBox="0 0 24 24" aria-hidden="true">{paths[name]}</svg>
}
