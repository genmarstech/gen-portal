"use client";

import { useEffect, useState } from "react";
import { applyTheme, readStoredTheme, storeTheme, type Theme } from "./theme";
import styles from "./ThemeToggle.module.css";

const OPTIONS: { value: Theme; label: string; title: string }[] = [
  { value: "light", label: "Light", title: "Light" },
  { value: "system", label: "Auto", title: "Match system" },
  { value: "dark", label: "Dark", title: "Dark" },
];

/**
 * Three-way theme control: light / auto / dark.
 *
 * "Auto" is a real option rather than an implementation detail. A two-way toggle
 * silently overrides the OS preference the moment it is touched, and there is
 * then no way back to following the system — which is the setting most people
 * actually want.
 *
 * Renders a disabled placeholder until mounted. The server cannot know the
 * stored choice, so rendering the real state immediately would hydrate a control
 * showing the wrong selection.
 */
export function ThemeToggle() {
  const [theme, setTheme] = useState<Theme>("system");
  const [mounted, setMounted] = useState(false);

  useEffect(() => {
    setTheme(readStoredTheme());
    setMounted(true);
  }, []);

  function choose(next: Theme) {
    setTheme(next);
    storeTheme(next);
    applyTheme(next);
  }

  return (
    <div
      className={styles.group}
      role="radiogroup"
      aria-label="Colour theme"
      data-no-print
    >
      {OPTIONS.map((option) => {
        const active = mounted && theme === option.value;
        return (
          <button
            key={option.value}
            type="button"
            role="radio"
            aria-checked={active}
            title={option.title}
            className={`${styles.option} ${active ? styles.active : ""}`}
            onClick={() => choose(option.value)}
          >
            <Icon theme={option.value} />
            <span className="visually-hidden">{option.label}</span>
          </button>
        );
      })}
    </div>
  );
}

function Icon({ theme }: { theme: Theme }) {
  const common = {
    width: 15,
    height: 15,
    viewBox: "0 0 24 24",
    fill: "none",
    stroke: "currentColor",
    strokeWidth: 1.6,
    strokeLinecap: "round" as const,
    strokeLinejoin: "round" as const,
    "aria-hidden": true,
    focusable: "false" as const,
  };

  if (theme === "light") {
    // A sun, drawn as the orbit mark's sibling: a body with rays.
    return (
      <svg {...common}>
        <circle cx="12" cy="12" r="4.2" />
        <path d="M12 2.2v2.2M12 19.6v2.2M2.2 12h2.2M19.6 12h2.2M4.9 4.9l1.6 1.6M17.5 17.5l1.6 1.6M19.1 4.9l-1.6 1.6M6.5 17.5l-1.6 1.6" />
      </svg>
    );
  }

  if (theme === "dark") {
    return (
      <svg {...common}>
        <path d="M20.5 14.6A8.6 8.6 0 1 1 9.4 3.5a6.9 6.9 0 0 0 11.1 11.1Z" />
      </svg>
    );
  }

  // Auto — a planet with a trajectory, echoing the Orbit G.
  return (
    <svg {...common}>
      <circle cx="12" cy="12" r="4.6" />
      <ellipse cx="12" cy="12" rx="10.4" ry="4.1" transform="rotate(-30 12 12)" />
    </svg>
  );
}
