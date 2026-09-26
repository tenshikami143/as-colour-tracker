import os
import re
import requests
from supabase import create_client, Client

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")
ASCOLOUR_GRAPHQL_TOKEN = os.getenv("ASCOLOUR_GRAPHQL_TOKEN", "")

# Define target style codes (e.g., 5026 for Classic Tee)
PRODUCT_STYLES = [
    {"code": "5026", "title": "Classic Tee 5026"},
    {"code": "5001", "title": "Staple Tee 5001"},
    {"code": "4001", "title": "WOS Maple Tee 4001"},
    {"code": "5102", "title": "Stencil Hood 5102"},
]

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

def send_alert(message: str):
    if DISCORD_WEBHOOK_URL:
        try:
            requests.post(DISCORD_WEBHOOK_URL, json={"content": message}, timeout=10)
        except Exception as e:
            print(f"Failed to send Discord alert: {e}")

def fetch_product_inventory(style_code: str):
    graphql_url = "https://www.ascolour.com.au/graphql"
    
    # Storefront GraphQL query to pull exact variant inventory levels
    query = """
    query getProductInventory($searchTerm: String!) {
      site {
        search {
          searchProducts(filters: { searchTerms: $searchTerm }) {
            products {
              edges {
                node {
                  entityId
                  name
                  sku
                  variants {
                    edges {
                      node {
                        entityId
                        sku
                        inventory {
                          isInStock
                          aggregated {
                            availableToSell
                          }
                        }
                        options {
                          edges {
                            node {
                              displayName
                              values {
                                edges {
                                  node {
                                    label
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
            }
          }
        }
      }
    }
    """

    headers = {
        "Content-Type": "application/json",
        "X-Bc-Store-Id": "1001021066",
        "Authorization": ASCOLOUR_GRAPHQL_TOKEN,
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }

    payload = {
        "query": query,
        "variables": {"searchTerm": style_code}
    }

    try:
        response = requests.post(graphql_url, json=payload, headers=headers, timeout=15)
        
        if response.status_code != 200:
            print(f"GraphQL request failed for style {style_code}: Status {response.status_code}")
            print(f"Response snippet: {response.text[:300]}")
            return []

        data = response.json()
        
        if "errors" in data:
            print(f"GraphQL error returned for {style_code}: {data['errors']}")
            return []

        products = data.get("data", {}).get("site", {}).get("search", {}).get("searchProducts", {}).get("products", {}).get("edges", [])
        
        if not products:
            print(f"No products found for style code {style_code}")
            return []

        # Extract variants from the matched product node
        product_node = products[0]["node"]
        variants_data = []

        for edge in product_node.get("variants", {}).get("edges", []):
            v_node = edge["node"]
            
            # Inventory calculation
            inv = v_node.get("inventory", {})
            stock_qty = 0
            if inv.get("aggregated") and inv["aggregated"].get("availableToSell") is not None:
                stock_qty = int(inv["aggregated"]["availableToSell"])
            elif inv.get("isInStock"):
                stock_qty = 100  # Default baseline if exact number is omitted

            # Parse color and size labels from options
            color_name = "DEFAULT"
            size_name = "STD"
            
            for opt_edge in v_node.get("options", {}).get("edges", []):
                opt = opt_edge["node"]
                opt_name = opt.get("displayName", "").lower()
                vals = opt.get("values", {}).get("edges", [])
                val_label = vals[0]["node"]["label"] if vals else ""

                if "color" in opt_name or "colour" in opt_name:
                    color_name = val_label
                elif "size" in opt_name:
                    size_name = val_label

            clean_c = re.sub(r'[^A-Za-z0-9]', '', color_name).upper()
            clean_s = re.sub(r'[^A-Za-z0-9]', '', size_name).upper()
            variant_id = f"{style_code}-{clean_c}-{clean_s}"

            variants_data.append({
                "variant_id": variant_id,
                "product_title": product_node.get("name", style_code),
                "colour_name": color_name,
                "size_name": size_name,
                "stock_count": stock_qty
            })

        return variants_data

    except Exception as e:
        print(f"Error fetching inventory for style {style_code}: {e}")
        return []

def process_inventory():
    print("Starting AS Colour inventory processing...")
    
    for item in PRODUCT_STYLES:
        style_code = item["code"]
        print(f"Checking style: {style_code}...")
        
        variants = fetch_product_inventory(style_code)
        print(f"Retrieved {len(variants)} variants for style {style_code}.")

        for v in variants:
            variant_id = v["variant_id"]
            title = v["product_title"]
            colour = v["colour_name"]
            size = v["size_name"]
            stock = v["stock_count"]

            is_low_stock = (stock <= 25)

            # Check database for existing watchlist record
            res = supabase.table("inventory_states").select("*").eq("variant_id", variant_id).execute()
            existing_records = res.data

            if is_low_stock:
                if not existing_records:
                    # New low-stock item found
                    supabase.table("inventory_states").insert({
                        "variant_id": variant_id,
                        "colour_name": colour,
                        "size_name": size,
                        "stock_count": stock,
                        "is_below_25": True
                    }).execute()
                    send_alert(f"⚠️ **LOW STOCK ALERT**: {title} (`{variant_id}`) - **{colour} / {size}** has only **{stock}** units left!")
                    print(f"ALERT: {variant_id} added to watchlist ({stock} units left).")
                else:
                    # Update stock level for existing low-stock item
                    supabase.table("inventory_states").update({
                        "stock_count": stock,
                        "is_below_25": True,
                        "updated_at": "now()"
                    }).eq("variant_id", variant_id).execute()
            else:
                if existing_records:
                    # Item was previously low-stock but has now been restocked back above 25
                    send_alert(f"✅ **RESTOCK ALERT**: {title} (`{variant_id}`) - **{colour} / {size}** restocked to **{stock}** units!")
                    supabase.table("inventory_states").delete().eq("variant_id", variant_id).execute()
                    print(f"RESTOCK: {variant_id} removed from watchlist ({stock} units).")

    print("Inventory processing complete.")

if __name__ == "__main__":
    process_inventory()
