import os, time, json, math
import pandas as pd
import numpy as np
import streamlit as st
import pydeck as pdk

st.set_page_config(page_title="Grid 50s Real-Time Demo — Vyshhorod Focus", layout="wide")

# ===== CONFIG =====
DATA_FILE = "synthetic_grid_outage_50sec_5s.csv"  # keep this exact filename
TICK_SECONDS = 1.0        # refresh every 1s
WINDOW_SECONDS = 120      # last 2 minutes in view (file is 50s, so it shows all)
# Vyshhorod center (approx)
FOCUS_LAT = 50.583
FOCUS_LON = 30.486

# ===== SESSION =====
ss = st.session_state
if "df" not in ss: ss.df = None
if "replay_index" not in ss: ss.replay_index = 0
if "running" not in ss: ss.running = True

# ===== LOAD DATA =====
def load_csv(path):
    df = pd.read_csv(path, encoding="utf-8-sig")
    expected = {"timestamp","asset_id","lat","lon",
                "outage_prob","damage_index","crew_eta_min","criticality","customer_impact"}
    missing = expected - set(df.columns)
    if missing:
        raise ValueError(f"CSV missing columns: {missing}")
    if df["timestamp"].dtype == object:
        df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce", utc=True)
    return df.sort_values(["timestamp","asset_id"]).reset_index(drop=True)

if ss.df is None:
    if not os.path.exists(DATA_FILE):
        st.error(f"Can't find {DATA_FILE} next to app.py")
        st.stop()
    ss.df = load_csv(DATA_FILE)

df_all = ss.df
assets_count = df_all["asset_id"].nunique()
total_rows = len(df_all)

# ===== HELPERS =====
def compute_composite(df):
    if df.empty:
        return df.assign(composite=np.nan)
    outage = df["outage_prob"]
    damage = df["damage_index"]
    crit   = df["criticality"]
    crew   = np.clip((df["crew_eta_min"]/180.0)*100.0, 0, 100)
    comp = 0.40*damage + 0.30*outage + 0.20*crit - 0.10*crew
    return df.assign(composite=np.clip(comp, 0, 100))

def now_timestamp():
    if ss.replay_index == 0:
        return df_all["timestamp"].min()
    i = min(ss.replay_index-1, total_rows-1)
    return df_all.iloc[i]["timestamp"]

def haversine_km(lat1, lon1, lat2, lon2):
    R = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi/2)**2 + math.cos(p1)*math.cos(p2)*math.sin(dlmb/2)**2
    return 2*R*math.asin(math.sqrt(a))

# ===== SIDEBAR =====
st.sidebar.title("Controls (Vyshhorod focus)")

with st.sidebar.expander("File Debug", expanded=False):
    st.write("**Working directory:**", os.getcwd())
    st.write("**Files here:**", os.listdir(".")[:50])
    if os.path.exists(DATA_FILE):
        st.success(f"{DATA_FILE} exists ✓ size: {os.path.getsize(DATA_FILE):,} bytes")
    else:
        st.error(f"{DATA_FILE} NOT FOUND")

# Focus options
focus_on = st.sidebar.checkbox("Focus on Vyshhorod area", value=True)
radius_km = st.sidebar.slider("Focus radius (km)", 5, 50, 25, step=5)

# Playback
c1, c2, c3 = st.sidebar.columns(3)
if c1.button("▶ Start"): ss.running = True
if c2.button("⏸ Stop"):  ss.running = False
if c3.button("↺ Reset"):
    ss.running = False
    ss.replay_index = 0

# Fast-Forward: one “slice” = one timestamp across all assets (~5s)
ff1, ff2, ff3 = st.sidebar.columns(3)
def fast_forward_slices(slices:int):
    ss.replay_index = min(ss.replay_index + slices*assets_count, total_rows)
if ff1.button("+2 slices"): fast_forward_slices(2)   # ~10s
if ff2.button("+4 slices"): fast_forward_slices(4)   # ~20s
if ff3.button("+8 slices"): fast_forward_slices(8)   # ~40s

# Inject Damage (safe implementation)
st.sidebar.markdown("### Inject Damage")
col_h1, col_h2 = st.sidebar.columns(2)
hot_asset = col_h1.selectbox("Asset", sorted(df_all["asset_id"].unique()))
hot_slices = col_h2.select_slider("Duration (slices)", [1,2,3,4,5], value=3)
hot_boost = st.sidebar.slider("Magnitude (+damage)", 5, 50, 20, step=5)
if st.sidebar.button("🔥 Inject"):
    t0 = now_timestamp()
    if pd.isna(t0):
        st.sidebar.warning("Start the replay first.")
    else:
        times = df_all["timestamp"].drop_duplicates().sort_values().reset_index(drop=True)
        pos0 = int(times.searchsorted(t0, side="left"))
        pos0 = min(max(pos0, 0), len(times)-1)
        pos1 = min(pos0 + hot_slices, len(times))
        apply_times = times.iloc[pos0:pos1]
        mask = (df_all["asset_id"].eq(hot_asset)) & (df_all["timestamp"].isin(apply_times))
        df_all.loc[mask, "damage_index"] = np.clip(df_all.loc[mask, "damage_index"] + hot_boost, 0, 100)
        df_all.loc[mask, "outage_prob"]  = np.clip(df_all.loc[mask, "outage_prob"]  + hot_boost*0.5, 0, 100)
        df_all.loc[mask, "crew_eta_min"] = np.clip(df_all.loc[mask, "crew_eta_min"] + hot_boost*0.3, 5, 240)
        st.sidebar.success(f"Injected on {hot_asset} for {len(apply_times)} slices starting {times.iloc[pos0]}.")

# ===== PRELOAD FIRST VIEW =====
if ss.replay_index == 0:
    ss.replay_index = min(2*assets_count, total_rows)  # show motion immediately

# ===== ADVANCE POINTER =====
if ss.running:
    ss.replay_index = min(ss.replay_index + assets_count, total_rows)  # advance one 5s slice
i = ss.replay_index

# ===== BUILD CURRENT VIEW =====
colA, colB = st.columns([1,3])

if i > 0:
    buf = df_all.iloc[:i].copy()
    # Keep only last WINDOW_SECONDS of data
    if buf["timestamp"].notna().any():
        t_max = buf["timestamp"].max()
        if pd.notna(t_max):
            buf = buf[buf["timestamp"] >= t_max - pd.Timedelta(seconds=WINDOW_SECONDS)]
    # Focus filter (distance to Vyshhorod center)
    if focus_on and not buf.empty:
        dist_km = buf.apply(lambda r: haversine_km(FOCUS_LAT, FOCUS_LON, r["lat"], r["lon"]), axis=1)
        buf = buf.assign(dist_km=dist_km)
        view = buf[buf["dist_km"] <= float(radius_km)].copy()
    else:
        view = buf.copy()
        if "dist_km" not in view.columns:
            view["dist_km"] = np.nan

    view = compute_composite(view)

    # Status + recommendations
    with colA:
        st.markdown("### Status")
        st.metric("Replay index", f"{i:,} / {total_rows:,}")
        st.write("Playing:", "✅" if ss.running else "⏸️")
        st.write("Focus:", f"Vyshhorod within {radius_km} km" if focus_on else "All assets")
        st.progress(i / max(total_rows, 1))

        if view.empty:
            st.warning("No assets in the selected focus radius. Increase radius or disable focus.")
        else:
            recs = []
            grp = view.groupby("asset_id").agg(
                comp=("composite","mean"),
                dmg=("damage_index","mean"),
                out=("outage_prob","mean"),
                crew=("crew_eta_min","mean"),
                cust=("customer_impact","mean")
            ).reset_index()
            for _, r in grp.iterrows():
                if r["comp"]>80 and r["cust"]>10000:
                    recs.append({"asset": r["asset_id"], "priority": 1,
                                 "action":"Dispatch nearest crew; backfeed & partial load-shed; UAV recon",
                                 "why": f"Composite {r['comp']:.0f}, customers {int(r['cust']):,}"})
                if r["dmg"]>70 and r["crew"]>45:
                    recs.append({"asset": r["asset_id"], "priority": 2,
                                 "action":"Re-route standby crew; pre-position spares",
                                 "why": f"Damage {r['dmg']:.0f} and crew ETA {r['crew']:.0f}m"})
            recs = sorted(recs, key=lambda x: x["priority"])[:6]
            if recs:
                st.markdown("#### Recommendations")
                for r in recs:
                    st.write(f"**{r['asset']}** — {r['action']}  \n*Why:* {r['why']}")
                rec_df = pd.DataFrame(recs)
                st.download_button("📥 Actions (CSV)", data=rec_df.to_csv(index=False).encode("utf-8"),
                                   file_name="actions.csv", mime="text/csv")
                st.download_button("📥 Actions (JSON)", data=json.dumps(recs, indent=2).encode("utf-8"),
                                   file_name="actions.json", mime="application/json")

    # Map + table
    if not view.empty:
        center_lat = FOCUS_LAT if focus_on else float(view["lat"].mean())
        center_lon = FOCUS_LON if focus_on else float(view["lon"].mean())
        layer = pdk.Layer(
            "HeatmapLayer",
            data=view,
            get_position='[lon, lat]',
            get_weight='composite',
            radiusPixels=45,
        )
        ring = pdk.Layer(  # visual hint for focus radius
            "ScatterplotLayer",
            data=[{"lat": FOCUS_LAT, "lon": FOCUS_LON, "name": "Vyshhorod"}],
            get_position='[lon, lat]',
            get_radius=int(radius_km*30) if focus_on else 0,  # rough visual scale
            get_fill_color='[30,160,60,40]',
            pickable=True
        )
        tooltip = {"text": "{asset_id}\\nComposite={composite:.1f}\\nDamage={damage_index:.1f}\\nOutage={outage_prob:.1f}\\nETA={crew_eta_min:.0f}m"}
        deck = pdk.Deck(
            initial_view_state=pdk.ViewState(latitude=center_lat, longitude=center_lon, zoom=10.5 if focus_on else 6),
            layers=[layer, ring] if focus_on else [layer],
            tooltip=tooltip
        )
        with colB:
            st.pydeck_chart(deck)
            st.dataframe(view.tail(30), use_container_width=True)
    else:
        with colB:
            st.info("No rows in view. Try increasing the focus radius or disable focus.")
else:
    with colB:
        st.info("Click ▶ Start to play.")

# ===== AUTO-RERUN =====
if ss.running:
    time.sleep(TICK_SECONDS)
    st.rerun()
