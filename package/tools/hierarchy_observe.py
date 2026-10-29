"""Independent limited orthogonal scalar-hierarchy reader. No product imports.

Character lexer reused byte-for-byte in raw_reader_base; hierarchy traversal,
sheet-pin interface links and independent physical partitions are defined here.
This method is intentionally limited to the declared passive standard symbols,
scalar wires/labels and ordinary sheets used by the holdout requirements.
"""
from __future__ import annotations
import hashlib
import os
import json
from pathlib import Path
import xml.etree.ElementTree as ET
from raw_reader_base import Node, parse, source, point, nm, node_uuid, properties, on_segment

def richer_source(path):
    result=source(path)
    text=Path(path).read_text(encoding='utf-8-sig')
    document=parse(text)
    allowed={'version','generator','generator_version','uuid','paper','title_block','lib_symbols','symbol','wire',
      'hierarchical_label','label','global_label','junction','no_connect','sheet','sheet_instances','embedded_fonts'}
    unsupported=[n.name for n in document.children if isinstance(n,Node) and n.name not in allowed]
    if unsupported:raise ValueError('Independent declared reader does not cover these source objects: '+str(unsupported))
    by_uuid={o['uuid']:o for o in result['objects']}
    for node in document.all('sheet'):
        row=by_uuid[node_uuid(node)]
        row['size_nm']=point(node.one('size').vals())
        row['sheet_pins']=[]
        for ordinal,p in enumerate(node.all('pin')):
            vals=p.vals(); at=p.one('at').vals()
            row['sheet_pins'].append({'ordinal':ordinal,'uuid':node_uuid(p),'name':vals[0],
              'shape':vals[1], 'at_nm':point(at),'angle':int(at[2]),'tokens':text[p.start:p.end]})
        row['sheet_instances']=[{'path':p.vals()[0],'page':p.one('page').vals()[0]}
          for projects in node.all('instances') for project in projects.all('project') for p in project.all('path')]
    for node in document.all('hierarchical_label'):
        by_uuid[node_uuid(node)]['shape']=node.one('shape').vals()[0]
    return result

def project(root):
    root=Path(root).resolve(); base=root.parent; sources={}; pages=[]
    def visit(path, instance, chain, display, parent=None, via_sheet=None):
        actual=path.resolve()
        if actual in chain:raise ValueError('Source reference cycle: '+str(actual))
        key=actual.relative_to(base).as_posix()
        if key not in sources:sources[key]=richer_source(actual)
        page={'source':key,'instance':instance,'display_path':display,'parent':parent,'via_sheet':via_sheet}
        pages.append(page)
        for obj in sources[key]['objects']:
            if obj['kind']=='sheet':
                visit(actual.parent/obj['sheet_file'],instance+'/'+obj['uuid'],chain+[actual],
                      display+obj['sheet_name']+'/',instance,obj['uuid'])
    first=richer_source(root)
    visit(root,'/'+first['root_uuid'],[],'/')
    if len(pages)>64:raise ValueError('Independent reader page resource limit64 exceeded')
    return {'root':str(root),'sources':sources,'logical_pages':pages}

def read_pins(data):
    result=[]
    refs={}
    for page in data['logical_pages']:
        document=data['sources'][page['source']]
        for obj in document['objects']:
            if obj['kind']!='symbol':continue
            matches=[i for i in obj['instances'] if i['path']==page['instance']]
            if len(matches)!=1:raise ValueError('Missing/ambiguous annotation '+obj['uuid']+' '+page['instance'])
            annotation=matches[0]
            if annotation['unit']!=obj['unit']:raise ValueError('Effective unit disagreement')
            previous=refs.setdefault(annotation['reference'],set())
            if obj['unit'] in previous:raise ValueError('Duplicate reference/unit '+annotation['reference'])
            previous.add(obj['unit'])
            for pin in obj['pins']:
                result.append({'identity':[page['source'],obj['uuid'],page['instance'],obj['unit'],pin['body'],pin['ordinal']],
                  'reference':annotation['reference'],'number':pin['number'],'world_nm':pin['world_nm'],
                  'source':page['source'],'instance':page['instance'],'type':pin['electrical_type']})
    return result

def graph(data):
    participants=[]; wires=[]; labels={}; parent_pins={}; ncs=[]
    pin_rows=read_pins(data)
    for pin in pin_rows:
        participants.append({'identity':pin['identity'],'page':pin['instance'],'point':pin['world_nm'],
                             'physical_pin':[pin['reference'],pin['number']]})
    for page in data['logical_pages']:
        for obj in data['sources'][page['source']]['objects']:
            base=[page['source'],obj['uuid'],page['instance']]
            if obj['kind']=='wire':
                if len(obj['points_nm'])!=2:raise ValueError('Independent reader supports two-endpoint saved wire only')
                indexes=[]
                for index,point_value in enumerate(obj['points_nm']):
                    indexes.append(len(participants))
                    participants.append({'identity':base+['vertex',index],'page':page['instance'],'point':point_value})
                wires.append((page['instance'],obj['points_nm'][0],obj['points_nm'][1],indexes))
            elif obj['kind'] in ('label','global_label','hierarchical_label','junction'):
                row={'identity':base,'page':page['instance'],'point':obj['at_nm']}
                if obj['kind']!='junction':
                    key=('global',obj['text']) if obj['kind']=='global_label' else (page['instance'],obj['text'])
                    row['name_key']=key
                    labels.setdefault(key,[]).append(len(participants))
                    row.update(label_kind=obj['kind'],label_text=obj['text'],shape=obj.get('shape'))
                participants.append(row)
            elif obj['kind']=='sheet':
                for pin in obj['sheet_pins']:
                    key=(page['instance']+'/'+obj['uuid'],pin['name'])
                    if key in parent_pins:raise ValueError('Duplicate sheet interface name '+str(key))
                    parent_pins[key]=(len(participants),pin)
                    participants.append({'identity':base+['sheet_pin',pin['ordinal'],pin['uuid']],
                       'page':page['instance'],'point':pin['at_nm'],'sheet_pin':pin['name'],'shape':pin['shape']})
            elif obj['kind']=='no_connect':ncs.append({'identity':base,'page':page['instance'],'point':obj['at_nm']})
            elif obj['kind']=='symbol':pass
            else:raise ValueError('Unsupported independent geometry: '+obj['kind'])
    parent=list(range(len(participants)))
    def find(i):
        while parent[i]!=i:parent[i]=parent[parent[i]];i=parent[i]
        return i
    def union(a,b):parent[find(a)]=find(b)
    for i,a in enumerate(participants):
        for j,b in enumerate(participants[:i]):
            if a['page']==b['page'] and a['point']==b['point']:union(i,j)
    for page,a,b,indexes in wires:
        union(indexes[0],indexes[1])
        for i,p in enumerate(participants):
            if p['page']==page and on_segment(p['point'],a,b):union(i,indexes[0])
    for values in labels.values():
        for value in values[1:]:union(values[0],value)
    pairs=[]
    for (child_instance,name),(index,pin) in parent_pins.items():
        candidates=[i for i,p in enumerate(participants) if p['page']==child_instance and
            p.get('label_kind')=='hierarchical_label' and p.get('label_text')==name]
        if len(candidates)!=1:raise ValueError('Missing/ambiguous child interface '+str((child_instance,name)))
        child=participants[candidates[0]]
        if child['shape']!=pin['shape']:raise ValueError('Interface direction mismatch '+str((child_instance,name)))
        union(index,candidates[0]);pairs.append({'child_instance':child_instance,'name':name,'shape':pin['shape']})
    for i,p in enumerate(participants):
        if p.get('label_kind')=='hierarchical_label' and (p['page'],p['label_text']) not in parent_pins:
            raise ValueError('Unpaired hierarchical label '+str(p['identity']))
    groups={}
    for i,p in enumerate(participants):groups.setdefault(find(i),[]).append(p)
    actual=[]
    for values in groups.values():
        pins=sorted(p['physical_pin'] for p in values if 'physical_pin' in p)
        actual.append({'pins':pins,'identities':sorted([p['identity'] for p in values],key=str)})
    for marker in ncs:
        marker['associated_pin_identities']=[p['identity'] for p in pin_rows if p['instance']==marker['page'] and p['world_nm']==marker['point']]
    return {'raw_pin_occurrences':pin_rows,'interface_pairs':pairs,'groups':sorted(actual,key=str),'nc':ncs}

def official_xml(path):
    root=ET.parse(path).getroot()
    components=[{'reference':n.attrib['ref'],'value':n.findtext('value'),'path':n.find('sheetpath').attrib}
                for n in root.findall('./components/comp')]
    nets=[{'name':n.attrib['name'],'pins':sorted([p.attrib['ref'],p.attrib['pin']] for p in n.findall('node'))}
          for n in root.findall('./nets/net')]
    return {'components':sorted(components,key=lambda x:x['reference']),'nets':sorted(nets,key=str)}

if __name__=='__main__':
    import sys
    result=project(Path(sys.argv[1])); raw=graph(result)
    report={'reader':'Independent limited scalar/hierarchy observation, not automatic acceptance','project':result,'raw_graph':raw}
    if len(sys.argv)>2:report['official_XML_observation']=official_xml(Path(sys.argv[2]))
    print(json.dumps(report,ensure_ascii=False,indent=2))