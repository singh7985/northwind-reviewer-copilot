import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        compliant: "#16a34a",
        flagged: "#d97706",
        rejected: "#dc2626",
        review: "#2563eb",
      },
    },
  },
  plugins: [],
};
export default config;
