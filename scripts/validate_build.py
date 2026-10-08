#!/usr/bin/env python3
import json
import os
import re
from bs4 import BeautifulSoup

REQUIRED_IDS = {
    "hero","levelNum","levelLabel","levelReason","updatedAt","nextAt",
    "geoCard","hidroCard","inmetCard","defesaCard","defesaConnection",
    "dcStage","dcSourceStatus","dcBasis","dcOfficialUpdated","dcHcnscImpact","dcHcnscImpactDetail",
    "dcSignalTitle","dcSignalMeta","dcSignalLink","dcLatestTitle","dcLatestDate","dcLatestLink",
    "dcEmergency","dcSms","dcHomeLink","dcBulletinLink","dcWhatsappLink","dcRouteSummary","dcRouteStatus","dcCacheStatus","dcCacheMeta","dcHistoryBody","dcHistoryCount",
    "pluvioBody","pluvioHidden","bingenHistoryBody","bingenHistoryStatus","bingenHistoryLast","bingenHistory1h","bingenHistory24h","bingenHistoryDistance",
    "historyBody","pluvioHistoryBody","historyCount","historyRiskView","historyRainView","exportPluvioHistory",
    "nearestPluvioName","nearestPluvio1h","nearestPluvio24h","pluvioConnection","weatherRefConnection",
    "climateCurrentCard","climateCurrentIcon","climateCurrentTemp","climateHistoryGrid",
    "weatherMap","mapLegend","mapUpdated","mapRadarStatus","mapNearestStation","mapNearestRain","mapCurrentWeather",
    "roadsConnection","roadsAlertState","roadsWeatherTemp","roadsWeatherCondition",
    "roadsSerraTitle","roadsScheduleTitle","roadsBulletinTitle",
    "historyOldest","historyNewest","historyMonths","exportHistory"
}

def load_json(path):
    with open(path,"r",encoding="utf-8") as f:
        return json.load(f)

with open("index.html","r",encoding="utf-8") as f:
    html=f.read()
soup=BeautifulSoup(html,"html.parser")
ids={el.get("id") for el in soup.find_all(attrs={"id":True})}
missing=sorted(REQUIRED_IDS-ids)
if missing:
    raise SystemExit("IDs obrigatórios ausentes: "+", ".join(missing))

status=load_json("data/status.json")
level=(status.get("overall") or {}).get("level")
if level not in (1,2,3,4,5):
    raise SystemExit(f"Nível HCNSC inválido: {level}")
for key in ("cemaden_geological","cemaden_hydrological","inmet_alerts","defesa_civil"):
    if key not in (status.get("sources") or {}):
        raise SystemExit(f"Fonte ausente: {key}")
if "pluviometers" not in status or "forecast" not in status or "weather_map" not in status or "roads" not in status:
    raise SystemExit("Blocos operacionais obrigatórios ausentes no status.json")
weather_map=status.get("weather_map") or {}
if not isinstance(weather_map.get("grid"),list) or len(weather_map.get("grid") or []) < 9:
    raise SystemExit("Grade meteorológica do mapa ausente ou insuficiente")
center=weather_map.get("center") or {}
if center.get("latitude") is None or center.get("longitude") is None:
    raise SystemExit("Centro do mapa meteorológico não definido")

roads=status.get("roads") or {}
if roads.get("provider")!="ELOvias + K-Infra Rodovia do Aço" or not isinstance(roads.get("endpoint_status"),dict):
    raise SystemExit("ELOvias + K-Infra Rodovia do Aço ausente ou inválida no status.json")

if not (status.get("pluviometers") or {}).get("nearest_to_hcnsc"):
    raise SystemExit("Pluviômetro de referência próximo ao HCNSC não identificado")
reference=(status.get("pluviometers") or {}).get("nearest_to_hcnsc") or {}
if not reference.get("name"):
    raise SystemExit("Estação pluviométrica de referência não identificada")

history=load_json("data/history.json")
snaps=history.get("snapshots",[])
if not isinstance(snaps,list) or not snaps:
    raise SystemExit("Histórico sem snapshots")
if any(s.get("level") not in (1,2,3,4,5) for s in snaps if isinstance(s,dict)):
    raise SystemExit("Histórico contém nível HCNSC inválido")

index=load_json("data/history_index.json")
months=index.get("months",[])
if not isinstance(months,list) or not months:
    raise SystemExit("Índice mensal do histórico vazio")
for m in months:
    path=m.get("file")
    if not path or not os.path.exists(path):
        raise SystemExit(f"Arquivo mensal inexistente: {path}")
    archive=load_json(path)
    if not archive.get("snapshots"):
        raise SystemExit(f"Arquivo mensal sem snapshots: {path}")

print(f"VALIDATION_OK level={level} history={len(snaps)} months={len(months)}")


climate=load_json("data/climate_history.json")
periods=climate.get("periods",[])
if not isinstance(periods,list):
    raise SystemExit("Histórico climático inválido")
valid_periods={"madrugada","manha","tarde","noite"}
for item in periods:
    if item.get("period") not in valid_periods:
        raise SystemExit(f"Período climático inválido: {item.get('period')}")
print(f"CLIMATE_HISTORY_OK periods={len(periods)}")
