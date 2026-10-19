/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        temple: {
          bg: '#0d0d12',
          surface: '#16161d',
          border: '#2a2a3a',
          primary: '#6d5aff',
          primaryHover: '#8475ff',
          accent: '#00d4aa',
          danger: '#ff4757',
          warning: '#ffa502',
          success: '#2ed573',
          text: '#e8e8ed',
          textMuted: '#888899',
        },
      },
      fontFamily: {
        sans: ['Inter', 'system-ui', 'sans-serif'],
        mono: ['JetBrains Mono', 'monospace'],
      },
      animation: {
        'pulse-slow': 'pulse 3s cubic-bezier(0.4, 0, 0.6, 1) infinite',
        'spin-slow': 'spin 2s linear infinite',
      },
    },
  },
  plugins: [],
}