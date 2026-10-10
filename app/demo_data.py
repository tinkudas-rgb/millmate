"""Deterministic sample stock and movement history for a rice mill."""
from datetime import date, timedelta
import uuid

BRANDS = [
    {"name":"India Gate Basmati", "rice_type":"Basmati", "stock_kg":2650.0, "reorder_level_kg":650.0},
    {"name":"Sona Masoori", "rice_type":"Raw", "stock_kg":3280.0, "reorder_level_kg":700.0},
    {"name":"Ponni Boiled Rice", "rice_type":"Parboiled", "stock_kg":1840.0, "reorder_level_kg":500.0},
    {"name":"Kolam Rice", "rice_type":"Non-basmati", "stock_kg":2120.0, "reorder_level_kg":450.0},
    {"name":"Jeera Samba", "rice_type":"Raw", "stock_kg":940.0, "reorder_level_kg":350.0},
    {"name":"Matta Rice", "rice_type":"Parboiled", "stock_kg":1560.0, "reorder_level_kg":400.0},
]

def build_demo(today: date | None = None) -> tuple[list[dict], list[dict]]:
    """Return six brands and 30 days of movements, with Basmati nearly running out."""
    today = today or date.today()
    brands=[]
    for b in BRANDS:
        brands.append({"id":str(uuid.uuid5(uuid.NAMESPACE_URL, "rice-mill-demo:"+b['name'])), **b, "created":today.isoformat()})
    daily_out = [
        [190,180,200,175,185,170,195, 185,190,180,205,190,180,200, 190,210,205,195,215,200,220, 205,215,230,220,225,235,240,230,245],
        [95,100,90,105,95,100,110, 100,95,105,100,110,95,105, 100,105,110,100,105,115,110, 105,115,110,120,115,125,120,125,130],
        [65,70,60,65,75,70,65, 65,70,75,65,70,75,70, 70,75,70,80,75,70,80, 75,80,75,85,80,85,90,85,90],
        [55,50,60,55,65,60,55, 60,55,65,60,55,65,60, 60,65,60,70,65,60,70, 65,70,65,75,70,75,80,75,80],
        [38,35,40,42,36,39,41, 40,38,43,40,42,39,44, 42,45,43,46,42,48,45, 46,48,50,49,52,54,55,53,58],
        [48,45,50,46,52,49,47, 50,48,52,49,55,51,50, 52,54,50,56,53,58,55, 55,60,57,62,60,65,63,68,70],
    ]
    movements=[]
    for index, brand in enumerate(brands):
        # Opening level is chosen so the final inventory matches BRANDS stock.
        total_out=sum(daily_out[index])
        target=brand['stock_kg']
        opening=target+total_out-600
        # Seed with a received load near the start and opening stock held as stock.
        brand['stock_kg']=opening
        movements.append({"id":str(uuid.uuid5(uuid.NAMESPACE_URL, f"demo-in:{brand['id']}")),"brand_id":brand['id'],"kind":"in","kg":600.0,"date":(today-timedelta(days=29)).isoformat(),"note":"Regular mill delivery"})
        for day, kg in enumerate(daily_out[index]):
            movements.append({"id":str(uuid.uuid5(uuid.NAMESPACE_URL, f"demo-out:{brand['id']}:{day}")),"brand_id":brand['id'],"kind":"out","kg":float(kg),"date":(today-timedelta(days=29-day)).isoformat(),"note":"Daily dispatch"})
    # Keep basmati at less than a week's supply while all stock stays positive.
    basmati = brands[0]
    basmati_total_out = sum(m['kg'] for m in movements if m['brand_id'] == basmati['id'] and m['kind'] == 'out')
    basmati['stock_kg'] = 1100.0 + basmati_total_out - 600.0
    return brands, movements
