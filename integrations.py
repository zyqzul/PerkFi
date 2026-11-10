"""Server-side API adapters; never turn an outage into fabricated data."""
import os,time,threading,io,zipfile,csv,math
from datetime import datetime,timezone,timedelta
import requests
from google.transit import gtfs_realtime_pb2
CACHE={};LOCK=threading.RLock();MYT=timezone(timedelta(hours=8))
def latlon(): return float(os.getenv('SITE_LAT','3.1390')),float(os.getenv('SITE_LON','101.6869'))
def distance(lat,lon):
    a,b=latlon();r=math.pi/180;x=math.sin((lat-a)*r/2)**2+math.cos(a*r)*math.cos(lat*r)*math.sin((lon-b)*r/2)**2
    return 6371*2*math.asin(min(1,math.sqrt(x)))
def fetch(url,params=None):
    r=requests.get(url,params=params,timeout=20,headers={'User-Agent':'PerkFi-Student-Prototype/1.0'});r.raise_for_status();return r

def cached(key,ttl,loader,source):
    with LOCK:
        old=CACHE.get(key)
        if old and time.time()-old['checked']<ttl: return old['result']
        try:
            data=loader();result={'status':'ok','source':source,'retrieved_at':datetime.now(MYT).isoformat(),'data':data}
        except Exception:
            result={'status':'stale' if old and old['result'].get('data') else 'unavailable','source':source,'retrieved_at':old['result'].get('retrieved_at') if old else None,'data':old['result'].get('data') if old else None,'message':'Source unavailable. Cached information, if shown, may be outdated. Try again later.'}
        CACHE[key]={'checked':time.time(),'result':result};return result

def weather():
    def load():
        a,b=latlon();j=fetch('https://api.open-meteo.com/v1/forecast',{'latitude':a,'longitude':b,'current':'temperature_2m,relative_humidity_2m,weather_code','timezone':'Asia/Kuala_Lumpur'}).json()
        c=j['current'];return [{'Temperature':str(c['temperature_2m'])+' °C','Humidity':str(c['relative_humidity_2m'])+'%','WMO weather code':c['weather_code'],'Model time':c['time'],'Location':f'{a}, {b}'}]
    return cached('weather',600,load,'Open-Meteo · weather model data · https://open-meteo.com/ · CC BY 4.0')

def feed_url(kind):
    agency=os.getenv('GTFS_AGENCY','prasarana');category=os.getenv('GTFS_CATEGORY','rapid-bus-kl')
    if agency not in ['prasarana','ktmb','mybas-ipoh','mybas-johor','mybas-kangar','mybas-alor-setar','mybas-kota-bharu','mybas-kuala-terengganu','mybas-melaka','mybas-kuching']: raise ValueError('Unsupported agency')
    return 'https://api.data.gov.my/'+kind+'/'+agency,({'category':category} if agency=='prasarana' else {})

def static_feed():
    with LOCK:
        entry=CACHE.get('zip')
        if entry and time.time()-entry['at']<86400:return entry['tables']
        url,params=feed_url('gtfs-static');raw=fetch(url,params).content
        z=zipfile.ZipFile(io.BytesIO(raw));tables={}
        for name in ['routes','stops','trips','stop_times','calendar','calendar_dates']:
            f=name+'.txt'
            if f in z.namelist():
                if z.getinfo(f).file_size>120_000_000: raise ValueError('Feed too large')
                tables[name]=list(csv.DictReader(io.TextIOWrapper(z.open(f),encoding='utf-8-sig')))
            else: tables[name]=[]
        CACHE['zip']={'at':time.time(),'tables':tables};return tables

def schedules():
    t=static_feed();today=datetime.now(MYT);ymd=today.strftime('%Y%m%d');weekday=today.strftime('%A').lower()
    active={r['service_id'] for r in t['calendar'] if r.get(weekday)=='1' and r['start_date']<=ymd<=r['end_date']}
    for r in t['calendar_dates']:
        if r['date']==ymd:
            if r['exception_type']=='1':active.add(r['service_id'])
            else:active.discard(r['service_id'])
    trips={r['trip_id']:r for r in t['trips'] if r['service_id'] in active}
    routes={r['route_id']:r for r in t['routes']}
    nearby=sorted([s for s in t['stops'] if s.get('stop_lat') and s.get('stop_lon')],key=lambda s:distance(float(s['stop_lat']),float(s['stop_lon'])))[:5]
    stopmap={s['stop_id']:s for s in nearby if distance(float(s['stop_lat']),float(s['stop_lon']))<=float(os.getenv('TRANSIT_RADIUS_KM','15'))}
    current=today.hour*3600+today.minute*60+today.second;result=[]
    for s in t['stop_times']:
        if s['trip_id'] not in trips or s['stop_id'] not in stopmap: continue
        value=s.get('departure_time','')
        try:h,m,sec=map(int,value.split(':'));seconds=h*3600+m*60+sec
        except ValueError:continue
        if seconds<current:continue
        tr=trips[s['trip_id']];r=routes.get(tr['route_id'],{});stop=stopmap[s['stop_id']]
        result.append((seconds,{'Stop':stop['stop_name'],'Route':r.get('route_short_name') or tr['route_id'],'Destination':tr.get('trip_headsign',''),'Scheduled departure':value,'Service date':today.date().isoformat(),'Type':'Published schedule, not live ETA'}))
    return [r for _,r in sorted(result,key=lambda x:x[0])[:20]]

def positions():
    url,params=feed_url('gtfs-realtime/vehicle-position');f=gtfs_realtime_pb2.FeedMessage();f.ParseFromString(fetch(url,params).content);result=[]
    for e in f.entity:
        if not e.HasField('vehicle') or not e.vehicle.HasField('position'):continue
        v=e.vehicle;p=v.position;km=distance(p.latitude,p.longitude)
        if km>float(os.getenv('TRANSIT_RADIUS_KM','15')):continue
        ts=v.timestamp if v.HasField('timestamp') else f.header.timestamp
        age=max(0,int(time.time())-ts) if ts else None
        result.append((km,{'Vehicle':v.vehicle.label or v.vehicle.id or e.id,'Route':v.trip.route_id,'Latitude':round(p.latitude,5),'Longitude':round(p.longitude,5),'Distance km':round(km,1),'Age seconds':age if age is not None else 'Unknown','Freshness':'Stale' if age is None or age>120 else 'Recent','Type':'Position only, not arrival prediction'}))
    return [r for _,r in sorted(result,key=lambda x:x[0])[:25]]

def transport(kind):
    if kind not in ('positions','schedule'):return {'status':'unavailable','message':'Unknown transport view.'}
    return cached('transport-'+kind,60 if kind=='positions' else 300,positions if kind=='positions' else schedules,'data.gov.my / '+os.getenv('GTFS_AGENCY','prasarana')+' · CC BY 4.0 · https://developer.data.gov.my/realtime-api/gtfs-'+('realtime' if kind=='positions' else 'static'))
