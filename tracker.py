import os
import requests
from supabase import create_client, Client

# Initialize Supabase credentials
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# AS Colour GraphQL API credentials/endpoints
GRAPHQL_URL = os.environ.get("AS_COLOUR_GRAPHQL_URL")
API_TOKEN = os.environ.get("AS_COLOUR_API_TOKEN")

# List of target base style numbers extracted from your monitored URL list.
# Add or remove style codes here anytime to update what gets tracked.
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
    
    scanned_items = []
    has_next_products = True
    product_cursor = None

    query = """
    query FetchCatalog($productCursor: String, $variantCursor: String) {
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
              variants(first: 50, after: $variantCursor) {
                pageInfo {
                  hasNextPage
                  endCursor
                }
                edges {
                  node {
                    id
                    sku
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

    print(f"Scanning AS Colour catalog for {len(TARGET_STYLES)} target styles...")

    while has_next_products:
        variables = {"productCursor": product_cursor, "variantCursor": None}
        response = requests.post(GRAPHQL_URL, json={"query": query, "variables": variables}, headers=headers)
        
        if response.status_code != 200:
            print(f"API Error: {response.status_code} - {response.text}")
            break

        data = response.json()
        products_data = data.get("data", {}).get("site", {}).get("products", {})
        products = products_data.get("edges", [])
        
        for product_edge in products:
            product_node = product_edge["node"]
            
            has_next_variants = True
            variant_cursor = None
            
            while has_next_variants:
                if variant_cursor:
                    var_variables = {"productCursor": product_cursor, "variantCursor": variant_cursor}
                    v_response = requests.post(GRAPHQL_URL, json={"query": query, "variables": var_variables}, headers=headers)
                    if v_response.status_code != 200:
                        break
                    v_data = v_response.json()
                    p_edges = v_data.get("data", {}).get("site", {}).get("products", {}).get("edges", [])
                    target_p = next((p["node"] for p in p_edges if p["node"]["id"] == product_node["id"]), None)
                    if not target_p:
                        break
                    variants_data = target_p.get("variants", {})
                else:
                    variants_data = product_node.get("variants", {})

                variant_edges = variants_data.get("edges", [])

                for edge in variant_edges:
                    v_node = edge["node"]
                    inv = v_node.get("inventory", {})
                    full_sku = v_node.get("sku", "UNKNOWN-SKU")
                    
                    # Extract clean base style string
                    base_sku = full_sku.split("-")[0].lower() if "-" in full_sku else full_sku.lower()
                    
                    # FILTER: Skip variants if base style is not in our TARGET_STYLES list
                    if base_sku not in [s.lower() for s in TARGET_STYLES]:
                        continue
                    
                    colour = "UNKNOWN"
                    size = "OS"
                    
                    for option_edge in v_node.get("options", {}).get("edges", []):
                        opt_node = option_edge.get("node", {})
                        opt_name = opt_node.get("displayName", "").lower()
                        
                        vals = opt_node.get("values", {}).get("edges", [])
                        if vals:
                            val_label = vals[0].get("node", {}).get("label", "")
                            if "color" in opt_name or "colour" in opt_name:
                                colour = val_label
                            elif "size" in opt_name:
                                size = val_label
                                
                    if colour == "UNKNOWN" and "-" in full_sku:
                        parts = full_sku.split("-")
                        colour = parts[1] if len(parts) > 1 else "UNKNOWN"
                        size = "-".join(parts[2:]) if len(parts) > 2 else size

                    stock_qty = 999 
                    if inv and inv.get("aggregated") and inv["aggregated"].get("availableToSell") is not None:
                        stock_qty = int(inv["aggregated"]["availableToSell"])
                        
                    scanned_items.append({
                        "base_sku": base_sku.upper(),
                        "colour_name": colour,
                        "size_name": size,
                        "stock_count": stock_qty
                    })

                var_page_info = variants_data.get("pageInfo", {})
                has_next_variants = var_page_info.get("hasNextPage", False)
                variant_cursor = var_page_info.get("endCursor")

        prod_page_info = products_data.get("pageInfo", {})
        has_next_products = prod_page_info.get("hasNextPage", False)
        product_cursor = prod_page_info.get("endCursor")

    return scanned_items

def main():
    print("Starting target style inventory scan...")
    scanned_items = fetch_monitored_inventory()
    print(f"Scanned {len(scanned_items)} variants matching target style list.")
    
    low_stock_payload = []
    
    for item in scanned_items:
        base_sku = item["base_sku"]
        colour = item["colour_name"]
        size = item["size_name"]
        stock = item["stock_count"]
        
        if stock >= 25:
            # Delete/clean up restocked items from Supabase
            supabase.table("inventory_states").delete().match({
                "variant_id": base_sku,
                "colour_name": colour,
                "size_name": size
            }).execute()
        else:
            # Collect low-stock items (< 25)
            low_stock_payload.append({
                "variant_id": base_sku,
                "colour_name": colour,
                "size_name": size,
                "stock_count": stock,
                "is_below_25": True,
                "updated_in_deconetwork": False
            })
            
    if low_stock_payload:
        supabase.table("inventory_states").upsert(low_stock_payload).execute()
        print(f"Synced {len(low_stock_payload)} low-stock items across target styles to Supabase.")
    else:
        print("No low-stock items (< 25) found among monitored styles.")

if __name__ == "__main__":
    main()
