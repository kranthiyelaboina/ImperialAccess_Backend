import streamlit as st
import pandas as pd
import os
from datetime import datetime

from streamlit_autorefresh import st_autorefresh

# Auto-refresh every 5 seconds
st_autorefresh(interval=5000, key="attendance_refresh")

st.title("Attendance Dashboard")

date = datetime.now().strftime("%d-%m-%Y")
filepath = f"Attendance/Attendance_{date}.csv"

st.subheader(f"Date: {date}")

if os.path.isfile(filepath):
    df = pd.read_csv(filepath)
    st.metric("Total Attendance", len(df))
    st.dataframe(df.style.highlight_max(axis=0), use_container_width=True)
else:
    st.info(f"No attendance recorded yet for today ({date}).")
