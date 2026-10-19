"""Portable personal selection report using local images and source references."""
from datetime import date
from io import BytesIO
from pathlib import Path
from xml.sax.saxutils import escape
import json

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.utils import ImageReader
from reportlab.platypus import SimpleDocTemplate,Paragraph,Spacer,Table,TableStyle,PageBreak,Image,KeepTogether
from reportlab.lib.pagesizes import A4
from PIL import Image as PILImage,ImageOps
from src.app_presenter import number,distance,DIMENSION_LABELS,PROFILE_LABELS

FOREST=colors.HexColor('#173d35');LIME=colors.HexColor('#ccf15b');MUTED=colors.HexColor('#61716b');LINE=colors.HexColor('#dce3d9')

def build_personal_pdf(root,payload):
    root=Path(root);buffer=BytesIO();width=A4[0]-84
    doc=SimpleDocTemplate(buffer,pagesize=A4,rightMargin=42,leftMargin=42,topMargin=43,bottomMargin=42,title='HabitaMadrid - Mi selección de municipios',author='HabitaMadrid')
    styles={
        'title':ParagraphStyle('title',fontName='Helvetica-Bold',fontSize=27,leading=31,textColor=FOREST,spaceAfter=12),
        'h2':ParagraphStyle('h2',fontName='Helvetica-Bold',fontSize=16,leading=20,textColor=FOREST,spaceBefore=12,spaceAfter=7),
        'h3':ParagraphStyle('h3',fontName='Helvetica-Bold',fontSize=10,leading=14,textColor=FOREST,spaceBefore=9,spaceAfter=4),
        'body':ParagraphStyle('body',fontName='Helvetica',fontSize=9.5,leading=13,textColor=FOREST,spaceAfter=6),
        'small':ParagraphStyle('small',fontName='Helvetica',fontSize=7.4,leading=10,textColor=MUTED,spaceAfter=5,wordWrap='CJK'),
        'cell':ParagraphStyle('cell',fontName='Helvetica',fontSize=9,leading=12,textColor=FOREST),
        'head':ParagraphStyle('head',fontName='Helvetica-Bold',fontSize=9,leading=12,textColor=colors.white),
    }
    def p(text,style='body'):return Paragraph(escape(str(text)).replace('\n','<br/>'),styles[style])
    def table(rows,widths,header=True):
        cells=[[p(v,'head' if header and i==0 else 'cell') for v in row] for i,row in enumerate(rows)]
        t=Table(cells,colWidths=widths,hAlign='LEFT',repeatRows=1 if header else 0)
        t.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),FOREST if header else colors.HexColor('#edf2e8')),('VALIGN',(0,0),(-1,-1),'TOP'),('LEFTPADDING',(0,0),(-1,-1),9),('RIGHTPADDING',(0,0),(-1,-1),9),('TOPPADDING',(0,0),(-1,-1),8),('BOTTOMPADDING',(0,0),(-1,-1),8),('LINEBELOW',(0,0),(-1,-1),.4,LINE),('ROWBACKGROUNDS',(0,1),(-1,-1),[colors.white,colors.HexColor('#f5f7f2')])]))
        return t
    mode=payload['mode'];label='IVP personalizado' if mode=='personalizado' else 'IVP general';records=payload['records'];weights=payload['weights'];scope=sum(x>0 for x in weights.values())
    scheduled_transport=bool(payload.get('transporte_oferta_programada')) or any(r.get('explanation',{}).get('transport_offer_programmed') for r in records)
    def offer(record):
        return record.get('explanation',{}).get('transport_summary',record)
    story=[p('HABITAMADRID','h3'),p('Tu selección de municipios','title'),p('Una decisión que empieza con datos abiertos.'),p('Preparado el '+date.today().strftime('%d/%m/%Y')+' · '+PROFILE_LABELS[payload['profile']],'small'),Spacer(1,12),p('Tus prioridades','h2'),p(' · '.join(DIMENSION_LABELS[d]+': '+number(100*w,1)+' %' for d,w in weights.items())),p(label+' · '+str(scope)+' dimensiones activas. Normalización fija de la edición del IVP.','small')]
    if payload.get('filters_text'):story.append(p(payload['filters_text'],'small'))
    rows=[['Referencia']+[r['municipio'] for r in records]]
    for title,key,dec in [(label,'active_score',1),('Población · 2025','poblacion',0),('Alquiler · €/m²/mes · 2024','alquiler_m2_mes',2),('Fibra · % hogares · junio 2025','fibra_pct',1)]:rows.append([title]+[number(r.get(key),dec,missing='Sin datos') for r in records])
    if scheduled_transport:
        for title,key in [('Expediciones programadas · media/día','expediciones_dia'),('Destinos municipales directos · media/día','destinos_directos_dia')]:
            rows.append([title]+[number(offer(r).get(key),1,missing='Sin datos') for r in records])
    if records:story.extend([p('Comparación rápida','h2'),table(rows,[140]+[(width-140)/len(records)]*len(records))])
    interpretation=('Si falta una dimensión con peso positivo, ese escenario no recibe índice. Sanidad y educación utilizan distancias en línea recta desde el núcleo principal. Transporte combina las puntuaciones de expediciones intermunicipales y destinos municipales directos con la misma importancia. La distancia a una parada se mantiene como contexto auxiliar y no puntúa transporte.'
                    if scheduled_transport else 'Si falta una dimensión con peso positivo, ese escenario no recibe índice. La conectividad digital y los horarios se presentan como información complementaria. Las distancias a servicios son en línea recta desde el núcleo urbano.')
    dates_note=('Oferta programada de la semana del 19 al 25 de octubre de 2026, sin transbordos ni información en tiempo real. Cada expedición se cuenta una sola vez por municipio; las carencias de cobertura pueden impedir su puntuación. Los horarios no garantizan puntualidad o disponibilidad.'
                if scheduled_transport else 'Los horarios proceden del GTFS descargado el 15/10/2026. Comprueba las referencias antes de decidir.')
    story.extend([Spacer(1,15),p('Cómo interpretar tu selección','h2'),p('Los resultados se ordenan por el índice elegido entre los municipios que cumplen tus filtros. Los empates se calculan antes de redondear. Una puntuación alta expresa una posición relativa dentro de esta edición.'),p(interpretation),p('Los alquileres son medianas declaradas de 2024 y las coberturas digitales son agregados municipales de junio de 2025. '+dates_note,'small')])
    url=payload.get('share_url','')
    if url.startswith(('https://','http://')):
        story.append(Paragraph('<link href="'+escape(url,{'"':'&quot;'})+'" color="#173d35"><u>Abrir esta selección en HabitaMadrid</u></link>',styles['body']))
        if '127.0.0.1' in url or 'localhost' in url:story.append(p('El enlace apunta a la demo local y requiere que HabitaMadrid esté ejecutándose en este ordenador.','small'))
    photos=json.loads((root/'data/reference/municipality_photos/catalog.json').read_text())
    for record in records:
        story.extend([PageBreak(),p('TU SELECCIÓN · INE '+record['codigo_ine'],'small'),p(record['municipio'],'title'),p(label+': '+number(record.get('active_score'),1,missing='Sin datos')+' / 100 · '+str(record['explanation']['available'])+' de 4 dimensiones disponibles','body')])
        photo=photos.get(record['codigo_ine'],{});path=root/'data/reference/municipality_photos'/photo.get('file','missing')
        if photo.get('status')=='available' and path.is_file():
            with PILImage.open(path) as im:
                im=ImageOps.fit(im.convert('RGB'),(960,400));imgbuf=BytesIO();im.save(imgbuf,'JPEG',quality=84);imgbuf.seek(0)
            story.append(Image(imgbuf,width=width,height=width*400/960))
            story.append(p('Foto: '+photo['author']+' · '+photo['license']+' · Wikimedia Commons. Imagen redimensionada y encuadrada.','small'))
            story.append(Paragraph('<link href="'+escape(photo['source_url'],{'"':'&quot;'})+'" color="#61716b">Fuente y licencia de la fotografía</link>',styles['small']))
        story.append(p('Por qué puede encajar contigo','h3'))
        for text in record['explanation']['positives']:story.append(p('- '+text))
        story.append(p('Aspectos que valorar','h3'))
        for text in record['explanation']['consider']:story.append(p('- '+text))
        if scheduled_transport:
            transport=offer(record)
            story.extend([p('Oferta de transporte programada','h3'),
                          p(number(transport.get('expediciones_dia'),1,missing='Sin datos')+' expediciones intermunicipales/día y '+number(transport.get('destinos_directos_dia'),1,missing='Sin datos')+' destinos municipales directos/día de media.'),
                          p('Semana '+str(transport.get('inicio_semana','2026-10-19'))+' a '+str(transport.get('fin_semana','2026-10-25'))+'. Cobertura: '+str(transport.get('estado_cobertura','No publicada')).replace('_',' ')+'. '+str(transport.get('nota_cobertura','')),'small')])
        if record.get('fibra_pct') is not None:story.append(p('Fibra: '+number(record['fibra_pct'],1)+' % de hogares · junio 2025. La cobertura de una vivienda concreta requiere consulta al operador.','small'))
        story.append(p('Referencias de los indicadores','h3'))
        for x in record.get('sources',[]):
            story.append(p(x['Dimensión']+': '+x['Referencia']+' · '+x['Disponible']+' · '+x['Fuente'],'small'))
            if x.get('Enlace'):story.append(Paragraph('<link href="'+escape(x['Enlace'],{'"':'&quot;'})+'" color="#61716b">Consultar fuente de '+escape(x['Dimensión'].lower())+'</link>',styles['small']))
    story.extend([Spacer(1,10),p('Fuentes complementarias','h3'),p('SETELECO: https://digital.gob.es/telecomunicaciones-infraestructuras-digitales/areas-interes/banda-ancha/informacion-cobertura','small'),p('Transporte: fuentes GTFS documentadas en las referencias de cada municipio. CRTM: https://datos.crtm.es/ · Renfe: https://data.renfe.com/ · La cobertura se declara por municipio y las salidas programadas no representan seguimiento en tiempo real.' if scheduled_transport else 'CRTM: https://datos.crtm.es/ · Autobuses interurbanos, catálogo parcial. Las salidas programadas no representan seguimiento en tiempo real.','small')])
    def footer(canvas,document):
        canvas.setStrokeColor(LINE);canvas.line(42,32,A4[0]-42,32);canvas.setFillColor(MUTED);canvas.setFont('Helvetica',7);canvas.drawString(42,21,'HabitaMadrid · Datos abiertos para decisiones informadas');canvas.drawRightString(A4[0]-42,21,str(document.page))
    doc.build(story,onFirstPage=footer,onLaterPages=footer)
    return buffer.getvalue()
