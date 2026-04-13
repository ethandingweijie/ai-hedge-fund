"""
Curated SGX stock universe — STI 30 + Mid Caps + Major REITs.

This replaces the API-driven universe source used for US (FMP company-screener)
and HK (AKShare stock_hk_famous_spot_em). Since no free API provides a comprehensive
SGX stock list, we curate the investable universe manually.

Update frequency: Semi-annually or when STI components change.
"""

SGX_UNIVERSE: list[dict] = [
    # ═══════════════════════════════════════════════════════════════════════
    # STI 30 Components (as of April 2026)
    # ═══════════════════════════════════════════════════════════════════════
    {"code": "D05",  "name": "DBS Group Holdings",              "sector": "Financials",   "industry": "Banks"},
    {"code": "O39",  "name": "OCBC Bank",                       "sector": "Financials",   "industry": "Banks"},
    {"code": "U11",  "name": "UOB",                             "sector": "Financials",   "industry": "Banks"},
    {"code": "Z74",  "name": "SingTel",                         "sector": "Telco",        "industry": "Telecom Services"},
    {"code": "C6L",  "name": "Singapore Airlines",              "sector": "Industrials",  "industry": "Air Transport"},
    {"code": "BN4",  "name": "Keppel Corporation",              "sector": "Industrials",  "industry": "Conglomerates"},
    {"code": "F34",  "name": "Wilmar International",            "sector": "Consumer",     "industry": "Food Products"},
    {"code": "V03",  "name": "Venture Corporation",             "sector": "Tech",         "industry": "Electronics Manufacturing"},
    {"code": "BS6",  "name": "Yangzijiang Shipbuilding",        "sector": "Industrials",  "industry": "Shipbuilding"},
    {"code": "Y92",  "name": "Thai Beverage",                   "sector": "Consumer",     "industry": "Beverages"},
    {"code": "U96",  "name": "Sembcorp Industries",             "sector": "Industrials",  "industry": "Utilities & Energy"},
    {"code": "S63",  "name": "ST Engineering",                  "sector": "Industrials",  "industry": "Aerospace & Defence"},
    {"code": "S68",  "name": "Singapore Exchange",              "sector": "Financials",   "industry": "Capital Markets"},
    {"code": "G13",  "name": "Genting Singapore",               "sector": "Consumer",     "industry": "Casinos & Gaming"},
    {"code": "C09",  "name": "City Developments",               "sector": "Property",     "industry": "Real Estate Development"},
    {"code": "H78",  "name": "Hongkong Land",                   "sector": "Property",     "industry": "Real Estate Development"},
    {"code": "J36",  "name": "Jardine Matheson",                "sector": "Industrials",  "industry": "Conglomerates"},
    {"code": "J37",  "name": "Jardine Cycle & Carriage",        "sector": "Industrials",  "industry": "Conglomerates"},
    {"code": "9CI",  "name": "CapitaLand Investment",           "sector": "Financials",   "industry": "Asset Management"},
    {"code": "U14",  "name": "UOL Group",                       "sector": "Property",     "industry": "Real Estate Development"},
    {"code": "E5H",  "name": "Golden Agri-Resources",           "sector": "Consumer",     "industry": "Agricultural Products"},
    {"code": "S58",  "name": "SATS",                            "sector": "Industrials",  "industry": "Airport Services"},
    {"code": "C52",  "name": "ComfortDelGro",                   "sector": "Industrials",  "industry": "Transportation"},
    {"code": "AWX",  "name": "AEM Holdings",                    "sector": "Tech",         "industry": "Semiconductor Equipment"},
    # ═══════════════════════════════════════════════════════════════════════
    # Major REITs
    # ═══════════════════════════════════════════════════════════════════════
    {"code": "A17U", "name": "CapitaLand Ascendas REIT",        "sector": "REIT",         "industry": "Industrial REIT"},
    {"code": "C38U", "name": "CapitaLand Integrated Commercial Trust", "sector": "REIT",  "industry": "Retail REIT"},
    {"code": "N2IU", "name": "Mapletree Pan Asia Commercial Trust", "sector": "REIT",     "industry": "Commercial REIT"},
    {"code": "ME8U", "name": "Mapletree Industrial Trust",      "sector": "REIT",         "industry": "Industrial REIT"},
    {"code": "M44U", "name": "Mapletree Logistics Trust",       "sector": "REIT",         "industry": "Logistics REIT"},
    {"code": "BUOU", "name": "Frasers Logistics & Commercial Trust", "sector": "REIT",    "industry": "Logistics REIT"},
    {"code": "J69U", "name": "Frasers Centrepoint Trust",       "sector": "REIT",         "industry": "Retail REIT"},
    {"code": "T82U", "name": "Suntec REIT",                     "sector": "REIT",         "industry": "Commercial REIT"},
    {"code": "K71U", "name": "Keppel REIT",                     "sector": "REIT",         "industry": "Office REIT"},
    {"code": "AJBU", "name": "Keppel DC REIT",                  "sector": "REIT",         "industry": "Data Centre REIT"},
    {"code": "A7RU", "name": "Keppel DC REIT",                  "sector": "REIT",         "industry": "Data Centre REIT"},
    {"code": "AU8U", "name": "CapitaLand China Trust",          "sector": "REIT",         "industry": "China REIT"},
    {"code": "HMN",  "name": "CapitaLand Ascott Trust",         "sector": "REIT",         "industry": "Hospitality REIT"},
    {"code": "SK6U", "name": "Parkway Life REIT",               "sector": "REIT",         "industry": "Healthcare REIT"},
    {"code": "CWBU", "name": "NetLink NBN Trust",               "sector": "REIT",         "industry": "Infrastructure Trust"},
    {"code": "J91U", "name": "ESR-LOGOS REIT",                  "sector": "REIT",         "industry": "Industrial REIT"},
    {"code": "OXMU", "name": "CapitaLand India Trust",          "sector": "REIT",         "industry": "India REIT"},
    {"code": "CMOU", "name": "CDL Hospitality Trusts",          "sector": "REIT",         "industry": "Hospitality REIT"},
    {"code": "P40U", "name": "Starhill Global REIT",            "sector": "REIT",         "industry": "Retail REIT"},
    {"code": "Q5T",  "name": "Far East Hospitality Trust",      "sector": "REIT",         "industry": "Hospitality REIT"},
    {"code": "TS0U", "name": "OUE Commercial REIT",             "sector": "REIT",         "industry": "Commercial REIT"},
    {"code": "D8DU", "name": "Digital Core REIT",               "sector": "REIT",         "industry": "Data Centre REIT"},
    {"code": "RW0U", "name": "Cromwell European REIT",          "sector": "REIT",         "industry": "European REIT"},
    {"code": "CRPU", "name": "Sasseur REIT",                    "sector": "REIT",         "industry": "Outlet Mall REIT"},
    {"code": "JYEU", "name": "Lendlease Global Commercial REIT","sector": "REIT",         "industry": "Commercial REIT"},
    {"code": "BTOU", "name": "Manulife US REIT",                "sector": "REIT",         "industry": "US Office REIT"},
    # ═══════════════════════════════════════════════════════════════════════
    # Mid Caps & Others
    # ═══════════════════════════════════════════════════════════════════════
    {"code": "OYY",  "name": "PropNex",                         "sector": "Property",     "industry": "Real Estate Services"},
    {"code": "AGS",  "name": "Sheng Siong Group",               "sector": "Consumer",     "industry": "Grocery Retail"},
    {"code": "EB5",  "name": "First Resources",                 "sector": "Consumer",     "industry": "Palm Oil"},
    {"code": "S51",  "name": "Seatrium",                        "sector": "Industrials",  "industry": "Marine & Offshore"},
    {"code": "CC3",  "name": "StarHub",                         "sector": "Telco",        "industry": "Telecom Services"},
    {"code": "P8Z",  "name": "Bumitama Agri",                   "sector": "Consumer",     "industry": "Palm Oil"},
    {"code": "RE4",  "name": "Geo Energy Resources",            "sector": "Energy",       "industry": "Coal Mining"},
    {"code": "CLN",  "name": "Riverstone Holdings",             "sector": "Healthcare",   "industry": "Medical Gloves"},
    {"code": "5DD",  "name": "Micro-Mechanics Holdings",        "sector": "Tech",         "industry": "Semiconductor Equipment"},
    {"code": "ACV",  "name": "Vicom",                           "sector": "Industrials",  "industry": "Vehicle Inspection"},
    {"code": "MZH",  "name": "Nanofilm Technologies",           "sector": "Tech",         "industry": "Advanced Materials"},
    {"code": "BHK",  "name": "UMS Holdings",                    "sector": "Tech",         "industry": "Semiconductor Equipment"},
    {"code": "5CP",  "name": "Silverlake Axis",                 "sector": "Tech",         "industry": "Banking Software"},
    {"code": "W05",  "name": "Wing Tai Holdings",               "sector": "Property",     "industry": "Real Estate Development"},
    {"code": "G13",  "name": "Genting Singapore",               "sector": "Consumer",     "industry": "Casinos & Gaming"},
    {"code": "A50",  "name": "Thomson Medical Group",           "sector": "Healthcare",   "industry": "Healthcare Services"},
    {"code": "40T",  "name": "Centurion Corporation",           "sector": "Property",     "industry": "Workers Dormitory"},
    {"code": "MR7",  "name": "Marco Polo Marine",               "sector": "Industrials",  "industry": "Marine Services"},
    {"code": "T14",  "name": "Olam Group",                      "sector": "Consumer",     "industry": "Food & Agribusiness"},
    {"code": "S56",  "name": "Singpost",                        "sector": "Industrials",  "industry": "Postal & Logistics"},
    {"code": "U09",  "name": "United Overseas Insurance",       "sector": "Financials",   "industry": "Insurance"},
]

# Quick lookup: code → dict
_UNIVERSE_MAP: dict[str, dict] = {s["code"]: s for s in SGX_UNIVERSE}


def get_sg_universe() -> list[dict]:
    """Return the full curated SGX universe."""
    return SGX_UNIVERSE


def get_sg_stock_info(code: str) -> dict | None:
    """Lookup a single SGX stock by code. Returns None if not in universe."""
    return _UNIVERSE_MAP.get(code.strip().upper().replace(".SI", ""))


def get_sg_sectors() -> list[str]:
    """Return unique sectors in the SGX universe."""
    return sorted(set(s["sector"] for s in SGX_UNIVERSE))
