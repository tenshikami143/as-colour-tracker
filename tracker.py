import os
import random
import requests
from datetime import datetime, timezone
import streamlit as st
from supabase import create_client, Client

# Streamlit UI Title
st.title("Tee Junction Inventory Tracker")

# Diagnostic Supabase Initialization
@st.cache_resource
def init_supabase():
    url = None
    key = None
    
    st.write("--- **Secrets Diagnostic** ---")
    try:
        st.write("st.secrets keys available:", list(st.secrets.keys()))
        if "SUPABASE_URL" in st.secrets:
            url = st.secrets["SUPABASE_URL"]
            st.write("✅ SUPABASE_URL found in st.secrets")
        if "SUPABASE_KEY" in st.secrets:
            key = st.secrets["SUPABASE_KEY"]
            st.write("✅ SUPABASE_KEY found in st.secrets")
    except Exception as e:
        st.write(f"⚠️ st.secrets error or none found: {e}")
        
    # Fallback to environment variables (for GitHub Actions)
    if not url:
        url = os.environ.get("SUPABASE_URL")
        if url:
            st.write("✅ SUPABASE_URL found in os.environ")
    if not key:
        key = os.environ.get("SUPABASE_KEY")
        if key:
            st.write("✅ SUPABASE_KEY found in os.environ")
            
    st.write("----------------------------")
    
    if not url or not key:
        st.error("Supabase credentials missing! Please check your Streamlit Cloud Secrets.")
        return None
        
    return create_client(url, key)

supabase = init_supabase()

# 2. Fetch and Display Inventory Data on the Web Dashboard
@st.cache_data(ttl=60)
def load_web_data():
    if not supabase:
        return []
    response = supabase.table("inventory_states").select("*").execute()
    return response.data

# Render Web Dashboard UI if running inside Streamlit
if supabase:
    try:
        data = load_web_data()
        if data:
            col1, col2 = st.columns(2)
            col1.metric("Total Items Tracked", len(data))
            
            st.subheader("Current Stock Levels")
            st.dataframe(data, width="stretch")
        else:
            st.info("Connected to Supabase, but no inventory records found in 'inventory_states'.")
    except Exception as e:
        st.error(f"Error fetching data from Supabase: {e}")


# AS Colour Australia GraphQL API credentials
GRAPHQL_URL = "https://www.ascolour.com.au/graphql"
raw_token = os.environ.get("ASCOLOUR_GRAPHQL_TOKEN") or os.environ.get("AS_COLOUR_API_TOKEN")

if raw_token and raw_token.lower().startswith("bearer "):
    API_TOKEN = raw_token.split(" ", 1)[1].strip()
else:
    API_TOKEN = raw_token

# Discord Webhook
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL")

# Monitored Scope (73 Target Styles)
TARGET_STYLES = [
    "1000", "1001", "1002", "1117", "3001", "3003", "3005", "3006", "3009", "3010",
    "3030", "3031", "3032", "3033", "3034", "3035", "3036", "3037", "4001", "4001g",
    "4008", "4017", "4019", "4023", "4024", "4026", "4053", "4062", "4063g", "4064g",
    "4080", "4100", "4101", "4145", "4146", "4160", "4161", "4402", "5001", "5001g",
    "5001v", "5007", "5012", "5020", "5025", "5026", "5026g", "5040", "5053", "5065",
    "5069", "5070", "5071", "5073", "5074", "5076", "5079", "5080", "5082", "5100",
    "5101", "5102", "5102g", "5103", "5104", "5125", "5145", "5146", "5148", "5160",
    "5161", "5162", "5402"
]

# Translation dictionaries for cleaner readability
COLOUR_MAP = {
    "Snset": "Sunset",
    "Buttr": "Butter",
    "Orchi": "Orchid",
    "Powdr": "Powder",
    "Walnt": "Walnut",
    "GRE_M": "Grey Marle",
    "NAT_R": "Natural",
    "BLACK": "Black",
    "WHITE": "White",
    "NAVY": "Navy",
    "PINK": "Pink",
    "SAGE": "Sage",
    "ARMY": "Army",
    "BONE": "Bone",
    "BURGU": "Burgundy",
    "CAMEL": "Camel",
    "CHA_P": "Charcoal",
    "COAL": "Coal",
    "CREAM": "Cream",
    "DAR_C": "Dark Charcoal",
    "FORES": "Forest Green",
    "KEL_G": "Kelly Green",
    "KHAKI": "Khaki",
    "LBRTY": "Liberty",
    "MUSH": "Mushroom",
    "MUSTA": "Mustard",
    "ORANG": "Orange"
}

SIZE_MAP = {
    "P-Os": "One Size",
    "S1218": "12-18 Months",
    "T1824": "18-24 Months",
    "P0-3": "0-3 Months",
    "Q3-6": "3-6 Months",
    "R6-12": "6-12 Months",
    "OS": "One Size",
    "XSM": "XS",
    "SML": "Small",
    "MED": "Medium",
    "LGE": "Large",
    "XLG": "XL",
    "2XL": "2XL",
    "3XL": "3XL"
}


def parse_sku_attributes(variant_sku):
    parts = variant_sku.split("-")
    if len(parts) >= 3:
        raw_colour = parts[1]
        raw_size = "-".join(parts[2:])
        
        colour = COLOUR_MAP.get(raw_colour, raw_colour.replace("_", " ").title())
        size = SIZE_MAP.get(raw_size, raw_size.replace("_", " ").title())
        
        return colour, size
    return "N/A", "N/A"


def fetch_monitored_inventory():
    headers = {
        "Content-Type": "application/json"
    }
    if API_TOKEN:
        headers["Authorization"] = f"Bearer {API_TOKEN}"

    scanned_items = {}
    has_next_products = True
    product_cursor = None

    query = """
    query FetchCatalog($productCursor: String,$variantCursor: String) {
      site {
        products(first: 50, after: $productCursor) {
          pageInfo {
            hasNextPage
            endCursor
          }
          edges {
            node {
              id
              name
              sku
              variants(first: 50, after: $variantCursor) {
                pageInfo {
                  hasNextPage
                  endCursor
                }
                edges {
                  node {
                    id
                    sku
                    inventory {
                      aggregated {
                        availableToSell
                      }
                    }
                  }
                }
              }
            }
          }
        }
      }
    }
    """

    while has_next_products:
        variables = {
            "productCursor": product_cursor,
            "variantCursor": None
        }

        try:
            response = requests.post(GRAPHQL_URL, json={"query": query, "variables": variables}, headers=headers)
            if response.status_code != 200:
                print(f"API Error: {response.status_code} - {response.text}")
                break

            data = response.json()
            if "errors" in data:
                print(f"GraphQL Error: {data['errors']}")
                break

            products_data = data.get("data", {}).get("site", {}).get("products", {})
            edges = products_data.get("edges", [])

            for edge in edges:
                product = edge.get("node", {})
                style_sku = product.get("sku", "").strip()
                product_name = product.get("name", "").strip()

                if style_sku in TARGET_STYLES:
                    variants = product.get("variants", {}).get("edges", [])
                    for v_edge in variants:
                        v_node = v_edge.get("node", {})
                        variant_sku = v_node
