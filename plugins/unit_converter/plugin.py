"""Unit converter — first-party BUCS plugin. Pure Python, no permissions.

Tool convert_units: length, mass, volume, area, speed, time, data, energy, pressure,
temperature. Exact factors (SI definitions); temperatures use affine conversion.
"""

FACTORS = {
    "length": {"m": 1, "km": 1000, "cm": 0.01, "mm": 0.001, "um": 1e-6, "nm": 1e-9, "mi": 1609.344, "yd": 0.9144,
               "ft": 0.3048, "in": 0.0254, "nmi": 1852},
    "mass": {"kg": 1, "g": 0.001, "mg": 1e-6, "t": 1000, "lb": 0.45359237, "oz": 0.028349523125, "st": 6.35029318},
    "volume": {"l": 1, "ml": 0.001, "m3": 1000, "cm3": 0.001, "gal": 3.785411784, "qt": 0.946352946,
               "pt": 0.473176473, "cup": 0.2365882365, "floz": 0.0295735295625, "tbsp": 0.01478676478125,
               "tsp": 0.00492892159375},
    "area": {"m2": 1, "km2": 1e6, "cm2": 1e-4, "ha": 1e4, "acre": 4046.8564224, "ft2": 0.09290304, "in2": 0.00064516,
             "mi2": 2589988.110336},
    "speed": {"m/s": 1, "km/h": 1 / 3.6, "mph": 0.44704, "kn": 1852 / 3600, "ft/s": 0.3048},
    "time": {"s": 1, "ms": 0.001, "min": 60, "h": 3600, "day": 86400, "week": 604800, "year": 31557600},
    "data": {"b": 1, "kb": 1e3, "mb": 1e6, "gb": 1e9, "tb": 1e12, "kib": 1024, "mib": 1024 ** 2, "gib": 1024 ** 3,
             "tib": 1024 ** 4, "bit": 0.125},
    "energy": {"j": 1, "kj": 1000, "cal": 4.184, "kcal": 4184, "wh": 3600, "kwh": 3.6e6, "btu": 1055.05585262,
               "ev": 1.602176634e-19},
    "pressure": {"pa": 1, "kpa": 1000, "bar": 1e5, "atm": 101325, "psi": 6894.757293168, "mmhg": 133.322387415},
}
ALIASES = {"meter": "m", "meters": "m", "metre": "m", "kilometer": "km", "kilometers": "km", "kms": "km",
           "centimeter": "cm", "millimeter": "mm", "mile": "mi", "miles": "mi", "yard": "yd", "yards": "yd",
           "foot": "ft", "feet": "ft", "inch": "in", "inches": "in", "kilogram": "kg", "kilograms": "kg", "kgs": "kg",
           "gram": "g", "grams": "g", "pound": "lb", "pounds": "lb", "lbs": "lb", "ounce": "oz", "ounces": "oz",
           "tonne": "t", "liter": "l", "litre": "l", "liters": "l", "litres": "l", "milliliter": "ml", "gallon": "gal",
           "gallons": "gal", "kmh": "km/h", "kph": "km/h", "knots": "kn", "knot": "kn", "second": "s", "seconds": "s",
           "minute": "min", "minutes": "min", "hour": "h", "hours": "h", "days": "day", "weeks": "week",
           "years": "year", "byte": "b", "bytes": "b", "celsius": "c", "°c": "c", "fahrenheit": "f",
           "°f": "f", "kelvin": "k", "sqm": "m2", "acres": "acre", "hectare": "ha", "hectares": "ha",
           "sqft": "ft2", "joule": "j", "joules": "j", "calorie": "cal", "calories": "cal"}
TEMPS = {"c", "f", "k"}


def _norm(u):
    u = str(u or "").strip().lower().replace(" ", "")
    return ALIASES.get(u, u)


def _to_c(v, u):
    return v if u == "c" else (v - 32) * 5 / 9 if u == "f" else v - 273.15


def _from_c(v, u):
    return v if u == "c" else v * 9 / 5 + 32 if u == "f" else v + 273.15


def convert(value, src, dst):
    a, b = _norm(src), _norm(dst)
    v = float(value)
    if a in TEMPS and b in TEMPS:
        return _from_c(_to_c(v, a), b), "temperature"
    for cat, table in FACTORS.items():
        if a in table and b in table:
            return v * table[a] / table[b], cat
    raise ValueError(f"cannot convert {src!r} to {dst!r} (unknown or incompatible units)")


def register(api):
    def convert_units(args, ctx=None):
        out, cat = convert(args["value"], args["from_unit"], args["to_unit"])
        r = float(f"{out:.10g}")
        return {"value": args["value"], "from": args["from_unit"], "to": args["to_unit"], "result": r,
                "category": cat, "text": f"{args['value']} {args['from_unit']} = {r:g} {args['to_unit']}"}

    api.tool("convert_units", convert_units)
