"""TDF-Tracker Echtzeit-Paket.

Module:
  config      – Konstanten (URLs, Header, Bind-Namen, Codes)
  static      – Statische Daten (Rider/Teams/Etappen) via REST
  bootstrap   – REST-Bootstrap der aktuellen GC / Trikots / Telemetrie
  state       – State-Engine, Dispatcher (Fix), Hybrid-GC
  extrapolate – Haversine, Destination-Point, Dead-Reckon, Lerp
  profile     – Best-Effort-Höhenprofil aus /profils/-CSV
  server      – aiohttp: SSE-Consumer + HTTP /state + WS /ws
  prediction  – Restzeit-Vorhersage (Feature 1)
  gap_chart   – Gap-Chart-Zeitreihen (Feature 2)
  classification – Berg/Sprint-Klassifikation (Feature 3)
  alarms      – Ereignis-Alarme (Feature 4)
  map_data    – Karten-Koordinaten (Feature 5)
"""

__version__ = "3.1.0"
