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
    
    # 1. Extract color options
    colors = []
    color_swatches = soup.select(".form-option-swatch span, [data-product-attribute='swatch'] .form-option-variant")
    for swatch in color_swatches:
        title = swatch.get("title") or swatch.text.strip()
        if title and title not in colors:
            colors.append(title)

    # 2. Extract size options
    sizes = []
    size_options = soup.select("[data-product-attribute='set'] label, .form-type-set .form-option")
    for opt in size_options:
        text = opt.text.strip()
        if text and text not in sizes and not text.startswith("Selected"):
            sizes.append(text)

    # Fallbacks if specific selectors change
    if not colors:
        colors = ["Default"]
    if not sizes:
        sizes = ["Standard"]

    # 3. Extract BCData variant IDs and stock states
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
                    print(f"BCData error: {e}")

    variants = []

    if bc_data and "product_attributes" in bc_data:
        v_ids = bc_data.get("product_attributes", {}).get("available_variant_values", [])
        in_stock_list = bc_data.get("product_attributes", {}).get("in_stock_attributes", [])
        
        for idx, vid in enumerate(v_ids):
            vid_str = str(vid)
            
            # Precise grid indexing
            colour_name = colors[(idx // len(sizes)) % len(colors)]
            size_name = sizes[idx % len(sizes)]

            # Binary stock: 1 = In Stock, 0 = Out of Stock
            stock_count = 1 if vid in in_stock_list else 0

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

        # Alert if stock drops to 0 (Out of Stock)
        is_below_25 = (stock == 0)

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
            send_alert(f"⚠️ **OUT OF STOCK**: {product_title} ({colour} / {size}) is now out of stock!")
        elif prev_is_below_25 and not is_below_25:
            send_alert(f"✅ **RESTOCK**: {product_title} ({colour} / {size}) is back in stock!")

        supabase.table("inventory_states").update({
            "colour_name": colour,
            "size_name": size,
            "stock_count": stock,
            "is_below_25": is_below_25,
            "updated_at": "now()"
        }).eq("variant_id", variant_id).execute()

if __name__ == "__main__":
    for url in PRODUCT_URLS:
        process_product(url)
