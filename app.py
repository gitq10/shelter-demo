import os, time, json
import pandas as pd
import numpy as np
import streamlit as st
import pydeck as pdk

st.set_page_config(page_title="Grid 50s Real-Time Demo", layout="wide")

# ===== CONFIG =====
DATA_FILE = "synthetic_grid_outage_50sec_5s.csv" # <-- use this exact filename
TICK_SECONDS = 1.0   # refresh every 1s
WINDOW_SECONDS = 120 # show last 2 minutes in view (even though file is 50s)

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

# ===== SIDEBAR =====
st.sidebar.title("Controls")

with st.sidebar.expander("File Debug", expanded=False):
    st.write("**Working directory:**", os.getcwd())
    st.write("**Files here:**", os.listdir(".")[:50])
    if os.path.exists(DATA_FILE):
        st.success(f"{DATA_FILE} exists ✓ size: {os.path.getsize(DATA_FILE):,} bytes")
    else:
        st.error(f"{DATA_FILE} NOT FOUND")

# Playback
c1, c2, c3 = st.sidebar.columns(3)
if c1.button("▶ Start"): ss.running = True
if c2.button("⏸ Stop"):  ss.running = False
if c3.button("↺ Reset"):
    ss.running = False
    ss.replay_index = 0

# Fast-Forward: each 5 seconds of data = one timestamp slice across all assets
ff1, ff2, ff3 = st.sidebar.columns(3)
def fast_forward_slices(slices:int):
    ss.replay_index = min(ss.replay_index + slices*assets_count, total_rows)
if ff1.button("+2 slices"): fast_forward_slices(2)   # ~10s
if ff2.button("+4 slices"): fast_forward_slices(4)   # ~20s
if ff3.button("+8 slices"): fast_forward_slices(8)   # ~40s

# Inject Damage (for demo drama)
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
        # Pick a window starting now over N "slices" (timestamps)
        times = df_all["timestamp"].drop_duplicates().sort_values()
        # find start index
        try:
            i0 = times[times>=t0].index[0]
        except Exception:
            i0 = times.index[-1]
        apply_times = times.iloc[times.get_indexer([i0], method='nearest')[0]:][:hot_slices]
        mask = (df_all["asset_id"]==hot_asset) & (df_all["timestamp"].isin(apply_times))
        df_all.loc[mask, "damage_index"] = np.clip(df_all.loc[mask, "damage_index"] + hot_boost, 0, 100)
        df_all.loc[mask, "outage_prob"]  = np.clip(df_all.loc[mask, "outage_prob"]  + hot_boost*0.5, 0, 100)
        df_all.loc[mask, "crew_eta_min"] = np.clip(df_all.loc[mask, "crew_eta_min"] + hot_boost*0.3, 5, 240)
        st.sidebar.success(f"Injected on {hot_asset} for {len(apply_times)} slices.")

# ===== PRELOAD FIRST VIEW =====
if ss.replay_index == 0:
    # show first 2 slices immediately (for instant motion)
    ss.replay_index = min(2*assets_count, total_rows)

# ===== ADVANCE POINTER =====
if ss.running:
    ss.replay_index = min(ss.replay_index + assets_count, total_rows)  # advance one 5s slice
i = ss.replay_index

# ===== BUILD CURRENT VIEW =====
colA, colB = st.columns([1,3])

if i > 0:
    buf = df_all.iloc[:i].copy()
    # Keep only last WINDOW_SECONDS worth of data (based on timestamps)
    if buf["timestamp"].notna().any():
        t_max = buf["timestamp"].max()
        if pd.notna(t_max):
            buf = buf[buf["timestamp"] >= t_max - pd.Timedelta(seconds=WINDOW_SECONDS)]
    buf = compute_composite(buf)

    # Status + recommendations
    with colA:
        st.markdown("### Status")
        st.metric("Replay index", f"{i:,} / {total_rows:,}")
        st.write("Playing:", "✅" if ss.running else "⏸️")
        st.progress(i / max(total_rows, 1))

        # Simple recs
        recs = []
        grp = buf.groupby("asset_id").agg(
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
            # Downloads
            rec_df = pd.DataFrame(recs)
            st.download_button("📥 Actions (CSV)", data=rec_df.to_csv(index=False).encode("utf-8"),
                               file_name="actions.csv", mime="text/csv")
            st.download_button("📥 Actions (JSON)", data=json.dumps(recs, indent=2).encode("utf-8"),
                               file_name="actions.json", mime="application/json")

    # Map + table
    center_lat = float(buf["lat"].mean()) if len(buf) else 48.5
    center_lon = float(buf["lon"].mean()) if len(buf) else 36.5
    layer = pdk.Layer(
        "HeatmapLayer",
        data=buf,
        get_position='[lon, lat]',
        get_weight='composite',
        radiusPixels=40,
    )
    deck = pdk.Deck(
        initial_view_state=pdk.ViewState(latitude=center_lat, longitude=center_lon, zoom=6),
        layers=[layer],
        tooltip={"text": "{asset_id}\\nComposite={composite:.1f}\\nDamage={damage_index:.1f}\\nOutage={outage_prob:.1f}\\nETA={crew_eta_min:.0f}m"}
    )
    with colB:
        st.pydeck_chart(deck)
        st.dataframe(buf.tail(30), use_container_width=True)
else:
    with colB:
        st.info("Click ▶ Start to play.")

# ===== AUTO-RERUN =====
if ss.running:
    time.sleep(TICK_SECONDS)
    st.rerun()
