#!/usr/bin/env python3
"""Run the offline audit and export every check/anomaly without editing observations."""
from pathlib import Path
import sys,json,datetime
import pandas as pd
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from src.data_quality import audit

if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,default=ROOT/'reports/data_quality');args=parser.parse_args()
    result=audit(ROOT);result['generado_utc']=datetime.datetime.now(datetime.timezone.utc).isoformat()
    folder=args.output;folder.mkdir(parents=True,exist_ok=True)
    (folder/'audit.json').write_text(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False)+'\n')
    for name,key in [('validaciones','checks'),('anomalias','anomalies'),('datasets','inventory')]:
        pd.DataFrame(result[key]).to_csv(folder/(name+'.csv'),index=False,encoding='utf-8-sig')
    print(json.dumps({k:v for k,v in result.items() if k not in ['checks','inventory','anomalies','ivp_sha256']},ensure_ascii=False))
    for check in result['checks']:
        if check['resultado']!='correcto':print(check)
    if result['errores_confirmados']:sys.exit(1)
