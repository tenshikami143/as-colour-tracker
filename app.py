import os
import streamlit as st
import pandas as pd
from supabase import create_client, Client

# Page Configuration
st.set_page_config(
    page_title="AS Colour Inventory Dashboard",
    page_icon="👕",
    layout="wide"
)

# Initialize Supabase Client using Streamlit secrets/environment variables
SUPABASE_URL = os.environ.get("SUPABASE_URL") or st.secrets.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY") or st.secrets.get("SUPABASE_KEY")

if not SUPABASE_URL or not SUPABASE_KEY:
    st.error("Supabase credentials missing! Please check your environment variables or Streamlit secrets.")
    st.stop()

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# App Title & Overview
st.title("👕 AS Colour Inventory Command Center")
st.markdown("Manage your inventory tracking, view real-time stock levels, and update custom translation codes dynamically.")

# Tabs for Organization
tab1, tab2, tab3 = st.tabs(["📦 Live Inventory", "⚙️ Custom Mappings", "🚀 Manual Trigger"])

with tab1:
    st.header("Live Inventory States")
    st.markdown("Here is the latest snapshot of your 73 monitored styles synced from AS Colour Australia.")
    
    # Fetch inventory data from Supabase
    response = supabase.table("inventory_states").select("*").execute()
    data = response.data if response.data else []
    
    if data:
        df = pd.DataFrame(data)
        
        # Search and Filter Options
        col1, col2 = st.columns(2)
        with col1:
            search_query = st.text_input("🔍 Search by Product Name or SKU", "")
        with col2:
            filter_low = st.checkbox("Show Only Low Stock (< 25 units)", value=False)
            
        if search_query:
            df = df[df['product_name'].str.contains(search_query, case=False, na=False) | 
                    df['variant_id'].str.contains(search_query, case=False, na=False)]
            
        if filter_low:
            df = df[df['is_below_25'] == True]
            
        # Display DataFrame
        st.dataframe(
            df[['style_sku', 'variant_id', 'product_name', 'colour_name', 'size_name', 'stock_count', 'is_below_25', 'updated_at']],
            use_container_width=True
        )
    else:
        st.info("No inventory records found yet. Run your tracker script or trigger a sync!")

with tab2:
    st.header("Manage Shorthand Equivalents")
    st.markdown("Add or update your custom translation dictionaries for colors and sizes directly in the cloud.")
    
    col_a, col_b = st.columns(2)
    
    with col_a:
        st.subheader("Add Colour Mapping")
        with st.form("colour_form"):
            c_code = st.text_input("Shorthand Code (e.g., GRE_M)").strip().upper()
            c_name = st.text_input("Human-Readable Name (e.g., Grey Marle)").strip()
            submit_colour = st.form_submit_button("Save Colour Mapping")
            
            if submit_colour:
                if c_code and c_name:
                    # Save to Supabase settings table
                    payload = {"key": f"colour_{c_code}", "value": {"code": c_code, "name": c_name}}
                    supabase.table("settings").upsert(payload).execute()
                    st.success(f"Successfully saved mapping: {c_code} -> {c_name}")
                else:
                    st.error("Please fill in both fields.")

    with col_b:
        st.subheader("Add Size Mapping")
        with st.form("size_form"):
            s_code = st.text_input("Shorthand Size Code (e.g., S1218)").strip().upper()
            s_name = st.text_input("Human-Readable Size (e.g., 12-18 Months)").strip()
            submit_size = st.form_submit_button("Save Size Mapping")
            
            if submit_size:
                if s_code and s_name:
                    payload = {"key": f"size_{s_code}", "value": {"code": s_code, "name": s_name}}
                    supabase.table("settings").upsert(payload).execute()
                    st.success(f"Successfully saved mapping: {s_code} -> {s_name}")
                else:
                    st.error("Please fill in both fields.")

with tab3:
    st.header("Manual Pipeline Controls")
    st.markdown("Force an immediate synchronization scan outside of your scheduled GitHub Actions runs.")
    
    if st.button("🚀 Trigger Manual Inventory Sync"):
        st.warning("Manual pipeline invocation from the dashboard will be wired up in the next step! Your automated background GitHub Action is still running independently.")
