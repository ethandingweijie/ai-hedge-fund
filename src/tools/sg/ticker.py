"""
SGX ticker detection and normalisation utilities.

Supported input formats:
  "D05"      →  raw SGX code (alphanumeric 1-4 chars)
  "D05.SI"   →  yfinance format with .SI suffix
  "d05"      →  case-insensitive

Canonical form (used as cache keys / DB keys): "XXX.SI"
yfinance form: same as canonical "XXX.SI"

Detection strategy:
  1. If ticker ends with .SI → SGX
  2. If raw code (no suffix) is in the known-codes registry → SGX
  3. Otherwise → not SGX (falls through to US default)

Note: HK ticker detection (is_hk_ticker) must be checked BEFORE is_sg_ticker
because HK tickers are purely numeric and unambiguous.
"""

import re

# .SI suffix is the definitive SGX marker
_SI_PATTERN = re.compile(r"^[A-Z0-9]{1,5}\.SI$", re.IGNORECASE)

# Known SGX ticker codes — curated registry for detection without .SI suffix.
# This allows users to type "D05" instead of "D05.SI".
_SGX_KNOWN_CODES: frozenset[str] = frozenset({
    # ── STI 30 Components ───────────────────────────────────────────────
    "D05",   # DBS Group
    "O39",   # OCBC Bank
    "U11",   # UOB
    "Z74",   # SingTel
    "C6L",   # Singapore Airlines
    "BN4",   # Keppel Corporation
    "F34",   # Wilmar International
    "V03",   # Venture Corporation
    "BS6",   # Yangzijiang Shipbuilding
    "Y92",   # Thai Beverage
    "U96",   # Sembcorp Industries
    "S63",   # Singapore Technologies Engineering
    "S68",   # Singapore Exchange
    "G13",   # Genting Singapore
    "C09",   # City Developments
    "H78",   # Hongkong Land
    "J36",   # Jardine Matheson
    "J37",   # Jardine C&C (now Jardine Cycle & Carriage)
    "N2IU",  # Mapletree Pan Asia Commercial Trust
    "ME8U",  # Mapletree Industrial Trust
    "M44U",  # Mapletree Logistics Trust
    "9CI",   # CapitaLand Investment
    "U14",   # UOL Group
    "E5H",   # Golden Agri-Resources
    "AWX",   # AEM Holdings
    "S58",   # SATS
    "C52",   # ComfortDelGro
    "A7RU",  # Keppel DC REIT (STI component)
    # ── Major REITs ─────────────────────────────────────────────────────
    "A17U",  # CapitaLand Ascendas REIT
    "C38U",  # CapitaLand Integrated Commercial Trust
    "BUOU",  # Frasers Logistics & Commercial Trust
    "J69U",  # Frasers Centrepoint Trust
    "T82U",  # Suntec REIT
    "K71U",  # Keppel REIT
    "AJBU",  # Keppel DC REIT
    "AU8U",  # CapitaLand China Trust
    "HMN",   # CapitaLand Ascott Trust
    "JYEU",  # Lendlease Global Commercial REIT
    "RW0U",  # Cromwell European REIT
    "OXMU",  # CapitaLand India Trust
    "SK6U",  # Parkway Life REIT
    "CRPU",  # Sasseur REIT
    "CWBU",  # NetLink NBN Trust
    "CMOU",  # CDL Hospitality Trusts
    # ── Mid Caps ────────────────────────────────────────────────────────
    "OYY",   # PropNex
    "AGS",   # Sheng Siong Group
    "BVA",   # Top Glove Corporation
    "EB5",   # First Resources
    "S51",   # Seatrium (formerly Sembcorp Marine)
    "CC3",   # StarHub
    "P8Z",   # Bumitama Agri
    "A50",   # Thomson Medical Group
    "RE4",   # Geo Energy Resources
    "5DD",   # Micro-Mechanics Holdings
    "CLN",   # Riverstone Holdings
    "ACV",   # Vicom
    "S56",   # Singpost
    "T39",   # SPH (Singapore Press Holdings)
    "U09",   # United Overseas Insurance
    "S41",   # Hong Fok Corporation
    "CY6U",  # CapitaLand India Trust (alt code)
    "P40U",  # Starhill Global REIT
    "Q5T",   # Far East Hospitality Trust
    "J91U",  # ESR-LOGOS REIT
    "TS0U",  # OUE Commercial REIT
    "D8DU",  # Digital Core REIT
    "BTOU",  # Manulife US REIT
    "8C8U",  # Centurion Accommodation REIT (CAREIT) — listed Sep 2025
    # ── Notable Catalist / Others ───────────────────────────────────────
    "1D0",   # Koh Brothers Eco Engineering
    "MR7",   # Marco Polo Marine
    "S85",   # Straco Corporation
    "BHK",   # UMS Holdings
    "40T",   # Centurion Corporation
    "MZH",   # Nanofilm Technologies
    "5CP",   # Silverlake Axis
    "W05",   # Wing Tai Holdings
    "T14",   # Olam Group
    "F9D",   # Boustead Singapore
    "B61",   # Bukit Sembawang Estates
    "N03",   # Noel Gifts International
    "B2F",   # ThaiBev (alternate)
})


def is_sg_ticker(ticker: str) -> bool:
    """Return True if the ticker looks like an SGX stock code.

    Examples
    --------
    >>> is_sg_ticker("D05")      # DBS Group (known code)
    True
    >>> is_sg_ticker("D05.SI")   # explicit .SI suffix
    True
    >>> is_sg_ticker("AAPL")     # US stock
    False
    >>> is_sg_ticker("00700")    # HK stock (purely numeric)
    False
    """
    if not ticker:
        return False
    t = ticker.strip().upper()
    # .SI suffix is definitive
    if _SI_PATTERN.match(t):
        return True
    # Check known-codes registry (without suffix)
    raw = t.replace(".SI", "")
    return raw in _SGX_KNOWN_CODES


def to_yfinance_code(ticker: str) -> str:
    """Normalise to yfinance format: "XXX.SI".

    Examples
    --------
    >>> to_yfinance_code("D05")
    'D05.SI'
    >>> to_yfinance_code("d05.si")
    'D05.SI'
    """
    raw = ticker.strip().upper().replace(".SI", "")
    return raw + ".SI"


def to_canonical(ticker: str) -> str:
    """Return the canonical form used as cache keys and DB keys.
    Same as yfinance form for SGX: "XXX.SI".

    Examples
    --------
    >>> to_canonical("D05")
    'D05.SI'
    >>> to_canonical("d05.si")
    'D05.SI'
    """
    return to_yfinance_code(ticker)


def to_stockanalysis_code(ticker: str) -> str:
    """Return the code used in stockanalysis.com URLs: lowercase, no suffix.

    Examples
    --------
    >>> to_stockanalysis_code("D05.SI")
    'd05'
    """
    return ticker.strip().upper().replace(".SI", "").lower()
