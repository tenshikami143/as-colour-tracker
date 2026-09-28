import os
import re
import requests
from datetime import datetime
from supabase import create_client, Client

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")
ASCOLOUR_GRAPHQL_TOKEN = os.getenv("ASCOLOUR_GRAPHQL_TOKEN", "")

# Define target style codes
PRODUCT_STYLES = [
    {"code": "5026", "title": "Classic Tee 5026"},
    {"code": "5001", "title": "Staple Tee 5001"},
    {"code": "4001", "title": "WOS Maple Tee 4001"},
    {"code": "5102", "title": "Stencil Hood 5102"},
    {"code": "5080", "title": "Heavy Tee 5080"},
    {"code": "4001g", "title": "WOS Maple Organic Tee 4001g"},
    {"code": "3005", "title": "Kids Staple Tee 3005"},
    {"code": "3006", "title": "Youth Staple Tee 3006"},
    {"code": "5101", "title": "Supply Hood 5101"},
    {"code": "4101", "title": "WOS Supply Hood 4101"},
    {"code": "5161", "title": "Relax Hood 5161"},
    {"code": "5001g", "title": "Staple Organic Tee 5001g"},
    {"code": "5402", "title": "Chad Polo 5402"},
    {"code": "3003", "title": "Organic Infant Mini Me One Piece 3003"},
    {"code": "3001", "title": "Organic Infant Wee Tee 3001"},
    {"code": "3033", "title": "Youth Supply Hood 3033"},
    {"code": "3032", "title": "Kids Supply Hood 3032"},
    {"code": "4062", "title": "WOS Crop Tee 4062"},
    {"code": "4008", "title": "WOS Mali Tee 4008"},
    {"code": "5025", "title": "Barnard Tank 5025"},
    {"code": "5012", "title": "Raglan Tee 5012"},
    {"code": "5007", "title": "Lowdown Singlet 5007"},
    {"code": "1001", "title": "Carrie Tote 1001"},
    {"code": "1000", "title": "Parcel Tote 1000"},
    {"code": "1002", "title": "Shoulder Tote 1002"},
    {"code": "5040", "title": "Staple Stone Wash Tee 5040"},
    {"code": "4402", "title": "WOS Amy Polo 4402"},
    {"code": "5104", "title": "Stencil Zip Hood 5104"},
    {"code": "5020", "title": "Staple L S Tee 5020"},
    {"code": "5145", "title": "Heavy Crew 5145"},
    {"code": "5146", "title": "Heavy Hood 5146"},
    {"code": "5148", "title": "Heavy Zip Hood 5148"},
    {"code": "5100", "title": "Supply Crew 5100"},
    {"code": "4100", "title": "WOS Supply Crew 4100"},
    {"code": "5125", "title": "Stencil Half Zip Crew 5125"},
    {"code": "3031", "title": "Youth Supply Crew 3031"},
    {"code": "3030", "title": "Kids Supply Crew 3030"},
    {"code": "5162", "title": "Relax Zip Hood 5162"},
    {"code": "5160", "title": "Relax Crew 5160"},
    {"code": "4160", "title": "WOS Relax Crew 4160"},
    {"code": "3035", "title": "Youth Relax Crew 3035"},
    {"code": "3034", "title": "Kids Relax Crew 3034"},
    {"code": "4161", "title": "WOS Relax Hood 4161"},
    {"code": "4146", "title": "WOS Heavy Hood 4146"},
    {"code": "5001v", "title": "Staple V Neck Tee 5001v"},
    {"code": "5082", "title": "Heavy Faded Tee 5082"},
    {"code": "5074", "title": "Staple Minus Tee 5cm 5074"},
    {"code": "3009", "title": "Kids Barnard Tank 3009"},
    {"code": "3010", "title": "Youth Barnard Tank 3010"},
    {"code": "4080", "title": "WOS Heavy Tee 4080"},
    {"code": "4026", "title": "WOS Classic Tee 4026"},
    {"code": "1117", "title": "Bucket Hat 1117"},
    {"code": "4145", "title": "WOS Heavy Crew 4145"},
    {"code": "5026g", "title": "Classic Organic Tee 5026g"},
    {"code": "5102g", "title": "Organic Stencil Hood 5102g"},
    {"code": "5071", "title": "Classic L S Tee 5071"},
    {"code": "5073", "title": "Classic Tank 5073"},
    {"code": "5076", "title": "Staple Curve Tee 5076"},
    {"code": "5070", "title": "Classic Plus Tee 5cm 5070"},
    {"code": "5079", "title": "Classic Minus Tee 5cm 5079"},
    {"code": "4024", "title": "WOS Maple Curve Tee 4024"},
    {"code": "4023", "title": "WOS Maple V Neck Tee 4023"},
    {"code": "4017", "title": "WOS Maple Tank 4017"},
    {"code": "4019", "title": "WOS Maple Scoop Tee 4019"},
    {"code": "5065", "title": "Staple Faded Tee 5065"},
    {"code": "3037", "title": "Youth Relax Hood 3037"},
    {"code": "3036", "title": "Kids Relax Hood 3036"},
    {"code": "rib-singlet", "title": "Mens Organic Rib Singlet"},
    {"code": "4063g", "title": "WOS Organic Rib Tank 4063g"},
    {"code": "4064g", "title": "WOS Organic Rib Crop Tank 4064g"},
    {"code": "5103", "title": "Stencil Crew 5103"},
    {"code": "5069", "title": "Classic Oversized Tee 5069"},
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
    
    query = """
    query getProductInventory($searchTerm: String!) {
      site {
        search {
          searchProducts(filters: { searchTerm: $searchTerm }) {
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
            return []

        data = response.json()
        
        if "errors" in data:
            print(f"GraphQL error returned for {style_code}: {data['errors']}")
            return []

        products = data.get("data", {}).get("site", {}).get("search", {}).get("searchProducts", {}).get("products", {}).get("edges", [])
        
        if not products:
            print(f"No products found for style code {style_code}")
            return []

        product_node = products[0]["node"]
        variants_data = []

        for edge in product_node.get("variants", {}).get("edges", []):
            v_node = edge["node"]
            
            inv = v_node.get("inventory", {})
            stock_qty = 0
            if inv.get("aggregated") and inv["aggregated"].get("availableToSell") is not None:
                stock_qty = int(inv["aggregated"]["availableToSell"])
            elif inv.get("isInStock"):
                stock_qty = 100

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
    
    # Counter to track total alerts sent today
    alerts_sent_today = 0
    
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
                    alerts_sent_today += 1
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
                    # Item restocked
                    send_alert(f"✅ **RESTOCK ALERT**: {title} (`{variant_id}`) - **{colour} / {size}** restocked to **{stock}** units!")
                    alerts_sent_today += 1
                    supabase.table("inventory_states").delete().eq("variant_id", variant_id).execute()
                    print(f"RESTOCK: {variant_id} removed from watchlist ({stock} units).")

    # Send daily summary if no alerts were triggered
    if alerts_sent_today == 0:
        today_date = datetime.now().strftime("%Y-%m-%d")
        send_alert(f"ℹ️ **DAILY STATUS**: There are no stock updates for today ({today_date}). All monitored items are normal.")
        print(f"No stock updates sent today ({today_date}). Summary message delivered.")

    print("Inventory processing complete.")

if __name__ == "__main__":
    process_inventory()
