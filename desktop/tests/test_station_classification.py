"""Classification evidence must not change station identity or asset ownership."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from desktop.station_classification import classification, station_catalog_path, station_destination_changes


def station(**values):
    return {'id':'node/1', 'name':'甲站', 'kind':'station', 'station_type':'未定义',
            'province':'甲省', 'city':'甲市', 'coordinates':[120,30], 'properties':{},
            'line_ids':[], 'line_names':[], **values}


def test_two_axes_are_independent_and_do_not_rewrite_legacy_metadata():
    record=station(station_type='编组站')
    custom={'business_type':'货运站','folder_path':['甲省','甲市','自定义目录']}
    assert station_catalog_path(record,custom)==('甲省','甲市','编组站','货运站','自定义目录')
    assert record['id']=='node/1' and record['station_type']=='编组站'
    assert custom['folder_path']==['甲省','甲市','自定义目录']


@pytest.mark.parametrize('legacy,kind', [('车辆段','depot'),('检修站','workshop'),
                                      ('调车场','yard'),('线路所','junction'),('乘降所','halt')])
def test_non_station_facilities_are_other(legacy,kind):
    record=station(station_type=legacy,kind=kind)
    assert classification(record)['classification_group']=='其他'
    assert station_catalog_path(record)==('甲省','甲市','其他',legacy)


def test_no_default_middle_station_or_freight_exclusivity():
    record=station(properties={'node_tags':{'passenger':'yes'}})
    assert classification(record)['technical_type']=='待核实'
    assert classification(record)['business_type']=='待核实'
    record['properties']['node_tags']['freight']='yes'
    assert classification(record)['business_type']=='客货运站'


def test_drop_to_classification_folder_updates_axes_without_duplicate_folders():
    changes=station_destination_changes(['甲省','甲市','区段站','客货运站','原有子目录'])
    assert changes=={'folder_path':['甲省','甲市','原有子目录'],
                     'technical_type':'区段站','business_type':'客货运站'}
    assert station_catalog_path(station(),changes)==('甲省','甲市','区段站','客货运站','原有子目录')
    assert station_destination_changes(['甲省','甲市','其他','车辆段'])=={'folder_path':['甲省','甲市']}


def test_reference_identity_and_manual_override_precedence(tmp_path,monkeypatch):
    from desktop import station_classification as module
    path=tmp_path/'reference.json'
    facts={'coordinates':[121,30], 'technical_type':'区段站', 'business_type':'客货运站',
           'provenance':{'technical_type':{'source':'https://example.org/station','snapshot':'123',
                'verification_status':'source_unverified','confidence':None}}}
    path.write_text(json.dumps({'schema':'railscope.station-classification-reference.v1','stations':{'node/1':facts}}),encoding='utf-8')
    monkeypatch.setattr(module,'REFERENCE',path)
    assert classification(station())['technical_type']=='待核实'  # Same ID at another location is unsafe.
    record=station(coordinates=[121,30])
    assert classification(record)['technical_type']=='区段站'
    result=classification(record,{'technical_type':'中间站'})
    assert result['technical_type']=='中间站' and result['business_type']=='客货运站'
    assert result['classification_provenance']['technical_type']['verification_status']=='manual'
    yard=station(coordinates=[121,30],kind='yard',station_type='车场')
    assert classification(yard)['classification_group']=='车站'
    yard['station_type']='集装箱办理站'
    assert classification(yard)['classification_group']=='车站'
    depot=station(coordinates=[121,30],kind='depot',station_type='车辆段')
    assert classification(depot)['classification_group']=='其他'


def test_article_parser_rejects_other_station_and_historical_roles():
    from scripts.research_station_classification import extract_article, province_mentioned
    assert province_mentioned('湖南省','湖南怀化的铁路车站')
    assert province_mentioned('新疆维吾尔自治区','新疆乌鲁木齐')
    assert not province_mentioned('省界外 / 待核对','湖南怀化')
    base={'title':'甲站','lastrevid':123,'coordinates':[{'lon':120,'lat':30}],
          'fullurl':'https://zh.wikipedia.org/wiki/甲站'}
    for lead in ('甲站是甲省的铁路车站，曾为编组站。',
                 '甲站是甲省铁路车站，编组业务转移至乙编组站。'):
        facts,status=extract_article({**base,'extract':lead},station())
        assert status=='matched_article' and 'technical_type' not in facts
    facts,_=extract_article({**base,'extract':'甲站位于甲省，是铁路区段站。客运：办理旅客乘降；货运：办理整车货物发到。'},station())
    assert facts['technical_type']=='区段站' and facts['business_type']=='客货运站'
    assert facts['provenance']['technical_type']['snapshot']=='123'
    wikitext='{{Infobox station\n| type = 中间站、客货运站\n}}\n' + "'''甲站'''是湖南的铁路车站。"
    facts,_=extract_article({**base,'revisions':[{'revid':123,'slots':{'main':{'content':wikitext}}}]},station())
    assert facts['technical_type']=='中间站' and facts['business_type']=='客货运站'


def test_classification_changes_keep_station_children_and_can_be_undone(qtbot,tmp_path,monkeypatch):
    from desktop.rail_catalog_ui import RailCatalog
    from desktop.tests.test_operating_ui import MapStub
    monkeypatch.setattr('desktop.rail_catalog_ui.rail_station_records',lambda *a,**k:([station()],1))
    widget=RailCatalog(tmp_path,tmp_path/'settings.json',MapStub());qtbot.addWidget(widget)
    widget.save_station_override('node/1',technical_type='区段站',business_type='客货运站')
    assert widget._station_path(widget.station_record_by_id['node/1'])==('甲省','甲市','区段站','客货运站')
    assert set(widget.station_record_by_id)=={'node/1'}
    evidence=widget.overrides['station:node/1']['classification_provenance']['technical_type']
    assert evidence['verification_status']=='manual' and evidence['snapshot'].isdigit()
    widget.undo_catalog()
    assert widget._station_path(widget.station_record_by_id['node/1'])==('甲省','甲市','技术作业待核实','业务性质待核实')
    widget.redo_catalog()
    assert widget.station_record_by_id['node/1']['technical_type']=='区段站'


def test_parent_owned_index_is_removed_after_real_worker_exit(tmp_path):
    from railscope.services.importers.native_paths import native_worker_cache
    root=Path(__file__).resolve().parents[2]
    with native_worker_cache(tmp_path/'sample.pbf') as cache:
        env={**os.environ,'RAILSCOPE_NATIVE_TEMP':str(cache),'PYTHONPATH':str(root/'backend')}
        code="from railscope.services.importers.native_paths import native_path; p=native_path('output.idx',output=True); assert p==native_path('output.idx',output=True); p.write_bytes(b'coordinate-index'); print(p)"
        result=subprocess.run([sys.executable,'-c',code],env=env,check=True,capture_output=True,text=True,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform=='win32' else 0)
        index=Path(result.stdout.strip())
        assert index.parent==cache and index.exists()
    assert not cache.exists()
