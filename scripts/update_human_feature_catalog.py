"""Generate the supported three-product catalog from the producer's contract."""
from __future__ import annotations

from pathlib import Path
import yaml
from human.pipelines.delivery.validation import product_contract
from human.pipelines.population.collected import DISPLAY_LABELS, COLLECTED_LIMITATION
from human.pipelines.native.features import summary_fields, GROUPS


def build_catalog():
    collections={}
    for name, product in product_contract()['products'].items():
        columns = product['columns'] + (product_contract().get('extensions', {}).get('ports', {}).get('columns', []) if name == 'static.parquet' else [])
        definitions=summary_fields([c['name'] for c in columns]) if name=='static.parquet' else {}
        features=[]
        for field in columns:
            key=field['name'];definition=definitions.get(key,{})
            selected=key in DISPLAY_LABELS or key in definitions
            if key in DISPLAY_LABELS:
                definition={'label':DISPLAY_LABELS[key],'family':'Population','limitation':COLLECTED_LIMITATION,
                    'units':'persons/km2' if 'density' in key else 'persons'}
            features.append({'name':key,'arrow_type':field['arrow_type'],'role':'evidence' if selected else 'support',
                'model_eligible':False,'selectable':selected,**definition})
        collections[name.removesuffix('.parquet').replace('-','_')]={
            'artifact':name,'schema':product['schema'],'primary_key':product['primary_key'],
            'model_policy':'evidence_only','features':features}
    collections['static_native']['groups']=GROUPS
    return {'schema_version':2,'generated_by':'scripts/update_human_feature_catalog.py',
        'pipeline':'human.get_human_data','default_model_policy':{},'collections':collections}


if __name__=='__main__':
    target=Path(__file__).resolve().parents[1]/'src/human/resources/feature_catalog.yaml'
    target.write_text(yaml.safe_dump(build_catalog(),sort_keys=False,allow_unicode=True))
