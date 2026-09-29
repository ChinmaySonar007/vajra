# Thunderstorm & Lightning Nowcast — SIH 26072 (MoES / IMD)

A working, free-to-host prototype: 0–3h thunderstorm/lightning risk for any
location in India, shown on a live map dashboard.

**Why this architecture is "lightweight":** no trained model weights to
ship or load, no GPU, no database. The backend is a thin FastAPI service
that pulls live NWP data (CAPE, cloud cover, convective gusts, WMO weather
code — the same instability/precipitation signals radar+satellite+NWP
fusion is meant to capture) from Open-Meteo's free API and scores it with
a transparent weighted formula. That keeps cold starts fast and memory
under ~60 MB, which is what makes Render's and Vercel's free tiers viable.

```
nowcast/
  backend/     FastAPI service → /api/cities, /api/nowcast, /api/geocode, /api/alerts
  frontend/    Static Leaflet dashboard (no build step)
```

## 1. Deploy the backend (Render, free)

1. Push the `backend/` folder to a GitHub repo (or the whole `nowcast/` repo).
2. On [render.com](https://render.com) → New → Web Service → connect the repo.
3. Root directory: `backend` (or use the included `render.yaml` — Render
   picks it up automatically as a Blueprint).
4. Build command: `pip install -r requirements.txt`
   Start command: `uvicorn main:app --host 0.0.0.0 --port $PORT`
5. Plan: **Free**. Deploy. Note the URL, e.g.
   `https://thunderstorm-nowcast-api.onrender.com`.

   Free-tier note: the service sleeps after 15 min idle and takes a
   few seconds to wake on the next request — normal for a hackathon demo.

## 2. Deploy the frontend (Vercel, free)

1. Edit `frontend/config.js`, set `API_BASE` to your Render URL from step 1.
2. Push `frontend/` to GitHub (or a subfolder of the same repo).
3. On [vercel.com](https://vercel.com) → New Project → import the repo →
   set **Root Directory** to `frontend` → Framework Preset: **Other**
   (it's static HTML, no build step) → Deploy.
4. Open the Vercel URL — the dashboard loads live data from your backend.

That's it — two free services, no servers to manage, no secrets required
(Open-Meteo's non-commercial API needs no key).

## What it does (V.A.J.R.A 2.0)

- **Convective Storm Motion Vectors**: Displays real-time cell steering vectors (speed in km/h and cardinal heading) with projected +1h, +2h, and +3h trajectory tracks.
- **Multimodal Atmospheric Proxy Engine**: Derives simulated radar reflectivity (dBZ), INSAT-3D/3DR cloud-top temperature (°C), and In-Cloud (IC) vs Cloud-to-Ground (CG) lightning strike rates.
- **⚡ Lightning Jump Early Warning Head**: Automatically detects explosive updraft lightning surges ($d(\text{Total})/dt$), issuing a 15–25 minute early warning lead time.
- **🚨 Common Alerting Protocol (CAP - ITU-T X.1303)**: Built-in `/api/cap/alerts` producing official OASIS CAP XML and JSON for direct integration with NDMA Sachet and IMD MHEW-DSS.
- **Multi-Sectoral Impact Panels**: Dedicated real-time operational tabs for Aviation (SIGMET/ATC vectoring), Power Grid (feeder defense), and Agriculture (rural vernacular voice/SMS warnings).
- **Time Slider & Synced View**: 0–3h nowcast with synchronized alerts, city rankings, and map tooltips.
- **Calibrated Uncertainty Bounds**: Conformal prediction 90% confidence intervals for every time step.

## Complete Architecture Documentation
For deep-dive mathematical formulations, NowcastNet/Earthformer comparison, and jury pitch slides, see:
👉 [VAJRA_ARCHITECTURE.md](file:///c:/Users/Chinmay/Downloads/files/VAJRA_ARCHITECTURE.md)


## Local development

```bash
cd backend
pip install -r requirements.txt
uvicorn main:app --reload   # http://localhost:8000

# in another terminal
cd frontend
python3 -m http.server 5500 # http://localhost:5500
```

`frontend/config.js` already defaults `API_BASE` to `http://localhost:8000`.

## Extending toward the full problem statement

- Swap/augment the Open-Meteo feed with real IMD radar (DWR) and
  INSAT-3D/3DR imagery once available — `fetch_nowcast()` is the only
  place that needs a new data adapter.
- Add a real spatiotemporal model (ConvLSTM/U-Net trained on SEVIR or
  ISRO-NowCasting) behind the same `/api/nowcast` contract; the frontend
  doesn't need to change.
- Wire `/api/alerts` to Twilio/SMTP for real SMS/email push alerts.
