"""Independent acceptance reader. No KiCad product/parser imports.

The lexer is character-driven with spans; exact tokens are retained for protection
comparison. Pin occurrences use library ordinal, never a ref/pin-number dictionary.
"""
from __future__ import annotations
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
import hashlib
import json
import xml.etree.ElementTree as ET

@dataclass
class Atom:
    value: str
    start: int
    end: int
    quoted: bool=False

@dataclass
class Node:
    children: list
    start: int
    end: int
    @property
    def name(self): return self.children[0].value if self.children and isinstance(self.children[0],Atom) else None
    def all(self,name): return [x for x in self.children[1:] if isinstance(x,Node) and x.name==name]
    def one(self,name,default=None):
        found=self.all(name)
        if len(found)>1: raise ValueError(f'duplicate direct child {name}')
        return found[0] if found else default
    def vals(self): return [x.value for x in self.children[1:] if isinstance(x,Atom)]

def parse(text):
    position=0; size=len(text)
    def skip():
        nonlocal position
        while position<size and text[position].isspace(): position+=1
    def term():
        nonlocal position
        skip();start=position
        if position>=size:raise ValueError('premature end')
        if text[position]=='(':
            position+=1;children=[]
            while True:
                skip()
                if position>=size:raise ValueError('unclosed list at '+str(start))
                if text[position]==')':position+=1;return Node(children,start,position)
                children.append(term())
        if text[position]==')':raise ValueError('unexpected close at '+str(position))
        if text[position]=='"':
            position+=1;chars=[]
            while position<size:
                ch=text[position];position+=1
                if ch=='"':return Atom(''.join(chars),start,position,True)
                if ch=='\\':
                    if position>=size:raise ValueError('trailing escape')
                    ch=text[position];position+=1
                    ch={'n':'\n','r':'\r','t':'\t'}.get(ch,ch)
                chars.append(ch)
            raise ValueError('unclosed string')
        while position<size and not text[position].isspace() and text[position] not in '()':position+=1
        return Atom(text[start:position],start,position)
    node=term();skip()
    if position!=size:raise ValueError('trailing source at '+str(position))
    return node

def number(atom):return Decimal(atom)
def nm(atom):
    result=number(atom)*1000000
    if result!=result.to_integral_value():raise ValueError('noninteger nm')
    return int(result)
def point(vals):return [nm(vals[0]),nm(vals[1])]
def rotate(point,deg):
    x,y=point
    return {0:(x,y),90:(y,-x),180:(-x,-y),270:(-y,x)}[deg]
def transform(p,translation=(0,0),angle=0,pivot=(0,0)):
    dx,dy=rotate((p[0]-pivot[0],p[1]-pivot[1]),angle)
    return [pivot[0]+dx+translation[0],pivot[1]+dy+translation[1]]
def node_uuid(node):return node.one('uuid').vals()[0]
def properties(node):return {n.vals()[0]:n.vals()[1] for n in node.all('property')}

def source(path):
    raw=Path(path).read_bytes();text=raw.decode('utf-8-sig');doc=parse(text)
    if doc.name!='kicad_sch':raise ValueError('not schematic')
    root=doc.one('uuid').vals()[0]
    libraries={n.vals()[0]:n for n in doc.one('lib_symbols').all('symbol')}
    rows=[]
    for n in doc.children[1:]:
        if not isinstance(n,Node) or n.name in ('lib_symbols','uuid'):continue
        if n.name not in ('symbol','wire','label','global_label','hierarchical_label','junction','no_connect','sheet','polyline','text'):continue
        row={'kind':n.name,'uuid':node_uuid(n),'span':[n.start,n.end],'tokens':text[n.start:n.end]}
        at=n.one('at')
        if at:row['at_nm']=point(at.vals());row['angle']=int(at.vals()[2]) if len(at.vals())>2 else None
        pts=n.one('pts')
        if pts:row['points_nm']=[point(x.vals()) for x in pts.all('xy')]
        row['fields']=[{'name':p.vals()[0],'value':p.vals()[1],'at_nm':point(p.one('at').vals()),'angle':int(p.one('at').vals()[2]),'tokens':text[p.start:p.end]} for p in n.all('property')]
        if n.name in ('label','global_label','hierarchical_label','text'):row['text']=n.vals()[0]
        if n.name=='sheet':row['sheet_file']=properties(n)['Sheetfile'];row['sheet_name']=properties(n)['Sheetname']
        if n.name=='symbol':
            lib_id=n.one('lib_id').vals()[0];unit=int(n.one('unit').vals()[0]);mirror=n.one('mirror');mirror=mirror.vals()[0] if mirror else None
            row.update(lib_id=lib_id,unit=unit,mirror=mirror,reference=properties(n)['Reference'])
            row['instances']=[{'path':p.vals()[0],'reference':p.one('reference').vals()[0],'unit':int(p.one('unit').vals()[0])} for project in n.one('instances').all('project') for p in project.all('path')]
            row['pins']=[];ordinal=0
            for libunit in libraries[lib_id].all('symbol'):
                suffix=libunit.vals()[0].rsplit('_',2)
                if len(suffix)!=3:raise ValueError('fixture sub-symbol naming')
                effective=int(suffix[1]);body=int(suffix[2])
                for pin in libunit.all('pin'):
                    original_ordinal=ordinal;ordinal+=1
                    if effective not in (0,unit) or body not in (0,1):continue
                    coords=point(pin.one('at').vals());px,py=coords
                    wx,wy=rotate((px,-py),row['angle'])
                    if mirror=='x':wy=-wy
                    if mirror=='y':wx=-wx
                    row['pins'].append({'ordinal':original_ordinal,'number':pin.one('number').vals()[0],'unit':effective,'body':body,'local_nm':coords,'world_nm':[row['at_nm'][0]+wx,row['at_nm'][1]+wy], 'electrical_type':pin.vals()[0], 'pin_style':pin.vals()[1], 'name':pin.one('name').vals()[0], 'local_angle':int(pin.one('at').vals()[2]), 'length_nm':nm(pin.one('length').vals()[0])})
        rows.append(row)
    return {'path':str(Path(path).resolve()),'sha256':hashlib.sha256(raw).hexdigest(),'root_uuid':root,'objects':rows}

def project(root):
    result={};logical=[]
    def visit(path,instance,chain,display='/'):
        key=Path(path).resolve()
        if key in chain:raise ValueError('recursive fixture')
        if key not in result:result[key]=source(key)
        logical.append({'source':key.name,'instance':instance,'display_path':display})
        for row in result[key]['objects']:
            if row['kind']=='sheet':visit(key.parent/row['sheet_file'],instance+'/'+row['uuid'],chain+[key],display+row['sheet_name']+'/')
    initial=source(root);visit(root,'/'+initial['root_uuid'],[])
    return {'sources':{p.name:r for p,r in result.items()},'logical_pages':logical}

def pin_occurrences(project_data):
    result=[]
    for page in project_data['logical_pages']:
        doc=project_data['sources'][page['source']]
        for obj in doc['objects']:
            if obj['kind']!='symbol':continue
            matches=[i for i in obj['instances'] if i['path']==page['instance']]
            if len(matches)!=1:raise ValueError('missing/ambiguous own annotation '+obj['uuid']+' '+page['instance'])
            for pin in obj['pins']:
                result.append({'identity':[obj['uuid'],page['instance'],obj['unit'],pin['body'],pin['ordinal']], 'reference':matches[0]['reference'],'number':pin['number'],'world_nm':pin['world_nm'],'source':page['source']})
    return result

def on_segment(p,a,b):
    return (b[0]-a[0])*(p[1]-a[1])==(b[1]-a[1])*(p[0]-a[0]) and min(a[0],b[0])<=p[0]<=max(a[0],b[0]) and min(a[1],b[1])<=p[1]<=max(a[1],b[1])

def connectivity(data):
    # Every terminal/vertex/label/junction is an independent participant. Intersections
    # connect only at endpoints or explicit junctions, matching our orthogonal fixture.
    all_groups=[];ncs=[];pins=pin_occurrences(data)
    for page in data['logical_pages']:
        obj=data['sources'][page['source']]['objects']; participants=[];segments=[]
        for p in pins:
            if p['identity'][1]==page['instance']:participants.append({'id':p['identity'],'point':p['world_nm'],'pin':[p['reference'],p['number']]})
        for row in obj:
            if row['kind']=='wire':
                for index,p in enumerate(row['points_nm']):participants.append({'id':[row['uuid'],page['instance'],'vertex',index],'point':p})
                segments.extend(zip(row['points_nm'],row['points_nm'][1:]))
            if row['kind'] in ('label','global_label','hierarchical_label','junction'):
                r={'id':[row['uuid'],page['instance']],'point':row['at_nm']}
                if 'text' in row:r['name']=('/' if row['kind']=='global_label' else page['instance']+'/')+row['text']
                participants.append(r)
        parent=list(range(len(participants)))
        def find(i):
            while parent[i]!=i:parent[i]=parent[parent[i]];i=parent[i]
            return i
        def union(a,b):parent[find(a)]=find(b)
        for i,a in enumerate(participants):
            for j,b in enumerate(participants[:i]):
                if a['point']==b['point'] or ('name'in a and a.get('name')==b.get('name')):union(i,j)
            for start,end in segments:
                if on_segment(a['point'],start,end):
                    for j,b in enumerate(participants[:i]):
                        if on_segment(b['point'],start,end):union(i,j)
        groups={}
        for i,p in enumerate(participants):groups.setdefault(find(i),[]).append(p)
        for group in groups.values():
            all_groups.append({'identities':sorted([p['id'] for p in group],key=str),'pins':sorted([p['pin'] for p in group if 'pin'in p]),'names':sorted({p['name'] for p in group if 'name'in p})})
        for row in obj:
            if row['kind']=='no_connect':ncs.append({'id':[row['uuid'],page['instance']],'pins':sorted([p['identity'] for p in pins if p['identity'][1]==page['instance'] and p['world_nm']==row['at_nm']],key=str)})
    # Global names couple logical sheet instances. Merge their raw participants
    # while retaining every source pin ordinal and every instance identity.
    changed=True
    while changed:
        changed=False
        for i,a in enumerate(all_groups):
            ag={x for x in a['names'] if x.count('/')==1 and x.startswith('/')}
            for j in range(i):
                b=all_groups[j];bg={x for x in b['names'] if x.count('/')==1 and x.startswith('/')}
                if ag & bg:
                    b['identities']=sorted(b['identities']+a['identities'],key=str)
                    b['pins']=sorted(b['pins']+a['pins'])
                    b['names']=sorted(set(b['names']+a['names']))
                    all_groups.pop(i);changed=True;break
            if changed:break
    return {'groups':sorted(all_groups,key=lambda x:str(x['identities'])),'nc':sorted(ncs,key=lambda x:str(x['id']))}

def expected_physical(data,graph):
    mapping={p['instance']:p['display_path'] for p in data['logical_pages']}
    nets=[]
    for g in graph['groups']:
        if not g['pins']:continue
        names=[]
        for name in g['names']:
            if name.count('/')==1:names.append(name[1:])
            else:
                path,_,label=name.rpartition('/')
                names.append(mapping[path]+label)
        nets.append({'pins':[list(x) for x in sorted({tuple(p) for p in g['pins']})],'explicit_names':sorted(set(names))})
    unique={json.dumps(n,sort_keys=True):n for n in nets}
    return sorted(unique.values(),key=str)

def compare_native(expected,xml_path):
    actual=native_xml(xml_path)
    wanted={tuple(map(tuple,n['pins'])):n['explicit_names'] for n in expected}
    got={tuple(map(tuple,n['pins'])):n['name'] for n in actual['nets']}
    if wanted.keys()!=got.keys():raise ValueError('native physical pin partitions differ from independent intent')
    for pins,names in wanted.items():
        if names and got[pins] not in names:raise ValueError('native explicit net name differs: '+got[pins])
    return {'physical_net_groups':len(got),'named_groups':sum(bool(x) for x in wanted.values())}

def native_xml(path):
    tree=ET.parse(path).getroot();nets=[]
    for n in tree.findall('./nets/net'):
        pins=sorted({(p.attrib['ref'],p.attrib['pin']) for p in n.findall('node')})
        nets.append({'name':n.attrib['name'],'pins':[list(p) for p in pins]})
    components=sorted((c.attrib['ref'],c.findtext('value')) for c in tree.findall('./components/comp'))
    return {'components':components,'nets':sorted(nets,key=lambda x:x['name'])}
