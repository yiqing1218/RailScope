"""Audit every directory object against source tags and batched public articles.

Only explicit present-tense classification statements are extracted. Missing,
ambiguous, historical or mismatched articles stay unresolved. No article bodies
are saved. Reruns reuse the small fact cache and retain a per-object audit trail.
"""
import argparse
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
import csv
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import sys
import time
from urllib.parse import urlencode, quote
from urllib.request import Request, urlopen
from urllib.error import HTTPError

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT/'desktop'), str(ROOT/'backend')]
from desktop.station_classification import classification
from desktop.catalog_workspace import read_overrides
from desktop.persistence import write_json_atomic

SCHEMA = 'railscope.station-classification-reference.v1'
PARSER_VERSION = 2
FIELDS = {'technical_type': ('中间站', '区段站', '编组站'),
          'business_type': ('客运站', '货运站', '客货运站')}


def apply_primary_references(stations, records):
    path=ROOT/'data/catalog/station_classification_primary.json'
    if not path.exists(): return
    sources=json.loads(path.read_text(encoding='utf-8'))['sources']
    by_name=defaultdict(list)
    for record in records:
        names={record['name'],record.get('properties',{}).get('node_tags',{}).get('name','')}
        for name in names:
            if name: by_name[name.removesuffix('站')].append(record)
    for source in sources:
        for name in source['station_names']:
            matches={r['id']:r for r in by_name.get(name,[])}
            if len(matches)!=1: continue  # Do not silently resolve a homonym.
            sid,record=next(iter(matches.items()))
            facts=stations.setdefault(sid,{'coordinates':record['coordinates'],'provenance':{}})
            for field in FIELDS:
                if field not in source: continue
                if field in facts and facts[field]!=source[field]:
                    facts.setdefault('reference_conflicts',{})[field]={'value':facts[field],
                        'provenance':facts['provenance'].get(field,{})}
                facts[field]=source[field]
                facts['provenance'][field]={'source':source['url'],
                    **{k:source[k] for k in ('snapshot','retrieved_at','verification_status','confidence')},
                    'evidence_kind':'explicit_named_station_in_official_source'}


def plain(value):
    value = re.sub(r'<ref\b[^>]*>.*?</ref>|<ref\b[^>]*/>', '', value, flags=re.S|re.I)
    value = re.sub(r'\[\[[^\]|]+\|([^\]]+)\]\]', r'\1', value)
    value = value.replace('[[', '').replace(']]', '').replace("'''", '')
    # Classification labels sometimes use traditional Chinese. Convert only
    # the required vocabulary; this is not a guessed station-name translation.
    for a,b in [('編組','编组'),('區段','区段'),('中間','中间'),('客貨','客货'),
                ('貨運','货运'),('客運','客运'),('車站','车站')]: value=value.replace(a,b)
    value=re.sub(r'(客货运|客运|货运)(?:特等|[一二三四五12345]等)站',r'\1站',value)
    return value


def coordinate(raw):
    match = re.search(r'\{\{[Cc]oord\s*\|([^{}]+)', raw)
    if not match: return None
    parts=[p.strip() for p in match[1].split('|')]
    numbers=[]; axes=[]
    for part in parts:
        if re.fullmatch(r'-?\d+(?:\.\d+)?', part): numbers.append(float(part))
        elif part in ('N','S','E','W') and numbers:
            value=sum(n/60**i for i,n in enumerate(numbers)) * (-1 if part in ('S','W') else 1)
            axes.append(value); numbers=[]
        elif axes: break
    if len(axes)==2: return [axes[1],axes[0]]
    if len(numbers)>=2: return [numbers[1],numbers[0]]
    return None


def province_mentioned(province, body):
    # Public descriptions commonly write 湖南/广西/新疆 rather than the full
    # administrative label stored by the map. Keep the same province evidence.
    short = re.sub(r'(维吾尔自治区|壮族自治区|回族自治区|自治区|省|市)$', '', province)
    return bool(short and len(short) >= 2 and short in plain(body))


def extract_article(page, record):
    raw=page.get('revisions',[{}])[0].get('slots',{}).get('main',{}).get('content','')
    excerpt=page.get('extract')
    if excerpt is not None:
        raw=excerpt
    if not raw: return {}, 'missing_article'
    if excerpt is None and not re.search(r'Infobox\s+(?:China railway station|station|railway station)',raw,re.I):
        return {}, 'not_station_article'
    body=plain(raw)
    lead_start=re.search(r"^\s*'''[^\n]+?'''", raw, re.M)
    lead=plain(excerpt) if excerpt is not None else plain(raw[lead_start.start():].split('\n==',1)[0][:1800]) if lead_start else ''
    # Reject metro-only articles and homonyms in another city/at another site.
    if not re.search(r'铁路|鐵路|国铁|國鐵',lead+body[:3000]): return {}, 'not_national_rail'
    geo=page.get('coordinates',[{}])[0]
    point=[geo['lon'],geo['lat']] if 'lon' in geo else coordinate(raw)
    x,y=record['coordinates'][:2]
    if point:
        gap=math.hypot((point[0]-x)*math.cos(math.radians(y)), point[1]-y)*111.195
        if gap>5: return {}, 'location_mismatch'
    elif not province_mentioned(record.get('province',''), body[:4000]):
        return {}, 'location_unconfirmed'
    values={}; provenance={}
    # Only named infobox fields and the station's introductory description.
    cells=re.findall(r'^\s*\|\s*(?:type|station_type|车站类型|車站類型|车站功能|車站功能|车站性质|車站性質|技术性质|技術性質|业务性质|業務性質)\s*=\s*([^\n]*)',raw,re.M|re.I)
    explicit=plain(' '.join(cells))
    for field,allowed in FIELDS.items():
        found={label for label in allowed if label in explicit}
        if field=='business_type' and '客货运站' in found: found-={'客运站','货运站'}
        if not found:
            sentences=re.split(r'[。\n]',lead)
            for sentence in sentences:
                # Older roles and future proposals are not current evidence.
                if re.search(r'曾|原为|原為|改建前|计划|計劃|拟建|擬建|将成为|將成為|历史|歷史|转移|轉移|迁移|遷移|接替|取代|取消',sentence): continue
                matches={label for label in allowed if label in sentence}
                if field=='business_type' and '客货运站' in matches: matches-={'客运站','货运站'}
                subject=record['name'].removesuffix('站')
                if matches and re.search(r'(?:'+re.escape(subject)+r'站|车站|本站|按技术|按业务).{0,130}?(?:为|為|是|属|屬).{0,35}?(?:中间站|区段站|编组站|客运站|货运站|客货运站)',sentence):
                    found|=matches
        if len(found)==1:
            values[field]=found.pop()
            provenance[field]={'source':page.get('fullurl') or 'https://zh.wikipedia.org/wiki/'+quote(page['title']),
                'snapshot':str(page.get('lastrevid') or page['revisions'][0]['revid']), 'retrieved_at':datetime.now(timezone.utc).isoformat(),
                'verification_status':'source_unverified', 'confidence':None,
                'evidence_kind':'explicit_infobox_or_current_intro'}
    if 'business_type' not in values:
        service=lead
        pno=bool(re.search(r'不办理(?:客运|旅客)|不辦理(?:客運|旅客)',service))
        fno=bool(re.search(r'不办理货运|不辦理貨運|不办理货物',service))
        pyes=bool(re.search(r'客运[：:]\s*办理|办理旅客乘降|办理客运业务',service)) and not pno
        fyes=bool(re.search(r'货运[：:]\s*(?:仅)?办理|办理[^。；;]{0,25}(?:整车货物|货物发到)|办理货运业务',service)) and not fno
        label='客货运站' if pyes and fyes else '客运站' if pyes and fno else '货运站' if fyes and pno else None
        if label:
            values['business_type']=label
            provenance['business_type']={'source':page.get('fullurl') or 'https://zh.wikipedia.org/wiki/'+quote(page['title']),
                'snapshot':str(page.get('lastrevid') or page['revisions'][0]['revid']), 'retrieved_at':datetime.now(timezone.utc).isoformat(),
                'verification_status':'source_unverified','confidence':None,'evidence_kind':'explicit_current_service_pair'}
    return {'coordinates':record['coordinates'],**values,'provenance':provenance}, 'matched_article'


def fetch_batch(titles):
    args={'action':'query','format':'json','formatversion':2,'prop':'revisions|info',
          'rvprop':'ids|content','rvslots':'main','rvsection':0,
          'inprop':'url','redirects':1,
          'converttitles':1,'titles':'|'.join(titles),'maxlag':5}
    url='https://zh.wikipedia.org/w/api.php?'+urlencode(args)
    for attempt in range(3):
        try:
            time.sleep(3.5)
            with urlopen(Request(url,headers={'User-Agent':'RailScopeStationAudit/1.0 (classification research)'}),timeout=45) as handle:
                payload=json.load(handle)
            if 'error' in payload: raise ValueError(payload['error'].get('code','api_error'))
            query=payload['query']; aliases={}
            for key in ('normalized','converted','redirects'):
                for row in query.get(key,[]): aliases[row['from']]=row['to']
            pages={p['title']:p for p in query['pages']}
            result={}
            for title in titles:
                target=title;seen=set()
                while target in aliases and target not in seen:seen.add(target);target=aliases[target]
                result[title]=pages.get(target,{'title':target,'missing':True})
            return result
        except Exception as error:
            if attempt==2: raise
            if isinstance(error, HTTPError) and error.code==429:
                print('Source rate limit; wait 45 seconds before retrying',flush=True)
                time.sleep(45)
            else: time.sleep(3+attempt)


def run(inventory, output, audit, workers=1):
    records=json.loads(Path(inventory).read_text(encoding='utf-8'))
    overrides=read_overrides(ROOT/'data/user_settings/rail_catalog.json')
    cache=Path(audit).with_suffix('.lookup.json')
    lookups=json.loads(cache.read_text(encoding='utf-8')) if cache.exists() else {}
    stations={}; wanted=defaultdict(list); state={}
    for record in records:
        sid=record['id']; current=classification(record,overrides.get('station:'+sid,{}))
        generic_yard = record.get('station_type') in ('车场','工业站','港湾站','集装箱办理站') and not record['name'].startswith('未命名') and not record['name'].endswith('场')
        if current['classification_group']=='其他' and not generic_yard: state[sid]='other_facility';continue
        name=record['name']
        if name.isdigit() or name.startswith(('node/','way/','relation/','节点 ','未命名')):
            state[sid]='unnamed_source_object';continue
        tags=record['properties'].get('node_tags',{})
        article=tags.get('wikipedia:zh') or tags.get('wikipedia','')
        title=article[3:] if article.startswith('zh:') else name if name.endswith('站') else name+'站'
        if sid in lookups:
            saved=lookups[sid]
            if (saved.get('coordinates')==record['coordinates'] and saved.get('lookup_title')==title
                    and saved.get('lookup_version')==PARSER_VERSION
                    and not saved.get('lookup_status','').startswith('fetch_failed')
                    and (saved.get('lookup_status') != 'location_unconfirmed' or
                         saved.get('lookup_location_version') == 2)
                    and (saved.get('lookup_status') != 'matched_article' or
                         saved.get('lookup_type_fields_version') == 2 or
                         all(field in saved for field in FIELDS))
                    and (saved.get('lookup_scope')=='lead_wikitext' or
                         saved.get('lookup_status') in ('missing_article','not_station_article') or
                         all(field in saved for field in FIELDS))):
                state[sid]=saved['lookup_status']
                if saved.get('provenance'):stations[sid]={k:v for k,v in saved.items() if not k.startswith('lookup_')}
                continue
        wanted[title].append(record)
    titles=list(wanted); batches=[titles[i:i+40] for i in range(0,len(titles),40)]
    print(f'Audit {len(records)} objects; look up {len(titles)} unique station titles in {len(batches)} batches',flush=True)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures={pool.submit(fetch_batch,batch):batch for batch in batches}
        for completed,future in enumerate(as_completed(futures),1):
            batch=futures[future]
            try: pages=future.result(); error=None
            except Exception as exc:pages={};error=type(exc).__name__
            for title in batch:
                for record in wanted[title]:
                    sid=record['id']
                    facts,status=extract_article(pages.get(title,{}),record) if not error else ({},'fetch_failed_'+error)
                    saved={'coordinates':record['coordinates'],**facts,'lookup_title':title,'lookup_status':status,
                           'lookup_version':PARSER_VERSION,'lookup_scope':'lead_wikitext',
                           'lookup_location_version':2,'lookup_type_fields_version':2}
                    lookups[sid]=saved;state[sid]=status
                    if facts.get('provenance'):stations[sid]=facts
            if completed%10==0 or completed==len(batches):
                cache.parent.mkdir(parents=True,exist_ok=True)
                write_json_atomic(cache,lookups)
                print(f'Completed {completed}/{len(batches)} batches; extracted {len(stations)} station references',flush=True)
    target=Path(output);target.parent.mkdir(parents=True,exist_ok=True)
    apply_primary_references(stations,records)
    write_json_atomic(target,{'schema':SCHEMA,'retrieved_at':datetime.now(timezone.utc).isoformat(),
        'inventory_sha256':hashlib.sha256(Path(inventory).read_bytes()).hexdigest(),'stations':stations})
    rows=[]
    for record in records:
        result=classification(record,overrides.get('station:'+record['id'],{}))
        rows.append({'source_id':record['id'],'name':record['name'],'province':record['province'],
            'group':result['classification_group'],'technical_type':result['technical_type'],
            'business_type':result['business_type'],'lookup_status':state.get(record['id'],'not_looked_up'),
            'sources':' | '.join(sorted({v.get('source','') for v in result['classification_provenance'].values()} |
                ({'https://zh.wikipedia.org/wiki/'+quote(lookups[record['id']]['lookup_title'])}
                 if state.get(record['id'])=='matched_article' else set())))})
    with Path(audit).open('w',encoding='utf-8-sig',newline='') as handle:
        writer=csv.DictWriter(handle,fieldnames=list(rows[0]))
        writer.writerow({'source_id':'来源对象编号','name':'名称','province':'省份',
            'group':'目录大类','technical_type':'技术作业性质','business_type':'业务性质',
            'lookup_status':'查证结果','sources':'资料来源或链接'})
        labels={'other_facility':'原始名称/标签明确为其他设施',
            'unnamed_source_object':'无可用名称，无法按站名查询',
            'matched_article':'已找到对应条目；性质只填写明确有依据的字段',
            'missing_article':'未找到对应条目','not_station_article':'条目不是铁路车站',
            'location_mismatch':'同名条目位置不符','location_unconfirmed':'条目位置无法确认',
            'not_national_rail':'未确认是国铁车站','not_looked_up':'未查询'}
        writer.writerows({**row,'lookup_status':labels.get(row['lookup_status'],row['lookup_status'])} for row in rows)
    station_rows=[r for r in rows if r['group']=='车站']
    summary={'total':len(rows),'groups':dict(Counter(r['group'] for r in rows)),
        'reference_objects':len(stations),'lookup':dict(Counter(r['lookup_status'] for r in rows)),
        'technical':dict(Counter(r['technical_type'] for r in rows if r['group']=='车站')),
        'business':dict(Counter(r['business_type'] for r in rows if r['group']=='车站')),
        'both_unresolved':sum(r['technical_type']==r['business_type']=='待核实' for r in station_rows),
        'both_classified':sum(r['technical_type']!='待核实' and r['business_type']!='待核实' for r in station_rows)}
    write_json_atomic(Path(audit).with_suffix('.summary.json'),summary)
    print(json.dumps(summary,ensure_ascii=False),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--inventory',required=True)
    parser.add_argument('--output',default=str(ROOT/'data/catalog/station_classification_reference.json'))
    parser.add_argument('--audit',default=str(ROOT/'data/user_settings/station-classification-audit.csv'))
    parser.add_argument('--workers',type=int,default=1)
    opts=parser.parse_args();run(opts.inventory,opts.output,opts.audit,opts.workers)
