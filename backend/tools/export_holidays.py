"""Regenerate the embedded calendar using holidays==0.104 (development only)."""
import gzip
import json
from pathlib import Path
import holidays
if holidays.__version__ != '0.104':
    raise RuntimeError('Use holidays==0.104 for reproducible calendar data')
dates={}
for country in sorted(holidays.list_supported_countries(include_aliases=True)):
    calendar=holidays.country_holidays(country,years=range(1970,2101),expand=False)
    dates[country.upper()]=sorted(day.isoformat() for day in calendar)
output=Path(__file__).resolve().parents[1]/'internal/domain/holidays.json.gz'
output.write_bytes(gzip.compress(json.dumps(dates,ensure_ascii=False,separators=(',',':'),sort_keys=True).encode(),mtime=0))
