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
    style_code = product_url.rstrip("/").split("-")[-1]

    # Search scripts for detailed variant stock matrix with numeric inventory
    scripts = soup.find_all("script")
    inventory_matrix = []

    for script in scripts:
        if not script.string:
            continue
        
        # 1. Look for embedded stock grid data (AS Colour matrix object)
        if "stockMatrix" in script.string or "variantStock" in script.string:
            match = re.search(r'stockData\s*=\s*(\[\{.*?\}\]);', script.string, re.DOTALL)
            if match:
                try:
                    inventory_matrix = json.loads(match.group(1))
                    break
                except Exception:
                    pass

    # 2. Extract DOM grid if JSON matrix isn't present
    # Extract color swatches and size options directly
    colors = []
    color_swatches = soup.select(".form-option-swatch span, [data-product-attribute='swatch'] .form-option-variant, [data-product-attribute='swatch'] label")
    for swatch in color_swatches:
        title = swatch.get("title") or swatch.text.strip()
        if title and title not in colors and "Selected" not in title:
            colors.append(title)

    sizes = []
    for select in soup.find_all("select"):
        for opt in select.find_all("option"):
            text = opt.text.strip()
            if text and text not in sizes and not text.startswith("Choose") and not text.startswith("Select"):
                sizes.append(text)

    if not sizes:
        size_options = soup.select("[data-product-attribute='set'] label, .form-type-set .form-option, .form-option-variant")
        for opt in size_options:
            text = opt.text.strip()
            if text and text not in sizes and not text.startswith("Selected") and text not in colors:
                sizes.append(text)

    # 3. Check BCData for fallback stock states
    bc_data = None
    for script in scripts:
        if script.string and "var BCData =" in script.string:
            match = re.search(r'BCData\s*=\s*(\{.*?\});', script.string, re.DOTALL)
            if match:
                try:
                    bc_data = json.loads(match.group(1))
                    break
                except Exception:
                    pass

    variants = []

    if inventory_matrix:
        # Use exact numeric quantities from AS Colour's stock matrix
        for item in inventory_matrix:
            c_name = item.get("color") or item.get("colour")
            s_name = item.get("size")
            qty = int(item.get("qty") or item.get("stock") or 0)
            
            clean_c = re.sub(r'[^A-Za-z0-9]', '', str(c_name)).upper()
            clean_s = re.sub(r'[^A-Za-z0-9]', '', str(s_name)).upper()
            
            variants.append({
                "id": f"{style_code}-{clean_c}-{clean_s}",
                "colour": c_name,
                "size": s_name,
                "stock": qty
            })
    elif bc_data and "product_attributes" in bc_data:
        # Check if stock quantities are passed inside stock_level attribute
        stock_levels = bc_data.get("product_attributes", {}).get("stock_levels", {})
        v_ids = bc_data.get("product_attributes", {}).get("available_variant_values", [])
        in_stock_list = bc_data.get("product_attributes", {}).get("in_stock_attributes", [])

        for idx, vid in enumerate(v_ids):
            colour_name = colors[(idx // len(sizes)) % len(colors)] if colors else "DEFAULT"
            size_name = sizes[idx % len(sizes)] if sizes else "STD"

            clean_c = re.sub(r'[^A-Za-z0-9]', '', colour_name).upper()
            clean_s = re.sub(r'[^A-Za-z0-9]', '', size_name).upper()
            variant_id = f"{style_code}-{clean_c}-{clean_s}"

            # Extract exact integer if present, or fallback to high baseline when in stock
            if str(vid) in stock_levels:
                stock_count = int(stock_levels[str(vid)])
            else:
                # Set default high stock (100) if in stock, or 0 if out of stock
                stock_count = 100 if vid in in_stock_list else 0

            variants.append({
                "id": variant_id,
                "colour": colour_name,
                "size": size_name,
                "stock": stock_count
            })

    # Evaluate variants against the real <= 25 rule
    for v in variants:
        variant_id = v["id"]
        colour = v["colour"]
        size = v["size"]
        stock = v["stock"]

        # Real low stock threshold evaluation
        is_low_stock = (stock <= 25)

        res = supabase.table("inventory_states").select("*").eq("variant_id", variant_id).execute()
        existing_records = res.data

        if is_low_stock:
            if not existing_records:
                supabase.table("inventory_states").insert({
                    "variant_id": variant_id,
                    "colour_name": colour,
                    "size_name": size,
                    "stock_count": stock,
                    "is_below_25": True
                }).execute()
                send_alert(f"⚠️ **LOW STOCK ALERT**: {product_title} (`{variant_id}`) - {colour} / {size} has only **{stock}** units left!")
            else:
                supabase.table("inventory_states").update({
                    "stock_count": stock,
                    "is_below_25": True,
                    "updated_at": "now()"
                }).eq("variant_id", variant_id).execute()
        else:
            if existing_records:
                send_alert(f"✅ **RESTOCK ALERT**: {product_title} (`{variant_id}`) - {colour} / {size} restocked to **{stock}** units!")
                supabase.table("inventory_states").delete().eq("variant_id", variant_id).execute()

if __name__ == "__main__":
    for url in PRODUCT_URLS:
        process_product(url)
