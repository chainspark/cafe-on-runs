"""Places left out of the cafe dataset: big chains / franchises, and places tagged cafe that aren't.

Matched against the OSM name *and* brand tags after normalising (lower case, accents and
punctuation stripped). A pattern matches as a phrase inside the name; a pattern starting with
"=" must match the whole name (for short, ambiguous names). Small local multi-location roasters
(Jimmy's, Dark Horse, Pilot, Balzac's, Sam James, ...) are deliberately kept.
"""
from __future__ import annotations

import re
import unicodedata

CHAINS = {
    # coffee / donut chains and franchises
    "Tim Hortons": ["tim hortons", "tim horton", "=tims"],
    "Starbucks": ["starbucks"],
    "McDonald's / McCafé": ["mcdonald", "mccafe"],
    "Second Cup": ["second cup"],
    "Coffee Time": ["coffee time"],
    "Country Style": ["country style"],
    "Timothy's": ["timothy s", "timothys"],
    "Aroma Espresso Bar": ["aroma espresso"],
    "Pret A Manger": ["pret a manger", "=pret"],
    "% Arabica": ["=arabica", "=arabica coffee"],
    "Cotti Coffee": ["cotti coffee"],
    "Mofer Coffee": ["mofer coffee", "=mofer"],
    "Café Landwer": ["landwer"],
    "Costa Coffee": ["costa coffee"],
    "Dunkin'": ["dunkin"],
    "Krispy Kreme": ["krispy kreme"],
    "Blenz": ["blenz"],
    "Coffee Culture": ["coffee culture"],
    "DAVIDsTEA": ["davidstea", "davids tea", "david s tea"],
    "Luckin Coffee": ["luckin"],
    "Café Dépôt": ["cafe depot"],
    "The Coffee": ["=the coffee"],
    "Chaiiwala": ["chaiiwala"],
    # bubble-tea franchises
    "Real Fruit Bubble Tea": ["real fruit bubble tea"],
    "CoCo Fresh Tea & Juice": ["coco fresh"],
    "Chatime": ["chatime"],
    "Gong Cha": ["gong cha"],
    "Kung Fu Tea": ["kung fu tea"],
    "Presotea": ["presotea"],
    "The Alley": ["=the alley"],
    "Chicha San Chen": ["chicha san chen"],
    "Palgong Tea": ["palgong"],
    "Royaltea": ["royaltea"],
    "Sharetea": ["sharetea"],
    "Tiger Sugar": ["tiger sugar"],
    "Xing Fu Tang": ["xing fu tang"],
    "Machi Machi": ["machi machi"],
    "Yi Fang Tea": ["yi fang"],
    "Happy Lemon": ["happy lemon"],
    "Shuyi Tealicious": ["shuyi"],
    "Heytea": ["heytea"],
    "Macu": ["=macu", "macu tea"],
    "Cha Payom": ["cha payom"],
}

NOT_CAFES = {
    "Tokyo Smoke": ["tokyo smoke"],  # cannabis retailer, sometimes tagged amenity=cafe
}


def normalise(s: str | None) -> str:
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


def _matches(text: str, patterns: list[str]) -> bool:
    t = normalise(text)
    if not t:
        return False
    for p in patterns:
        if p.startswith("="):
            if t == p[1:]:
                return True
        elif re.search(rf"(^| ){re.escape(p)}( |$)", t):
            return True
    return False


def excluded(name: str | None, brand: str | None = None) -> tuple[str, str] | None:
    """(category, label) if this place should be left out, else None."""
    for category, table in (("chain", CHAINS), ("not a cafe", NOT_CAFES)):
        for label, patterns in table.items():
            if _matches(name, patterns) or _matches(brand, patterns):
                return category, label
    return None
