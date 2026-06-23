from __future__ import annotations

from typing import Iterable


ANIMAL_SHEEP = "oveja"
ANIMAL_GOAT = "cabra"
ANIMAL_COW = "vaca"

ANIMAL_OPTIONS = [
    ("Oveja", ANIMAL_SHEEP),
    ("Cabra", ANIMAL_GOAT),
    ("Vaca", ANIMAL_COW),
]

TEMP_CHANNELS = ("A0", "A1", "A2", "A3")
TEMP_MAPPING_DEFAULT = "A0_RT_A1_LT"
TEMP_MAPPING_INVERTED = "A0_LT_A1_RT"
TEMP_MAPPING_COW_DEFAULT = "A0_FRT_A1_FLT_A2_RRT_A3_RLT"
TEMP_MAPPING_COW_INVERTED = "A0_FLT_A1_FRT_A2_RLT_A3_RRT"

POSITION_LABELS = {
    "RT": "derecha (RT)",
    "LT": "izquierda (LT)",
    "FRT": "delantera derecha (FRT)",
    "FLT": "delantera izquierda (FLT)",
    "RRT": "trasera derecha (RRT)",
    "RLT": "trasera izquierda (RLT)",
}

POSITION_SUMMARY_PREFIXES = {
    "RT": "temp_rt",
    "LT": "temp_lt",
    "FRT": "temp_frt",
    "FLT": "temp_flt",
    "RRT": "temp_rrt",
    "RLT": "temp_rlt",
}


def normalize_animal_type(value: str) -> str:
    text = (value or "").strip().lower()
    if text in {"vaca", "cow", "bovino", "bovina"}:
        return ANIMAL_COW
    if text in {"cabra", "goat", "caprino", "caprina"}:
        return ANIMAL_GOAT
    return ANIMAL_SHEEP


def animal_label(value: str) -> str:
    animal = normalize_animal_type(value)
    for label, key in ANIMAL_OPTIONS:
        if key == animal:
            return label
    return "Oveja"


def positions_for_animal(animal_type: str) -> tuple[str, ...]:
    return ("FLT", "FRT", "RLT", "RRT") if normalize_animal_type(animal_type) == ANIMAL_COW else ("RT", "LT")


def normalize_position(value: str, animal_type: str = "") -> str:
    text = (value or "").strip().upper().replace(" ", "_").replace("-", "_")
    if text in POSITION_LABELS:
        return text
    if "DELANTERA" in text and "DERECHA" in text:
        return "FRT"
    if "DELANTERA" in text and "IZQUIERDA" in text:
        return "FLT"
    if "TRASERA" in text and "DERECHA" in text:
        return "RRT"
    if "TRASERA" in text and "IZQUIERDA" in text:
        return "RLT"
    if text.startswith("RIGHT") or "DERECHA" in text or text == "R":
        return "RT"
    if text.startswith("LEFT") or "IZQUIERDA" in text or text == "L":
        return "LT"
    positions = positions_for_animal(animal_type)
    return text if text in positions else positions[0]


def default_position_for_animal(animal_type: str) -> str:
    return "FRT" if normalize_animal_type(animal_type) == ANIMAL_COW else "RT"


def default_mapping_for_animal(animal_type: str) -> str:
    return TEMP_MAPPING_COW_DEFAULT if normalize_animal_type(animal_type) == ANIMAL_COW else TEMP_MAPPING_DEFAULT


def inverted_mapping_for_animal(animal_type: str) -> str:
    return TEMP_MAPPING_COW_INVERTED if normalize_animal_type(animal_type) == ANIMAL_COW else TEMP_MAPPING_INVERTED


def parse_temp_mapping(mapping: str, animal_type: str = "") -> dict[str, str]:
    animal = normalize_animal_type(animal_type)
    text = (mapping or "").strip().upper()
    if not text:
        text = default_mapping_for_animal(animal)

    tokens = [token for token in text.split("_") if token]
    parsed: dict[str, str] = {}
    for idx in range(0, len(tokens) - 1, 2):
        channel = tokens[idx]
        position = tokens[idx + 1]
        if channel in TEMP_CHANNELS:
            parsed[channel] = normalize_position(position, animal)

    if not parsed:
        if text != default_mapping_for_animal(animal):
            return parse_temp_mapping(default_mapping_for_animal(animal), animal)
        parsed = {"A0": "RT", "A1": "LT"}
    defaults = parse_temp_mapping(default_mapping_for_animal(animal), animal) if text != default_mapping_for_animal(animal) else {}
    for channel, position in defaults.items():
        parsed.setdefault(channel, position)
    return parsed


def mapping_from_assignments(assignments: dict[str, str], animal_type: str = "") -> str:
    positions = set(positions_for_animal(animal_type))
    chunks: list[str] = []
    for channel in TEMP_CHANNELS:
        position = normalize_position(assignments.get(channel, ""), animal_type)
        if position in positions:
            chunks.extend([channel, position])
    return "_".join(chunks) or default_mapping_for_animal(animal_type)


def primary_channel_for(position: str, mapping: str, animal_type: str = "") -> str:
    wanted = normalize_position(position, animal_type)
    assignments = parse_temp_mapping(mapping, animal_type)
    for channel, assigned in assignments.items():
        if assigned == wanted:
            return channel
    return "A0"


def display_position(value: str) -> str:
    position = normalize_position(value)
    return POSITION_LABELS.get(position, position)


def display_mapping(mapping: str, animal_type: str = "") -> str:
    assignments = parse_temp_mapping(mapping, animal_type)
    parts = []
    for channel in TEMP_CHANNELS:
        position = assignments.get(channel)
        if position:
            parts.append(f"{channel} {POSITION_LABELS.get(position, position)}")
    return " / ".join(parts)


def channel_field_prefix(channel: str) -> str:
    return f"temp_{channel.lower()}"


def iter_position_prefixes(animal_type: str = "") -> Iterable[tuple[str, str]]:
    for position in positions_for_animal(animal_type):
        yield position, POSITION_SUMMARY_PREFIXES[position]
