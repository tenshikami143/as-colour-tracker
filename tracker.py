import os
import requests
from bs4 import BeautifulSoup

PRODUCT_URLS = [
    "https://www.ascolour.com.au/wos-maple-tee-4001",
]

def inspect_product(product_url: str):
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    
    response = requests.get(product_url, headers=headers)
    soup = BeautifulSoup(response.text, "html.parser")
    
    print("--- INVENTORIES & OPTIONS DUMP ---")
    
    # 1. Look for form inputs / option selectors
    selects = soup.find_all("select")
    for s in selects:
        print(f"Select Name/ID: {s.get('name')} | {s.get('id')}")
        for opt in s.find_all("option"):
            print(f"  Option: {opt.text.strip()} | Value: {opt.get('value')}")
            
    # 2. Look for any embedded JSON or variant scripts
    scripts = soup.find_all("script")
    for idx, script in enumerate(scripts):
        if script.string and ("product" in script.string.lower() or "variant" in script.string.lower()):
            if len(script.string) < 2000:
                print(f"\n--- Script {idx} Snippet ---")
                print(script.string[:500])

if __name__ == "__main__":
    inspect_product(PRODUCT_URLS[0])
