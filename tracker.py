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

def fetch_product_inventory():
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {API_TOKEN}" if API_TOKEN else ""
    }
    
    query = """
    {
      site {
        products(first: 50) {
          edges {
            node {
              id
              name
              variants(first: 50) {
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
    
    response = requests.post(GRAPHQL_URL, json={"query": query}, headers=headers)
    if response.status_code != 200:
        print(f"API Error: {response.status_code} - {response.text}")
        return []
        
    data = response.json()
    products = data.get("data", {}).get("site", {}).get("products", {}).get("edges", [])
    
    scanned_items = []
    
    for product_edge in products:
        product_node = product_edge["node"]
        
        for edge in product_node.get("variants", {}).get("edges", []):
            v_node = edge["node"]
            
            inv = v_node.get("inventory", {})
            full_sku = v_node.get("sku", "UNKNOWN-SKU")
            
            # Extract clean base style number (e.g., '1000' from '1000-BLACK-P-OS')
            base_sku = full_sku.split("-")[0] if "-" in full_sku else full_sku
            
            colour = "UNKNOWN"
            size = "OS"
            
            # Extract option names from catalog schema
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
                        
            # Fallback to SKU parsing if options array isn't populated
            if colour == "UNKNOWN" and "-" in full_sku:
                parts = full_sku.split("-")
                colour = parts[1] if len(parts) > 1 else "UNKNOWN"
                size = "-".join(parts[2:]) if len(parts) > 2 else size

            # BigCommerce hides high stock values (None). Default to 999.
            stock_qty = 999 
            if inv and inv.get("aggregated") and inv["aggregated"].get("availableToSell") is not None:
                stock_qty = int(inv["aggregated"]["availableToSell"])
                
            scanned_items.append({
                "base_sku": base_sku,
                "colour_name": colour,
                "size_name": size,
                "stock_count": stock_qty
            })
                
    return scanned_items

def main():
    print("Starting AS Colour inventory scan...")
    scanned_items = fetch_product_inventory()
    print(f"Scanned {len(scanned_items)} variants from API.")
    
    low_stock_payload = []
    
    for item in scanned_items:
        base_sku = item["base_sku"]
        colour = item["colour_name"]
        size = item["size_name"]
        stock = item["stock_count"]
        
        if stock >= 25:
            # Self-clean restocked items from Supabase
            supabase.table("inventory_states").delete().match({
                "variant_id": base_sku,
                "colour_name": colour,
                "size_name": size
            }).execute()
        else:
            # Stage items with stock < 25
            low_stock_payload.append({
                "variant_id": base_sku,
                "colour_name": colour,
                "size_name": size,
                "stock_count": stock,
                "is_below_25": True,
                "updated_in_deconetwork": False
            })
            
    if low_stock_payload:
        # Upsert using composite primary key (variant_id, colour_name, size_name)
        supabase.table("inventory_states").upsert(low_stock_payload).execute()
        print(f"Successfully synced {len(low_stock_payload)} low-stock items to Supabase.")
    else:
        print("No items below threshold (< 25) found in this run.")

if __name__ == "__main__":
    main()
