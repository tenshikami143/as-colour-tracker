import os
import requests
import json
import re
from bs4 import BeautifulSoup
from supabase import create_client, Client

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")

# Add as many product URLs here as you need
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
    
    # Extract style code from URL (e.g., '4001')
    style_code = product_url.rstrip("/").split("-")[-1]

    # 1. Extract color options
    colors = []
    color_swatches = soup.select(".form-option-swatch span, [data-product-attribute='swatch'] .form-option-variant, [data-product-attribute='swatch'] label")
    for swatch in color_swatches:
        title = swatch.get("title") or swatch.text.strip()
        if title and title not in colors and "Selected" not in title:
            colors.append(title)

    # 2. Extract size options
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

    if not colors:
        colors = ["DEFAULT"]
    if not sizes:
        sizes = ["STD"]

    # 3. Extract BCData stock states
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

    if not bc_data or "product_attributes" not in bc_data:
        return

    v_ids = bc_data.get("product_attributes", {}).get("available_variant_values", [])
    in_stock_list = bc_data.get("product_attributes", {}).get("in_stock_attributes", [])
    
    for idx, vid in enumerate(v_ids):
        colour_name = colors[(idx // len(sizes)) % len(colors)]
        size_name = sizes[idx % len(sizes)]

        # Build clean SKU format
        clean_color_code = re.sub(r'[^A-Za-z0-9]', '', colour_name).upper()
        clean_size_code = re.sub(r'[^A-Za-z0-9]', '', size_name).upper()
        variant_id = f"{style_code}-{clean_color_code}-{clean_size_code}"

        # Stock evaluation (Note: Webfront binary availability maps 0 = out/low stock, 1 = available)
        # If AS Colour provides exact quantity numbers in your region's response, stock_count will evaluate it directly.
        stock_count = 1 if vid in in_stock_list else 0
        is_low_stock = (stock_count <= 25) if stock_count > 1 else (stock_count == 0)

        # Check existing watchlist state in Supabase
        res = supabase.table("inventory_states").select("*").eq("variant_id", variant_id).execute()
        existing_records = res.data

        if is_low_stock:
            # Item is at or below threshold (or out of stock)
            if not existing_records:
                # Newly dropped into low stock watchlist
                supabase.table("inventory_states").insert({
                    "variant_id": variant_id,
                    "colour_name": colour_name,
                    "size_name": size_name,
                    "stock_count": stock_count,
                    "is_below_25": True
                }).execute()
                send_alert(f"⚠️ **LOW STOCK ALERT**: {product_title} (`{variant_id}`) - {colour_name} / {size_name} is below threshold!")
            else:
                # Update existing record on watchlist
                supabase.table("inventory_states").update({
                    "stock_count": stock_count,
                    "is_below_25": True,
                    "updated_at": "now()"
                }).eq("variant_id", variant_id).execute()
        else:
            # Item stock is healthy (>25)
            if existing_records:
                # Item was previously on the low-stock watchlist, but has now been restocked!
                send_alert(f"✅ **RESTOCK ALERT**: {product_title} (`{variant_id}`) - {colour_name} / {size_name} has been restocked back above 25!")
                # Remove from low stock table to keep database lightweight
                supabase.table("inventory_states").delete().eq("variant_id", variant_id).execute()

if __name__ == "__main__":
    for url in PRODUCT_URLS:
        process_product(url)
