import os
from supabase import create_client, Client

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

def scan_supabase_variants():
    print("Fetching all records from Supabase...")
    # Fetch variant IDs from Supabase (handling pagination if needed, or pulling in chunks)
    response = supabase.table("inventory_states").select("variant_id, colour_name, size_name").execute()
    
    if not response.data:
        print("No data found in Supabase inventory_states.")
        return

    unique_colours = set()
    unique_sizes = set()
    raw_color_codes = set()
    raw_size_codes = set()

    for item in response.data:
        variant_id = item.get("variant_id", "")
        parts = variant_id.split("-")
        if len(parts) >= 3:
            raw_color_codes.add(parts[1])
            raw_size_codes.add("-".join(parts[2:]))
        
        unique_colours.add(item.get("colour_name"))
        unique_sizes.add(item.get("size_name"))

    print("\n--- UNIQUE RAW COLOR CODES FOUND ---")
    for c in sorted(raw_color_codes):
        print(f"  {c}")

    print("\n--- UNIQUE RAW SIZE CODES FOUND ---")
    for s in sorted(raw_size_codes):
        print(f"  {s}")

if __name__ == "__main__":
    scan_supabase_variants()
