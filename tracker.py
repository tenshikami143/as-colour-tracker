import os
import random
import requests
from datetime import datetime, timezone
from supabase import create_client, Client

# Initialize Supabase credentials
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# AS Colour GraphQL API credentials
GRAPHQL_URL = os.environ.get("AS_COLOUR_GRAPHQL_URL")
API_TOKEN = os.environ.get("AS_COLOUR_API_TOKEN")

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

def fetch_monitored_inventory():
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {API_TOKEN}" if API_TOKEN else ""
    }
    
    scanned_items = {}
    has_next_products = True
    product_cursor = None

    query = """
    query FetchCatalog($productCursor: String,$variantCursor: String) {
      site {
        products(first: 50, after: $productCursor) {
          pageInfo { hasNextPage endCursor }
          edges {
            node {
              id name
              variants(first: 50, after: $variantCursor) {
                pageInfo { hasNextPage endCursor }
                edges {
                  node {
                    id sku
                    options {
                      edges {
                        node {
                          displayName
                          values { edges { node { label } } }
                        }
                      }
                    }
                    inventory { aggregated { availableToSell } }
                  }
                }
              }
            }
          }
        }
      }
    }
    """

    print(f"Fetching catalog data for {len(TARGET_STYLES)} target styles...")

    while has_next_products:
        variables = {"productCursor": product_cursor, "variantCursor": None}
        response = requests.post(GRAPHQL_URL, json={"query": query, "variables": variables}, headers=headers)
        if response.status_code != 200:
            print(f"API Error: {response.status_code} - {response.text}")
            break

        products_data = response.json().get("data", {}).get("site", {}).get("products", {})
        
        for product_edge in products_data.get("edges", []):
            product_node = product_edge["node"]
            has_next_variants = True
            variant_cursor = None
            
            while has_next_variants:
                if variant_cursor:
                    var_vars = {"productCursor": product_cursor, "variantCursor": variant_cursor}
                    v_res = requests.post(GRAPHQL_URL, json={"query": query, "variables": var_vars}, headers=headers)
                    if v_res.status_code != 200: 
                        break
                    p_edges = v_res.json().get("data", {}).get("site", {}).get("products", {}).get("edges", [])
                    target_p = next((p["node"] for p in p_edges if p["node"]["id"] == product_node["id"]), None)
                    if not target_p: 
                        break
                    variants_data = target_p.get("variants", {})
                else:
                    variants_data = product_node.get("variants", {})

                for edge in variants_data.get("edges", []):
                    v_node = edge["node"]
                    inv = v_node.get("inventory", {})
                    full_sku = v_node.get("sku", "UNKNOWN-SKU")
                    
                    base_sku = full_sku.split("-")[0].upper() if "-" in full_sku else full_sku.upper()
                    
                    # Filter out non-monitored styles
                    if base_sku.lower() not in [s.lower() for s in TARGET_STYLES]:
                        continue
                    
                    colour, size = "UNKNOWN", "OS"
                    for opt_edge in v_node.get("options", {}).get("edges", []):
                        opt_node = opt_edge.get("node", {})
                        opt_name = opt
