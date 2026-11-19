"""Securities Services product (instrument reference data) UDM generator.

Each message is a full-state snapshot of one product, keyed by PRODUCT_ID and
versioned by PRODUCT_VERSION, so it can be applied as an idempotent upsert
(CDC interactive table: PRIMARY KEY ... INITIALLY DEFERRED + VERSION BY).
"""

import random
import string
from datetime import date, datetime, timedelta, timezone

SOURCE_SYSTEM = "AMPS-IDP"
TS_FMT = "%Y-%m-%d %H:%M:%S.%f"

# country, currency, MIC, place of settlement, settlement cycle
MARKETS = [
    ("US", "USD", "XNYS", "DTC", "T+1"),
    ("US", "USD", "XNAS", "DTC", "T+1"),
    ("GB", "GBP", "XLON", "CREST", "T+2"),
    ("DE", "EUR", "XETR", "CBF", "T+2"),
    ("FR", "EUR", "XPAR", "EUROCLEAR", "T+2"),
    ("NL", "EUR", "XAMS", "EUROCLEAR", "T+2"),
    ("IE", "EUR", "XDUB", "EUROCLEAR", "T+2"),
    ("JP", "JPY", "XTKS", "JASDEC", "T+2"),
    ("CA", "CAD", "XTSE", "CDS", "T+1"),
    ("AU", "AUD", "XASX", "CHESS", "T+2"),
]

ASSET_CLASSES = [
    # asset class, subclass, CFI code, weight
    ("EQUITY", "COMMON_STOCK", "ESVUFR", 40),
    ("EQUITY", "PREFERRED", "EPNXXR", 3),
    ("FIXED_INCOME", "GOVERNMENT_BOND", "DBFTFR", 15),
    ("FIXED_INCOME", "CORPORATE_BOND", "DBFUFR", 20),
    ("FIXED_INCOME", "MUNICIPAL_BOND", "DBFNFR", 4),
    ("FUND", "ETF", "CEOGEU", 10),
    ("FUND", "MUTUAL_FUND", "CIOGEU", 6),
    ("DERIVATIVE", "LISTED_OPTION", "OCASPS", 2),
]

ISSUER_STEMS = [
    "Atlas", "Meridian", "Northbridge", "Sterling", "Harbor", "Summit", "Pioneer",
    "Crescent", "Granite", "Beacon", "Evergreen", "Liberty", "Orion", "Keystone",
    "Redwood", "Silverline", "Union", "Vanguard", "Westfield", "Zenith",
]
ISSUER_SUFFIX = ["Holdings", "Group", "Industries", "Capital", "Energy", "Technologies",
                 "Financial", "Pharma", "Logistics", "Resources"]
CA_TYPES = ["DIVIDEND", "COUPON", "STOCK_SPLIT", "RIGHTS_ISSUE", "MERGER", "TENDER", "REDEMPTION"]


def _luhn_digit(digits: str) -> int:
    total = 0
    for i, d in enumerate(reversed(digits)):
        n = int(d)
        if i % 2 == 0:
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return (10 - total % 10) % 10


def isin_check_digit(body11: str) -> str:
    expanded = "".join(str(int(c, 36)) for c in body11)
    return str(_luhn_digit(expanded))


def cusip_check_digit(body8: str) -> str:
    total = 0
    for i, c in enumerate(body8):
        v = int(c) if c.isdigit() else ord(c) - 55
        if i % 2 == 1:
            v *= 2
        total += v // 10 + v % 10
    return str((10 - total % 10) % 10)


def sedol_check_digit(body6: str) -> str:
    weights = [1, 3, 1, 7, 3, 9]
    total = sum((int(c) if c.isdigit() else ord(c) - 55) * w for c, w in zip(body6, weights))
    return str((10 - total % 10) % 10)


class ProductUniverse:
    """Deterministic universe of instruments plus a mutable current state per product."""

    def __init__(self, size: int, seed: int = 42):
        self.rng = random.Random(seed)
        self.size = size
        self._classes = [c for c in ASSET_CLASSES for _ in range(c[3])]
        self.products = [self._make(i) for i in range(size)]

    def _alnum(self, n: int, alphabet: str = string.ascii_uppercase + string.digits) -> str:
        return "".join(self.rng.choice(alphabet) for _ in range(n))

    def _make(self, i: int) -> dict:
        rng = self.rng
        country, ccy, mic, pos, cycle = rng.choice(MARKETS)
        asset_class, subclass, cfi, _ = rng.choice(self._classes)
        issuer = f"{rng.choice(ISSUER_STEMS)} {rng.choice(ISSUER_SUFFIX)}"
        if subclass == "GOVERNMENT_BOND":
            issuer = f"Government of {country}"

        cusip8 = self._alnum(6, string.digits + "ABCDEFGHJKLMNPRSTUVWXYZ") + self._alnum(2, string.digits)
        cusip = cusip8 + cusip_check_digit(cusip8)
        nsin = cusip if country in ("US", "CA") else self._alnum(9, string.digits)
        isin11 = country + nsin
        sedol6 = self._alnum(6, "0123456789BCDFGHJKLMNPQRSTVWXYZ")

        issue = date(2005, 1, 1) + timedelta(days=rng.randint(0, 7000))
        is_bond = asset_class == "FIXED_INCOME"
        ticker = "".join(w[0] for w in issuer.split()) + self._alnum(2, string.ascii_uppercase)

        return {
            "PRODUCT_ID": f"P{i:09d}",
            "PRODUCT_VERSION": 0,
            "ISIN": isin11 + isin_check_digit(isin11),
            "CUSIP": cusip,
            "SEDOL": sedol6 + sedol_check_digit(sedol6),
            "FIGI": "BBG" + self._alnum(9, "0123456789BCDFGHJKLMNPQRSTVWXYZ"),
            "TICKER": None if is_bond else ticker,
            "MIC": mic,
            "PRODUCT_NAME": (f"{issuer} {rng.uniform(0.5, 7.5):.3f}% {issue.year + rng.randint(2, 30)}"
                             if is_bond else f"{issuer} {subclass.replace('_', ' ').title()}"),
            "ASSET_CLASS": asset_class,
            "ASSET_SUBCLASS": subclass,
            "CFI_CODE": cfi,
            "ISSUER_NAME": issuer,
            "ISSUER_LEI": self._alnum(18) + self._alnum(2, string.digits),
            "COUNTRY_OF_ISSUE": country,
            "CURRENCY": ccy,
            "COUPON_RATE": round(rng.uniform(0.5, 7.5), 6) if is_bond else None,
            "ISSUE_DATE": issue,
            "MATURITY_DATE": issue + timedelta(days=365 * rng.randint(2, 30)) if is_bond else None,
            "PRICE": round(rng.uniform(85, 115) if is_bond else rng.uniform(5, 900), 6),
            "PLACE_OF_SETTLEMENT": pos,
            "SETTLEMENT_CYCLE": cycle,
            "CA_PENDING": False,
            "CA_EVENT_TYPE": None,
            "STATUS": "ACTIVE",
        }

    def next_event(self) -> dict:
        """Mutate one random product and return its full-state UDM row."""
        rng = self.rng
        p = rng.choice(self.products)
        if p["PRODUCT_VERSION"] == 0:
            event_type = "CREATE"
        else:
            event_type = "UPDATE"
            r = rng.random()
            if r < 0.80:      # price move
                p["PRICE"] = round(max(0.01, p["PRICE"] * (1 + rng.gauss(0, 0.004))), 6)
            elif r < 0.92:    # corporate action announced / completed
                p["CA_PENDING"] = not p["CA_PENDING"]
                p["CA_EVENT_TYPE"] = rng.choice(CA_TYPES) if p["CA_PENDING"] else None
            elif r < 0.98:    # status change
                p["STATUS"] = rng.choice(["ACTIVE", "ACTIVE", "SUSPENDED"])
            else:             # delist / mature (soft delete)
                p["STATUS"] = "MATURED" if p["ASSET_CLASS"] == "FIXED_INCOME" else "DELISTED"
                event_type = "DELETE"

        now = datetime.now(timezone.utc).replace(tzinfo=None)
        # Epoch-microsecond version: keeps increasing across restarts and region
        # switches, so VERSION BY always picks the newest snapshot after a failover.
        now_us = int(now.replace(tzinfo=timezone.utc).timestamp() * 1_000_000)
        p["PRODUCT_VERSION"] = max(p["PRODUCT_VERSION"] + 1, now_us)
        return to_row(p, event_type, now)


def to_row(p: dict, event_type: str, ts: datetime) -> dict:
    """Flatten the product into table columns and attach the nested UDM in PAYLOAD."""
    def d(v):
        return v.isoformat() if isinstance(v, date) else v

    ts_s = ts.strftime(TS_FMT)
    payload = {
        "header": {
            "messageType": "PRODUCT",
            "schemaVersion": "1.2",
            "eventType": event_type,
            "eventTimestamp": ts_s,
            "sourceSystem": SOURCE_SYSTEM,
            "productId": p["PRODUCT_ID"],
            "productVersion": p["PRODUCT_VERSION"],
        },
        "identifiers": {k.lower(): p[k] for k in ("ISIN", "CUSIP", "SEDOL", "FIGI", "TICKER")},
        "classification": {
            "assetClass": p["ASSET_CLASS"],
            "assetSubclass": p["ASSET_SUBCLASS"],
            "cfiCode": p["CFI_CODE"],
        },
        "issuer": {"name": p["ISSUER_NAME"], "lei": p["ISSUER_LEI"], "countryOfIssue": p["COUNTRY_OF_ISSUE"]},
        "terms": {
            "currency": p["CURRENCY"],
            "couponRate": p["COUPON_RATE"],
            "issueDate": d(p["ISSUE_DATE"]),
            "maturityDate": d(p["MATURITY_DATE"]),
        },
        "listing": {"mic": p["MIC"], "status": p["STATUS"]},
        "pricing": {"price": p["PRICE"], "currency": p["CURRENCY"], "priceTimestamp": ts_s},
        "settlement": {"placeOfSettlement": p["PLACE_OF_SETTLEMENT"], "cycle": p["SETTLEMENT_CYCLE"]},
        "corporateActions": {"pending": p["CA_PENDING"], "eventType": p["CA_EVENT_TYPE"]},
    }

    row = {k: d(v) for k, v in p.items()}
    row.update({
        "EVENT_TYPE": event_type,
        "EVENT_TS": ts_s,
        "SOURCE_SYSTEM": SOURCE_SYSTEM,
        "PRICE_TS": ts_s,
        "PAYLOAD": payload,
    })
    return row
