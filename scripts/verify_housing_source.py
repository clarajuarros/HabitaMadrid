from pathlib import Path
import pandas as pd,openpyxl,math,json,hashlib
p=Path(__file__).resolve().parents[1]
df=pd.read_csv(p/'data/processed/integracion/vivienda_historico.csv',dtype={'codigo_ine':str})
raw=p/'data/raw/vivienda_2026-10-15/serpavi_2011_2024.xlsx'
w=openpyxl.load_workbook(raw,read_only=True,data_only=True);s=w['Municipios']
needed=set(df.fila_fuente_excel.astype(int));rows={i:r for i,r in enumerate(s.iter_rows(max_row=max(needed),values_only=True),start=1) if i in needed}
metrics=['testigos']+[x for x in df.columns if x.startswith(('alquiler_','superficie_')) and not x.startswith('unidad')]
errors=[];checked=0;nulls=0
for _,r in df.iterrows():
 a=rows[int(r.fila_fuente_excel)]
 if str(a[2])!=r.codigo_ine or a[3]!=r.municipio_fuente:errors.append({'code':r.codigo_ine,'field':'identity'})
 for metric in metrics:
  rawval=a[openpyxl.utils.column_index_from_string(r['columna_fuente_'+metric])-1]
  actual=r[metric];checked+=1
  original=pd.to_numeric(rawval,errors='coerce')
  if pd.isna(original) and pd.isna(actual):nulls+=1
  elif pd.isna(original) or pd.isna(actual) or not math.isclose(float(original),float(actual),rel_tol=1e-12,abs_tol=1e-10):errors.append({'code':r.codigo_ine,'year':int(r.anio),'profile':r.tipo,'metric':metric,'raw':str(rawval),'derived':str(actual)})
with raw.open('rb') as h:sha=hashlib.file_digest(h,'sha256').hexdigest()
result={'source_sha256':sha,'records':len(df),'municipalities':df.codigo_ine.nunique(),'numeric_cells_compared':checked,'unpublished_cells_preserved':nulls,'errors':errors,'rozas':{'code':'28128','sheet':'Municipios','row':4415,'cell':'JJ4415','raw':rows[4415][269],'verified_value':3.7572254335260116,'testigos':13,'monthly_median':320,'median_surface':117}}
result['derived_sha256']=hashlib.sha256((p/'data/processed/integracion/vivienda_historico.csv').read_bytes()).hexdigest()
result['source_url']=str(df.url_fuente.iloc[0])
(p/'data/processed/data_quality/housing_source_verification.json').write_text(json.dumps(result,ensure_ascii=False,indent=2));print(json.dumps(result,ensure_ascii=False)[:5000])
