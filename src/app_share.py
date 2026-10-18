"""Versioned, bounded share links; no executable or arbitrary session state."""
import base64,json,math,os
from pathlib import Path
from urllib.parse import urlsplit,urlunsplit,urlencode

DIMS=('sanidad','educacion','transporte','vivienda')
FILTERS=('alquiler_max','sanidad_km','educacion_km','transporte_km')


def validate_plan(plan,allowed):
    if not isinstance(plan,dict) or plan.get('v')!=1:raise ValueError('Versión de enlace desconocida')
    if plan.get('code') not in allowed or plan.get('profile') not in ('VU','VC') or plan.get('mode') not in ('general','personalizado'):raise ValueError('Selección no válida')
    weights=plan.get('weights',{})
    if set(weights)!=set(DIMS) or any(isinstance(x,bool) or not isinstance(x,int) or not 0<=x<=100 for x in weights.values()) or sum(weights.values())<=0:raise ValueError('Prioridades inválidas')
    population=plan.get('population')
    if not isinstance(population,list) or len(population)!=2 or any(isinstance(x,bool) or not isinstance(x,int) for x in population) or not 0<=population[0]<=population[1]<=50000:raise ValueError('Población inválida')
    filters=plan.get('filters',{})
    if not isinstance(filters,dict) or set(filters)-set(FILTERS):raise ValueError('Filtros inválidos')
    for k,x in filters.items():
        if x is not None and (isinstance(x,bool) or not isinstance(x,(int,float)) or not math.isfinite(x) or not 0<=x<=(1000 if k=='alquiler_max' else 200)):raise ValueError('Filtro fuera de rango')
    codes=plan.get('compare',[])
    if not isinstance(codes,list) or len(codes)>3 or any(c not in allowed for c in codes) or len(set(codes))!=len(codes):raise ValueError('Comparación inválida')
    if not isinstance(plan.get('query',''),str) or len(plan.get('query',''))>100:raise ValueError('Texto de filtro demasiado largo')
    if any(k in plan and not isinstance(plan[k],bool) for k in ('only_index','recommendations')):raise ValueError('Opciones inválidas')
    return plan


def make_plan(state,code,profile,mode,compare=None):
    return {'v':1,'code':code,'profile':profile,'mode':mode,'weights':{d:int(state.get('peso_'+d,25)) for d in DIMS},'population':list(state.get('filtro_poblacion',(0,50000))),'filters':{k:state.get('filtro_'+k) for k in FILTERS},'only_index':bool(state.get('filtro_solo_indice',False)),'query':state.get('filtro_nombre',''),'compare':list(compare if compare is not None else state.get('comparar_codigos',[]))[:3],'recommendations':bool(state.get('_assistant_results',False))}


def encode_plan(plan):
    raw=json.dumps(plan,ensure_ascii=False,separators=(',',':'),allow_nan=False).encode()
    if len(raw)>4096:raise ValueError('Selección demasiado larga')
    return base64.urlsafe_b64encode(raw).decode().rstrip('=')


def decode_plan(token,allowed):
    if not isinstance(token,str) or len(token)>6000:raise ValueError('Enlace demasiado largo')
    try:plan=json.loads(base64.b64decode(token+'='*(-len(token)%4),altchars=b'-_',validate=True))
    except (ValueError,UnicodeError):raise ValueError('Enlace no válido') from None
    return validate_plan(plan,set(allowed))


def restore_plan(token,state,allowed):
    if not token or state.get('_restored_plan')==token:return False
    plan=decode_plan(token,allowed)
    state['municipio_codigo']=plan['code'];state['perfil_vivienda']=plan['profile'];state['modo_indice']=plan['mode']
    for d,x in plan['weights'].items():state['peso_'+d]=x
    state['filtro_poblacion']=tuple(plan['population'])
    for k in FILTERS:state['filtro_'+k]=plan['filters'].get(k)
    state['filtro_solo_indice']=bool(plan.get('only_index',False));state['filtro_nombre']=plan.get('query','');state['comparar_codigos']=plan.get('compare',[])
    state['_assistant_results']=bool(plan.get('recommendations',False));state['_restored_plan']=token
    return True


def public_base(root,context_url=''):
    configured=os.environ.get('HABITAMADRID_PUBLIC_URL','')
    if not configured:
        try:configured=(Path(root)/'.streamlit/public_url.txt').read_text().strip()
        except OSError:pass
    candidate=configured or context_url or 'http://127.0.0.1:8501/'
    p=urlsplit(candidate)
    if p.scheme not in ('http','https') or not p.netloc or p.username or p.password:raise ValueError('URL de la aplicación inválida')
    return urlunsplit((p.scheme,p.netloc,p.path or '/','',''))


def share_url(base,plan):
    return base+'?'+urlencode({'municipio':plan['code'],'vivienda':plan['profile'],'plan':encode_plan(plan)})+'#ficha-municipal'
