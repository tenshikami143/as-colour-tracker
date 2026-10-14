import os
import random
import base64
import json
import requests
from datetime import datetime, timezone
from supabase import create_client, Client

# Initialize Supabase credentials
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# AS Colour Australia GraphQL API credentials
GRAPHQL_URL = "https://www.ascolour.com.au/graphql"
raw_token = os.environ.get("ASCOLOUR_GRAPHQL_TOKEN") or os.environ.get("AS_COLOUR_API_TOKEN")

if raw_token and raw_token.lower().startswith("bearer "):
    API_TOKEN = raw_token.split(" ", 1)[1].strip()
else:
    API_TOKEN = raw_token

# Discord Webhook
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL")

# Monitored Scope (73 Target Styles)
TARGET_STYLES = [
    "1000", "1001", "1002", "1117", "3001", "3003", "3005", "3006", "3009", "3010",
    "3030", "3031", "3032", "3033", "3034", "3035", "3036", "3037", "4001", "4001g",
    "4008", "4017", "4019", "4023", "4024", "4026", "4053", "4062", "4063g", "4064g",
    "4080", "4100", "4101", "4145", "4146", "4160", "4161", "4402", "5001", "5001g",
    "5001v", "5007", "5012", "5020", "5025", "5026", "5026g", "5040", "5053", "5065",
    "5069", "5070", "5071", "5073", "5074", "5076", "5079", "5080", "5082", "5100",
    "5101", "5102", "5102g", "5103", "5104", "5125", "5145", "5146", "5148", "5160",
    "5161", "5162", "5402"
]

# Translation dictionaries for cleaner readability
COLOUR_MAP = {
    "ARC_B": "Arctic Blue",
    "ARMY": "Army",
    "ASH": "Ash",
    "ASP_M": "Asphalt Marle",
    "BBGUM": "Bubblegum",
    "BL/WH": "Black/White",
    "BLACK": "Black",
    "BLA_A": "Black Acid",
    "BLU_A": "Blue Acid",
    "BONE": "Bone",
    "BRI_R": "Bright Royal",
    "BURGU": "Burgundy",
    "BUTTR": "Butter",
    "CARDI": "Cardinal",
    "CAR_B": "Carolina Blue",
    "CHARC": "Charcoal",
    "CHA_P": "Charcoal",
    "COAL": "Coal",
    "COBLT": "Cobalt",
    "COPPR": "Copper",
    "CREAM": "Cream",
    "ECRU": "Ecru",
    "EMRLD": "Emerald",
    "EUCAL": "Eucalyptus",
    "FORES": "Forest Green",
    "F_ARM": "Faded Army",
    "F_BLK": "Faded Black",
    "F_BLU": "Faded Blue",
    "F_DUS": "Faded Dust",
    "F_MUS": "Faded Mustard",
    "F_ROS": "Faded Rose",
    "F_SLT": "Faded Slate",
    "GOLD": "Gold",
    "GRE_M": "Grey Marle",
    "GR_AS": "Grey Acid",
    "H_PNK": "Hot Pink",
    "KEL_G": "Kelly Green",
    "KHAKI": "Khaki",
    "LAGON": "Lagoon",
    "MID_B": "Mid Blue",
    "MOSS": "Moss Green",
    "NAT_R": "Natural",
    "NAVY": "Navy",
    "ORCHD": "Orchid",
    "ORCHI": "Orchid",
    "PETRL": "Petrol Blue",
    "PINE": "Pine Green",
    "PINK": "Pink",
    "POWDR": "Powder",
    "P_BLU": "Powder Blue",
    "P_PNK": "Powder Pink",
    "RED": "Red",
    "SAGE": "Sage",
    "SNSET": "Sunset",
    "Snset": "Sunset",
    "STORM": "Storm",
    "WALNT": "Walnut",
    "WH/RE": "White/Red",
    "WHITE": "White
