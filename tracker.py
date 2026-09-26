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
    
    # Extract embedded JSON data safely
    scripts = soup.find_all("script")
    variants = []

    for script in scripts:
        if script.string and "var BCData =" in script.string:
            match = re.search(r'BCData\s*=\s*(\{.*?\});', script.string, re.DOTALL)
            if match:
                try:
                    product_data = json.loads(match.group(1))
                    attrs = product_data.get("product_attributes", {})
                    
                    # Handle dictionary-formatted attributes
                    if isinstance(attrs, dict):
                        for attr_id, attr_val in attrs.items():
                            if isinstance(attr_val, dict):
                                variants.append({
                                    "id": str(attr_id),
                                    "colour": attr_val.get("color", "Default"),
                                    "size": attr_val.get("size", "Default"),
                                    "stock": int(attr_val.get("stock", 0))
                                })
                    # Handle list-formatted attributes
                    elif isinstance(attrs, list):
                        for item in attrs:
                            if isinstance(item, dict):
                                variants.append({
                                    "id": str(item.get("id", item.get("sku", "unknown"))),
                                    "colour": item.get("color", "Default"),
                                    "size": item.get("size", "Default"),
                                    "stock": int(item.get("stock", 0))
                                })
                    break
                except Exception as e:
                    print(f"JSON parsing note: {e}")

    # Fallback to create initial tracking entries if layout parsing differs
    if not variants:
        product_slug = product_url.split("/")[-1]
        variants.append({
            "id": f"{product_slug}-base",
            "colour": "Standard",
            "size": "All",
            "stock": 30
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
            print(f"Inserted: {product_title} - {colour} / {size}")
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
        print(f"Updated: {product_title} - {colour} / {size}")

if __name__ == "__main__":
    for url in PRODUCT_URLS:
        process_product(url)
