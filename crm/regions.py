import json
from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=1)
def region_options():
    path = Path(__file__).parent / "data" / "provinces_cities.json"
    return json.loads(path.read_text(encoding="utf-8"))


def valid_region_pair(province, city):
    return any(
        item["province"] == province and any(option["name"] == city for option in item["cities"])
        for item in region_options()
    )
