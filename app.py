import time, math, os
import streamlit as st
import pydeck as pdk

st.set_page_config(page_title="Nearest Safe Shelter (Demo)", layout="wide")

# ----------------------------
# Grandma settings (edit these)
# ----------------------------
# Default "home" (central Kyiv). Change for other city.
HOME_LAT, HOME_LON = 50.4501, 30.5234

# Demo shelters (you can edit/add rows here)
SHELTERS = [
    {"name":"Khreshchatyk Metro","lat":50.4479,"lon":30.5227},
    {"name":"Teatralna Metro","lat":50.4443,"lon":30.5180},
    {"name":"Zoloti Vorota Metro","lat":50.4472,"lon":30.5157},
    {"name":"Maidan Nezalezhnosti","lat":50.4500,"lon":30.5233},
    {"name":"Arsenalna Metro","lat":50.4415,"lon":30.5539},
    {"name":"Universytet Metro","lat":50.4432,"lon":30.5050},
]

# Demo timeline pattern (seconds): ALERT/SAFE cycles to look alive
PATTERN = [("ALERT",120), ("SAFE",60), ("ALERT",45), ("SAFE",90)]  # loops forever
TICK_SECONDS = 1.0  # refresh cadence

# ----------------------------
# Helpers
# ----------------------------
def haversine_km(lat1, lon1, lat2, lon2):
    R = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi/2)**2 + math.cos(p1)*math.cos(p2)*math.sin(dlmb/2)**2
    return 2*R*math.asin(math.sqrt(a))

def pattern_length():
    return sum(d for _, d in PATTERN)

def state_at(t):
    """Return (state, elapsed_in_state, remaining_in_state) for t seconds into the loop."""
    t_mod = t % pattern_length()
    cum = 0
    for state, dur in PATTERN:
        if t_mod < cum + dur:
            elapsed = t_mod - cum
            remain = dur - elapsed
            return state, int(elapsed), int(remain)
        cum += dur
    return "SAFE", 0, PATTERN[1][1]  # fallback

# ----------------------------
# Session
# ----------------------------
if "running" not in st.session_state:
    st.session_state.running = True
if "tick" not in st.session_state:
    st.session_state.tick = 0
if "home_lat" not in st.session_state:
    st.session_state.home_lat = HOME_LAT
if "home_lon" not in st.session_state:
    st.session_state.home_lon = HOME_LON

# ----------------------------
# Sidebar controls
# ----------------------------
st.sidebar.title("Controls")
st.sidebar.write("**Mode:** synthetic timeline (1s ticks). Swap to real feeds later.")

# Home picker
st.sidebar.markdown("### Your location")
st.session_state.home_lat = st.sidebar.number_input("Latitude", value=float(st.session_state.home_lat), step=0.0001, format="%.6f")
st.session_state.home_lon = st.sidebar.number_input("Longitude", value=float(st.session_state.home_lon), step=0.0001, format="%.6f")

c1, c2, c3, c4 = st.sidebar.columns(4)
if c1.button("▶ Start"): st.session_state.running = True
if c2.button("⏸ Stop"):  st.session_state.running = False
if c3.button("↺ Reset"): 
    st.session_state.running = False
    st.session_state.tick = 0
if c4.button("🔥 Inject Alert"):
    # Put the timeline at the start of an ALERT segment for drama
    # (we align tick to the beginning of PATTERN[0], which is ALERT)
    pass  # the pattern already starts with ALERT; you can add custom logic if needed

ff1, ff2, ff3 = st.sidebar.columns(3)
if ff1.button("+30s"): st.session_state.tick += 30
if ff2.button("+2m"):  st.session_state.tick += 120
if ff3.button("+5m"):  st.session_state.tick += 300

# ----------------------------
# Compute current state
# ----------------------------
state, elapsed, remain = state_at(st.session_state.tick)
is_alert = (state == "ALERT")

# Rank shelters by distance
home_lat, home_lon = st.session_state.home_lat, st.session_state.home_lon
scored = []
for s in SHELTERS:
    d = haversine_km(home_lat, home_lon, s["lat"], s["lon"])
    scored.append({**s, "dist_km": d, "eta_min": max(1, int(d*12))})  # walk ~5 km/h -> 12 min per km

scored.sort(key=lambda x: x["dist_km"])
top2 = scored[:2]

# ----------------------------
# Header status
# ----------------------------
colA, colB = st.columns([1,3])

with colA:
    st.markdown("### Status")
    if is_alert:
        st.markdown(
            f"<div style='padding:12px;border-radius:12px;background:#ffeded;color:#b00020;font-weight:700;'>"
            f"🚨 ALERT — Go to shelter now<br/>Time remaining: {remain}s</div>", unsafe_allow_html=True)
    else:
        st.markdown(
            f"<div style='padding:12px;border-radius:12px;background:#e7f7ee;color:#0b7a3b;font-weight:700;'>"
            f"✅ SAFE — Stay ready<br/>Next change in ~{remain}s</div>", unsafe_allow_html=True)

    st.write(f"Tick: {st.session_state.tick}s • State elapsed: {elapsed}s")

    st.markdown("#### Nearest shelters")
    for s in top2:
        if is_alert:
            st.write(f"**{s['name']}** — {s['dist_km']:.2f} km • ~{s['eta_min']} min walk")
        else:
            st.write(f"{s['name']} — {s['dist_km']:.2f} km • ~{s['eta_min']} min")

    st.markdown("#### What to do")
    if is_alert:
        st.write("- Move now to the **closest shelter** shown above.")
        st.write("- If outside: get **underground** or behind **two walls** away from windows.")
        st.write("- Bring essentials (ID, phone, power bank).")
    else:
        st.write("- Keep devices charged, shoes ready, and know your route.")
        st.write("- Practice the walk to your primary & backup shelter.")

# ----------------------------
# Map
# ----------------------------
# Build map layers: home + shelters
home_layer = pdk.Layer(
    "ScatterplotLayer",
    data=[{"name":"You","lat":home_lat,"lon":home_lon}],
    get_position='[lon, lat]',
    get_radius=60,
    get_fill_color='[200, 30, 30]' if is_alert else '[30, 160, 60]',
    pickable=True
)

shelter_layer = pdk.Layer(
    "ScatterplotLayer",
    data=SHELTERS,
    get_position='[lon, lat]',
    get_radius=50,
    get_fill_color='[30, 120, 200]',
    pickable=True
)

# Optional: highlight lines to top2 shelters
paths = []
for s in top2:
    paths.append({"path":[[home_lon, home_lat],[s["lon"], s["lat"]]], "name":f"→ {s['name']}"})
path_layer = pdk.Layer(
    "PathLayer",
    data=paths,
    get_path="path",
    width_scale=2,
    get_width=5,
    get_color=[255, 140, 0] if is_alert else [120,120,120],
    pickable=True
)

deck = pdk.Deck(
    initial_view_state=pdk.ViewState(latitude=home_lat, longitude=home_lon, zoom=13),
    layers=[home_layer, shelter_layer, path_layer],
    tooltip={"text": "{name}"}
)
with colB:
    st.pydeck_chart(deck)
    st.caption("Blue = shelters, Green/Red = your location, Orange lines = nearest routes")

# ----------------------------
# Auto-tick & rerun
# ----------------------------
if st.session_state.running:
    time.sleep(TICK_SECONDS)
    st.session_state.tick += 1
    st.rerun()
