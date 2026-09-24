# Local Dashboard

Start it from the project root:

```bash
app/run_dashboard.sh            # macOS / Linux
app\run_dashboard.ps1           # Windows PowerShell
python3 -m housing_agent serve  # any platform (after `pip install -e .`)
```

Then open http://127.0.0.1:8765.

The dashboard reads and writes only the active profile folder (see the main README for how it is chosen). With no profile of your own it shows the read-only sample in `profile.example/`.

The map uses Leaflet with OpenStreetMap tiles, so it needs no API key, but it does need internet access to load the Leaflet assets and map tiles.
