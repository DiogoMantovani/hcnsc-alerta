#!/usr/bin/env python3
import json
import os
import re
import unicodedata
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

TZ = ZoneInfo("America/Sao_Paulo")
CITY = "TRÊS RIOS"
OUT = os.path.join("data", "status.json")
HISTORY_OUT = os.path.join("data", "history.json")
HISTORY_INDEX_OUT = os.path.join("data", "history_index.json")
CLIMATE_HISTORY_OUT = os.path.join("data", "climate_history.json")
ARCHIVE_DIR = os.path.join("data", "archive")
PREVIOUS_URL = "https://diogomantovani.github.io/hcnsc-alerta/data/status.json"
HISTORY_URL = "https://diogomantovani.github.io/hcnsc-alerta/data/history.json"

CEMADEN_BASE = "https://painelcemadenrj.defesacivil.rj.gov.br/monitoramento/v2/municipio/"
CEMADEN_PLUVIO = "https://resources.cemaden.gov.br/graficos/interativo/getJson2.php?uf=RJ"
CEMADEN_COORDS_REGISTRY = "https://observatorio.infraestrutura.mg.gov.br/server/rest/services/00_PUBLICACOES/cemaden_estacoes_pluviometricas/FeatureServer/1/query"
CEMADEN_COORDS_REGISTRY_ALT = "https://gis.vitoria.es.gov.br/arcgis/rest/services/Opendata/DadosAbertos/MapServer/18/query"
CEMADEN_STATIC_COORD_SOURCE = "https://cbhmedioparaiba.org.br/conteudo/mps-rb-2020.pdf"
INMET_WEATHER = "https://apitempo.inmet.gov.br/estacao/{start}/{end}/A625"
INMET_ALERTS = "https://apiprevmet3.inmet.gov.br/avisos/ativos"
INMET_FORECAST = "https://apiprevmet3.inmet.gov.br/previsao/3306008"
DEFESA_CIVIL_HOME = "https://tresrios.rj.gov.br/secretaria-de-protecao-e-defesa-civil/"
DEFESA_CIVIL_TAG = DEFESA_CIVIL_HOME
DEFESA_CIVIL_BOLETIM = DEFESA_CIVIL_HOME
DEFESA_CIVIL_METEO_PAGE = DEFESA_CIVIL_HOME
DEFESA_CIVIL_JOURNALISM = DEFESA_CIVIL_HOME
DEFESA_CIVIL_RSS = DEFESA_CIVIL_HOME
DEFESA_CIVIL_CACHE_READER = "https://r.jina.ai/"
DEFESA_CIVIL_NEWS_INDEX = "https://news.google.com/rss/search"
DEFESA_CIVIL_WHATSAPP = ""
HCNSC_LAT = -22.1123187
HCNSC_LON = -43.2131388
OPEN_METEO_CURRENT = "https://api.open-meteo.com/v1/forecast"
RAINVIEWER_MAPS = "https://api.rainviewer.com/public/weather-maps.json"
ELOVIAS_API = "https://cliente.api.elovias.com.br/v1/"
ELOVIAS_HOME = "https://elovias.com.br/home"
ELOVIAS_MAP = "https://elovias.com.br/mapa"
KINFRA_HOME = "https://www.rodoviadoaco.com.br/"
KINFRA_CONTACT = "https://www.rodoviadoaco.com.br/contato"
KINFRA_NEWS = "https://www.rodoviadoaco.com.br/noticia"
SAAETRI_HOME = "https://saaetri.com.br/"
ENEL_RIO_CHANNELS = "https://www.enel.com.br/pt/Canais.html"

RISK_TO_LEVEL = {"MUITO BAIXO":1,"BAIXO":2,"MODERADO":3,"ALTO":4,"MUITO ALTO":5}
LEVEL_LABELS = {1:"Vigilância",2:"Observação",3:"Atenção",4:"Alerta",5:"Alerta Máximo"}
ALERT_LEVEL = {"SEM AVISO":1,"AMARELO":2,"LARANJA":3,"VERMELHO":4}
DC_LEVEL = {"VIGILANCIA":1,"OBSERVACAO":2,"ATENCAO":3,"ALERTA":4,"ALERTA MAXIMO":5,"CRISE":5}

def norm(value):
    value = unicodedata.normalize("NFKD", str(value or ""))
    return "".join(c for c in value if not unicodedata.combining(c)).upper().strip()

def get_json(url, timeout=25):
    r = requests.get(url, timeout=timeout, headers={"User-Agent":"Mozilla/5.0 HCNSC-Alerta/1.0"})
    r.raise_for_status()
    return r.json()

def load_previous():
    try:
        return get_json(PREVIOUS_URL, timeout=12)
    except Exception:
        try:
            with open(OUT,"r",encoding="utf-8") as f: return json.load(f)
        except Exception:
            return {"schema_version":2,"generated_at":None,"overall":{"level":1,"label":"Vigilância","reason":"Aguardando primeira coleta."},"sources":{},"weather":{}}

def parse_dt(value):
    if not value: return None
    text=str(value).strip()
    for fmt in ("%d/%m/%Y %H:%M:%S","%Y-%m-%dT%H:%M:%S%z","%Y-%m-%d %H:%M:%S"):
        try:
            d=datetime.strptime(text,fmt)
            return d if d.tzinfo else d.replace(tzinfo=TZ)
        except Exception: pass
    try:
        d=datetime.fromisoformat(text.replace("Z","+00:00"))
        return d.astimezone(TZ) if d.tzinfo else d.replace(tzinfo=TZ)
    except Exception: return None

def safe_float(v):
    if v in (None,"","null","None"): return None
    try: return float(str(v).replace(",","."))
    except Exception: return None

def fetch_cemaden(action,key,label,previous):
    url=CEMADEN_BASE+f"?action={action}"
    prev=(previous.get("sources") or {}).get(key,{})
    now=datetime.now(TZ)
    try:
        r=requests.get(url,timeout=25,headers={"User-Agent":"Mozilla/5.0 HCNSC-Alerta/1.0","Accept":"text/html,application/xhtml+xml"})
        r.raise_for_status()
        soup=BeautifulSoup(r.text,"html.parser")
        match=None
        for tr in soup.find_all("tr"):
            cells=[re.sub(r"\s+"," ",td.get_text(" ",strip=True)) for td in tr.find_all(["td","th"])]
            if cells and norm(cells[0])==norm(CITY):
                match=cells; break
        if not match or len(match)<4: raise RuntimeError(f"{CITY} não encontrado")
        raw_risk=match[2].strip().upper()
        if raw_risk not in RISK_TO_LEVEL: raise RuntimeError(f"Risco não reconhecido: {raw_risk}")
        observed=parse_dt(match[3])
        age=round((now-observed).total_seconds()/3600,1) if observed else None

        if age is None or age>24:
            return {
                "name":label,
                "provider":"CEMADEN-RJ / Defesa Civil RJ",
                "status":"no_recent_update",
                "risk":"Sem atualização oficial recente",
                "level":None,
                "official_updated_at":observed.isoformat() if observed else match[3],
                "age_hours":age,
                "last_known_risk":raw_risk.title(),
                "last_known_level":RISK_TO_LEVEL[raw_risk],
                "last_known_updated_at":observed.isoformat() if observed else match[3],
                "freshness_limit_hours":24,
                "message":"Sem atualização oficial relevante nas últimas 24 h. Isso não significa ausência de risco.",
                "collected_at":now.isoformat(),
                "url":url,
                "error":None,
            }

        return {
            "name":label,
            "provider":"CEMADEN-RJ / Defesa Civil RJ",
            "status":"ok",
            "risk":raw_risk.title(),
            "level":RISK_TO_LEVEL[raw_risk],
            "official_updated_at":observed.isoformat() if observed else match[3],
            "age_hours":age,
            "last_known_risk":raw_risk.title(),
            "last_known_level":RISK_TO_LEVEL[raw_risk],
            "last_known_updated_at":observed.isoformat() if observed else match[3],
            "freshness_limit_hours":24,
            "message":"Informação oficial dentro da janela de 24 h.",
            "collected_at":now.isoformat(),
            "url":url,
            "error":None,
        }
    except Exception as exc:
        return {
            "name":label,
            "provider":"CEMADEN-RJ / Defesa Civil RJ",
            "status":"source_unconfirmed",
            "risk":"Sem informação oficial recente confirmada",
            "level":None,
            "last_known_risk":prev.get("last_known_risk") or prev.get("risk"),
            "last_known_level":prev.get("last_known_level") if prev.get("last_known_level") is not None else prev.get("level"),
            "last_known_updated_at":prev.get("last_known_updated_at") or prev.get("official_updated_at"),
            "freshness_limit_hours":24,
            "message":"Não foi possível confirmar uma atualização oficial recente nesta coleta. Isso não significa ausência de risco.",
            "collected_at":now.isoformat(),
            "url":url,
            "error":str(exc)[:300],
        }

def observation_datetime(row):
    date=row.get("DT_MEDICAO") or row.get("data") or row.get("DATA")
    hour=row.get("HR_MEDICAO") or row.get("hora") or row.get("HORA")
    if date:
        ds=str(date).strip()
        if hour not in (None,""):
            hs=str(hour).strip().zfill(4)
            try:
                d=datetime.strptime(ds+" "+hs[:2]+":"+hs[2:4],"%Y-%m-%d %H:%M")
                # INMET automatic station timestamps are commonly published in UTC.
                return d.replace(tzinfo=ZoneInfo("UTC")).astimezone(TZ)
            except Exception: pass
        d=parse_dt(ds)
        if d: return d
    return None

def fetch_inmet_weather(previous):
    prev=previous.get("weather") or {}
    now=datetime.now(TZ)
    today=now.strftime("%Y-%m-%d")
    yesterday=(now-timedelta(days=1)).strftime("%Y-%m-%d")

    # INMET has changed/retired some public API paths over time. Try the
    # documented station range path first, then the official all-stations
    # daily paths and filter A625 locally.
    candidates_urls=[
        INMET_WEATHER.format(start=yesterday,end=today),
        f"https://apitempo.inmet.gov.br/estacao/dados/{today}",
        f"https://apitempo.inmet.gov.br/estacao/dados/{yesterday}",
    ]
    errors=[]
    for url in candidates_urls:
        try:
            r=requests.get(url,timeout=5,headers={"User-Agent":"Mozilla/5.0 HCNSC-Alerta/1.0","Accept":"application/json,text/plain,*/*"})
            if r.status_code==204 or not r.text.strip():
                raise RuntimeError(f"HTTP {r.status_code} sem conteúdo")
            r.raise_for_status()
            try:
                data=r.json()
            except Exception:
                raise RuntimeError(f"Resposta não JSON (HTTP {r.status_code}, content-type={r.headers.get('content-type')})")

            if isinstance(data,dict):
                rows=data.get("dados") or data.get("data") or data.get("results") or []
                if not rows:
                    # Some APIs may key records by station/time.
                    rows=[v for v in data.values() if isinstance(v,dict)]
            else:
                rows=data
            if not isinstance(rows,list):
                raise RuntimeError("Formato de dados inesperado")

            valid=[]
            for row in rows:
                if not isinstance(row,dict): continue
                code=norm(row.get("CD_ESTACAO") or row.get("codigo") or row.get("station"))
                name=norm(row.get("DC_NOME") or row.get("nome") or row.get("station_name"))
                if code=="A625" or ("TRES RIOS" in name and "PARQUE NACIONAL" in name):
                    valid.append(row)
            # The station-specific endpoint may omit CD_ESTACAO in every row.
            if not valid and "A625" in url and rows:
                valid=[r for r in rows if isinstance(r,dict)]

            if not valid:
                raise RuntimeError("A625 não encontrada na resposta")

            valid.sort(key=lambda row: observation_datetime(row) or datetime.min.replace(tzinfo=TZ))
            row=valid[-1]
            obs=observation_datetime(row)
            age=round((now-obs).total_seconds()/3600,1) if obs else None
            status="stale" if age is not None and age>6 else "ok"
            return {
              "provider":"INMET",
              "station":{"code":"A625","name":"Três Rios","type":"estação automática local"},
              "status":status,
              "observed_at":obs.isoformat() if obs else None,
              "age_hours":age,
              "temperature_c":safe_float(row.get("TEM_INS") if row.get("TEM_INS") is not None else row.get("temperatura")),
              "humidity_pct":safe_float(row.get("UMD_INS") if row.get("UMD_INS") is not None else row.get("umidade")),
              "rain_1h_mm":safe_float(row.get("CHUVA") if row.get("CHUVA") is not None else row.get("precipitacao")),
              "wind_gust_ms":safe_float(row.get("VEN_RAJ") if row.get("VEN_RAJ") is not None else row.get("rajada")),
              "pressure_hpa":safe_float(row.get("PRE_INS") if row.get("PRE_INS") is not None else row.get("pressao")),
              "collected_at":now.isoformat(),"url":url,"error":None
            }
        except Exception as exc:
            errors.append(url+" -> "+str(exc)[:180])

    fallback=dict(prev)
    fallback.update({
      "provider":"INMET",
      "station":{"code":"A625","name":"Três Rios","type":"estação automática local"},
      "status":"unavailable",
      "collected_at":now.isoformat(),
      "url":candidates_urls[0],
      "error":" | ".join(errors)[:900]
    })
    return fallback

def weather_code_text(code):
    mapping={
        0:"Céu limpo",1:"Predominantemente limpo",2:"Parcialmente nublado",3:"Nublado",
        45:"Nevoeiro",48:"Nevoeiro com geada",
        51:"Garoa fraca",53:"Garoa moderada",55:"Garoa forte",
        61:"Chuva fraca",63:"Chuva moderada",65:"Chuva forte",
        80:"Pancadas de chuva fracas",81:"Pancadas de chuva moderadas",82:"Pancadas de chuva fortes",
        95:"Trovoada",96:"Trovoada com granizo fraco",99:"Trovoada com granizo forte",
    }
    return mapping.get(int(code) if code is not None else -1,"Condição não classificada")

def fetch_open_meteo_current(previous):
    prev=previous.get("weather_reference") or {}
    now=datetime.now(TZ)
    params={
        "latitude":HCNSC_LAT,
        "longitude":HCNSC_LON,
        "current":"temperature_2m,relative_humidity_2m,apparent_temperature,precipitation,rain,weather_code,wind_speed_10m,wind_gusts_10m",
        "timezone":"America/Sao_Paulo",
    }
    try:
        r=requests.get(OPEN_METEO_CURRENT,params=params,timeout=18,headers={"User-Agent":"HCNSC-Alerta/1.0"})
        r.raise_for_status()
        data=r.json()
        cur=data.get("current") or {}
        observed=parse_dt(cur.get("time"))
        if observed is None and cur.get("time"):
            try:
                observed=datetime.fromisoformat(str(cur["time"])).replace(tzinfo=TZ)
            except Exception:
                observed=None
        age=round((now-observed).total_seconds()/3600,1) if observed else None
        status="ok" if age is not None and -0.25<=age<=2 else "stale"
        return {
            "provider":"Open-Meteo",
            "source_type":"estimativa meteorológica complementar",
            "official":False,
            "status":status,
            "location":{"name":"Hospital Santa Teresa","latitude":HCNSC_LAT,"longitude":HCNSC_LON},
            "observed_at":observed.isoformat() if observed else cur.get("time"),
            "age_hours":age,
            "temperature_c":safe_float(cur.get("temperature_2m")),
            "apparent_temperature_c":safe_float(cur.get("apparent_temperature")),
            "humidity_pct":safe_float(cur.get("relative_humidity_2m")),
            "precipitation_mm":safe_float(cur.get("precipitation")),
            "rain_mm":safe_float(cur.get("rain")),
            "wind_speed_kmh":safe_float(cur.get("wind_speed_10m")),
            "wind_gust_kmh":safe_float(cur.get("wind_gusts_10m")),
            "weather_code":cur.get("weather_code"),
            "condition":weather_code_text(cur.get("weather_code")),
            "collected_at":now.isoformat(),
            "url":r.url,
            "message":"Referência complementar próxima ao HCNSC. Não substitui INMET, CEMADEN ou Defesa Civil para alertas oficiais.",
            "error":None,
        }
    except Exception as exc:
        fallback=dict(prev)
        fallback.update({
            "provider":"Open-Meteo",
            "source_type":"estimativa meteorológica complementar",
            "official":False,
            "status":"source_unconfirmed",
            "collected_at":now.isoformat(),
            "message":"Sem condição meteorológica complementar recente confirmada. Isso não significa ausência de risco.",
            "error":str(exc)[:500],
        })
        return fallback

def fetch_weather_map(previous):
    prev=previous.get("weather_map") or {}
    now=datetime.now(TZ)

    # Operational grid centered on HCNSC, covering central Três Rios.
    lat_offsets=(-0.09,-0.06,-0.03,0.0,0.03,0.06,0.09)
    lon_offsets=(-0.12,-0.08,-0.04,0.0,0.04,0.08,0.12)
    coords=[(round(HCNSC_LAT+a,5),round(HCNSC_LON+b,5)) for a in lat_offsets for b in lon_offsets]

    grid=[]
    grid_error=None
    try:
        params={
            "latitude":",".join(str(x[0]) for x in coords),
            "longitude":",".join(str(x[1]) for x in coords),
            "current":"temperature_2m,cloud_cover,precipitation,wind_speed_10m,wind_direction_10m,weather_code",
            "timezone":"America/Sao_Paulo",
        }
        r=requests.get(OPEN_METEO_CURRENT,params=params,timeout=25,headers={"User-Agent":"HCNSC-Alerta/1.0"})
        r.raise_for_status()
        data=r.json()
        items=data if isinstance(data,list) else [data]
        for requested,item in zip(coords,items):
            if not isinstance(item,dict):
                continue
            cur=item.get("current") or {}
            grid.append({
                "latitude":safe_float(item.get("latitude")) if item.get("latitude") is not None else requested[0],
                "longitude":safe_float(item.get("longitude")) if item.get("longitude") is not None else requested[1],
                "requested_latitude":requested[0],
                "requested_longitude":requested[1],
                "observed_at":cur.get("time"),
                "temperature_c":safe_float(cur.get("temperature_2m")),
                "cloud_cover_pct":safe_float(cur.get("cloud_cover")),
                "precipitation_mm":safe_float(cur.get("precipitation")),
                "wind_speed_kmh":safe_float(cur.get("wind_speed_10m")),
                "wind_direction_deg":safe_float(cur.get("wind_direction_10m")),
                "weather_code":cur.get("weather_code"),
            })
    except Exception as exc:
        grid_error=str(exc)[:500]
        grid=(prev.get("grid") or []) if isinstance(prev,dict) else []

    radar={"status":"unavailable","provider":"RainViewer","error":None}
    try:
        r=requests.get(RAINVIEWER_MAPS,timeout=15,headers={"User-Agent":"HCNSC-Alerta/1.0"})
        r.raise_for_status()
        data=r.json()
        frames=((data.get("radar") or {}).get("past") or [])
        if not frames:
            raise RuntimeError("RainViewer sem quadros de radar")
        frame=frames[-1]
        frame_time=datetime.fromtimestamp(int(frame.get("time")),tz=ZoneInfo("UTC")).astimezone(TZ)
        radar={
            "status":"ok",
            "provider":"RainViewer",
            "host":data.get("host"),
            "path":frame.get("path"),
            "frame_time":frame_time.isoformat(),
            "generated_unix":data.get("generated"),
            "tile_color_scheme":2,
            "url":RAINVIEWER_MAPS,
            "error":None,
        }
    except Exception as exc:
        prior=(prev.get("radar") or {}) if isinstance(prev,dict) else {}
        radar={
            "status":"unavailable",
            "provider":"RainViewer",
            "last_known_frame_time":prior.get("frame_time") or prior.get("last_known_frame_time"),
            "url":RAINVIEWER_MAPS,
            "error":str(exc)[:500],
        }

    grid_ok=bool(grid)
    return {
        "status":"ok" if grid_ok else "source_unconfirmed",
        "provider":"Open-Meteo + RainViewer",
        "source_type":"mapa meteorológico complementar",
        "official":False,
        "center":{"name":"Hospital Santa Teresa","latitude":HCNSC_LAT,"longitude":HCNSC_LON},
        "operational_radius_km":5,
        "grid_updated_at":now.isoformat() if grid_ok else prev.get("grid_updated_at"),
        "grid":grid,
        "grid_points":len(grid),
        "grid_error":grid_error,
        "radar":radar,
        "collected_at":now.isoformat(),
        "message":"Mapa complementar. Alertas e níveis HCNSC permanecem baseados nas fontes oficiais integradas.",
    }

def severity_from_alert(item):
    blob=norm(json.dumps(item,ensure_ascii=False))
    for color in ("VERMELHO","LARANJA","AMARELO"):
        if color in blob: return color
    # Fallback to common severity wording.
    if "GRANDE PERIGO" in blob or "EXTREMO" in blob: return "VERMELHO"
    if re.search(r"\bPERIGO\b",blob): return "LARANJA"
    if "PERIGO POTENCIAL" in blob or "POTENCIAL" in blob: return "AMARELO"
    return None

def alert_times(item):
    blob=item if isinstance(item,dict) else {}
    start=blob.get("inicio") or blob.get("Início") or blob.get("start") or blob.get("data_inicio")
    end=blob.get("fim") or blob.get("Fim") or blob.get("end") or blob.get("data_fim")
    return start,end

def fetch_inmet_alerts(previous):
    prev=(previous.get("sources") or {}).get("inmet_alerts",{})
    now=datetime.now(TZ)
    try:
        data=get_json(INMET_ALERTS)
        items=[]
        if isinstance(data,list): items=data
        elif isinstance(data,dict):
            for key in ("avisos","data","results","features"):
                if isinstance(data.get(key),list):
                    items=data[key]; break
            if not items:
                # Some INMET endpoints return a mapping keyed by alert id.
                vals=[v for v in data.values() if isinstance(v,dict)]
                if vals: items=vals
        matching=[]
        for item in items:
            blob=norm(json.dumps(item,ensure_ascii=False))
            if "TRES RIOS" in blob and ("RJ" in blob or "RIO DE JANEIRO" in blob):
                sev=severity_from_alert(item)
                matching.append((ALERT_LEVEL.get(sev,1),sev,item))
        matching.sort(key=lambda x:x[0],reverse=True)
        if matching:
            level,sev,item=matching[0]
            start,end=alert_times(item if isinstance(item,dict) else {})
            title=(item.get("evento") or item.get("aviso") or item.get("descricao") or item.get("headline") or "Aviso meteorológico") if isinstance(item,dict) else "Aviso meteorológico"
            return {"name":"Meteorológico","provider":"INMET","status":"ok","risk":sev.title() if sev else "Aviso ativo","level":level,"title":str(title)[:250],"active_count":len(matching),"starts_at":start,"ends_at":end,"collected_at":now.isoformat(),"url":INMET_ALERTS,"error":None}
        return {"name":"Meteorológico","provider":"INMET","status":"ok","risk":"Sem aviso","level":1,"title":"Nenhum aviso ativo identificado para Três Rios/RJ","active_count":0,"starts_at":None,"ends_at":None,"collected_at":now.isoformat(),"url":INMET_ALERTS,"error":None}
    except Exception as exc:
        fallback=dict(prev)
        fallback.update({"name":"Meteorológico","provider":"INMET","status":"unavailable","collected_at":now.isoformat(),"url":INMET_ALERTS,"error":str(exc)[:300]})
        if "level" not in fallback: fallback.update({"level":None,"risk":"Indisponível","title":"Fonte de avisos INMET indisponível"})
        return fallback

def distance_km(lat1,lon1,lat2,lon2):
    from math import radians,sin,cos,asin,sqrt
    r=6371.0
    p1,p2=radians(lat1),radians(lat2)
    dlat=radians(lat2-lat1)
    dlon=radians(lon2-lon1)
    a=sin(dlat/2)**2+cos(p1)*cos(p2)*sin(dlon/2)**2
    return 2*r*asin(sqrt(a))

# Coordenadas publicadas para estações CEMADEN usadas como referências
# geográficas do HCNSC. estação pluviométrica de referência é a estação verificada mais próxima
# dentre as estações CEMADEN com coordenadas consolidadas nesta base.
# Coordenada cadastral pública da estação CEMADEN de referência em Três Rios.
# Usada somente para geolocalização; a chuva observada vem do CEMADEN.
KNOWN_CEMADEN_COORDS={
    norm("TRÊS RIOS_Centro"):(-22.1130,-43.2038),
    norm("TRES RIOS_Centro"):(-22.1130,-43.2038),
    norm("Três Rios - Centro"):(-22.1130,-43.2038),
    norm("Centro"):(-22.1130,-43.2038),
}

def fetch_cemaden_coordinate_registry():
    """Return station coordinates from public government GIS mirrors.

    Registries are used only for geolocation/distance. Rainfall values always
    come from the CEMADEN monitoring endpoint.
    """
    attempts=[
        (CEMADEN_COORDS_REGISTRY,["codibge=3306008","cidade LIKE 'Teres%'"]),
        (CEMADEN_COORDS_REGISTRY_ALT,["codibge='3306008'","codibge=3306008","cidade LIKE 'Teres%'"]),
    ]
    errors=[]
    last_url=CEMADEN_COORDS_REGISTRY
    for endpoint,where_options in attempts:
        for where in where_options:
            try:
                r=requests.get(
                    endpoint,
                    params={
                        "where":where,
                        "outFields":"idestacao,nomeestacao,latitude,longitude",
                        "returnGeometry":"false",
                        "f":"json",
                    },
                    timeout=10,
                    headers={"User-Agent":"Mozilla/5.0 HCNSC-Alerta/1.0","Accept":"application/json"},
                )
                last_url=r.url
                r.raise_for_status()
                data=r.json()
                features=data.get("features") or []
                by_id={}
                by_name={}
                for feature in features:
                    attrs=(feature or {}).get("attributes") or {}
                    lat=safe_float(attrs.get("latitude"))
                    lon=safe_float(attrs.get("longitude"))
                    if lat is None or lon is None:
                        continue
                    sid=str(attrs.get("idestacao") or "").strip()
                    name=str(attrs.get("nomeestacao") or "").strip()
                    coords=(lat,lon)
                    if sid:
                        by_id[sid]=coords
                    if name:
                        by_name[norm(name)]=coords
                if by_id or by_name:
                    return {
                        "status":"ok",
                        "by_id":by_id,
                        "by_name":by_name,
                        "count":max(len(by_id),len(by_name)),
                        "url":r.url,
                        "registry":endpoint,
                        "query":where,
                        "error":None,
                    }
            except Exception as exc:
                errors.append(f"{endpoint}: {exc}")
                continue
    return {
        "status":"empty" if not errors else "unavailable",
        "by_id":{},
        "by_name":{},
        "count":0,
        "url":last_url,
        "registry":None,
        "query":None,
        "error":"; ".join(errors)[:500] if errors else None,
    }

def fetch_cemaden_pluviometers(previous):
    prev=previous.get("pluviometers") or {}
    now=datetime.now(TZ)
    try:
        r=requests.get(CEMADEN_PLUVIO,timeout=25,headers={"User-Agent":"Mozilla/5.0 HCNSC-Alerta/1.0","Accept":"application/json,text/plain,*/*"})
        r.raise_for_status()
        data=json.loads(r.text)
        if not isinstance(data,list):
            raise RuntimeError("Formato inesperado do endpoint público de pluviômetros")

        coord_registry=fetch_cemaden_coordinate_registry()
        stations=[]
        for row in data:
            if not isinstance(row,dict) or str(row.get("codibge"))!="3306008":
                continue
            raw_dt=str(row.get("datahoraUltimovalor") or "").strip()
            observed=None
            try:
                observed=datetime.strptime(raw_dt,"%d/%m/%y %H:%M").replace(tzinfo=ZoneInfo("UTC")).astimezone(TZ)
            except Exception:
                pass
            age=round((now-observed).total_seconds()/3600,1) if observed else None
            # A reading materially ahead of the collector clock is kept as raw
            # evidence, but is not treated as a fresh/valid reading for summaries.
            # Small clock skew up to 15 minutes is tolerated.
            if age is None:
                health="stale"
            elif age < -0.25:
                health="time_anomaly"
            elif age <= 2:
                health="ok"
            else:
                health="stale"
            def mm(key):
                v=row.get(key)
                return safe_float(v) if v not in ("-",None,"") else None
            station_name=row.get("nomeestacao") or "Estação sem nome"
            fallback_coords=KNOWN_CEMADEN_COORDS.get(norm(station_name))
            registry_coords=(
                (coord_registry.get("by_id") or {}).get(str(row.get("idestacao") or "").strip())
                or (coord_registry.get("by_name") or {}).get(norm(station_name))
            )
            row_lat=safe_float(row.get("latitude"))
            row_lon=safe_float(row.get("longitude"))
            station_lat=(
                row_lat if row_lat is not None
                else registry_coords[0] if registry_coords
                else fallback_coords[0] if fallback_coords
                else None
            )
            station_lon=(
                row_lon if row_lon is not None
                else registry_coords[1] if registry_coords
                else fallback_coords[1] if fallback_coords
                else None
            )
            coordinate_method=(
                "cemaden_endpoint" if row_lat is not None and row_lon is not None
                else "public_gis_registry" if registry_coords
                else "regional_hydrometeorological_register" if fallback_coords
                else None
            )
            stations.append({
                "id":row.get("idestacao"),
                "name":station_name,
                "status":health,
                "latitude":station_lat,
                "longitude":station_lon,
                "coordinate_method":coordinate_method,
                "distance_to_hcnsc_km":round(distance_km(HCNSC_LAT,HCNSC_LON,station_lat,station_lon),2) if station_lat is not None and station_lon is not None else None,
                "raw_timestamp":raw_dt or None,
                "observed_at":observed.isoformat() if observed else raw_dt or None,
                "age_hours":age,
                "last_mm":mm("ultimovalor"),
                "acc1h_mm":mm("acc1hr"),
                "acc3h_mm":mm("acc3hr"),
                "acc6h_mm":mm("acc6hr"),
                "acc12h_mm":mm("acc12hr"),
                "acc24h_mm":mm("acc24hr"),
                "acc48h_mm":mm("acc48hr"),
                "acc72h_mm":mm("acc72hr"),
                "acc96h_mm":mm("acc96hr"),
                "station_type":row.get("tipoestacao"),
            })

        if not stations:
            raise RuntimeError("Nenhum pluviômetro de Três Rios encontrado")

        recent=[s for s in stations if s["status"]=="ok"]
        anomalies=[s for s in stations if s["status"]=="time_anomaly"]
        georeferenced=[s for s in stations if isinstance(s.get("distance_to_hcnsc_km"),(int,float))]
        georeferenced_recent=[s for s in recent if isinstance(s.get("distance_to_hcnsc_km"),(int,float))]
        if georeferenced_recent:
            nearest=min(georeferenced_recent,key=lambda s:s["distance_to_hcnsc_km"])
        elif recent:
            def reference_score(station):
                rain_keys=("acc1h_mm","acc3h_mm","acc6h_mm","acc12h_mm","acc24h_mm","acc48h_mm","acc72h_mm","acc96h_mm")
                completeness=sum(1 for key in rain_keys if isinstance(station.get(key),(int,float)))
                return (-completeness, station.get("age_hours") if isinstance(station.get("age_hours"),(int,float)) else 9999, station.get("name") or "")
            nearest=sorted(recent,key=reference_score)[0]
        elif georeferenced:
            nearest=min(georeferenced,key=lambda s:s["distance_to_hcnsc_km"])
        else:
            nearest=stations[0] if stations else None
        def highest(key):
            # Only fresh, internally time-consistent stations contribute to
            # dashboard maxima. Stale/anomalous values remain visible in the table.
            vals=[s for s in recent if isinstance(s.get(key),(int,float))]
            if not vals: return {"value":None,"station":None}
            top=max(vals,key=lambda s:s[key])
            return {"value":top[key],"station":top["name"]}

        stations.sort(
            key=lambda s:(
                s.get("distance_to_hcnsc_km") is None,
                s.get("distance_to_hcnsc_km") if s.get("distance_to_hcnsc_km") is not None else 9999,
                s["name"],
            )
        )
        return {
            "provider":"CEMADEN",
            "status":"ok" if recent else "stale",
            "source_note":"Chuvas do Mapa Interativo do CEMADEN; horários em UTC convertidos para Brasília. Coordenadas vêm do próprio endpoint quando disponíveis, de cadastro GIS público ou do cadastro hidrometeorológico regional da Região Hidrográfica do Médio Paraíba (base Dez/2020), exclusivamente para distância ao HCNSC.",
            "collected_at":now.isoformat(),
            "url":CEMADEN_PLUVIO,
            "total_stations":len(stations),
            "recent_stations":len(recent),
            "hidden_stations":len(stations)-len(recent),
            "time_anomaly_stations":len(anomalies),
            "georeferenced_stations":len(georeferenced),
            "coordinate_source":"CEMADEN + registros geográficos públicos para geolocalização",
            "static_coordinate_source":CEMADEN_STATIC_COORD_SOURCE,
            "coordinate_registry_status":coord_registry.get("status"),
            "coordinate_registry_url":coord_registry.get("url"),
            "coordinate_registry_provider":coord_registry.get("registry"),
            "coordinate_registry_query":coord_registry.get("query"),
            "coordinate_registry_error":coord_registry.get("error"),
            "highest_1h":highest("acc1h_mm"),
            "highest_24h":highest("acc24h_mm"),
            "nearest_to_hcnsc":nearest,
            "nearest_note":"A referência prioriza estação recente e georreferenciada. Se nenhuma estação recente tiver coordenada consolidada, o HCNSC Alerta usa temporariamente a estação recente com melhor conjunto de acumulados, sem inventar distância.",
            "stations":stations,
            "error":None,
        }
    except Exception as exc:
        fallback=dict(prev)
        fallback.update({
            "provider":"CEMADEN",
            "status":"unavailable",
            "collected_at":now.isoformat(),
            "url":CEMADEN_PLUVIO,
            "error":str(exc)[:600],
        })
        return fallback

def parse_portuguese_datetime(text):
    if not text:
        return None
    value=re.sub(r"\\s+"," ",str(text)).strip()
    parsed=parse_dt(value)
    if parsed:
        return parsed
    months={
        "JANEIRO":1,"FEVEREIRO":2,"MARCO":3,"ABRIL":4,"MAIO":5,"JUNHO":6,
        "JULHO":7,"AGOSTO":8,"SETEMBRO":9,"OUTUBRO":10,"NOVEMBRO":11,"DEZEMBRO":12,
    }
    clean=norm(value)
    m=re.search(r"(\\d{1,2})\\s+(JANEIRO|FEVEREIRO|MARCO|ABRIL|MAIO|JUNHO|JULHO|AGOSTO|SETEMBRO|OUTUBRO|NOVEMBRO|DEZEMBRO)\\s+(\\d{4})(?:\\s+(\\d{1,2}):(\\d{2}))?",clean)
    if not m:
        return None
    try:
        return datetime(int(m.group(3)),months[m.group(2)],int(m.group(1)),int(m.group(4) or 0),int(m.group(5) or 0),tzinfo=TZ)
    except Exception:
        return None

def article_datetime(soup):
    for attrs in (
        {"property":"article:published_time"},
        {"itemprop":"datePublished"},
        {"name":"date"},
    ):
        tag=soup.find("meta",attrs=attrs)
        if tag and tag.get("content"):
            d=parse_portuguese_datetime(tag.get("content"))
            if d: return d
    text=soup.get_text(" ",strip=True)
    return parse_portuguese_datetime(text[:2500])

def fetch_defesa_civil(previous):
    """Monitor the official Três Rios Civil Defense page and recent publications.

    Municipal content is used conservatively. Generic news never escalates the
    HCNSC level. Only an explicit, recent operational stage or severe alert can
    contribute as an official risk signal.
    """
    prev=(previous.get("sources") or {}).get("defesa_civil",{})
    now=datetime.now(TZ)
    headers={"User-Agent":"Mozilla/5.0 HCNSC-Alerta/1.0","Accept":"text/html,application/xhtml+xml"}
    channels={
        "emergency":"199",
        "sms":"40199",
        "phone":"(24) 2220-2408",
        "home":DEFESA_CIVIL_HOME,
        "bulletin":DEFESA_CIVIL_HOME,
        "whatsapp":None,
    }
    route_results={}
    articles=[]
    direct_ok=False
    error=None

    try:
        r=requests.get(DEFESA_CIVIL_HOME,timeout=10,headers=headers,allow_redirects=True)
        r.raise_for_status()
        direct_ok=True
        route_results["portal"]={"status":"ok","url":r.url,"http_status":r.status_code}
        soup=BeautifulSoup(r.text,"html.parser")
        candidates=[]
        seen=set()
        for a in soup.find_all("a",href=True):
            title=re.sub(r"\s+"," ",a.get_text(" ",strip=True)).strip()
            href=str(a.get("href") or "").strip()
            if not title or not href:
                continue
            if href.startswith("/"):
                href="https://tresrios.rj.gov.br"+href
            elif not href.startswith("http"):
                href="https://tresrios.rj.gov.br/"+href.lstrip("/")
            hay=norm(title+" "+href)
            if "TRESRIOS.RJ.GOV.BR" not in norm(href):
                continue
            if not any(term in hay for term in ("DEFESA CIVIL","ALERTA","CHUVA","DESLIZ","INUND","METEOROLOG")):
                continue
            key=(title,href)
            if key in seen:
                continue
            seen.add(key)
            candidates.append((title,href))
            if len(candidates)>=12:
                break

        def fetch_article(item):
            title,href=item
            try:
                ar=requests.get(href,timeout=7,headers=headers,allow_redirects=True)
                ar.raise_for_status()
                art=BeautifulSoup(ar.text,"html.parser")
                h=art.find("h1") or art.find("h2")
                full_title=re.sub(r"\s+"," ",h.get_text(" ",strip=True) if h else title).strip()
                published=article_datetime(art)
                body=re.sub(r"\s+"," ",art.get_text(" ",strip=True)).strip()
                return {
                    "title":full_title or title,
                    "url":ar.url or href,
                    "published":published,
                    "text":body,
                    "normalized":norm(full_title+" "+body),
                }
            except Exception:
                return {"title":title,"url":href,"published":None,"text":title,"normalized":norm(title)}

        if candidates:
            with ThreadPoolExecutor(max_workers=min(6,len(candidates))) as ex:
                futures=[ex.submit(fetch_article,item) for item in candidates]
                for fut in as_completed(futures):
                    try: articles.append(fut.result())
                    except Exception: pass
    except Exception as exc:
        error=str(exc)[:400]
        route_results["portal"]={"status":"unavailable","url":DEFESA_CIVIL_HOME,"error":exc.__class__.__name__}

    articles.sort(
        key=lambda item:item.get("published") or datetime.min.replace(tzinfo=TZ),
        reverse=True,
    )
    latest=articles[0] if articles else None

    # Contextual RSS fallback. It never escalates risk.
    cache_result={"provider":"Google News RSS","status":"not_used","used":False,"can_escalate":False,"routes":{}}
    if not latest:
        try:
            q='site:tresrios.rj.gov.br "Defesa Civil" "Três Rios"'
            nr=requests.get(
                DEFESA_CIVIL_NEWS_INDEX,
                params={"q":q,"hl":"pt-BR","gl":"BR","ceid":"BR:pt-419"},
                timeout=8,
                headers={"User-Agent":"HCNSC-Alerta/1.0","Accept":"application/rss+xml,application/xml,text/xml,*/*"},
            )
            nr.raise_for_status()
            root=ET.fromstring(nr.text)
            items=[]
            for item in root.findall(".//item")[:15]:
                title=(item.findtext("title") or "").strip()
                link=(item.findtext("link") or "").strip()
                pub_raw=(item.findtext("pubDate") or "").strip()
                title_norm=norm(title)
                if "DEFESA CIVIL" not in title_norm:
                    continue
                if title_norm in (
                    "SECRETARIA DE SAUDE E DEFESA CIVIL - PREFEITURA MUNICIPAL - TRES RIOS",
                    "SECRETARIA DE PROTECAO E DEFESA CIVIL - TRES RIOS",
                    "DEFESA CIVIL - TRES RIOS",
                ):
                    continue
                published=None
                try:
                    published=parsedate_to_datetime(pub_raw)
                    published=published.astimezone(TZ) if published.tzinfo else published.replace(tzinfo=TZ)
                except Exception:
                    pass
                items.append({"title":title,"url":link,"published":published,"text":title,"normalized":norm(title)})
            items.sort(key=lambda x:x.get("published") or datetime.min.replace(tzinfo=TZ),reverse=True)
            if items:
                latest=items[0]
                cache_result={
                    "provider":"Google News RSS","status":"ok","used":True,"can_escalate":False,
                    "routes":{"news_index":{"status":"ok","url":nr.url,"items":len(items)}},
                    "message":"Índice secundário ativo somente para contexto; não pode elevar o Nível HCNSC.",
                }
        except Exception as exc:
            cache_result={
                "provider":"Google News RSS","status":"unavailable","used":False,"can_escalate":False,
                "routes":{"news_index":{"status":"unavailable","error":exc.__class__.__name__}},
                "message":"Índice secundário indisponível.",
            }

    effective=None
    basis=None
    official_updated=None
    operational_signal=None
    for item in articles:
        published=item.get("published")
        if not published:
            continue
        age=(now-published).total_seconds()/3600
        if age < -0.5 or age > 24:
            continue
        hay=item.get("normalized") or ""
        level=None
        label=None
        if "ALERTA MAXIMO" in hay or "RISCO MUITO ALTO" in hay:
            level=5; label="Alerta máximo municipal recente"
        elif (
            ("ALERTA" in hay or "RISCO" in hay)
            and any(t in hay for t in ("DESLIZAMENTO","INUNDACAO","ENCHENTE","CHUVA INTENSA","TEMPESTADE"))
            and any(t in hay for t in ("DEFESA CIVIL","MUNICIPIO","TRÊS RIOS","TRES RIOS"))
        ):
            level=4; label="Alerta municipal recente"
        if level:
            effective=level
            basis=label
            official_updated=published
            operational_signal={
                "type":"municipal_alert",
                "label":label,
                "level":level,
                "published_at":published.isoformat(),
                "url":item.get("url"),
            }
            break

    latest_payload=None
    if latest:
        latest_payload={
            "title":latest.get("title"),
            "url":latest.get("url"),
            "published_at":latest.get("published").isoformat() if latest.get("published") else None,
            "verification":"direct" if direct_ok and latest in articles else "cache_index",
        }

    common={
        "name":"Defesa Civil",
        "provider":"Defesa Civil de Três Rios",
        "channels":channels,
        "routes":route_results,
        "route_summary":{"available":1 if direct_ok else 0,"total":1,"available_routes":["portal"] if direct_ok else []},
        "cache":cache_result,
        "cache_hint":None,
        "freshness":{"stage_hours":24,"operational_signal_hours":24},
        "collected_at":now.isoformat(),
        "url":DEFESA_CIVIL_HOME,
        "latest_news":latest_payload,
        "latest_bulletin":latest_payload,
    }
    if effective is not None:
        return {
            **common,
            "status":"ok","stage":None,"risk":LEVEL_LABELS.get(effective,"Alerta"),
            "level":effective,"basis":basis,
            "official_updated_at":official_updated.isoformat() if official_updated else None,
            "age_hours":round((now-official_updated).total_seconds()/3600,1) if official_updated else None,
            "operational_signal":operational_signal,
            "message":"Alerta operacional recente identificado em publicação oficial da Defesa Civil de Três Rios.",
            "error":None,
        }
    if direct_ok:
        return {
            **common,
            "status":"no_recent_update","stage":None,"risk":"Sem alerta operacional recente identificado",
            "level":None,"basis":None,"official_updated_at":None,"age_hours":None,
            "operational_signal":None,
            "last_known_stage":prev.get("stage") or prev.get("last_known_stage"),
            "last_known_level":prev.get("level") if prev.get("level") is not None else prev.get("last_known_level"),
            "last_known_updated_at":prev.get("official_updated_at") or prev.get("last_known_updated_at"),
            "message":"Portal oficial acessível; nenhuma publicação recente elegível como gatilho operacional foi identificada.",
            "error":None,
        }
    if cache_result.get("used"):
        return {
            **common,
            "status":"cache_only","stage":None,"risk":"Fonte direta indisponível · índice secundário ativo",
            "level":None,"basis":None,"official_updated_at":None,"age_hours":None,
            "operational_signal":None,
            "last_known_stage":prev.get("stage") or prev.get("last_known_stage"),
            "last_known_level":prev.get("level") if prev.get("level") is not None else prev.get("last_known_level"),
            "last_known_updated_at":prev.get("official_updated_at") or prev.get("last_known_updated_at"),
            "message":"Fonte direta indisponível; índice secundário disponível apenas para contexto.",
            "error":error,
        }
    return {
        **common,
        "status":"source_unconfirmed","stage":None,"risk":"Sem informação oficial recente confirmada",
        "level":None,"basis":None,"official_updated_at":None,"age_hours":None,
        "operational_signal":None,
        "last_known_stage":prev.get("stage") or prev.get("last_known_stage"),
        "last_known_level":prev.get("level") if prev.get("level") is not None else prev.get("last_known_level"),
        "last_known_updated_at":prev.get("official_updated_at") or prev.get("last_known_updated_at"),
        "message":"Canal oficial e índice secundário indisponíveis nesta coleta. Fonte indisponível ≠ ausência de risco.",
        "error":error,
    }

def _plain_excerpt(text, limit=460):
    text=re.sub(r"<[^>]+>"," ",str(text or ""))
    text=re.sub(r"\s+"," ",text).strip()
    if len(text)<=limit:
        return text
    return text[:limit].rsplit(" ",1)[0]+"…"

def _elovias_get(endpoint, timeout=12):
    r=requests.get(
        ELOVIAS_API+endpoint,
        timeout=timeout,
        headers={"User-Agent":"Mozilla/5.0 HCNSC-Alerta/1.0","Accept":"application/json"},
    )
    r.raise_for_status()
    return r.json()

def _iso_sort_value(item):
    return str((item or {}).get("data") or (item or {}).get("dataTempo") or "")

def fetch_roads(previous):
    """Monitor the two federal road corridors most relevant to Três Rios."""
    prev=previous.get("roads") or {}
    now=datetime.now(TZ)

    # BR-040 / ELOvias
    elo_prev=prev.get("br040") or {}
    elo_status={}
    alerts=[]; weather_points=[]; news=[]; bulletins=[]
    for key,endpoint in (("alerts","aviso"),("weather","mapa/trecho-principal"),("news","noticia/recente"),("bulletins","boletim")):
        try:
            data=_elovias_get(endpoint)
            elo_status[key]="ok"
            if key=="alerts": alerts=data if isinstance(data,list) else []
            elif key=="weather": weather_points=data if isinstance(data,list) else []
            elif key=="news": news=data if isinstance(data,list) else []
            elif key=="bulletins": bulletins=data if isinstance(data,list) else []
        except Exception:
            elo_status[key]="unavailable"
            prior=elo_prev.get(key)
            if key=="alerts" and isinstance(prior,list): alerts=prior
            if key=="weather" and isinstance(prior,list): weather_points=prior
            if key=="news" and isinstance(prior,list): news=prior
            if key=="bulletins" and isinstance(prior,list): bulletins=prior

    tres_rios_weather=None
    for item in weather_points:
        if norm(item.get("nome")) in ("TRES RIOS","TRES RIOS RJ"):
            tres_rios_weather={
                "name":"Três Rios",
                "condition":item.get("condicaoTempoDescription"),
                "temperature_c":safe_float(item.get("temperatura")),
                "wind_speed_kmh":safe_float(item.get("vento")),
                "wind_direction":item.get("ventoDirecaoDescription"),
                "updated_at":item.get("dataTempo"),
                "source":"ELOvias",
                "is_road_official_weather":True,
            }
            break

    news=sorted([x for x in news if isinstance(x,dict)],key=_iso_sort_value,reverse=True)
    bulletins=sorted([x for x in bulletins if isinstance(x,dict)],key=_iso_sort_value,reverse=True)

    def elo_news_payload(item):
        if not item: return None
        slug=item.get("slug")
        return {
            "title":item.get("titulo"),
            "summary":_plain_excerpt(item.get("chamada") or item.get("texto"),520),
            "published_at":item.get("data"),
            "url":("https://elovias.com.br/noticias/"+slug) if slug else None,
            "provider":"ELOvias",
            "road":"BR-040",
        }

    tr_news=[]
    for item in news:
        hay=norm(" ".join([str(item.get("titulo") or ""),str(item.get("chamada") or ""),str(item.get("texto") or "")]))
        if "TRES RIOS" in hay or "BR-040" in hay:
            tr_news.append(item)

    elo_active=[]
    for a in alerts[:12]:
        if not isinstance(a,dict): continue
        text=norm(" ".join(str(a.get(k) or "") for k in ("titulo","nome","assunto","texto","mensagem","descricao","chamada")))
        # Alert endpoint may be broad; retain only corridor-relevant items when locality is stated.
        if any(loc in text for loc in ("TRES RIOS","COMENDADOR LEVY GASPARIAN","PARAIBA DO SUL","BR-040")) or not text:
            elo_active.append({
                "title":a.get("titulo") or a.get("nome") or a.get("assunto") or "Aviso ELOvias",
                "summary":_plain_excerpt(a.get("texto") or a.get("mensagem") or a.get("descricao") or a.get("chamada"),500),
                "start_at":a.get("dataInicio") or a.get("inicio") or a.get("data"),
                "end_at":a.get("dataFim") or a.get("fim"),
                "provider":"ELOvias","road":"BR-040",
            })

    elo_connected=sum(1 for v in elo_status.values() if v=="ok")
    br040={
        "provider":"ELOvias",
        "road":"BR-040/RJ",
        "scope":"BR-040/RJ · acessos Rio–Três Rios–Juiz de Fora",
        "status":"ok" if elo_connected>=2 else ("degraded" if elo_connected else "unavailable"),
        "endpoint_status":elo_status,
        "active_alerts":elo_active,
        "news":[elo_news_payload(x) for x in tr_news[:8]],
        "latest_update":elo_news_payload(tr_news[0]) if tr_news else None,
        "weather":tres_rios_weather,
        "emergency_phone":"0800-040-0495",
        "accessibility_phone":"0800-040-1495",
        "whatsapp":"(21) 98040-0113",
        "home_url":ELOVIAS_HOME,
        "map_url":ELOVIAS_MAP,
    }

    # BR-393 / K-Infra Rodovia do Aço
    kin_prev=prev.get("br393") or {}
    kin_status={"home":"unavailable","contact":"unavailable"}
    kin_errors=[]
    kin_text=""
    for key,url in (("home",KINFRA_HOME),("contact",KINFRA_CONTACT)):
        try:
            r=requests.get(url,timeout=10,headers={"User-Agent":"Mozilla/5.0 HCNSC-Alerta/1.0","Accept":"text/html,application/xhtml+xml"})
            r.raise_for_status()
            kin_status[key]="ok"
            kin_text+=" "+re.sub(r"\s+"," ",BeautifulSoup(r.text,"html.parser").get_text(" ",strip=True))
        except Exception as exc:
            kin_errors.append(f"{key}: {exc}")
    kin_connected=sum(1 for v in kin_status.values() if v=="ok")
    br393={
        "provider":"K-Infra Rodovia do Aço",
        "road":"BR-393/RJ",
        "scope":"BR-393/RJ · Três Rios · Rodovia do Aço",
        "status":"ok" if kin_connected==2 else ("degraded" if kin_connected else "unavailable"),
        "endpoint_status":kin_status,
        "active_alerts":[],
        "latest_update":None,
        "emergency_phone":"0800 2853 393",
        "accessibility_phone":"0800 0959 393",
        "service_phone":"(24) 3512-0420",
        "home_url":KINFRA_HOME,
        "contact_url":KINFRA_CONTACT,
        "três_rios_sau":"Km 159,0 · Três Rios",
        "message":"Canal oficial da Rodovia do Aço consultado. Ausência de publicação identificada não equivale a garantia de tráfego livre.",
        "error":"; ".join(kin_errors)[:500] if kin_errors else None,
    }

    combined_alerts=elo_active
    states=[br040["status"],br393["status"]]
    status="ok" if all(x=="ok" for x in states) else ("degraded" if any(x in ("ok","degraded") for x in states) else "unavailable")
    latest=br040.get("latest_update")
    return {
        "provider":"ELOvias + K-Infra Rodovia do Aço",
        "scope":"BR-040/RJ + BR-393/RJ · acessos a Três Rios",
        "status":status,
        "source_mode":"dual_official",
        "endpoint_status":{"br040":br040.get("status"),"br393":br393.get("status")},
        "active_alerts":combined_alerts,
        "active_alert_count":len(combined_alerts),
        "no_active_alerts":False,
        "tres_rios_weather":tres_rios_weather,
        "latest_news":latest,
        "latest_serra_update":latest,
        "latest_schedule":None,
        "latest_bulletin":None,
        "alerts":combined_alerts,
        "weather":weather_points,
        "news":br040.get("news") or [],
        "bulletins":[],
        "emergency_phone":"ELOvias 0800-040-0495 · K-Infra 0800 2853 393",
        "accessibility_phone":"ELOvias 0800-040-1495 · K-Infra 0800 0959 393",
        "whatsapp":"ELOvias (21) 98040-0113",
        "home_url":ELOVIAS_HOME,
        "map_url":ELOVIAS_MAP,
        "br040":br040,
        "br393":br393,
        "collected_at":now.isoformat(),
        "message":"BR-040 e BR-393 consultadas em canais oficiais independentes. Fonte indisponível ou ausência de aviso não equivale a tráfego livre.",
        "error":None,
    }

def fetch_utilities(previous):
    """Check official Três Rios water and energy channels without inferring continuity."""
    prev=previous.get("utilities") or {}
    now=datetime.now(TZ)
    headers={"User-Agent":"Mozilla/5.0 HCNSC-Alerta/1.0","Accept":"text/html,application/xhtml+xml"}

    water_prev=prev.get("water") or {}
    try:
        r=requests.get(SAAETRI_HOME,timeout=10,headers=headers)
        r.raise_for_status()
        soup=BeautifulSoup(r.text,"html.parser")
        text_body=re.sub(r"\s+"," ",soup.get_text(" ",strip=True)).strip()
        water={
            "provider":"SAAETRI",
            "status":"ok",
            "url":SAAETRI_HOME,
            "operational_notice":None,
            "has_operational_notice":False,
            "phone":"(24) 2251-6950",
            "whatsapp":"(24) 9993-2649",
            "collected_at":now.isoformat(),
            "message":"Canal oficial do SAAETRI acessível. Acessibilidade do portal não prova continuidade do abastecimento no hospital.",
            "error":None,
        }
    except Exception as exc:
        water=dict(water_prev)
        water.update({
            "provider":"SAAETRI","status":"unavailable","url":SAAETRI_HOME,
            "collected_at":now.isoformat(),
            "message":"Canal oficial do SAAETRI indisponível nesta coleta. Fonte indisponível ≠ ausência de risco.",
            "error":str(exc)[:300],
        })

    energy_prev=prev.get("energy") or {}
    try:
        r=requests.get(ENEL_RIO_CHANNELS,timeout=10,headers=headers)
        r.raise_for_status()
        energy={
            "provider":"Enel Distribuição Rio","status":"ok","url":ENEL_RIO_CHANNELS,
            "phone":"0800 28 00 120","whatsapp":"(21) 99601-9608",
            "collected_at":now.isoformat(),
            "message":"Canal oficial acessível. A situação específica da unidade consumidora deve ser confirmada pelos canais da Enel quando necessário.",
            "error":None,
        }
    except Exception as exc:
        energy=dict(energy_prev)
        energy.update({
            "provider":"Enel Distribuição Rio","status":"unavailable","url":ENEL_RIO_CHANNELS,
            "collected_at":now.isoformat(),
            "message":"Canal oficial de energia indisponível nesta coleta. Fonte indisponível ≠ ausência de risco.",
            "error":str(exc)[:300],
        })
    states=[water.get("status"),energy.get("status")]
    return {
        "status":"ok" if all(x=="ok" for x in states) else ("degraded" if any(x=="ok" for x in states) else "unavailable"),
        "water":water,"energy":energy,"collected_at":now.isoformat(),
    }

def history_snapshot(payload):
    sources=payload.get("sources") or {}
    pv=payload.get("pluviometers") or {}
    bingen=pv.get("nearest_to_hcnsc") or next(
        (s for s in (pv.get("stations") or []) if (s or {}).get("status")=="ok"),
        None,
    )
    return {
        "ts":payload.get("generated_at"),
        "level":(payload.get("overall") or {}).get("level"),
        "label":(payload.get("overall") or {}).get("label"),
        "reason":(payload.get("overall") or {}).get("reason"),
        "geological":{
            "level":(sources.get("cemaden_geological") or {}).get("level"),
            "risk":(sources.get("cemaden_geological") or {}).get("risk"),
            "status":(sources.get("cemaden_geological") or {}).get("status"),
        },
        "hydrological":{
            "level":(sources.get("cemaden_hydrological") or {}).get("level"),
            "risk":(sources.get("cemaden_hydrological") or {}).get("risk"),
            "status":(sources.get("cemaden_hydrological") or {}).get("status"),
        },
        "inmet":{
            "level":(sources.get("inmet_alerts") or {}).get("level"),
            "risk":(sources.get("inmet_alerts") or {}).get("risk"),
            "status":(sources.get("inmet_alerts") or {}).get("status"),
        },
        "defesa_civil":{
            "level":(sources.get("defesa_civil") or {}).get("level"),
            "risk":(sources.get("defesa_civil") or {}).get("risk"),
            "status":(sources.get("defesa_civil") or {}).get("status"),
            "stage":(sources.get("defesa_civil") or {}).get("stage"),
            "basis":(sources.get("defesa_civil") or {}).get("basis"),
            "official_updated_at":(sources.get("defesa_civil") or {}).get("official_updated_at"),
            "signal_type":((sources.get("defesa_civil") or {}).get("operational_signal") or {}).get("type"),
            "signal_label":((sources.get("defesa_civil") or {}).get("operational_signal") or {}).get("label"),
        },
        "pluviometers":{
            "highest_1h":pv.get("highest_1h"),
            "highest_24h":pv.get("highest_24h"),
            "recent_stations":pv.get("recent_stations"),
            "total_stations":pv.get("total_stations"),
            "bingen":{
                "name":bingen.get("name"),
                "status":bingen.get("status"),
                "observed_at":bingen.get("observed_at"),
                "age_hours":bingen.get("age_hours"),
                "distance_to_hcnsc_km":bingen.get("distance_to_hcnsc_km"),
                "acc1h_mm":bingen.get("acc1h_mm"),
                "acc3h_mm":bingen.get("acc3h_mm"),
                "acc6h_mm":bingen.get("acc6h_mm"),
                "acc12h_mm":bingen.get("acc12h_mm"),
                "acc24h_mm":bingen.get("acc24h_mm"),
                "acc48h_mm":bingen.get("acc48h_mm"),
                "acc72h_mm":bingen.get("acc72h_mm"),
                "acc96h_mm":bingen.get("acc96h_mm"),
            } if bingen else None,
        },
        "weather":{
            "status":(payload.get("weather_reference") or {}).get("status") or (payload.get("weather") or {}).get("status"),
            "provider":(payload.get("weather_reference") or {}).get("provider") or (payload.get("weather") or {}).get("provider"),
            "temperature_c":(payload.get("weather_reference") or {}).get("temperature_c") if (payload.get("weather_reference") or {}).get("temperature_c") is not None else (payload.get("weather") or {}).get("temperature_c"),
        },
    }

def persist_history(payload):
    os.makedirs(ARCHIVE_DIR,exist_ok=True)
    now=parse_dt(payload.get("generated_at")) or datetime.now(TZ)
    snap=history_snapshot(payload)

    history=[]
    try:
        with open(HISTORY_OUT,"r",encoding="utf-8") as f:
            raw=json.load(f)
            history=raw.get("snapshots",[]) if isinstance(raw,dict) else raw
    except Exception:
        try:
            raw=get_json(HISTORY_URL,timeout=10)
            history=raw.get("snapshots",[]) if isinstance(raw,dict) else raw
        except Exception:
            history=[]

    history=[x for x in history if isinstance(x,dict) and x.get("ts")!=snap.get("ts")]
    history.append(snap)
    history.sort(key=lambda x:str(x.get("ts") or ""))
    cutoff=now-timedelta(days=90)
    rolling=[]
    for x in history:
        d=parse_dt(x.get("ts"))
        if d and d>=cutoff:
            rolling.append(x)
    with open(HISTORY_OUT,"w",encoding="utf-8") as f:
        json.dump({
            "schema_version":1,
            "generated_at":now.isoformat(),
            "retention_days":90,
            "snapshots":rolling,
        },f,ensure_ascii=False,separators=(",",":"))

    month=now.strftime("%Y-%m")
    archive_path=os.path.join(ARCHIVE_DIR,month+".json")
    archive=[]
    try:
        with open(archive_path,"r",encoding="utf-8") as f:
            raw=json.load(f)
            archive=raw.get("snapshots",[]) if isinstance(raw,dict) else raw
    except Exception:
        archive=[]
    archive=[x for x in archive if isinstance(x,dict) and x.get("ts")!=snap.get("ts")]
    archive.append(snap)
    archive.sort(key=lambda x:str(x.get("ts") or ""))
    with open(archive_path,"w",encoding="utf-8") as f:
        json.dump({"schema_version":1,"month":month,"snapshots":archive},f,ensure_ascii=False,separators=(",",":"))

    months=[]
    for name in sorted(os.listdir(ARCHIVE_DIR),reverse=True):
        if not re.fullmatch(r"\d{4}-\d{2}\.json",name):
            continue
        path=os.path.join(ARCHIVE_DIR,name)
        count=None
        try:
            with open(path,"r",encoding="utf-8") as f:
                raw=json.load(f)
                count=len(raw.get("snapshots",[]) if isinstance(raw,dict) else raw)
        except Exception:
            pass
        months.append({"month":name[:-5],"file":"data/archive/"+name,"snapshots":count})
    with open(HISTORY_INDEX_OUT,"w",encoding="utf-8") as f:
        json.dump({"schema_version":1,"generated_at":now.isoformat(),"months":months},f,ensure_ascii=False,separators=(",",":"))

def climate_period(dt):
    hour=dt.hour
    if 0 <= hour < 6:
        return "madrugada", "Madrugada"
    if 6 <= hour < 12:
        return "manha", "Manhã"
    if 12 <= hour < 18:
        return "tarde", "Tarde"
    return "noite", "Noite"

def climate_sample(weather, weather_reference, now):
    official_ok=(weather or {}).get("status")=="ok" and (weather or {}).get("temperature_c") is not None
    source=weather if official_ok else weather_reference
    if not isinstance(source,dict) or source.get("status")!="ok":
        return None
    temp=safe_float(source.get("temperature_c"))
    if temp is None:
        return None
    return {
        "observed_at":source.get("observed_at") or now.isoformat(),
        "provider":source.get("provider"),
        "source_type":"oficial" if official_ok else "complementar",
        "condition":source.get("condition") or ("Observação meteorológica" if official_ok else "Condição atual"),
        "weather_code":source.get("weather_code"),
        "temperature_c":temp,
        "apparent_temperature_c":safe_float(source.get("apparent_temperature_c")),
        "humidity_pct":safe_float(source.get("humidity_pct")),
        "precipitation_mm":safe_float(source.get("precipitation_mm") if source.get("precipitation_mm") is not None else source.get("rain_1h_mm")),
        "wind_speed_kmh":safe_float(source.get("wind_speed_kmh")),
        "wind_gust_kmh":safe_float(source.get("wind_gust_kmh")),
    }

def persist_climate_history(weather, weather_reference):
    now=datetime.now(TZ)
    period_key,period_label=climate_period(now)
    if not period_key:
        return

    sample=climate_sample(weather,weather_reference,now)
    if not sample:
        return

    try:
        with open(CLIMATE_HISTORY_OUT,"r",encoding="utf-8") as f:
            raw=json.load(f)
            periods=raw.get("periods",[]) if isinstance(raw,dict) else raw
    except Exception:
        periods=[]

    date_key=now.strftime("%Y-%m-%d")
    existing=None
    for item in periods:
        if isinstance(item,dict) and item.get("date")==date_key and item.get("period")==period_key:
            existing=item
            break

    if existing is None:
        existing={
            "date":date_key,
            "period":period_key,
            "period_label":period_label,
            "first_observed_at":sample["observed_at"],
            "last_observed_at":sample["observed_at"],
            "samples":0,
            "provider":sample.get("provider"),
            "source_type":sample.get("source_type"),
            "condition":sample.get("condition"),
            "weather_code":sample.get("weather_code"),
            "temperature_min_c":sample.get("temperature_c"),
            "temperature_max_c":sample.get("temperature_c"),
            "temperature_last_c":sample.get("temperature_c"),
            "apparent_temperature_c":sample.get("apparent_temperature_c"),
            "humidity_min_pct":sample.get("humidity_pct"),
            "humidity_max_pct":sample.get("humidity_pct"),
            "humidity_last_pct":sample.get("humidity_pct"),
            "precipitation_last_mm":sample.get("precipitation_mm"),
            "wind_speed_last_kmh":sample.get("wind_speed_kmh"),
            "wind_gust_max_kmh":sample.get("wind_gust_kmh"),
        }
        periods.append(existing)

    existing["samples"]=int(existing.get("samples") or 0)+1
    existing["last_observed_at"]=sample["observed_at"]
    existing["provider"]=sample.get("provider")
    existing["source_type"]=sample.get("source_type")
    existing["condition"]=sample.get("condition")
    existing["weather_code"]=sample.get("weather_code")
    existing["temperature_last_c"]=sample.get("temperature_c")
    existing["apparent_temperature_c"]=sample.get("apparent_temperature_c")
    existing["humidity_last_pct"]=sample.get("humidity_pct")
    existing["precipitation_last_mm"]=sample.get("precipitation_mm")
    existing["wind_speed_last_kmh"]=sample.get("wind_speed_kmh")

    for key,value,mode in (
        ("temperature_min_c",sample.get("temperature_c"),"min"),
        ("temperature_max_c",sample.get("temperature_c"),"max"),
        ("humidity_min_pct",sample.get("humidity_pct"),"min"),
        ("humidity_max_pct",sample.get("humidity_pct"),"max"),
        ("wind_gust_max_kmh",sample.get("wind_gust_kmh"),"max"),
    ):
        if value is None:
            continue
        old=existing.get(key)
        if old is None:
            existing[key]=value
        elif mode=="min":
            existing[key]=min(old,value)
        else:
            existing[key]=max(old,value)

    cutoff=(now-timedelta(days=90)).date()
    kept=[]
    for item in periods:
        try:
            d=datetime.strptime(item.get("date",""),"%Y-%m-%d").date()
            if d>=cutoff:
                kept.append(item)
        except Exception:
            continue

    order={"madrugada":0,"manha":1,"tarde":2,"noite":3}
    kept.sort(key=lambda x:(x.get("date",""),order.get(x.get("period"),9)))
    with open(CLIMATE_HISTORY_OUT,"w",encoding="utf-8") as f:
        json.dump({
            "schema_version":1,
            "generated_at":now.isoformat(),
            "retention_days":90,
            "period_definition":{
                "madrugada":"00:00–05:59",
                "manha":"06:00–11:59",
                "tarde":"12:00–17:59",
                "noite":"18:00–23:59"
            },
            "periods":kept,
        },f,ensure_ascii=False,separators=(",",":"))

def fetch_inmet_forecast(previous):
    prev=previous.get("forecast") or {}
    now=datetime.now(TZ)
    try:
        data=get_json(INMET_FORECAST, timeout=25)
        root=data
        if isinstance(data,dict) and "3306008" in data:
            root=data["3306008"]
        if not isinstance(root,dict):
            raise RuntimeError("Formato inesperado da previsão INMET")

        days=[]
        for date_key, day in sorted(root.items(), key=lambda kv: str(kv[0])):
            if not isinstance(day,dict):
                continue
            periods=[]
            for period_name in ("manha","tarde","noite"):
                p=day.get(period_name)
                if isinstance(p,dict):
                    periods.append((period_name,p))
            if not periods and any(k in day for k in ("resumo","tempo","temp_min","temp_max")):
                periods=[("dia",day)]
            if not periods:
                continue

            mins=[safe_float(p.get("temp_min")) for _,p in periods]
            maxs=[safe_float(p.get("temp_max")) for _,p in periods]
            hum_min=[safe_float(p.get("umidade_min")) for _,p in periods]
            hum_max=[safe_float(p.get("umidade_max")) for _,p in periods]
            mins=[v for v in mins if v is not None]
            maxs=[v for v in maxs if v is not None]
            hum_min=[v for v in hum_min if v is not None]
            hum_max=[v for v in hum_max if v is not None]

            summaries=[]
            period_payload=[]
            for name,p in periods:
                summary=str(p.get("resumo") or p.get("tempo") or "").strip()
                if summary and summary not in summaries:
                    summaries.append(summary)
                period_payload.append({
                    "period":name,
                    "summary":summary or None,
                    "temperature_min_c":safe_float(p.get("temp_min")),
                    "temperature_max_c":safe_float(p.get("temp_max")),
                    "humidity_min_pct":safe_float(p.get("umidade_min")),
                    "humidity_max_pct":safe_float(p.get("umidade_max")),
                    "wind_direction":p.get("dir_vento"),
                    "wind_intensity":p.get("int_vento"),
                    "weekday":p.get("dia_semana"),
                })

            days.append({
                "date":str(date_key),
                "summary":" / ".join(summaries) if summaries else "Previsão disponível",
                "temperature_min_c":min(mins) if mins else None,
                "temperature_max_c":max(maxs) if maxs else None,
                "humidity_min_pct":min(hum_min) if hum_min else None,
                "humidity_max_pct":max(hum_max) if hum_max else None,
                "periods":period_payload,
            })
            if len(days)>=3:
                break

        if not days:
            raise RuntimeError("Previsão INMET sem dias válidos")

        return {
            "provider":"INMET",
            "status":"ok",
            "municipality_code":"3306008",
            "municipality":"Três Rios/RJ",
            "collected_at":now.isoformat(),
            "url":INMET_FORECAST,
            "days":days,
            "error":None,
        }
    except Exception as exc:
        fallback=dict(prev)
        fallback.update({
            "provider":"INMET",
            "status":"unavailable",
            "municipality_code":"3306008",
            "municipality":"Três Rios/RJ",
            "collected_at":now.isoformat(),
            "url":INMET_FORECAST,
            "error":str(exc)[:600],
        })
        return fallback

OBS_RAIN_1H_MM=20.0
OBS_RAIN_24H_MM=50.0
BINGEN_SAFE_MAX_AGE_HOURS=2.0
BINGEN_SAFE_SHORT_MM=20.0
BINGEN_SAFE_24H_MM=50.0
DEESCALATION_OFFICIAL_HOLD_HOURS=2
DEESCALATION_CONFIRMATIONS=3
DEESCALATION_CONFIRMATION_MIN_INTERVAL_MINUTES=15

def supplemental_observation_signals(forecast, pluviometers):
    """Complementary signals may raise only the HCNSC Observation level (2).

    They are intentionally not allowed to create Attention/Alert/Crisis by
    themselves. Higher levels remain driven by official risk/alert sources.
    """
    signals=[]

    if isinstance(forecast,dict) and forecast.get("status")=="ok":
        heavy_terms=(
            "CHUVA FORTE","CHUVAS FORTES","CHUVA INTENSA","CHUVAS INTENSAS",
            "TEMPESTADE","FORTES PANCADAS","CHUVA VOLUMOSA","ACUMULADO DE CHUVA",
        )
        for day in (forecast.get("days") or [])[:2]:
            summary=str((day or {}).get("summary") or "")
            normalized=norm(summary)
            if any(term in normalized for term in heavy_terms):
                label=str((day or {}).get("date") or "próximas horas")
                signals.append(f"Previsão INMET com chuva forte/intensa ou tempestade ({label}).")
                break

    if isinstance(pluviometers,dict) and pluviometers.get("status")=="ok":
        h1=pluviometers.get("highest_1h") or {}
        h24=pluviometers.get("highest_24h") or {}
        v1=safe_float(h1.get("value"))
        v24=safe_float(h24.get("value"))
        if v1 is not None and v1>=OBS_RAIN_1H_MM:
            station=h1.get("station") or "estação CEMADEN"
            signals.append(f"Chuva elevada no CEMADEN: {v1:.1f} mm em 1 h em {station}.")
        if v24 is not None and v24>=OBS_RAIN_24H_MM:
            station=h24.get("station") or "estação CEMADEN"
            signals.append(f"Acumulado elevado no CEMADEN: {v24:.1f} mm em 24 h em {station}.")

    return signals

def hydrological_normalization_evidence(hydro, geo, inmet, defesa, forecast, pluviometers):
    """Evaluate whether stale hydrological information can stop blocking de-escalation.

    estação pluviométrica de referência is a local corroborating sensor, not a replacement for the
    official CEMADEN hydrological risk classification.
    """
    evidence={
        "applicable":False,
        "safe":False,
        "reference_station":"estação pluviométrica de referência",
        "reference_distance_km":None,
        "bingen_status":None,
        "bingen_age_hours":None,
        "bingen_short_window":"1 h",
        "bingen_short_mm":None,
        "bingen_24h_mm":None,
        "city_highest_1h_mm":None,
        "city_highest_24h_mm":None,
        "inmet_clear":False,
        "forecast_and_pluvio_clear":False,
        "defesa_clear":False,
        "geological_old_allowed":False,
        "checks":{},
        "blockers":[],
    }

    hydro_status=(hydro or {}).get("status")
    hydro_age=safe_float((hydro or {}).get("age_hours"))
    evidence["applicable"]=bool(
        hydro_status=="no_recent_update"
        and hydro_age is not None
        and hydro_age>=(hydro or {}).get("freshness_limit_hours",24)
    )
    if not evidence["applicable"]:
        evidence["blockers"].append("Hidrológico ainda não está em condição de dado antigo elegível.")
        return evidence

    stations=(pluviometers or {}).get("stations") or []
    bingen=(pluviometers or {}).get("nearest_to_hcnsc") or next(
        (s for s in stations if (s or {}).get("status")=="ok"),
        None,
    )
    if not bingen:
        evidence["blockers"].append("Estação pluviométrica de referência não localizada na coleta.")
        return evidence

    evidence["reference_station"]=bingen.get("name")
    evidence["reference_distance_km"]=bingen.get("distance_to_hcnsc_km")
    evidence["bingen_status"]=bingen.get("status")
    evidence["bingen_age_hours"]=safe_float(bingen.get("age_hours"))

    bingen_1h=safe_float(bingen.get("acc1h_mm"))
    bingen_12h=safe_float(bingen.get("acc12h_mm"))
    bingen_24h=safe_float(bingen.get("acc24h_mm"))
    # If the 1 h accumulator is temporarily absent, a longer non-negative
    # accumulation below the same 20 mm threshold is a conservative upper bound
    # for any contained 1 h interval. Prefer 12 h, then 24 h.
    if bingen_1h is not None:
        short_mm=bingen_1h
        short_window="1 h"
    elif bingen_12h is not None:
        short_mm=bingen_12h
        short_window="12 h (substituto conservador)"
    else:
        short_mm=bingen_24h
        short_window="24 h (substituto conservador)"
    evidence["bingen_short_window"]=short_window
    evidence["bingen_short_mm"]=short_mm
    evidence["bingen_24h_mm"]=bingen_24h

    city_h1=safe_float(((pluviometers or {}).get("highest_1h") or {}).get("value"))
    city_h24=safe_float(((pluviometers or {}).get("highest_24h") or {}).get("value"))
    evidence["city_highest_1h_mm"]=city_h1
    evidence["city_highest_24h_mm"]=city_h24

    bingen_recent=(
        bingen.get("status")=="ok"
        and evidence["bingen_age_hours"] is not None
        and -0.25<=evidence["bingen_age_hours"]<=BINGEN_SAFE_MAX_AGE_HOURS
    )
    bingen_short_safe=short_mm is not None and short_mm<BINGEN_SAFE_SHORT_MM
    bingen_24h_safe=(
        evidence["bingen_24h_mm"] is not None
        and evidence["bingen_24h_mm"]<BINGEN_SAFE_24H_MM
    )
    city_1h_safe=city_h1 is not None and city_h1<OBS_RAIN_1H_MM
    city_24h_safe=city_h24 is not None and city_h24<OBS_RAIN_24H_MM

    inmet_clear=(
        (inmet or {}).get("status")=="ok"
        and isinstance((inmet or {}).get("level"),int)
        and int(inmet.get("level"))<=1
    )
    evidence["inmet_clear"]=inmet_clear

    supplemental=supplemental_observation_signals(forecast,pluviometers)
    evidence["forecast_and_pluvio_clear"]=not supplemental

    defesa_clear=not (
        (defesa or {}).get("status")=="ok"
        and isinstance((defesa or {}).get("level"),int)
        and int(defesa.get("level"))>=2
    )
    evidence["defesa_clear"]=defesa_clear

    geo_status=(geo or {}).get("status")
    evidence["geological_old_allowed"]=geo_status=="no_recent_update"

    checks={
        "bingen_recent":bingen_recent,
        "bingen_short_safe":bingen_short_safe,
        "bingen_24h_safe":bingen_24h_safe,
        "city_1h_safe":city_1h_safe,
        "city_24h_safe":city_24h_safe,
        "inmet_clear":inmet_clear,
        "forecast_and_pluvio_clear":evidence["forecast_and_pluvio_clear"],
        "defesa_clear":defesa_clear,
    }
    evidence["checks"]=checks

    labels={
        "bingen_recent":"estação pluviométrica de referência sem leitura recente",
        "bingen_short_safe":"estação pluviométrica de referência acima do limite de curto prazo",
        "bingen_24h_safe":"estação pluviométrica de referência acima do limite de 24 h",
        "city_1h_safe":"Há pluviômetro recente em Três Rios acima do limite de 1 h",
        "city_24h_safe":"Há pluviômetro recente em Três Rios acima do limite de 24 h",
        "inmet_clear":"INMET não confirma cenário de normalização",
        "forecast_and_pluvio_clear":"Previsão ou pluviometria ainda contém gatilho de Observação",
        "defesa_clear":"Defesa Civil possui sinal operacional oficial recente",
    }
    evidence["blockers"]=[labels[k] for k,v in checks.items() if not v]
    evidence["safe"]=all(checks.values())
    return evidence


def deescalation_source_readiness(geo, hydro, inmet, local_hydro):
    """Return blockers for de-escalation, treating stale data differently from outage."""
    blockers=[]

    inmet_status=(inmet or {}).get("status")
    if not (
        inmet_status=="ok"
        and isinstance((inmet or {}).get("level"),int)
    ):
        blockers.append("INMET sem confirmação atual")

    hydro_status=(hydro or {}).get("status")
    if hydro_status=="ok" and isinstance((hydro or {}).get("level"),int):
        pass
    elif hydro_status=="no_recent_update":
        if not (local_hydro or {}).get("safe"):
            blockers.append("Hidrológico antigo sem confirmação local segura pela estação de referência")
    else:
        blockers.append("Hidrológico indisponível ou não confirmado")

    geo_status=(geo or {}).get("status")
    if geo_status=="ok" and isinstance((geo or {}).get("level"),int):
        pass
    elif geo_status=="no_recent_update":
        # A geological classification older than its validity window no longer
        # freezes de-escalation. If it updates again, it immediately participates.
        pass
    else:
        blockers.append("Geológico indisponível ou não confirmado")

    return blockers


def _official_event_from_source(key, source, allow_last_known=False):
    """Return a stable official elevated-risk event without treating rereads as new."""
    if not isinstance(source,dict):
        return None

    status=source.get("status")
    level=source.get("level")
    historical=False

    if not (status=="ok" and isinstance(level,int) and level>1):
        if not allow_last_known:
            return None
        level=source.get("last_known_level")
        if not (isinstance(level,int) and level>1):
            return None
        historical=True

    if key in ("cemaden_geological","cemaden_hydrological"):
        risk=(source.get("last_known_risk") if historical else source.get("risk")) or ""
        official_at=(
            source.get("last_known_updated_at")
            if historical
            else source.get("official_updated_at")
        ) or source.get("official_updated_at")
        identity={
            "source":key,
            "level":level,
            "risk":risk,
            "official_at":official_at,
        }
    elif key=="inmet_alerts":
        official_at=source.get("starts_at") or source.get("official_updated_at")
        identity={
            "source":key,
            "level":level,
            "title":source.get("title"),
            "starts_at":source.get("starts_at"),
            "ends_at":source.get("ends_at"),
        }
    elif key=="defesa_civil":
        signal=source.get("operational_signal") or {}
        bulletin=source.get("latest_bulletin") or {}
        official_at=(
            source.get("official_updated_at")
            or signal.get("published_at")
            or bulletin.get("published_at")
        )
        identity={
            "source":key,
            "level":level,
            "stage":source.get("stage"),
            "basis":source.get("basis"),
            "signal_type":source.get("signal_type"),
            "signal_label":source.get("signal_label"),
            "official_at":official_at,
        }
    else:
        return None

    signature=json.dumps(identity,ensure_ascii=False,sort_keys=True,separators=(",",":"))
    return {
        "source":key,
        "level":int(level),
        "signature":signature,
        "official_at":official_at,
        "historical":historical,
    }


def _official_driver_floor(level):
    """Minimum official source level capable of sustaining the current HCNSC level."""
    try:
        level=int(level)
    except Exception:
        return 2
    if level<=2:
        return 2
    if level==3:
        return 3
    return level-1


def update_official_hold_state(previous, current_level, geo, hydro, inmet, defesa):
    """Track new official publications and derive the two-hour no-descent window."""
    now=datetime.now(TZ)
    prev_overall=(previous.get("overall") or {}) if isinstance(previous,dict) else {}
    prev_state=prev_overall.get("deescalation") or {}
    tracked=dict(prev_state.get("official_signals") or {})
    prev_sources=(previous.get("sources") or {}) if isinstance(previous,dict) else {}

    sources={
        "cemaden_geological":geo,
        "cemaden_hydrological":hydro,
        "inmet_alerts":inmet,
        "defesa_civil":defesa,
    }

    for key,source in sources.items():
        event=_official_event_from_source(key,source,allow_last_known=True)

        # If the current source no longer carries an elevated event, migrate the
        # last known official event from the previous payload when needed.
        if event is None and key not in tracked:
            event=_official_event_from_source(
                key,
                prev_sources.get(key),
                allow_last_known=True,
            )

        if event is None:
            continue

        old=tracked.get(key) or {}
        same_signature=old.get("signature")==event.get("signature")

        parsed_official=parse_dt(event.get("official_at"))
        if parsed_official and parsed_official>now+timedelta(minutes=15):
            parsed_official=None

        if same_signature:
            effective_at=(
                parse_dt(old.get("effective_at"))
                or parsed_official
                or parse_dt(old.get("first_seen_at"))
                or now
            )
            first_seen=parse_dt(old.get("first_seen_at")) or effective_at
        else:
            # Historical/stale CEMADEN information must retain its original
            # timestamp and must not become "new" merely because the code saw it.
            effective_at=parsed_official or now
            first_seen=now

        tracked[key]={
            "source":key,
            "level":event.get("level"),
            "signature":event.get("signature"),
            "official_at":(
                parsed_official.isoformat()
                if parsed_official
                else event.get("official_at")
            ),
            "effective_at":effective_at.isoformat(),
            "first_seen_at":first_seen.isoformat(),
            "last_seen_at":now.isoformat(),
            "historical":bool(event.get("historical")),
        }

    floor=_official_driver_floor(current_level)
    relevant=[]
    for item in tracked.values():
        try:
            lvl=int(item.get("level") or 0)
        except Exception:
            lvl=0
        when=parse_dt(item.get("effective_at"))
        if lvl>=floor and when:
            relevant.append((when,item))

    if not relevant:
        return {
            "official_signals":tracked,
            "official_driver_floor":floor,
            "last_relevant_official_at":None,
            "last_relevant_official_source":None,
            "official_hold_until":None,
            "official_hold_active":False,
            "official_hold_remaining_minutes":0,
        }

    last_when,last_item=max(relevant,key=lambda x:x[0])
    hold_until=last_when+timedelta(hours=DEESCALATION_OFFICIAL_HOLD_HOURS)
    remaining=max(0.0,(hold_until-now).total_seconds()/60.0)

    return {
        "official_signals":tracked,
        "official_driver_floor":floor,
        "last_relevant_official_at":last_when.isoformat(),
        "last_relevant_official_source":last_item.get("source"),
        "official_hold_until":hold_until.isoformat(),
        "official_hold_active":remaining>0,
        "official_hold_remaining_minutes":round(remaining,1),
    }


def apply_deescalation_hysteresis(candidate_level, previous, geo, hydro, inmet, defesa, local_hydro):
    """Escalate immediately; descend after 2 h quiet + 3 spaced safe checks."""
    now=datetime.now(TZ)
    prev_overall=(previous.get("overall") or {}) if isinstance(previous,dict) else {}
    try:
        previous_level=int(prev_overall.get("level") or 1)
    except Exception:
        previous_level=1

    prior_state=prev_overall.get("deescalation") or {}
    blockers=deescalation_source_readiness(geo,hydro,inmet,local_hydro)
    mode="bingen_local" if (local_hydro or {}).get("safe") else "standard"
    official=update_official_hold_state(
        previous,previous_level,geo,hydro,inmet,defesa
    )

    def state_base():
        return {
            "mode":mode,
            "local_hydrological_evidence":local_hydro,
            "official_hold_hours":DEESCALATION_OFFICIAL_HOLD_HOURS,
            "confirmations_required":DEESCALATION_CONFIRMATIONS,
            "confirmation_min_interval_minutes":DEESCALATION_CONFIRMATION_MIN_INTERVAL_MINUTES,
            **official,
        }

    if candidate_level>=previous_level:
        return candidate_level,{
            **state_base(),
            "pending":False,
            "phase":"stable_or_escalating",
            "target_level":None,
            "blocked_by_source_gap":False,
            "blocked_by_official_hold":False,
            "blocked_reasons":blockers,
            "consecutive_confirmations":0,
            "last_confirmation_at":None,
        },False

    target=max(candidate_level,previous_level-1)

    # A new/recent official publication supporting the current elevated level
    # blocks any descent for two full hours. Re-reading the same publication does
    # not restart this timer because its stable signature is preserved above.
    if official.get("official_hold_active"):
        return previous_level,{
            **state_base(),
            "pending":True,
            "phase":"official_hold",
            "target_level":target,
            "blocked_by_source_gap":False,
            "blocked_by_official_hold":True,
            "blocked_reasons":[],
            "consecutive_confirmations":0,
            "last_confirmation_at":None,
        },True

    if blockers:
        return previous_level,{
            **state_base(),
            "pending":True,
            "phase":"blocked",
            "target_level":target,
            "blocked_by_source_gap":True,
            "blocked_by_official_hold":False,
            "blocked_reasons":blockers,
            "consecutive_confirmations":0,
            "last_confirmation_at":None,
        },True

    same_target=(
        prior_state.get("phase")=="confirming"
        and prior_state.get("target_level")==target
        and not prior_state.get("blocked_by_source_gap")
        and not prior_state.get("blocked_by_official_hold")
    )
    count=int(prior_state.get("consecutive_confirmations") or 0) if same_target else 0
    last_confirmation=parse_dt(prior_state.get("last_confirmation_at")) if same_target else None

    may_count=(
        last_confirmation is None
        or (now-last_confirmation).total_seconds()/60.0
           >=DEESCALATION_CONFIRMATION_MIN_INTERVAL_MINUTES
    )
    if may_count:
        count+=1
        last_confirmation=now

    if count>=DEESCALATION_CONFIRMATIONS:
        new_level=target
        still_pending=candidate_level<new_level
        next_target=max(candidate_level,new_level-1) if still_pending else None
        return new_level,{
            **state_base(),
            "pending":still_pending,
            "phase":"confirming" if still_pending else "stable",
            "target_level":next_target,
            "blocked_by_source_gap":False,
            "blocked_by_official_hold":False,
            "blocked_reasons":[],
            "consecutive_confirmations":0,
            "last_confirmation_at":None,
            "last_step_at":now.isoformat(),
        },False

    return previous_level,{
        **state_base(),
        "pending":True,
        "phase":"confirming",
        "target_level":target,
        "blocked_by_source_gap":False,
        "blocked_by_official_hold":False,
        "blocked_reasons":[],
        "consecutive_confirmations":count,
        "last_confirmation_at":last_confirmation.isoformat() if last_confirmation else None,
    },True

def main():
    os.makedirs("data",exist_ok=True)
    previous=load_previous()
    geo=fetch_cemaden(1,"cemaden_geological","Deslizamento",previous)
    hydro=fetch_cemaden(2,"cemaden_hydrological","Hidrológico",previous)
    weather=fetch_inmet_weather(previous)
    weather_reference=fetch_open_meteo_current(previous)
    weather_map=fetch_weather_map(previous)
    pluviometers=fetch_cemaden_pluviometers(previous)
    forecast=fetch_inmet_forecast(previous)
    inmet=fetch_inmet_alerts(previous)
    defesa=fetch_defesa_civil(previous)
    roads=fetch_roads(previous)
    road_weather=weather_reference if weather_reference.get("status")=="ok" else weather
    roads["tresrios_weather"]={
        "source":"Referência meteorológica próxima ao HCNSC" if road_weather is weather_reference else "INMET A625",
        "is_road_official_weather":False,
        "condition":road_weather.get("condition") or ("Observação INMET" if road_weather.get("status")=="ok" else None),
        "temperature_c":road_weather.get("temperature_c"),
        "wind_speed_kmh":road_weather.get("wind_speed_kmh"),
        "wind_direction":road_weather.get("wind_direction"),
        "updated_at":road_weather.get("observed_at"),
    }
    utilities=fetch_utilities(previous)

    # Only fresh/confirmed official sources may create a new escalation.
    # Stale or unavailable sources can hold a previous level through hysteresis,
    # but their preserved numeric level must not drive a new increase.
    usable=[
        s for s in (geo,hydro,inmet,defesa)
        if s.get("status")=="ok" and isinstance(s.get("level"),int)
    ]
    official_candidate=max([s["level"] for s in usable], default=1)

    # Corroboration between independent official providers can escalate one level
    # only when both providers involved have a current confirmed status.
    cemaden_level=max(
        [
            s.get("level") or 0
            for s in (geo,hydro)
            if s.get("status")=="ok" and isinstance(s.get("level"),int)
        ],
        default=0,
    )
    inmet_level=(
        inmet.get("level")
        if inmet.get("status")=="ok" and isinstance(inmet.get("level"),int)
        else 0
    )
    escalated=False
    candidate=official_candidate
    if cemaden_level>=3 and inmet_level>=3:
        candidate=max(candidate,min(5,max(cemaden_level,inmet_level)+1))
        escalated=True

    # Forecast and pluviometers are early-warning evidence only.
    supplemental_signals=supplemental_observation_signals(forecast,pluviometers)
    supplemental_observation=bool(supplemental_signals)
    if supplemental_observation and candidate<2:
        candidate=2

    local_hydro_normalization=hydrological_normalization_evidence(
        hydro,geo,inmet,defesa,forecast,pluviometers
    )

    overall,deescalation,deescalation_held=apply_deescalation_hysteresis(
        candidate,previous,geo,hydro,inmet,defesa,local_hydro_normalization
    )

    driver_floor=max(1,candidate-(1 if escalated else 0))
    top=[]
    for s in (geo,hydro,inmet,defesa):
        if (
            s.get("status")=="ok"
            and isinstance(s.get("level"),int)
            and s["level"]>=driver_floor
            and s["level"]>1
        ):
            detail=s.get("basis") or s.get("risk")
            top.append(f'{s["name"]}: {detail}')

    reason_parts=[]
    if top:
        reason_parts.append(", ".join(top))
    elif candidate<=1:
        reason_parts.append("Sem condição oficial de risco acima de Vigilância nas fontes integradas")

    if escalated:
        reason_parts.append("Escalada por corroboração de CEMADEN-RJ e INMET em nível 3 ou superior")
    if supplemental_signals:
        reason_parts.extend(supplemental_signals)

    gaps=[s["name"] for s in (geo,hydro,inmet) if s.get("status") in ("no_recent_update","source_unconfirmed","unavailable")]
    if gaps:
        reason_parts.append("Sem confirmação oficial recente em: "+", ".join(gaps))

    if local_hydro_normalization.get("safe"):
        short_value=local_hydro_normalization.get("bingen_short_mm")
        day_value=local_hydro_normalization.get("bingen_24h_mm")
        short_window=local_hydro_normalization.get("bingen_short_window") or "curto prazo"
        reason_parts.append(
            "Normalização hidrológica local elegível pela estação pluviométrica de referência "
            +f"({short_value:.1f} mm/{short_window}; {day_value:.1f} mm/24 h)"
        )

    if deescalation_held:
        if deescalation.get("blocked_by_official_hold"):
            remaining=safe_float(deescalation.get("official_hold_remaining_minutes")) or 0
            reason_parts.append(
                "Descida bloqueada pela janela de segurança após informação oficial: "
                +f"restam aproximadamente {remaining:.0f} min das "
                +str(DEESCALATION_OFFICIAL_HOLD_HOURS)
                +" h mínimas"
            )
        elif deescalation.get("blocked_by_source_gap"):
            blocked=", ".join(deescalation.get("blocked_reasons") or [])
            reason_parts.append(
                "Rebaixamento retido por evidência insuficiente"
                +((": "+blocked) if blocked else "")
            )
        else:
            reason_parts.append(
                "Normalização em confirmação: "
                +str(deescalation.get("consecutive_confirmations") or 0)
                +"/"+str(DEESCALATION_CONFIRMATIONS)
                +" verificações válidas para o próximo nível"
            )
    elif deescalation.get("pending"):
        reason_parts.append(
            "Nível reduzido uma faixa; a próxima redução exigirá "
            +str(DEESCALATION_CONFIRMATIONS)
            +" novas verificações válidas se a melhora persistir"
        )


    reason=". ".join(x.rstrip(".") for x in reason_parts if x)+"."

    now=datetime.now(TZ)
    payload={
      "schema_version":3,
      "generated_at":now.isoformat(),
      "location":{"city":"Três Rios","state":"RJ","country":"Brasil"},
      "overall":{
          "level":overall,
          "label":LEVEL_LABELS[overall],
          "reason":reason,
          "candidate_level":candidate,
          "official_candidate_level":official_candidate,
          "supplemental_observation":supplemental_observation,
          "supplemental_signals":supplemental_signals,
          "deescalation":deescalation,
          "rule":"Escalada imediata somente por fonte oficial com status ok. Informação antiga não provoca nova subida. O risco Hidrológico é prioritário para o HCNSC; quando o CEMADEN Hidrológico ultrapassa sua janela de 24 h sem nova atualização, a estação pluviométrica de referência pode atuar como evidência local de normalização, desde que esteja recente e abaixo de 20 mm no curto prazo e 50 mm/24 h, sem outro pluviômetro recente acima desses gatilhos, sem aviso INMET, sem previsão forte e sem sinal operacional recente da Defesa Civil. O risco Geológico antigo não congela indefinidamente o rebaixamento, mas volta a participar imediatamente quando atualizado. Defesa Civil de Três Rios pode elevar por estágio ou sinal operacional oficial recente. CEMADEN-RJ + INMET, ambos atuais e em nível >=3, podem elevar +1. Após uma informação oficial relevante, o nível não pode cair por 2 horas. Encerrada essa janela sem nova informação de mesmo peso ou maior, são exigidas 3 verificações válidas consecutivas, separadas por ciclos reais de monitoramento, para reduzir apenas uma faixa. Novas publicações oficiais relevantes reiniciam as 2 horas; reler o mesmo aviso não reinicia o relógio. Depois da primeira queda, não há nova espera de 2 horas: são necessárias 3 novas verificações válidas para cada faixa seguinte, desde que não haja agravamento."
      },
      "sources":{"cemaden_geological":geo,"cemaden_hydrological":hydro,"inmet_alerts":inmet,"defesa_civil":defesa},
      "weather":weather,
      "weather_reference":weather_reference,
      "weather_map":weather_map,
      "pluviometers":pluviometers,
      "forecast":forecast,
      "roads":roads,
      "utilities":utilities,
      "notifications":{
          "group_operational_email":{
              "channel":"email",
              "label":"Grupo Operacional",
              "levels":[3,4,5],
              "recipient_configured":bool(os.getenv("HCNSC_EMAIL_GRUPO_OPERACIONAL","").strip()),
              "recipient_count":len([x for x in re.split(r"[,;\\n]+",os.getenv("HCNSC_EMAIL_GRUPO_OPERACIONAL","")) if x.strip()]),
              "provider_configured":bool(
                  os.getenv("HCNSC_SMTP_USER","").strip()
                  and os.getenv("HCNSC_SMTP_APP_PASSWORD","").strip()
              ),
              "provider":"smtp" if (
                  os.getenv("HCNSC_SMTP_USER","").strip()
                  and os.getenv("HCNSC_SMTP_APP_PASSWORD","").strip()
              ) else None,
              "automatic_sending_enabled":os.getenv("HCNSC_EMAIL_ENABLED","").strip().lower() in ("1","true","yes","on","sim"),
              "status":"active" if (
                  os.getenv("HCNSC_EMAIL_ENABLED","").strip().lower() in ("1","true","yes","on","sim")
                  and os.getenv("HCNSC_EMAIL_GRUPO_OPERACIONAL","").strip()
              ) else "ready_disabled" if os.getenv("HCNSC_EMAIL_GRUPO_OPERACIONAL","").strip() else "awaiting_recipient"
          },
          "group_managers_email":{
              "channel":"email",
              "label":"Grupo de Gerentes",
              "levels":[4,5],
              "recipient_configured":bool(os.getenv("HCNSC_EMAIL_GRUPO_GERENTES","").strip()),
              "recipient_count":len([x for x in re.split(r"[,;\\n]+",os.getenv("HCNSC_EMAIL_GRUPO_GERENTES","")) if x.strip()]),
              "provider_configured":bool(
                  os.getenv("HCNSC_SMTP_USER","").strip()
                  and os.getenv("HCNSC_SMTP_APP_PASSWORD","").strip()
              ),
              "provider":"smtp" if (
                  os.getenv("HCNSC_SMTP_USER","").strip()
                  and os.getenv("HCNSC_SMTP_APP_PASSWORD","").strip()
              ) else None,
              "automatic_sending_enabled":os.getenv("HCNSC_EMAIL_ENABLED","").strip().lower() in ("1","true","yes","on","sim"),
              "status":"active" if (
                  os.getenv("HCNSC_EMAIL_ENABLED","").strip().lower() in ("1","true","yes","on","sim")
                  and os.getenv("HCNSC_EMAIL_GRUPO_GERENTES","").strip()
              ) else "ready_disabled" if os.getenv("HCNSC_EMAIL_GRUPO_GERENTES","").strip() else "awaiting_recipient"
          }
      },
      "integrations":{"cemaden_rj":"active","defesa_civil_tresrios":defesa.get("status","source_unconfirmed"),"inmet_alerts":"active","inmet_forecast":forecast.get("status","unavailable"),"inmet_weather":weather.get("status","unavailable"),"weather_reference":weather_reference.get("status","source_unconfirmed"),"weather_map":weather_map.get("status","source_unconfirmed"),"radar":(weather_map.get("radar") or {}).get("status","unavailable"),"pluviometers":pluviometers.get("status","unavailable"),"roads":roads.get("status","unavailable"),"utilities":utilities.get("status","unavailable")}
    }
    with open(OUT,"w",encoding="utf-8") as f: json.dump(payload,f,ensure_ascii=False,indent=2)
    persist_history(payload)
    persist_climate_history(weather,weather_reference)
    print(json.dumps(payload,ensure_ascii=False,indent=2))

if __name__=="__main__":
    main()
