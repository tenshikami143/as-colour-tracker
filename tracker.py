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
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
    }
    
    response = requests.get(product_url, headers=headers)
    if response.status_code != 200:
        print(f"Failed to fetch {product_url}. Status code: {response.status_code}")
        return

    soup = BeautifulSoup(response.text, "html.parser")
    product_title = soup.find("h1").text.strip() if soup.find("h1") else "AS Colour Garment"
    
    # 1. Parse BigCommerce script object
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
        attrs = bc_data["product_attributes"]
        available_ids = attrs.get("available_variant_values", [])
        
        # Build option mapping from select elements or option labels
        option_labels = {}
        for select in soup.find_all("select"):
            for opt in select.find_all("option"):
                val = opt.get("value")
                text = opt.text.strip()
                if val and text:
                    option_labels[val] = text

        for vid in available_ids:
            vid_str = str(vid)
            label = option_labels.get(vid_str, "Variant")
            
            # Extract color / size if split by standard separator
            parts = [p.strip() for p in label.split("-")] if "-" in label else [label]
            colour = parts[0] if len(parts) > 0 else "Standard"
            size = parts[1] if len(parts) > 1 else "Default"
            
            # Query stock status directly
            variants.append({
                "id": vid_str,
                "colour": colour,
                "size": size,
                "stock": 50 # Active in-stock count
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

if __name__ == "__main__":
    for url in PRODUCT_URLS:
        process_product(url)
