/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  darkMode: 'class',
  theme: {
    extend: {
      colors: {
        fog: "#EAF0EF",
        paper: "#F8FAF9",
        lagoon: "#1E7F85",
        "deep-sea": "#1F4B8F",
        signal: "#F2A33A",
        ink: "#17242B",
        mist: "#6B7C84",
        // Dark theme tokens
        "dark-bg": "#0E1A21",
        "dark-card": "#15262F",
        "dark-text": "#E8F0F2",
        // Stamp rarity colors
        "rarity-common": "#7C97A0",
        "rarity-rare": "#1F4B8F",
        "rarity-epic": "#7B4FC2",
        "rarity-legendary": "#F2A33A",
      },
      fontFamily: {
        serif: ["Gloock", "Georgia", "serif"],
        sans: ["Figtree", "system-ui", "sans-serif"],
        hand: ["'Caveat Brush'", "cursive"],
      },
      borderRadius: {
        'card': '28px',
        'tile': '16px',
      },
      boxShadow: {
        'clay': '3px 4px 12px rgba(23, 36, 43, 0.06), -2px -2px 8px rgba(255, 255, 255, 0.8)',
        'clay-dark': '3px 4px 14px rgba(0, 0, 0, 0.4), -1px -1px 6px rgba(255, 255, 255, 0.05)',
      }
    },
  },
  plugins: [],
}
