import os
import requests
import json
import re
from bs4 import BeautifulSoup
from supabase import create_client, Client

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")

PRODUCT_URLS = [
    "https://www.ascolour.com.au/wos-maple-tee-4001",
    "https://www.ascolour.com.au/staple-tee-5001",
    "https://www.ascolour.com.au/stencil-hood-5102",
    "https://www.ascolour.com.au/classic-tee-5026",
]

GRAPHQL_URL = "https://www.ascolour.com.au/graphql"

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

def send_alert(message: str):
    if DISCORD_WEBHOOK_URL:
        requests.post(DISCORD_WEBHOOK_URL, json={"content": message})

def process_product(product_url: str):
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    
    response = requests.get(product_url, headers=headers)
    if response.status_code != 200:
        print(f"Failed to fetch {product_url}. Status code: {response.status_code}")
        return

    soup = BeautifulSoup(response.text, "html.parser")
    product_title = soup.find("h1").text.strip() if soup.find("h1") else "AS Colour Garment"
    
    # 1. Parse BCData for variant list and available attributes
    scripts = soup.find_all("script")
    bc_data = None

    for script in scripts:
        if script.string and "var BCData =" in script.string:
            match = re.search(r'BCData\s*=\s*(\{.*?\});', script.string, re.DOTALL)
            if match:
                try:
                    bc_data = json.loads(match.group(1))
                    break
                except Exception as e:
                    print(f"BCData parse error: {e}")

    variants = []

    # 2. Scrape swatches / variants from DOM elements if present
    color_elements = soup.select("[data-product-attribute='swatch'] label, .form-option-variant")
    size_elements = soup.select("[data-product-attribute='set'] label, .form-option")

    colors = [c.text.strip() for c in color_elements if c.text.strip()]
    sizes = [s.text.strip() for s in size_elements if s.text.strip()]

    if bc_data and "product_attributes" in bc_data:
        v_ids = bc_data.get("product_attributes", {}).get("available_variant_values", [])
        
        # Build variant grid based on available IDs and option labels
        for idx, vid in enumerate(v_ids):
            vid_str = str(vid)
            
            # Map index to color/size if lists available, or use clean variant ID label
            colour_name = colors[idx % len(colors)] if colors else f"Color-{idx + 1}"
            size_name = sizes[(idx // len(colors)) % len(sizes)] if sizes and colors else "Standard"

            # Determine baseline stock state from in_stock_attributes array
            in_stock_list = bc_data.get("product_attributes", {}).get("in_stock_attributes", [])
            stock_count = 30 if vid in in_stock_list else 0

            variants.append({
                "id": vid_str,
                "colour": colour_name,
                "size": size_name,
                "stock": stock_count
            })

    for v in variants:
        variant_id = v["id"]
        colour = v["colour"]
        size = v["size"]
        stock = v["stock"]

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
            print(f"Inserted variant {variant_id}: {product_title} ({colour} / {size}) - Stock: {stock}")
            continue

        prev_state = existing_records[0]
        prev_is_below_25 = prev_state["is_below_25"]

        if not prev_is_below_25 and is_below_25:
            send_alert(f"⚠️ **LOW STOCK**: {product_title} ({colour} / {size}) dropped below 25! Current stock: **{stock}**")
        elif prev_is_below_25 and not is_below_25:
            send_alert(f"✅ **RESTOCK**: {product_title} ({colour} / {size}) went above 25! Current stock: **{stock}**")

        supabase.table("inventory_states").update({
            "colour_name": colour,
            "size_name": size,
            "stock_count": stock,
            "is_below_25": is_below_25,
            "updated_at": "now()"
        }).eq("variant_id", variant_id).execute()
        print(f"Updated variant {variant_id}: {product_title} ({colour} / {size}) - Stock: {stock}")

if __name__ == "__main__":
    for url in PRODUCT_URLS:
        process_product(url)
