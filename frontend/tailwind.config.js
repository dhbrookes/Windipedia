/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,jsx,ts,tsx}'],
  theme: {
    extend: {
      colors: {
        // Dark ocean-themed palette to complement the Mapbox dark basemap
        panel: {
          bg: '#0f1923',
          border: '#1e2d3d',
          hover: '#1a2740',
        },
      },
    },
  },
  plugins: [],
}
