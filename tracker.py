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
    
    # GraphQL query using first: 250 to ensure full variant catalog coverage
    query = """
    {
      products(first: 75) {
        edges {
          node {
            id
            title
            variants(first: 250) {
              edges {
                node {
                  id
                  sku
                  displayName
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
    """
    
    response = requests.post(GRAPHQL_URL, json={"query": query}, headers=headers)
    if response.status_code != 200:
        print(f"API Error: {response.status_code} - {response.text}")
        return []
        
    data = response.json()
    products = data.get("data", {}).get("products", {}).get("edges", [])
    
    low_stock_items = []
    
    for product_edge in products:
        product_node = product_edge["node"]
        product_title = product_node.get("title")
        
        for edge in product_node.get("variants", {}).get("edges", []):
            v_node = edge["node"]
            
            inv = v_node.get("inventory", {})
            
            # DEBUG: Print raw inventory data to check what the API returns
            print(f"DEBUG Variant {v_node.get('sku')}: inventory object = {inv}")
            
            stock_qty = 0
            if inv.get("aggregated") and inv["aggregated"].get("availableToSell") is not None:
                stock_qty = int(inv["aggregated"]["availableToSell"])
                
            # If stock is below your threshold (e.g., 25)
            if stock_qty < 25:
                # Parse title or display name into colour/size if applicable
                display_name = v_node.get("displayName", "")
                
                low_stock_items.append({
                    "variant_id": v_node.get("id"),
                    "sku": v_node.get("sku"),
                    "product_title": product_title,
                    "display_name": display_name,
                    "stock_count": stock_qty
                })
                
    return low_stock_items

def main():
    print("Starting AS Colour inventory scan...")
    low_stock_items = fetch_product_inventory()
    print(f"Found {len(low_stock_items)} items below threshold.")
    
    for item in low_stock_items:
        variant_id = item["variant_id"]
        stock = item["stock_count"]
        
        # Check existing record in Supabase
        existing = supabase.table("inventory_states").select("*").eq("variant_id", variant_id).execute()
        existing_records = existing.data
        
        if stock >= 25:
            # If restocked, remove it from the table (self-cleaning)
            if existing_records:
                supabase.table("inventory_states").delete().eq("variant_id", variant_id).execute()
                print(f"Restocked & removed from Supabase: {item['sku']}")
        else:
            # Low stock or out of stock item
            if not existing_records:
                # Insert new low-stock record
                supabase.table("inventory_states").insert({
                    "variant_id": variant_id,
                    "colour_name": item["display_name"],
                    "size_name": "", 
                    "stock_count": stock,
                    "is_below_25": True,
                    "updated_in_deconetwork": False
                }).execute()
                print(f"Added new low-stock item to Supabase: {item['sku']} (Stock: {stock})")
            else:
                # Update stock count if it changed
                supabase.table("inventory_states").update({
                    "stock_count": stock
                }).eq("variant_id", variant_id).execute()

if __name__ == "__main__":
    main()
