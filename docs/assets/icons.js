/* Minimal inline-SVG line icons, in the spirit of the HPE Design System's icon
 * guidance (single color via currentColor, always paired with a text label, decorative
 * instances marked aria-hidden). Not the actual @hpe-design/icons-grommet package --
 * that needs npm/build tooling this static site intentionally avoids. */
(function () {
  "use strict";

  const PATHS = {
    search: '<circle cx="9" cy="9" r="6"/><line x1="17" y1="17" x2="13.2" y2="13.2"/>',
    filter: '<path d="M3 5h14M6 10h8M9 15h2"/>',
    download: '<path d="M10 3v10M6 9l4 4 4-4M4 16h12"/>',
    upload: '<path d="M10 17V7M6 11l4-4 4 4M4 16h12"/>',
    info: '<circle cx="10" cy="10" r="7.5"/><line x1="10" y1="9" x2="10" y2="14"/><circle cx="10" cy="6.3" r="0.9" fill="currentColor" stroke="none"/>',
    warning: '<path d="M10 3.5 17.5 16h-15L10 3.5Z"/><line x1="10" y1="8.5" x2="10" y2="12"/><circle cx="10" cy="14.3" r="0.8" fill="currentColor" stroke="none"/>',
    check: '<path d="M4 10.5 8 14.5 16 5.5"/>',
    close: '<path d="M5 5l10 10M15 5 5 15"/>',
    plug: '<path d="M7 3v4M13 3v4M5 7h10v3a5 5 0 0 1-10 0V7Z"/><path d="M10 15v2"/>',
    refresh: '<path d="M4 10a6 6 0 0 1 10.2-4.2M16 10a6 6 0 0 1-10.2 4.2"/><path d="M14 3v3h-3M6 17v-3h3"/>',
    revert: '<path d="M5 6h7a4 4 0 0 1 0 8H8"/><path d="M8 3 5 6l3 3"/>',
    site: '<path d="M4 17V8l6-4 6 4v9"/><path d="M8 17v-5h4v5"/>',
    server: '<rect x="4" y="4" width="12" height="5" rx="1"/><rect x="4" y="11" width="12" height="5" rx="1"/><circle cx="6.5" cy="6.5" r="0.6" fill="currentColor" stroke="none"/><circle cx="6.5" cy="13.5" r="0.6" fill="currentColor" stroke="none"/>',
  };

  function iconSVG(name, { size = 16, title } = {}) {
    const body = PATHS[name] || "";
    const a11y = title ? `role="img" aria-label="${title}"` : 'aria-hidden="true"';
    return `<svg class="icon" width="${size}" height="${size}" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" ${a11y}>${body}</svg>`;
  }

  window.Icons = { svg: iconSVG };
})();
