"""
Extract car make and model from a free-text listing title.
Makes are matched longest-first so "Mercedes-Benz" wins over "Mercedes".
"""
import re

_MAKES = sorted([
    "Alfa Romeo", "Aston Martin", "Land Rover", "Range Rover",
    "Mercedes-Benz", "Rolls-Royce",
    "Acura", "Audi", "Bentley", "BMW", "Bugatti", "Buick",
    "Cadillac", "Chevrolet", "Chevy", "Chrysler", "Citroen",
    "Dacia", "Dodge", "Ferrari", "Fiat", "Ford", "Genesis",
    "GMC", "Honda", "Hummer", "Hyundai", "Infiniti",
    "Jaguar", "Jeep", "Kia", "Lamborghini", "Lancia", "Lexus",
    "Lincoln", "Lotus", "Maserati", "Mazda", "McLaren",
    "Mercedes", "Mini", "Mitsubishi", "Nissan", "Oldsmobile",
    "Opel", "Peugeot", "Plymouth", "Pontiac", "Porsche",
    "Ram", "Renault", "Saab", "Saturn", "Scion",
    "Skoda", "Seat", "Subaru", "Suzuki", "Tesla", "Toyota",
    "Vauxhall", "Volkswagen", "Volvo",
], key=len, reverse=True)

_NOISE = {
    "for", "with", "and", "the", "oem", "new", "used", "genuine",
    "original", "part", "parts", "fits", "compatible", "aftermarket",
    "pair", "set", "front", "rear", "left", "right", "upper", "lower",
    "complete", "assembly",
}


def extract_make_model(title: str) -> tuple[str | None, str | None]:
    """Return (make, model) extracted from a listing title, or (None, None)."""
    if not title:
        return None, None

    # Strip a leading year so "2019 Ford …" → "Ford …"
    t = re.sub(r"^\s*(19|20)\d{2}\s+", "", title)

    for make in _MAKES:
        # Word-boundary match, case-insensitive
        m = re.search(rf"(?<![A-Za-z]){re.escape(make)}(?![A-Za-z])", t, re.IGNORECASE)
        if not m:
            continue

        after = t[m.end():].strip()
        # Split on spaces, dashes, slashes, commas
        words = re.split(r"[\s,/]+", after)
        model_parts: list[str] = []
        for w in words:
            clean = w.strip("()[].")
            if not clean:
                continue
            if clean.lower() in _NOISE:
                break
            # Stop at a standalone year
            if re.fullmatch(r"(19|20)\d{2}", clean):
                break
            model_parts.append(clean)
            if len(model_parts) == 2:
                break

        model = " ".join(model_parts) or None
        return make, model

    return None, None
