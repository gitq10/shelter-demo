import time, math, os, io
import streamlit as st
import pandas as pd
import pydeck as pdk

st.set_page_config(page_title="Nearest Safe Shelter — Vyshhorod", layout="wide")

# ----------------------------
# Defaults (Vyshhorod center)
# ----------------------------
HOME_LAT, HOME_LON = 50.583, 30.486   # Vyshhorod
TICK_SECONDS = 1.0                     # refresh cadence

# ALERT/SAFE pattern in seconds (loops forever)
PATTERN = [("ALERT", 120), ("SAFE", 60), ("ALERT", 45), ("SAFE", 90)]

# Built-in demo shelters near Vyshhorod (edit freely).
# You can also upload a CSV (name,lat,lon[,type,capacity])
DEFAULT_SHELTERS = [
    {"name":"Community Shelter Alpha","lat":50.593,"lon":30.501},
    {"name":"School Shelter Beta","lat":50.590,"lon":30.472},
    {"name":"Clinic Shelter Gamma","lat":50.576,"lon":30.498},
    {"name":"Cultural Center Basement","lat":50.569,"lon":30.480},
    {"name":"Industrial Shelter East","lat":50.589,"lon":30.515},
    {"name":"Parking Level -2 (Public)","lat":50.585,"lon":30.463},
    {"name":"Gym Shelter Delta","lat":50.572,"lon":30.450},
    {"name":"Warehouse Underground","lat":50.600,"lon":30.490},
]

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
    return "SAFE", 0, PATTERN[1][1]

def load_shelters_from_csv(file) -> pd.DataFrame:
    df = pd.read_csv(file)
    if not {"name","lat","lon"}.issubset(df.columns):
        raise ValueError("CSV must have columns: name, lat, lon (optionally: type, capacity)")
    return df[["name","lat","lon"] + [c for c in ["type","capacity"] if c in df.columns]].copy()

# ----------------------------
# Session state
# ----------------------------
ss = st.session_state
if "running" not in ss: ss.running = True
if "tick" not in ss:    ss.tick = 0
if "home_lat" not in ss: ss.home_lat = HOME_LAT
if "home_lon" not in ss: ss.home_lon = HOME_LON
if "shelters_df" not in ss: ss.shelters_df = pd.DataFrame(DEFAULT_SHELTERS)

# ----------------------------
# Sidebar
# ----------------------------
st.sidebar.title("Vyshhorod Shelter Controls")

with st.sidebar.expander("Your location (Vyshhorod by default)", expanded=True):
    col_l1, col_l2 = st.columns(2)
    ss.home_lat = col_l1.number_input("Latitude", value=float(ss.home_lat), step=0.0005, format="%.6f")
    ss.home_lon = col_l2.number_input("Longitude", value=float(ss.home_lon), step=0.0005, format="%.6f")
    if st.button("Reset to Vyshhorod"):
        ss.home_lat, ss.home_lon = HOME_LAT, HOME_LON

with st.sidebar.expander("Shelters data", expanded=True):
    st.write("Use the built-in list or upload your own CSV with **name,lat,lon[,type,capacity]**.")
    up = st.file_uploader("Upload shelters.csv", type=["csv"])
    if up is not None:
        try:
            ss.shelters_df = load_shelters_from_csv(up)
            st.success(f"Loaded {len(ss.shelters_df)} shelters from CSV.")
        except Exception as e:
            st.error(f"CSV error: {e}")
    st.caption("Built-in demo list is used if no CSV is uploaded.")

radius_km = st.sidebar.slider("Focus radius (km)", 1, 10, 3, step=1)

c1, c2, c3, c4 = st.sidebar.columns(4)
if c1.button("▶ Start"): ss.running = True
if c2.button("⏸ Stop"):  ss.running = False
if c3.button("↺ Reset"):
    ss.running = False
    ss.tick = 0
if c4.button("🔥 Inject Alert"):
    # Align to the beginning of an ALERT phase (PATTERN starts with ALERT)
    # This keeps it simple; you could also change PATTERN dynamically.
    pass

ff1, ff2, ff3 = st.sidebar.columns(3)
if ff1.button("+30s"): ss.tick += 30
if ff2.button("+2m"):  ss.tick += 120
if ff3.button("+5m"):  ss.tick += 300

# ----------------------------
# Compute state & nearest shelters
# ----------------------------
state, elapsed, remain = state_at(ss.tick)
is_alert = (state == "ALERT")

home_lat, home_lon = ss.home_lat, ss.home_lon

df_s = ss.shelters_df.copy()
df_s["dist_km"] = df_s.apply(lambda r: haversine_km(home_lat, home_lon, r["lat"], r["lon"]), axis=1)
df_s["eta_min"] = (df_s["dist_km"] * 12).clip(lower=1).round().astype(int)  # walk ~5 km/h -> ~12 min/km

# Filter to focus radius
df_focus = df_s[df_s["dist_km"] <= radius_km].sort_values("dist_km")
top2 = df_focus.head(2) if not df_focus.empty else df_s.nsmallest(2, "dist_km")

# ----------------------------
# UI — Status + guidance
# ----------------------------
colA, colB = st.columns([1,3])

with colA:
    st.markdown("### Status (Vyshhorod)")
    if is_alert:
        st.markdown(
            f"<div style='padding:12px;border-radius:12px;background:#ffeded;color:#b00020;font-weight:700;'>"
            f"🚨 ALERT — Go to shelter now<br/>Time remaining: {remain}s</div>",
            unsafe_allow_html=True)
    else:
        st.markdown(
            f"<div style='padding:12px;border-radius:12px;background:#e7f7ee;color:#0b7a3b;font-weight:700;'>"
            f"✅ SAFE — Stay ready<br/>Next change in ~{remain}s</div>",
            unsafe_allow_html=True)

    st.write(f"Tick: {ss.tick}s • State elapsed: {elapsed}s")
    st.write(f"Focus radius: **{radius_km} km** • Your location: ({home_lat:.5f}, {home_lon:.5f})")

    st.markdown("#### Nearest shelters")
    if top2.empty:
        st.warning("No shelters within radius. Increase the radius or upload a CSV.")
    else:
        for _, srow in top2.iterrows():
            line = f"**{srow['name']}** — {srow['dist_km']:.2f} km • ~{srow['eta_min']} min walk"
            if "type" in srow and not pd.isna(srow["type"]):
                line += f" • {srow['type']}"
            st.write(line)

    st.markdown("#### What to do")
    if is_alert:
        st.write("- Move now to the **closest shelter** shown above.")
        st.write("- If outside: get **underground** or behind **two walls** away from windows.")
        st.write
