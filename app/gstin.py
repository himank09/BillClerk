"""GSTIN checks. A GSTIN is 15 characters: state code (2), PAN (10), entity
number, the letter Z, and a check character computed with GSTN's mod-36 method."""
import re

CHARS = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
STATE_CODES = {
    "27": "Maharashtra", "29": "Karnataka", "33": "Tamil Nadu", "32": "Kerala",
    "36": "Telangana", "07": "Delhi", "24": "Gujarat", "06": "Haryana",
}
PATTERN = re.compile(r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$")


def checksum(first14: str) -> str:
    total = 0
    for i, ch in enumerate(first14.upper()):
        factor = 2 if i % 2 else 1
        prod = CHARS.index(ch) * factor
        total += prod // 36 + prod % 36
    return CHARS[(36 - total % 36) % 36]


def make(state: str, pan: str, entity: str = "1") -> str:
    """Builds a valid GSTIN; used to create the test data."""
    base = f"{state}{pan}{entity}Z"
    return base + checksum(base)


def validate(gstin: str) -> dict:
    g = (gstin or "").strip().upper()
    out = {"gstin": g, "valid": False, "reason": None}
    if len(g) != 15:
        out["reason"] = f"length {len(g)}, expected 15"
    elif not PATTERN.match(g):
        out["reason"] = "does not match GSTIN structure"
    elif g[:2] not in STATE_CODES and not (1 <= int(g[:2]) <= 38):
        out["reason"] = "invalid state code"
    elif checksum(g[:14]) != g[14]:
        out["reason"] = f"checksum mismatch: expected {checksum(g[:14])}, got {g[14]}"
    else:
        out.update(valid=True, state_code=g[:2],
                   state=STATE_CODES.get(g[:2]), pan=g[2:12])
    return out

