export type NavigationIconName = "chat" | "courses" | "materials" | "notes" | "quiz" | "report";

/** Navigation artwork traced from the approved reference, using the menu's color. */
export default function NavigationIcon({ name }: { name: NavigationIconName }) {
  return <svg className="navigation-icon" viewBox="0 0 32 32" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false">
    {name === "chat" && <>
      <path d="M7.5 24.5C4.6 22.4 3 19.3 3 15.5V13C3 6.8 7.4 3 13 3h6c5.8 0 10 4.3 10 10v3c0 5.8-4.2 9.5-10 9.5h-4.5L7.5 30Z" />
      <g fill="currentColor" stroke="none"><circle cx="10" cy="14.5" r="1.6" /><circle cx="16" cy="14.5" r="1.6" /><circle cx="22" cy="14.5" r="1.6" /></g>
    </>}
    {name === "courses" && <>
      <path d="M16 6.5C12.5 3.7 8.5 3 3 4.5v23c5.5-1.5 9.5-.8 13 2 3.5-2.8 7.5-3.5 13-2v-23c-5.5-1.5-9.5-.8-13 2Z" />
      <path d="M16 6.5v23" />
    </>}
    {name === "materials" && <>
      <path d="M3 13V7.5A3.5 3.5 0 0 1 6.5 4h6l4 4h9A3.5 3.5 0 0 1 29 11.5V14" />
      <path d="M6.5 28a3 3 0 0 1-3-2.6L2.2 15a2 2 0 0 1 2-2.2h23.6a2 2 0 0 1 2 2.2l-1.3 10.4a3 3 0 0 1-3 2.6Z" />
    </>}
    {name === "notes" && <>
      <path d="M19 29H7a4 4 0 0 1-4-4V7a4 4 0 0 1 4-4h17a4 4 0 0 1 4 4v13Z" />
      <path d="M19 29v-6a3 3 0 0 1 3-3h6M9 10h12M9 16h12M9 22h5" />
    </>}
    {name === "quiz" && <>
      <path d="m4 25 2-8L22 2a2 2 0 0 1 2.8 0l3.2 3.2a2 2 0 0 1 0 2.8L12 23Z" />
      <path d="m19 5 6 6M6 17l6 6M4 25l4-1M3 30h24" />
    </>}
    {name === "report" && <>
      <rect x="4" y="16" width="4" height="10" rx=".8" /><rect x="13" y="9" width="4" height="17" rx=".8" /><rect x="22" y="2" width="4" height="24" rx=".8" />
      <path d="M2 30h26" />
    </>}
  </svg>;
}
