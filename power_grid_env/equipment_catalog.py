"""Offline, owner-anchored factual extracts and explicit modeling assumptions.

Hashes protect normalized extracts, not authenticity of a manufacturer's page.
No remote document, executable code or unit inference is loaded at runtime.
"""
from pathlib import Path
from typing import Literal
from datetime import date
from pydantic import Field, model_validator
from power_grid_env.electrical_sizing import Contract, Installation, SizingTask, Selection
from power_grid_env.equipment_design import EquipmentDesignTask,compile_equipment,run_equipment,verify_equipment
from power_grid_env.industrial_dol import IndustrialDOLCase
from power_grid_env.study_artifacts import encoded,digest,read_json,seal,verify


class Document(Contract):
    id: str = Field(pattern=r'^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$')
    kind: Literal['manufacturer','engineering_reference','owner_assumption']
    reference: str = Field(min_length=1,max_length=4096)
    locator: str = Field(min_length=1,max_length=4096)
    inspected_on: date
    conditions: str = Field(min_length=1,max_length=4096)
    @model_validator(mode='after')
    def nonblank(self):
        if any(not v.strip() for v in [self.reference,self.locator,self.conditions]):
            raise ValueError('nonblank document reference, locator and conditions required')
        return self


class Fact(Contract):
    value: float
    unit: Literal['mm2','A','V','kVA','percent','W','degC','ohm/km','1/degC','factor','A*sqrt(s)/mm2','s']
    document_id: str


class CablePassport(Contract):
    id: str = Field(pattern=r'^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$')
    product_reference: str = Field(min_length=1,max_length=128)
    material: Literal['copper']
    insulation: Literal['XLPE']
    area_mm2: Fact
    ampacity_air30_a: Fact
    dc_r20_ohm_km: Fact
    rated_voltage_v: Fact
    maximum_conductor_c: Fact


class TransformerPassport(Contract):
    id: str = Field(pattern=r'^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$')
    product_reference: str = Field(min_length=1,max_length=128)
    rating_kva: Fact
    secondary_v: Fact
    impedance_percent: Fact
    load_loss_w: Fact
    loss_temperature_c: Fact
    short_time_a: Fact
    short_time_s: Fact


class ModelAssumptions(Contract):
    conductor_temperature_c: Fact
    copper_alpha_per_c: Fact
    ac_dc_resistance_factor: Fact
    cable_reactance_ohm_km: Fact
    cable_k: Fact
    temperature_factor: Fact
    grouping_factor: Fact
    other_factor: Fact


UNITS=dict(area_mm2='mm2',ampacity_air30_a='A',dc_r20_ohm_km='ohm/km',rated_voltage_v='V',maximum_conductor_c='degC',
    rating_kva='kVA',secondary_v='V',impedance_percent='percent',load_loss_w='W',loss_temperature_c='degC',short_time_a='A',short_time_s='s',
    conductor_temperature_c='degC',copper_alpha_per_c='1/degC',ac_dc_resistance_factor='factor',cable_reactance_ohm_km='ohm/km',cable_k='A*sqrt(s)/mm2',temperature_factor='factor',grouping_factor='factor',other_factor='factor')


class AirTemperatureRow(Contract):
    ambient_c: float
    factor: Fact


class EquipmentCatalog(Contract):
    schema_version: Literal['power-grid-env.equipment-catalog/v1']='power-grid-env.equipment-catalog/v1'
    id: str = Field(pattern=r'^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$')
    documents: list[Document] = Field(min_length=1,max_length=128)
    cables: list[CablePassport] = Field(min_length=1,max_length=128)
    transformers: list[TransformerPassport] = Field(min_length=1,max_length=128)
    assumptions: ModelAssumptions
    air_temperature_factors: list[AirTemperatureRow] = Field(min_length=1,max_length=32)

    @model_validator(mode='after')
    def references(self):
        for entries in [self.documents,self.cables,self.transformers]:
            if len({e.id for e in entries})!=len(entries):raise ValueError('duplicate catalog id')
        docs={d.id:d for d in self.documents}
        for entry in [*self.cables,*self.transformers,self.assumptions]:
            for key,unit in UNITS.items():
                fact=getattr(entry,key,None)
                if fact is None:continue
                if fact.unit!=unit:raise ValueError('wrong unit for '+key)
                if fact.document_id not in docs:raise ValueError('missing fact document: '+key)
                if 'temperature' not in key and not key.endswith('_c') and fact.value<=0:
                    raise ValueError('positive fact required: '+key)
                if key in ['temperature_factor','grouping_factor','other_factor'] and fact.value>1:
                    raise ValueError('derating factor cannot exceed one')
                # Passport-only fields cannot silently inherit owner guesses.
                if isinstance(entry,(CablePassport,TransformerPassport)) and key not in ['short_time_a','short_time_s'] and docs[fact.document_id].kind!='manufacturer':
                    raise ValueError('manufacturer evidence required for passport field: '+key)
                if isinstance(entry,ModelAssumptions) and docs[fact.document_id].kind=='manufacturer':
                    raise ValueError('model assumptions need separate evidence, not a product passport')
        if len({r.ambient_c for r in self.air_temperature_factors})!=len(self.air_temperature_factors):
            raise ValueError('duplicate ambient temperature')
        for row in self.air_temperature_factors:
            f=row.factor
            if f.unit!='factor' or not 0<f.value<=1 or f.document_id not in docs:
                raise ValueError('invalid air temperature factor')
        if not any(r.ambient_c==30 and r.factor.value==1 for r in self.air_temperature_factors):
            raise ValueError('air30 rating needs a unity base row')
        if self.assumptions.ac_dc_resistance_factor.value<1:raise ValueError('AC resistance factor must be >=1')
        for c in self.cables:
            if not 20<=self.assumptions.conductor_temperature_c.value<=c.maximum_conductor_c.value:
                raise ValueError('conductor temperature outside passport bounds')
        for t in self.transformers:
            if 100*t.load_loss_w.value/(1000*t.rating_kva.value)>t.impedance_percent.value:
                raise ValueError('loss-derived resistance exceeds transformer impedance')
        return self


class CatalogBrief(Contract):
    schema_version: Literal['power-grid-env.catalog-brief/v1']='power-grid-env.catalog-brief/v1'
    id: str = Field(pattern=r'^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$')
    voltage_v: float = Field(gt=0,le=1000)
    demand_kva: float = Field(gt=0)
    lagging_power_factor: float = Field(gt=0,le=1)
    max_drop_percent: float = Field(gt=0,le=20)
    installation: Installation
    line_id: str = Field(min_length=1,max_length=128)
    mandatory_cases: list[IndustrialDOLCase] = Field(min_length=1,max_length=8)


def compile_catalog(catalog,brief,selection):
    catalog=EquipmentCatalog.model_validate(catalog);brief=CatalogBrief.model_validate(brief)
    selection=Selection.model_validate(selection)
    docs={d.id:d for d in catalog.documents};a=catalog.assumptions
    if any(c.study.network.frequency_hz!=50 for c in brief.mandatory_cases):
        raise ValueError('catalog v1 is restricted to 50 Hz; no frequency extrapolation')
    installation=brief.installation.model_dump(mode='json')
    if installation['method']!='manufacturer_air_single_circuit':
        raise ValueError('catalog v1 supports only manufacturer_air_single_circuit')
    row=next((r for r in catalog.air_temperature_factors if r.ambient_c==installation['ambient_c']),None)
    if row is None:raise ValueError('ambient temperature not tabulated; interpolation forbidden')
    if row.factor!=a.temperature_factor:
        raise ValueError('temperature factor must match exact catalog row and document')
    if a.grouping_factor.value!=1 or a.other_factor.value!=1:
        raise ValueError('grouped or additional installation corrections unsupported')
    # Derating has one authority. Refuse rather than silently overwrite owner criteria.
    for key in ['temperature_factor','grouping_factor','other_factor']:
        if installation[key]!=getattr(a,key).value:raise ValueError('brief/catalog derating mismatch: '+key)
    cables=[];trans=[];derived=[]
    def provenance(entry):
        ids=sorted({getattr(entry,key).document_id for key in UNITS if hasattr(entry,key)})
        return dict(reference='; '.join(docs[i].reference+' ['+docs[i].locator+']' for i in ids),
                    conditions='; '.join(docs[i].conditions for i in ids))
    for c in catalog.cables:
        hot=c.dc_r20_ohm_km.value*(1+a.copper_alpha_per_c.value*(a.conductor_temperature_c.value-20))*a.ac_dc_resistance_factor.value
        source=provenance(c)
        source['reference']+='; model assumptions catalog '+catalog.id
        cables.append(dict(id=c.id,source=source,area_mm2=c.area_mm2.value,ampacity_a=c.ampacity_air30_a.value,
            r_operating_ohm_km=hot,x_ohm_km=a.cable_reactance_ohm_km.value,k_a_sqrt_s_mm2=a.cable_k.value,rated_voltage_v=c.rated_voltage_v.value))
        derived.append(dict(id=c.id,quantity='r_operating_ohm_km',value=hot,
            equation='R20*(1+alpha*(theta-20))*AC_DC_factor',passport_fields=['dc_r20_ohm_km'],assumption_fields=['copper_alpha_per_c','conductor_temperature_c','ac_dc_resistance_factor']))
    for t in catalog.transformers:
        resistance=100*t.load_loss_w.value/(1000*t.rating_kva.value)
        trans.append(dict(id=t.id,source=provenance(t),rating_kva=t.rating_kva.value,secondary_v=t.secondary_v.value,
            impedance_percent=t.impedance_percent.value,resistance_percent=resistance,short_time_a=t.short_time_a.value,short_time_s=t.short_time_s.value))
        derived.append(dict(id=t.id,quantity='resistance_percent',value=resistance,
            equation='100*load_loss_W/(rating_kVA*1000)',passport_fields=['load_loss_w','rating_kva','loss_temperature_c'],
            caveat='effective series resistance including load/stray loss, at published loss temperature; not DC winding resistance'))
    sizing=SizingTask(id=brief.id,voltage_v=brief.voltage_v,demand_kva=brief.demand_kva,lagging_power_factor=brief.lagging_power_factor,max_drop_percent=brief.max_drop_percent,installation=installation,cables=cables,transformers=trans)
    task=EquipmentDesignTask(id=brief.id,sizing=sizing,line_id=brief.line_id,mandatory_cases=brief.mandatory_cases)
    compile_equipment(task,selection)
    lineage=[]
    for entry in [*catalog.cables,*catalog.transformers,a]:
        for field in UNITS:
            fact=getattr(entry,field,None)
            if fact is None:continue
            doc=docs[fact.document_id]
            lineage.append(dict(equipment_id=getattr(entry,'id','model-assumptions'),field=field,
                                fact=fact.model_dump(mode='json'),evidence_kind=doc.kind,
                                reference=doc.reference,locator=doc.locator,conditions=doc.conditions))
    evidence=dict(schema='power-grid-env.catalog-compilation/v1',catalog_sha256=digest(encoded(catalog.model_dump(mode='json'))),
        brief_sha256=digest(encoded(brief.model_dump(mode='json'))),selection=selection.model_dump(mode='json'),
        compiled_task_sha256=digest(encoded(task.model_dump(mode='json'))),derived=derived,lineage=lineage,
        installation_lookup=dict(method=installation['method'],ambient_c=installation['ambient_c'],factor=row.factor.model_dump(mode='json'),interpolation=False),
        owner_assumptions_present=any(d.kind=='owner_assumption' for d in catalog.documents),
        manufacturer_authenticated=False,document_bytes_archived=False,normative_approval=False,
        scope='50 Hz curated numeric extracts; hashes anchor local facts, not remote pages or authenticity; no inferred installation compatibility')
    encoded(evidence)
    return catalog,brief,selection,task,evidence


def run_catalog(catalog,brief,selection,out,*,cancelled=None):
    catalog,brief,selection,task,evidence=compile_catalog(catalog,brief,selection)
    root=Path(out);root.mkdir(parents=True,exist_ok=False)
    for name,value in [('catalog',catalog.model_dump(mode='json')),('brief',brief.model_dump(mode='json')),('selection',selection.model_dump(mode='json')),('compilation',evidence)]:
        (root/f'{name}.json').write_bytes(encoded(value))
    result=run_equipment(task,selection,root/'equipment',cancelled=cancelled)
    result=dict(schema='power-grid-env.catalog-design-result/v1',catalog_sha256=evidence['catalog_sha256'],brief_sha256=evidence['brief_sha256'],equipment=result,
                reward=result['reward'],status=result['status'],manufacturer_authenticated=False,normative_approval=False)
    (root/'result.json').write_bytes(encoded(result));seal(root);return result


def verify_catalog(root,*,expected_catalog=None,expected_brief=None,expected_selection=None):
    root=Path(root);before=verify(root)
    catalog,brief,selection,task,evidence=compile_catalog(read_json(root/'catalog.json'),read_json(root/'brief.json'),read_json(root/'selection.json'))
    for expected,actual,model,label in [(expected_catalog,catalog,EquipmentCatalog,'catalog'),(expected_brief,brief,CatalogBrief,'brief'),(expected_selection,selection,Selection,'selection')]:
        if expected is not None and model.model_validate(expected)!=actual:raise ValueError('owner '+label+' mismatch')
    if read_json(root/'compilation.json')!=evidence:raise ValueError('catalog derivation mismatch')
    verify_equipment(root/'equipment',expected_task=task,expected_selection=selection)
    # Verification adds metadata; compare the exact persisted equipment result.
    original=read_json(root/'equipment'/'result.json')
    expected=dict(schema='power-grid-env.catalog-design-result/v1',catalog_sha256=evidence['catalog_sha256'],brief_sha256=evidence['brief_sha256'],equipment=original,
                  reward=original['reward'],status=original['status'],manufacturer_authenticated=False,normative_approval=False)
    if read_json(root/'result.json')!=expected:raise ValueError('catalog verdict mismatch')
    if verify(root)!=before:raise ValueError('catalog changed during verification')
    return dict(verified=True,manifest_sha256=before,owner_catalog_anchored=expected_catalog is not None,owner_brief_anchored=expected_brief is not None,**expected)
