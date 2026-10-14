print("🚀 TRACKER SCRIPT STARTED SUCCESSFULLY")

import os
import random
import base64
import json
import requests
from datetime import datetime, timezone
from supabase import create_client, Client

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

GRAPHQL_URL = "https://www.ascolour.com.au/graphql"
raw_token = os.environ.get("ASCOLOUR_GRAPHQL_TOKEN") or os.environ.get("AS_COLOUR_API_TOKEN")

if raw_token and raw_token.lower().startswith("bearer "):
    API_TOKEN = raw_token.split(" ", 1)[1].strip()
else:
    API_TOKEN = raw_token

DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL")

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

COLOUR_MAP = {
    "ARC_B": "Arctic Blue", "ARMY": "Army", "ASH": "Ash", "ASP_M": "Asphalt Marle",
    "BBGUM": "Bubblegum", "BL/WH": "Black/White", "BLACK": "Black", "BLA_A": "Black Acid",
    "BLU_A": "Blue Acid", "BONE": "Bone", "BRI_R": "Bright Royal", "BURGU": "Burgundy",
    "BUTTR": "Butter", "CARDI": "Cardinal", "CAR_B": "Carolina Blue", "CHARC": "Charcoal",
    "CHA_P": "Charcoal", "COAL": "Coal", "COBLT": "Cobalt", "COPPR": "Copper",
    "CREAM": "Cream", "ECRU": "Ecru", "EMRLD": "Emerald", "EUCAL": "Eucalyptus",
    "FORES": "Forest Green", "F_ARM": "Faded Army", "F_BLK": "Faded Black", "F_BLU": "Faded Blue",
    "F_DUS": "Faded Dust", "F_MUS": "Faded Mustard", "F_ROS": "Faded Rose", "F_SLT": "Faded Slate",
    "GOLD": "Gold", "GRE_M": "Grey Marle", "GR_AS": "Grey Acid", "H_PNK": "Hot Pink",
    "KEL_G": "Kelly Green", "KHAKI": "Khaki", "LAGON": "Lagoon", "MID_B": "Mid Blue",
    "MOSS": "Moss Green", "NAT_R": "Natural", "NAVY": "Navy", "ORCHD": "Orchid",
    "ORCHI": "Orchid", "PETRL": "Petrol Blue", "PINE": "Pine Green", "PINK": "Pink",
    "POWDR": "Powder", "P_BLU": "Powder Blue", "P_PNK": "Powder Pink", "RED": "Red",
    "SAGE": "Sage", "SNSET": "Sunset", "Snset": "Sunset", "STORM": "Storm",
    "WALNT": "Walnut", "WH/RE": "White/Red", "WHITE": "White", "WHI_B": "White/Black",
    "WHI_M": "White Marle", "WHI_N": "White/Navy", "DAR_C": "Dark Charcoal",
    "LBRTY": "Liberty", "MUSH": "Mushroom", "MUSTA": "Mustard", "ORANG": "Orange"
}

SIZE_MAP = {
    "F-XS": "XS", "G-S": "Small", "H-M": "Medium", "I-L": "Large", "J-XL": "XL",
    "K-2XL": "2XL", "L-3XL": "3XL", "M-4XL": "4XL", "N-5XL": "5XL", "P-Os": "One Size",
    "OS": "One Size", "P0-3": "0-3 Months", "Q3-6": "3-6 Months", "R6-12": "6-12 Months",
    "00-03": "0-3 Months", "03-06": "3-6 Months", "06-12": "6-12 Months", "12-18": "12-18 Months",
    "18-24": "18-24 Months", "S1218": "12-18 Months", "T1824": "18-24 Months",
    "XSM": "XS", "SML": "Small", "MED": "Medium", "LGE": "Large", "XLG": "XL", "2XL": "2XL", "3XL": "3XL"
}

def parse_sku_attributes(variant_sku):
    normalized_sku = variant_sku.replace("-CHARC-", "-CHA_P-")
    parts = normalized_sku.split("-")
    if len(parts) >= 3:
        raw_colour = parts[1]
        raw_size = "-".join(parts[2:])
        colour = COLOUR_MAP.get(raw_colour, raw_colour.replace("_", " ").title())
        size = SIZE_MAP.get(raw_size, raw_size.replace("_", " ").title())
        return colour, size, normalized_sku
    return "N/A", "N/A", variant_sku

def fetch_monitored_inventory():
    headers = {"Content-Type": "application/json"}
    if API_TOKEN:
        headers["Authorization"] = f"Bearer {API_TOKEN}"

    scanned_items = {}
    has_next_products = True
    product_cursor = None

    query = """
    query FetchCatalog($productCursor: String) {
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
              variants(first: 100) {
                edges {
                  node {
                    id
                    sku
                    inventory {
                      byLocation {
                        availableToSell
                        location {
                          name
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
    }
    """

    debug_printed = False

    while has_next_products:
        variables = {"productCursor": product_cursor}
        try:
            response = requests.post(GRAPHQL_URL, json={"query": query, "variables": variables}, headers=headers)
            if response.status_code != 200:
                break

            data = response.json()
            products_data = data.get("data", {}).get("site", {}).get("products", {})
            edges = products_data.get("edges", [])

            for edge in edges:
                product = edge.get("node", {})
                style_sku = product.get("sku", "").strip().lower()
                product_name = product.get("name", "").strip()

                is_target = any(style_sku.startswith(target.lower()) for target in TARGET_STYLES)

                if is_target:
                    variants = product.get("variants", {}).get("edges", [])
                    for v_edge in variants:
                        v_node = v_edge.get("node", {})
                        raw_v_sku = v_node.get("sku", "").strip()
                        
                        colour, size, variant_sku = parse_sku_attributes(raw_v_sku)
                        inv_data = v_node.get("inventory") or {}

                        if "4001" in style_sku and "BERRY" in variant_sku and not debug_printed:
                            print(f"🔍 RAW INVENTORY BY-LOCATION FOR BERRY: {json.dumps(inv_data, indent=2)}")
                            debug_printed = True

                        by_location = inv_data.get("byLocation") if isinstance(inv_data, dict) else []
                        stock = 0
                        if isinstance(by_location, list):
                            for loc in by_location:
                                if isinstance(loc, dict) and loc.get("availableToSell") is not None:
                                    stock += loc.get("availableToSell")

                        if variant_sku:
                            scanned_items[variant_sku] = {
                                "style_sku": style_sku.upper(),
                                "product_name": product_name,
                                "colour_name": colour,
                                "size_name": size,
                                "stock": stock
                            }

            page_info = products_data.get("pageInfo", {})
            has_next_products = page_info.get("hasNextPage", False)
            product_cursor = page_info.get("endCursor")
        except Exception as e:
            print(f"Exception: {e}")
            break

    return scanned_items

def sync_inventory_and_alert():
    current_inventory = fetch_monitored_inventory()
    print(f"Scanned {len(current_inventory)} target variants with inspection.")

if __name__ == "__main__":
    sync_inventory_and_alert()
