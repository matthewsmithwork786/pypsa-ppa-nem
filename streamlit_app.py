"""Data centre procurement with year-selectable historical NEM profiles."""
import streamlit as st

st.set_page_config(page_title="Data centre renewable procurement", page_icon="⚡", layout="wide")

from ui.procurement import render

render()
