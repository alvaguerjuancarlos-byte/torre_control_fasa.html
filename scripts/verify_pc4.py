import urllib.request, json

url = "http://127.0.0.1:8000/v3/gestion/coladas?referencia=2025-12-19T08:00&horas=48"
d = json.loads(urllib.request.urlopen(url).read())

for c in d["coladas"][:5]:
    print(f"colada={c['id_colada']}  PC-4={c['estados']['PC-4']}  PC-6={c['estados']['PC-6']}  temp_avg={c.get('temp_avg')}  pct_temp_ok={c.get('pct_temp_ok')}")
