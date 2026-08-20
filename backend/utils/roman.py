import re

VALUES = {"I":1,"V":5,"X":10,"L":50,"C":100,"D":500,"M":1000}

def roman_to_int(value: str) -> int:
    value = value.strip().upper()
    if not value or not re.fullmatch(r"M{0,3}(CM|CD|D?C{0,3})(XC|XL|L?X{0,3})(IX|IV|V?I{0,3})", value):
        raise ValueError(f"Invalid Roman numeral: {value}")
    total = 0
    for i, char in enumerate(value):
        total += -VALUES[char] if i + 1 < len(value) and VALUES[char] < VALUES[value[i+1]] else VALUES[char]
    return total

