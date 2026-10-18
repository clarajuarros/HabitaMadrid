"""Controlled diagnostics: separate year, reference fitting and added indicator.

These are arithmetic comparisons, not causal effects or a quality certificate.
"""
import numpy as np
import pandas as pd
from src.integration_common import IntegrationError
from src.ivp_calculation import DIMENSIONS, calculate_index, transform_normalization


def controlled_diagnostics(data, comparison, inputs, scenarios):
    details=[];weight_rows=[];summary={}
    for profile in ('VC','VU'):
        result=comparison.loc[comparison.perfil.eq(profile)].set_index('codigo_ine').copy()
        raw=inputs.loc[inputs.perfil.eq(profile)].set_index('codigo_ine').reindex(result.index)
        dimensions=data['dimensiones'].loc[data['dimensiones'].perfil_vivienda.eq(profile)].set_index('codigo_ine').reindex(result.index)
        key=data['configuracion']['perfiles'][profile]['indicador']
        reference=data['normalizacion']['minmax'][key]
        held_score=transform_normalization(raw.alquiler_m2_mes,reference)
        four=dimensions[list(DIMENSIONS)].copy();four.vivienda=held_score
        held=calculate_index(four,{d:1 for d in DIMENSIONS}).valor_indice
        result['ivp_2023_anclas_2024']=held
        result['efecto_anio_anclas_constantes']=held-result.ivp_original
        result['efecto_reajuste_referencias']=result.ivp_puente_mismo_anio-held
        result['efecto_nuevo_indicador_controlado']=result.ivp_propuesto-result.ivp_puente_mismo_anio
        common=result[['ivp_original','ivp_propuesto','ivp_2023_anclas_2024']].notna().all(axis=1)
        pieces=result[['efecto_anio_anclas_constantes','efecto_reajuste_referencias','efecto_nuevo_indicador_controlado']]
        expected=result.ivp_propuesto-result.ivp_original
        if not np.allclose(pieces.sum(axis=1,min_count=3)[common],expected[common],atol=1e-10,rtol=0):
            raise IntegrationError('La descomposición de vivienda no cierra')
        # Isolate the added variable with the same year and same municipality set.
        same_year=result[['ivp_puente_mismo_anio','ivp_propuesto']].notna().all(axis=1)
        result['puesto_coste_2023_muestra_controlada']=result.ivp_puente_mismo_anio.where(same_year).rank(method='min',ascending=False)
        result['puesto_compuesto_2023_muestra_controlada']=result.ivp_propuesto.where(same_year).rank(method='min',ascending=False)
        result['cambio_puesto_indicador_mismo_anio']=result.puesto_coste_2023_muestra_controlada-result.puesto_compuesto_2023_muestra_controlada
        moderate=scenarios.loc[scenarios.perfil.eq(profile)&scenarios.normalizacion_vivienda.eq('minmax')&scenarios.peso_coste.isin([.5,.75,.9])].copy()
        if moderate.duplicated(['codigo_ine','peso_coste']).any():raise IntegrationError('Escenarios duplicados')
        pivot=moderate.pivot(index='codigo_ine',columns='peso_coste',values='ivp').reindex(result.index)
        if set(pivot.columns)!={.5,.75,.9}:raise IntegrationError('Faltan ponderaciones de control')
        cohort=pivot.notna().all(axis=1)
        ranks=pivot.where(cohort,axis=0).rank(method='min',ascending=False)
        span=ranks.max(axis=1)-ranks.min(axis=1)
        for weight in (.5,.75,.9):
            weight_rows.append(pd.DataFrame({'codigo_ine':result.index,'perfil':profile,'peso_coste':weight,
                'ivp':pivot[weight].values,'puesto_muestra_constante':ranks[weight].values,
                'incluido_muestra_comun':cohort.values}))
        result['rango_puesto_pesos_moderados']=span
        top_sets=[set(ranks.loc[cohort,weight].sort_values(kind='stable').head(5).index) for weight in (.5,.75,.9)]
        outside=raw.alquiler_m2_mes.notna()&~raw.alquiler_m2_mes.between(reference['limite_inferior'],reference['limite_superior'])
        summary[profile]={
            'muestra_descomposicion':int(common.sum()),'muestra_mismo_anio':int(same_year.sum()),
            'cambio_max_puesto_solo_indicador':int(result.cambio_puesto_indicador_mismo_anio.abs().max()),
            'muestra_pesos_moderados':int(cohort.sum()),
            'rango_max_puesto_pesos_moderados':int(span.max()),
            'rango_mediano_puesto_pesos_moderados':float(span.median()),
            'top5_comunes_tres_pesos':len(set.intersection(*top_sets)),
            'observaciones_2023_fuera_anclas_2024':int(outside.sum()),
            'media_abs_efecto_anio':float(result.loc[common,'efecto_anio_anclas_constantes'].abs().mean()),
            'media_abs_efecto_referencias':float(result.loc[common,'efecto_reajuste_referencias'].abs().mean()),
            'media_abs_efecto_indicador':float(result.loc[common,'efecto_nuevo_indicador_controlado'].abs().mean()),
        }
        columns=['municipio','perfil','ivp_original','ivp_2023_anclas_2024','ivp_puente_mismo_anio','ivp_propuesto',
            'efecto_anio_anclas_constantes','efecto_reajuste_referencias','efecto_nuevo_indicador_controlado',
            'cambio_puesto_indicador_mismo_anio','rango_puesto_pesos_moderados']
        details.append(result[columns].reset_index())
    review={'estado_revision':'completada','decision':'conservar_ivp_principal',
        'interpretacion':'La descomposición distingue cambios aritméticos; no mide efectos causales.',
        'criterio':'No se ha acreditado que la propuesta mida mejor las condiciones residenciales. Los diagnósticos numéricos no son umbrales de aceptación automática.',
        'motivos':['El cociente agregado no representa esfuerzo individual de los inquilinos.',
                   'La nueva variable comparte información de alquiler con el indicador de coste.',
                   'La coincidencia temporal exige usar 2023 frente al alquiler vigente de 2024.',
                   'La cobertura y la clasificación cambian; eso por sí solo no acredita una mejora.'],
        'resumen':summary}
    return pd.concat(details,ignore_index=True),pd.concat(weight_rows,ignore_index=True),review
