import os
import requests
import json
import re
from bs4 import BeautifulSoup

PRODUCT_URLS = [
    "https://www.ascolour.com.au/wos-maple-tee-4001",
]

def inspect_options(product_url: str):
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    
    response = requests.get(product_url, headers=headers)
    soup = BeautifulSoup(response.text, "html.parser")
    
    scripts = soup.find_all("script")
    for idx, script in enumerate(scripts):
        if script.string and ("BCData" in script.string or "productAttributes" in script.string):
            print(f"--- Script {idx} Raw Data ---")
            print(script.string[:1500])

if __name__ == "__main__":
    inspect_options(PRODUCT_URLS[0])
