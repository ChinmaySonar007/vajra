// Point this at your deployed Render backend, e.g.
// "https://thunderstorm-nowcast-api.onrender.com"
// Leave as-is for local testing against `uvicorn main:app --reload` on :8000.
window.APP_CONFIG = {
  API_BASE: "http://localhost:8000",

  // Default basemap: free OpenStreetMap (no API key required)
  TILE_URL: "https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png",
  TILE_ATTRIBUTION: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',

  // Optional: If you obtain a free CARTO API key from https://carto.com/basemaps/apikey,
  // you can uncomment and provide it below:
  // CARTO_RASTER_URL: "https://basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}.png?key=YOUR_CARTO_KEY",
};

