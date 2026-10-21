import type { Config } from "tailwindcss";

const config: Config = {
  darkMode: "class",
  content: [
    "./src/pages/**/*.{js,ts,jsx,tsx,mdx}",
    "./src/components/**/*.{js,ts,jsx,tsx,mdx}",
    "./src/app/**/*.{js,ts,jsx,tsx,mdx}",
  ],
  theme: {
    extend: {
      colors: {
        page: "var(--page-bg)",
        card: {
          DEFAULT: "var(--card-bg)",
          inner: "var(--inner-card-bg)",
          hover: "var(--card-hover)",
        },
        border: "var(--border-color)",
        secondary: "var(--secondary-text)",
        muted: "var(--muted-text)",
        foreground: {
          DEFAULT: "var(--text-color)",
          secondary: "var(--secondary-text)",
          muted: "var(--muted-text)",
        },
        accent: {
          DEFAULT: "var(--accent-color)",
          fg: "var(--accent-text)",
          bg: "var(--accent-bg)",
          hover: "var(--accent-hover)",
          sky: "var(--accent-sky)",
          soft: "var(--accent-soft)",
          pale: "var(--accent-pale)",
          deep: "var(--accent-deep)",
        },
        sky: {
          DEFAULT: "var(--accent-sky)",
          soft: "var(--accent-soft)",
          pale: "var(--accent-pale)",
          med: "var(--accent-color)",
          deep: "var(--accent-deep)",
        },
        pink: {
          hover: "var(--pink-hover)",
          blush: "var(--pink-blush)",
          border: "var(--pink-border)",
          accent: "var(--pink-accent)",
          text: "var(--pink-text)",
          deep: "var(--pink-deep)",
        },
        rose: {
          blush: "var(--rose-blush)",
          med: "var(--rose-med)",
          deep: "var(--rose-deep)",
        },
        status: {
          success: {
            DEFAULT: "var(--success-color)",
            bg: "var(--success-bg)",
            border: "var(--success-border)",
          },
          warning: {
            DEFAULT: "var(--warning-color)",
            bg: "var(--warning-bg)",
            border: "var(--warning-border)",
          },
          danger: {
            DEFAULT: "var(--danger-color)",
            bg: "var(--danger-bg)",
            border: "var(--danger-border)",
          },
          info: {
            DEFAULT: "var(--info-color)",
            bg: "var(--info-bg)",
            border: "var(--info-border)",
          },
        },
      },
      boxShadow: {
        xs: "0 1px 2px 0 rgba(16, 24, 40, 0.04)",
        sm: "0 1px 3px 0 rgba(16, 24, 40, 0.06), 0 1px 2px -1px rgba(16, 24, 40, 0.03)",
        card: "0 1px 3px 0 rgba(16, 24, 40, 0.04), 0 1px 2px 0 rgba(16, 24, 40, 0.02)",
        "card-hover": "0 6px 16px -2px rgba(217, 137, 161, 0.12), 0 2px 6px -1px rgba(217, 137, 161, 0.06)",
      },
      borderRadius: {
        card: "12px",
        btn: "8px",
      },
      transitionDuration: {
        DEFAULT: "180ms",
        180: "180ms",
        200: "200ms",
      },
      transitionTimingFunction: {
        DEFAULT: "ease-out",
      },
    },
  },
  plugins: [],
};
export default config;

