export type Theme = "light" | "dark" | "system";

export const THEME_KEY = "gm-theme";

/**
 * Applies a theme choice to the document.
 *
 * "system" removes the attribute entirely rather than writing a resolved value,
 * so the `prefers-color-scheme` media query in globals.css stays in charge and
 * the page follows the OS live — including when the user flips it mid-session.
 */
export function applyTheme(theme: Theme) {
  const root = document.documentElement;
  if (theme === "system") {
    root.removeAttribute("data-theme");
  } else {
    root.setAttribute("data-theme", theme);
  }
}

export function readStoredTheme(): Theme {
  try {
    const value = localStorage.getItem(THEME_KEY);
    if (value === "light" || value === "dark" || value === "system") {
      return value;
    }
  } catch {
    // Private mode, blocked site data, or a browser that throws on access.
    // Falling through to "system" is the correct answer in every one of those.
  }
  return "system";
}

export function storeTheme(theme: Theme) {
  try {
    localStorage.setItem(THEME_KEY, theme);
  } catch {
    // Non-fatal. The theme still applies for this page view.
  }
}

/**
 * Runs before first paint, inlined into the document.
 *
 * Without this the page renders in light, then corrects to dark a frame later —
 * the white flash every themed site has to solve. Kept tiny and dependency-free
 * because it is parsed and executed on the critical path.
 *
 * Wrapped in try/catch: if storage throws, we must still not break the document.
 */
export const NO_FLASH_SCRIPT = `(function(){try{var t=localStorage.getItem("${THEME_KEY}");if(t==="light"||t==="dark"){document.documentElement.setAttribute("data-theme",t)}}catch(e){}})();`;
