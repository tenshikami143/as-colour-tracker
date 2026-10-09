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
    
    # Query variant options to extract full Color and Size display names from BigCommerce catalog
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
        product_title = product_node.get("name")
        
        for edge in product_node.get("variants", {}).get("edges", []):
            v_node = edge["node"]
            
            inv = v_node.get("inventory", {})
            full_sku = v_node.get("sku", "UNKNOWN-SKU")
            
            # Base style number from SKU prefix (e.g., '1000' from '1000-BLACK-P-OS')
            base_sku = full_sku.split("-")[0] if "-" in full_sku else full_sku
            
            # Extract full Color and Size labels from variant options
            colour = "UNKNOWN"
            size = "OS"
            
            for option_edge in v_node.get("options", {}).get("edges", []):
                opt_node = option_edge["node"]
                opt_name = opt_node.get("displayName", "").lower()
                
                vals = opt_node.get("values", {}).get("edges", [])
                if vals:
                    val_label = vals[0]["node"].get("label", "")
                    if "color" in opt_name or "colour" in opt_name:
                        colour = val_label
                    elif "size" in opt_name:
                        size = val_label
                        
            # Fallback to SKU parsing if options array isn't returned
            if colour == "UNKNOWN" and "-" in full_sku:
                parts = full_sku.split("-")
                colour = parts[1] if len(parts) > 1 else "UNKNOWN"
                size = "-".join(parts[2:]) if len(parts) > 2 else size

            # Default to 999 when stock is high/hidden by BigCommerce
            stock_qty = 999 
            if inv and inv.get("aggregated") and inv["aggregated"].get("availableToSell") is not None:
                stock_qty = int(inv["aggregated"]["availableToSell"])
                
            scanned_items.append({
                "full_sku": full_sku,
                "base_sku": base_sku,
                "colour_name": colour,
                "size_name": size,
                "product_title": product_title,
                "stock_count": stock_qty
            })
                
    return scanned_items

def main():
    print("Starting AS Colour inventory scan...")
    scanned_items = fetch_product_inventory()
    print(f"Scanned {len(scanned_items)} variants from API.")
    
    for item in scanned_items:
        full_sku = item["full_sku"]
        base_sku = item["base_sku"]
        colour = item["colour_name"]
        size = item["size_name"]
        stock = item["stock_count"]
        
        # Check existing records under either full SKU string or base SKU
        existing_full = supabase.table("inventory_states").select("*").eq("variant_id", full_sku).execute().data
        existing_base = supabase.table("inventory_states").select("*").eq("variant_id", base_sku).execute().data
        existing_records = existing_full or existing_base
        
        if stock >= 25:
            # Self-cleaning: remove item from table if restocked
            if existing_records:
                target_id = full_sku if existing_full else base_sku
                supabase.table("inventory_states").delete().eq("variant_id", target_id).execute()
                print(f"Restocked & removed from Supabase: {target_id}")
        else:
            # Low stock (< 25)
            if not existing_records:
                supabase.table("inventory_states").insert({
                    "variant_id": base_sku,
                    "colour_name": colour,
                    "size_name": size, 
                    "stock_count": stock,
                    "is_below_25": True,
                    "updated_in_deconetwork": False
                }).execute()
                print(f"Added low-stock item to Supabase: {base_sku} ({colour} / {size}) - Stock: {stock}")
            else:
                target_id = full_sku if existing_full else base_sku
                supabase.table("inventory_states").update({
                    "variant_id": base_sku,  # Ensures legacy full-SKU IDs get overwritten with clean style numbers
                    "colour_name": colour,
                    "size_name": size,
                    "stock_count": stock
                }).eq("variant_id", target_id).execute()
                print(f"Updated item in Supabase with full catalog names: {base_sku} ({colour} / {size})")

if __name__ == "__main__":
    main()
