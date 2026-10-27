"""Parse the piece-length and width fields typed in the admin and the Studio.

Shared so both editors accept exactly the same values. Errors are plain sentences for
non-technical staff.
"""

from __future__ import annotations

import math
from decimal import Decimal, InvalidOperation

MAX_PIECE_YARDS = Decimal(1000)
MAX_WIDTH_INCHES = 200


def parse_piece_length(raw: str | None) -> Decimal | None:
    """'1', '4.5' or '4 1/2' yards -> Decimal; blank -> None (length not recorded)."""
    text = (raw or "").strip().lower().removesuffix("yards").removesuffix("yard").strip()
    if not text:
        return None
    whole, _, fraction = text.partition(" ")
    try:
        value = Decimal(whole)
        if fraction:
            top, slash, bottom = fraction.partition("/")
            if not slash:
                raise InvalidOperation
            value += Decimal(top) / Decimal(bottom)
    except (InvalidOperation, ZeroDivisionError):
        raise ValueError("Enter the piece length in yards, for example 1, 4.5 or 6.") from None
    if not 0 < value <= MAX_PIECE_YARDS:
        raise ValueError("The piece length must be more than 0 and at most 1000 yards.")
    if value != value.quantize(Decimal("0.01")):
        raise ValueError("Use at most two decimals for the piece length, for example 4.5.")
    return value.quantize(Decimal("0.01"))


def parse_width(raw: str | None) -> int | None:
    """Fabric width in whole inches; blank -> None (a typical 45" is assumed for weights)."""
    text = (raw or "").strip().rstrip('"').removesuffix("inches").removesuffix("in").strip()
    if not text:
        return None
    if not text.isdigit() or not 1 <= int(text) <= MAX_WIDTH_INCHES:
        raise ValueError("Enter the fabric width in whole inches, for example 45.")
    return int(text)


GRAMS_PER_OUNCE = Decimal("28.349523125")
OUNCES_PER_POUND = 16


def format_weight(grams: int | None) -> str:
    """567 -> '1 lb 4 oz (567 g)'; under a pound -> '9.8 oz (278 g)'."""
    if not grams:
        return "not set"
    ounces = Decimal(grams) / GRAMS_PER_OUNCE
    if ounces < OUNCES_PER_POUND:
        return f"{ounces.quantize(Decimal('0.1'))} oz ({grams:,} g)"
    pounds, rest = divmod(int(ounces.quantize(Decimal("1"))), OUNCES_PER_POUND)
    return f"{pounds} lb {rest} oz ({grams:,} g)"


def usps_weight_step(grams: int) -> str:
    """The USPS price step a parcel falls in.

    Ground Advantage prices parcels under 1 lb in 4 oz steps (4, 8, 12, 15.99 oz), then by
    whole pound; Priority Mail and Express price by whole pound. Crossing a step is where
    postage goes up, so this tells the owner how close an order is to the next price.
    """
    ounces = Decimal(grams) / GRAMS_PER_OUNCE
    if ounces <= 4:
        return "up to 4 oz"
    if ounces <= 8:
        return "up to 8 oz"
    if ounces <= 12:
        return "up to 12 oz"
    if ounces < 16:
        return "up to 15.99 oz"
    return f"up to {math.ceil(ounces / OUNCES_PER_POUND)} lb"
