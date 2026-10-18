"""Read-only complementary connectivity and date-aware GTFS schedules."""
from contextlib import closing
from datetime import date
from functools import lru_cache
import json
from pathlib import Path
import sqlite3
import statistics

WEEKDAYS=('monday','tuesday','wednesday','thursday','friday','saturday','sunday')

@lru_cache(maxsize=4)
def _json(path, modified):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def load_living_data(root):
    path=Path(root)/'data/reference/living'
    result={}
    for key,file in [('connectivity','connectivity.json'),('transport','transport.json')]:
        try:
            p=path/file;result[key]=_json(str(p),p.stat().st_mtime_ns)
        except (OSError,ValueError):result[key]={}
    result['database']=str(path/'timetable.sqlite')
    return result


def active_services(metadata, day):
    if not isinstance(day,date):raise ValueError('Fecha inválida')
    stamp=day.strftime('%Y%m%d')
    services={x['service_id'] for x in metadata.get('calendar',[]) if x['start_date']<=stamp<=x['end_date'] and x[WEEKDAYS[day.weekday()]]=='1'}
    exceptions=[x for x in metadata.get('exceptions',[]) if x['date']==stamp]
    services-={x['service_id'] for x in exceptions if x['exception_type']=='2'}
    services|={x['service_id'] for x in exceptions if x['exception_type']=='1'}
    return services


def gtfs_time(seconds):
    seconds=int(seconds);days,seconds=divmod(seconds,86400);h,seconds=divmod(seconds,3600);m=seconds//60
    return f'{h:02d}:{m:02d}'+(f' (+{days} día)' if days else '')


def timetable(living, code, day, stop_id=None):
    """Deduplicate trips across municipal stops; intervals only at one stop/destination."""
    if not isinstance(code,str) or len(code)!=5 or not code.isdigit():raise ValueError('Código municipal inválido')
    meta=living.get('transport',{});services=active_services(meta,day)
    try:
        with closing(sqlite3.connect(Path(living['database']).resolve().as_uri()+'?mode=ro',uri=True)) as db:
            db.row_factory=sqlite3.Row
            stops=[dict(x) for x in db.execute('SELECT stop_id,name FROM stops WHERE codigo_ine=? ORDER BY name,stop_id',(code,))]
            query='SELECT d.* FROM departures d JOIN stops s ON s.stop_id=d.stop_id WHERE s.codigo_ine=?'
            params=[code]
            if stop_id is not None:
                if stop_id not in {x['stop_id'] for x in stops}:raise ValueError('La parada no pertenece al municipio')
                query+=' AND d.stop_id=?';params.append(stop_id)
            departures=[dict(x) for x in db.execute(query,params) if x['service_id'] in services]
    except (OSError,sqlite3.Error):return {'available':False,'stops':[],'rows':[],'departures':[]}
    groups={}
    for x in departures:
        key=(x['route_id'],x['direction_id'],x['headsign'])
        group=groups.setdefault(key,{})
        # A municipal itinerary may stop many times: count it once. At one stop,
        # an explicit second visit at a different time remains a scheduled stop.
        identity=(x['trip_id'],x['seconds']) if stop_id else x['trip_id']
        if identity not in group or x['seconds']<group[identity]['seconds']:group[identity]=x
    rows=[]
    for (rid,direction,headsign),trips in groups.items():
        route=meta.get('routes',{}).get(rid,{})
        times=sorted({x['seconds'] for x in trips.values()})
        gap=statistics.median([b-a for a,b in zip(times,times[1:])])/60 if stop_id and len(times)>1 else None
        rows.append({'route_id':rid,'linea':route.get('route_short_name') or rid,'destino':headsign or route.get('route_long_name') or 'Destino no publicado','recorrido':route.get('route_long_name',''),'salidas':len(times) if stop_id else len(trips),'expediciones_gtfs':len(trips),'primera':gtfs_time(times[0]),'ultima':gtfs_time(times[-1]),'intervalo_mediano_min':gap,'url':route.get('route_url',''),'direction_id':direction,'times':times})
    rows.sort(key=lambda x:(x['linea'],x['destino'],x['direction_id']))
    dates=[x for r in meta.get('calendar',[]) for x in (r['start_date'],r['end_date'])]+[r['date'] for r in meta.get('exceptions',[])]
    in_range=bool(dates and min(dates)<=day.strftime('%Y%m%d')<=max(dates))
    return {'available':bool(meta),'date_in_range':in_range,'stops':stops,'rows':rows,'departures':departures}
