import os
import requests
from supabase import create_client, Client

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")

# --- ADD YOUR AS COLOUR PRODUCT URLS HERE ---
PRODUCT_URLS = [
    "https://www.ascolour.com.au/wos-maple-tee-4001.json",
    "https://www.ascolour.com.au/staple-tee-5001.json",
    "https://www.ascolour.com.au/stencil-hood-5102.json",
    "https://www.ascolour.com.au/classic-tee-5026.json",
]

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

def send_alert(message: str):
    if DISCORD_WEBHOOK_URL:
        requests.post(DISCORD_WEBHOOK_URL, json={"content": message})

def process_product(product_url: str):
    # Standard Chrome browser user-agent to bypass basic scraping blocks
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "application/json"
    }
    
    response = requests.get(product_url, headers=headers)
    if response.status_code != 200:
        print(f"Failed to fetch {product_url}. Status code: {response.status_code}")
        return

    data = response.json()
    
    product_title = data.get("product", {}).get("title") if "product" in data else data.get("title", "Product")
    variants = data.get("product", {}).get("variants", []) if "product" in data else data.get("variants", [])

    for variant in variants:
        variant_id = str(variant.get("id"))
        colour = variant.get("option1", "Unknown")
        size = variant.get("option2", "Unknown")
        stock = int(variant.get("inventory_quantity", 0))

        is_below_25 = stock < 25

        res = supabase.table("inventory_states").select("*").eq("variant_id", variant_id).execute()
        existing_records = res.data

        if not existing_records:
            supabase.table("inventory_states").insert({
                "variant_id": variant_id,
                "colour_name": colour,
                "size_name": size,
                "stock_count": stock,
                "is_below_25": is_below_25
            }).execute()
            continue

        prev_state = existing_records[0]
        prev_is_below_25 = prev_state["is_below_25"]

        if not prev_is_below_25 and is_below_25:
            send_alert(f"⚠️ **LOW STOCK**: {product_title} ({colour} / {size}) dropped below 25! Current stock: **{stock}**")
        elif prev_is_below_25 and not is_below_25:
            send_alert(f"✅ **RESTOCK**: {product_title} ({colour} / {size}) went above 25! Current stock: **{stock}**")

        supabase.table("inventory_states").update({
            "stock_count": stock,
            "is_below_25": is_below_25,
            "updated_at": "now()"
        }).eq("variant_id", variant_id).execute()

def process_all_inventory():
    for url in PRODUCT_URLS:
        process_product(url)

if __name__ == "__main__":
    process_all_inventory()
