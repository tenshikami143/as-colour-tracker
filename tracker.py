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
        "X-Requested-With": "XMLHttpRequest"
    }
    
    response = requests.get(product_url, headers=headers)
    if response.status_code != 200:
        print(f"Failed to fetch {product_url}. Status code: {response.status_code}")
        return

    soup = BeautifulSoup(response.text, "html.parser")
    product_title = soup.find("h1").text.strip() if soup.find("h1") else "AS Colour Garment"
    
    # 1. Extract Product ID from BCData or meta tags
    product_id = None
    meta_pid = soup.find("meta", {"property": "og:product:id"}) or soup.find("input", {"name": "product_id"})
    if meta_pid:
        product_id = meta_pid.get("content") or meta_pid.get("value")

    if not product_id:
        scripts = soup.find_all("script")
        for script in scripts:
            if script.string and "var BCData =" in script.string:
                match = re.search(r'product_id\s*:\s*(\d+)', script.string)
                if match:
                    product_id = match.group(1)
                    break

    variants = []

    # 2. Fetch full variant attribute matrix from BigCommerce internal API
    if product_id:
        ajax_url = f"https://www.ascolour.com.au/remote/v1/product-attributes/{product_id}"
        ajax_resp = requests.post(
            ajax_url, 
            headers=headers,
            data={"action": "add", "product_id": product_id}
        )
        
        if ajax_resp.status_code == 200:
            try:
                data = ajax_resp.json()
                details = data.get("data", {})
                
                # Check for attributes/variants array
                if "attributes" in details:
                    for attr in details["attributes"]:
                        vid = str(attr.get("id", ""))
                        label = attr.get("label", "Variant")
                        stock = int(attr.get("stock", 0) or 0)
                        
                        parts = [p.strip() for p in label.split("-")] if "-" in label else [label]
                        colour = parts[0] if len(parts) > 0 else "Standard"
                        size = parts[1] if len(parts) > 1 else "Default"
                        
                        variants.append({
                            "id": vid,
                            "colour": colour,
                            "size": size,
                            "stock": stock
                        })
            except Exception as e:
                print(f"AJAX JSON parse error for product {product_id}: {e}")

    # Fallback to BCData variant parsing if AJAX doesn't yield variants
    if not variants:
        scripts = soup.find_all("script")
        for script in scripts:
            if script.string and "var BCData =" in script.string:
                match = re.search(r'BCData\s*=\s*(\{.*?\});', script.string, re.DOTALL)
                if match:
                    try:
                        bc_data = json.loads(match.group(1))
                        v_ids = bc_data.get("product_attributes", {}).get("available_variant_values", [])
                        for vid in v_ids:
                            variants.append({
                                "id": str(vid),
                                "colour": "Standard",
                                "size": "Default",
                                "stock": 30
                            })
                    except Exception as e:
                        print(f"BCData error: {e}")

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
