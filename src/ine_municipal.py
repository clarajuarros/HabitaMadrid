"""Official municipal context prepared offline, with independently hashed sources."""
from pathlib import Path
import gzip
import hashlib
import json
import unicodedata
import re
import numpy as np
import pandas as pd

BASE = 'data/processed/ine_municipal'
RAW = 'data/raw/ine_municipal'
AGE_IDS = {15668: 'poblacion_censo', 10582: 'menores_16', 283906: 'entre_16_64', 10584: 'mayores_65'}


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def spanish_number(values):
    # INE semicolon downloads use a dot for thousands and comma for decimals.
    return pd.to_numeric(values.str.replace('.', '', regex=False).str.replace(',', '.', regex=False), errors='coerce')


def official_name(value):
    # Display names may move the article to the front; the code remains the join key.
    text = unicodedata.normalize('NFKD', str(value)).casefold()
    return ' '.join(sorted(re.findall(r'\w+', ''.join(c for c in text if not unicodedata.combining(c)))))


def _source(root, sources, name):
    item = sources[name]
    if Path(item['archivo']).name != item['archivo']:
        raise ValueError('Ruta de fuente INE inválida')
    path = root / RAW / item['archivo']
    if digest(path) != item['sha256_archivo']:
        raise ValueError('Fuente INE alterada: ' + name)
    content = gzip.decompress(path.read_bytes())
    if hashlib.sha256(content).hexdigest() != item['sha256_original']:
        raise ValueError('Contenido INE alterado: ' + name)
    return content


def build_context(root):
    """Join every municipality by its official code; never impute or change IVP."""
    from io import BytesIO
    root = Path(root)
    sources = json.loads((root / RAW / 'sources.json').read_text())
    master = pd.read_csv(root/'data/processed/municipios/maestro_municipios.csv', dtype={'codigo_ine': str})
    if master.codigo_ine.duplicated().any():
        raise ValueError('Maestro municipal duplicado')
    padron = pd.read_csv(BytesIO(_source(root, sources, 'padron')), sep=';', dtype=str)
    padron = padron.loc[padron.Sexo.eq('Total') & padron.Municipios.str.match(r'28\d{3} ', na=False)].copy()
    padron['codigo_ine'] = padron.Municipios.str[:5]
    padron['poblacion_ine'] = spanish_number(padron.Total)
    padron['anio'] = pd.to_numeric(padron.Periodo, errors='raise')
    year = int(padron.anio.max())
    latest = padron.loc[padron.anio.eq(year)].set_index('codigo_ine')
    if latest.index.duplicated().any() or set(latest.index) != set(master.codigo_ine):
        raise ValueError('Cobertura oficial del padrón incompleta o duplicada')
    current = master.set_index('codigo_ine')
    context = master[['codigo_ine','municipio','poblacion','poblacion_anio','elegible_hasta_50000']].set_index('codigo_ine')
    context['nombre_oficial_ine'] = latest.Municipios.str[6:]
    context['poblacion_ine'] = latest.poblacion_ine
    context['padron_anio'] = year
    context['coincide_poblacion'] = context.poblacion.eq(context.poblacion_ine) & context.poblacion_anio.eq(year)
    context['coincide_nombre'] = pd.Series({c:official_name(n) for c,n in context.municipio.items()}).eq(context.nombre_oficial_ine.map(official_name))
    context['elegible_ine'] = context.poblacion_ine.le(50000)
    # A changed census must trigger upstream regeneration, not silently retain an old scope.
    if not context.coincide_poblacion.all() or not context.elegible_ine.eq(context.elegible_hasta_50000).all():
        raise ValueError('El padrón oficial exige actualizar primero el maestro y sus derivados')
    ages = json.loads(_source(root, sources, 'edades'))
    seen = set()
    for series in ages:
        metas = series['MetaData']
        municipal = [m for m in metas if m['T3_Variable']=='Municipios']
        age = [m['Id'] for m in metas if m['Id'] in AGE_IDS]
        if len(municipal)!=1 or len(age)!=1 or series['T3_Unidad']!='Personas' or len(series['Data'])!=1:
            raise ValueError('Serie municipal de edad ambigua')
        if not any(m['T3_Variable']=='Sexo' and m['Nombre']=='Total' for m in metas) or not any(m['Id']==304582 for m in metas):
            raise ValueError('Edad sin total de sexo o nacimiento')
        code, field, data = municipal[0]['Codigo'], AGE_IDS[age[0]], series['Data'][0]
        if (code,field) in seen or code not in context.index or data['T3_TipoDato']!='Definitivo' or data['Valor'] is None:
            raise ValueError('Edad duplicada, ajena al ámbito o no definitiva')
        seen.add((code,field))
        context.loc[code,field] = data['Valor']
        context.loc[code,'censo_anio'] = data['Anyo']
    if len(seen)!=len(context)*4:
        raise ValueError('Faltan grupos de edad municipales')
    if not np.allclose(context[['menores_16','entre_16_64','mayores_65']].sum(axis=1),context.poblacion_censo,atol=1e-8):
        raise ValueError('Los grupos de edad no suman la población del censo')
    for field in ['menores_16','entre_16_64','mayores_65']:
        context[field+'_pct'] = 100*context[field]/context.poblacion_censo
    for name, dimension, labels in [
        ('hogares','Tamaño del hogar',{'Total (tamaño del hogar)':'hogares_2021','1 persona':'hogares_unipersonales_2021'}),
        ('viviendas','Tipo de vivienda (principal o no)',{'Total':'viviendas_2021','Vivienda principal':'principales_2021','Vivienda no principal':'no_principales_2021'})]:
        frame = pd.read_csv(BytesIO(_source(root,sources,name)),sep=';',dtype=str)
        frame=frame.loc[frame.Municipios.str.match(r'28\d{3} ',na=False)].copy()
        frame['codigo_ine']=frame.Municipios.str[:5]
        frame['value']=spanish_number(frame.Total)
        if frame.duplicated(['codigo_ine',dimension]).any():
            raise ValueError('Dimensión censal duplicada')
        pivot=frame.pivot(index='codigo_ine',columns=dimension,values='value')
        if set(pivot.index)!=set(context.index):
            raise ValueError('Cobertura censal municipal incompleta')
        for source,target in labels.items(): context[target]=pivot[source]
    if not np.allclose(context.principales_2021+context.no_principales_2021,context.viviendas_2021,atol=1e-8):
        raise ValueError('Tipos de vivienda incoherentes')
    numeric=[*AGE_IDS.values(),'poblacion_ine','hogares_2021','hogares_unipersonales_2021','viviendas_2021','principales_2021','no_principales_2021']
    if context[numeric].isna().any().any() or (context[numeric]<0).any().any():
        raise ValueError('Dato censal ausente o negativo; requiere revisión')
    return context.reset_index(), padron[['codigo_ine','anio','poblacion_ine']].sort_values(['codigo_ine','anio']), sources


def context_signature(root):
    root=Path(root);base=root/BASE
    value=json.loads((base/'manifest.json').read_text())
    paths=[base/'manifest.json',*[root/name for name in value['files']]]
    return tuple((str(p),p.stat().st_mtime_ns,p.stat().st_size) for p in paths)


def load_context(root):
    root=Path(root);base=root/BASE
    manifest=json.loads((base/'manifest.json').read_text())
    if manifest.get('schema')!=1:
        raise ValueError('Manifiesto INE desconocido')
    for name,meta in manifest['files'].items():
        path=(root/name).resolve()
        if not path.is_relative_to(root.resolve()) or path.is_symlink() or path.stat().st_size!=meta['bytes'] or digest(path)!=meta['sha256']:
            raise ValueError('Contexto INE alterado: '+name)
    context=pd.read_csv(base/'contexto_municipal.csv',dtype={'codigo_ine':str})
    master=pd.read_csv(root/'data/processed/municipios/maestro_municipios.csv',dtype={'codigo_ine':str})
    if context.codigo_ine.duplicated().any() or set(context.codigo_ine)!=set(master.codigo_ine):
        raise ValueError('Maestro y contexto INE desincronizados')
    if not context.set_index('codigo_ine').poblacion_ine.equals(master.set_index('codigo_ine').poblacion.astype(float)):
        # Compare values independently of CSV integer/float serialization.
        if not np.array_equal(context.set_index('codigo_ine').poblacion_ine.sort_index(),master.set_index('codigo_ine').poblacion.sort_index()):
            raise ValueError('Población del maestro y fuente oficial distintas')
    return context.set_index('codigo_ine'),manifest,json.loads((root/RAW/'sources.json').read_text())
