// V.A.J.R.A. Runtime Configuration
// Automatically detects local environment vs production (Vercel / Cloud)
const isLocal = typeof window !== "undefined" && 
  (window.location.hostname === "localhost" || window.location.hostname === "127.0.0.1") &&
  window.location.port !== "3000";

window.APP_CONFIG = {
  // On localhost, default to local FastAPI dev server on :8000.
  // In production (Vercel), empty string routes directly to the serverless /api endpoints on the same domain.
  API_BASE: isLocal ? "http://localhost:8000" : "",

  // Default basemap: free OpenStreetMap (no API key required)
  TILE_URL: "https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png",
  TILE_ATTRIBUTION: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
};
