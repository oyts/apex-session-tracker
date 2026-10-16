from types import SimpleNamespace
import numpy as np
import pytest
from PIL import Image
from tracker.ocr import Recognizer


@pytest.mark.parametrize('outputs,accepted',[
    ([('0/3/0',.98),('0/3/0',.97)],True),
    ([('0/3/0',.98),('0/8/0',.99)],False),
    ([('0/3/0',.98),('0/3/2',.99)],False),
    ([('0/3/0',.93999)],False),
    ([('0/3/0',.99),('0/3/0',.93)],False),
    ([('O/3/0',.99)],False),
    ([('0/3',.99)],False),
    ([('',.99)],False),
    ([('0/3/0',float('nan'))],False),
])
def test_exact_double_verification_without_lowering_threshold(outputs,accepted):
    reader=Recognizer.__new__(Recognizer);pending=iter(outputs);arrays=[]
    def engine(pixels,**options):
        assert options==dict(use_det=False,use_cls=False)
        arrays.append(pixels.copy());text,score=next(pending)
        return SimpleNamespace(txts=[text],scores=[score])
    reader.engine=engine
    line=dict(x=5,y=5,right=60,bottom=30,score=.92811)
    result=reader.verify_whole_triple(Image.new('RGB',(100,50),'orange'),line,(0,3,0))
    assert bool(result and not result.get('conflict'))==accepted
    if accepted:
        assert result['scores']==[.98,.97] and result['raw_score']==.92811
        assert np.array_equal(arrays[1],255-arrays[0])


def test_high_confidence_disagreement_cannot_fall_through_to_another_method(tmp_path):
    reader=Recognizer.__new__(Recognizer)
    def line(text,y,bottom,score=.99):
        return dict(text=text,x=100,right=200,y=y,bottom=bottom,cy=(y+bottom)/2,score=score)
    reader.lines=lambda _: [line('A',195,215),line('0/3/0',250,280,.928),
                             line('造成伤害',300,320),line('295',326,350)]
    reader.engine=lambda *a,**k:SimpleNamespace(txts=['0/8/0'],scores=[.99])
    def forbidden(*a):pytest.fail('A conflicting strong reread cannot be bypassed')
    reader.verify_kills_assists=forbidden;reader.separated_triple=forbidden
    path=tmp_path/'synthetic.png';Image.new('RGB',(1000,600)).save(path)
    result=reader.recognize(path)
    assert result['cards'][0]['score']==.928
    assert result['cards'][0]['assists']==3  # Preserve the original; never substitute 8.
    assert result['cards'][0]['kills_assists_verification']['conflict'] is True
    assert result['warnings']


def test_empty_or_multiple_recognition_results_cannot_verify():
    reader=Recognizer.__new__(Recognizer);line=dict(x=1,y=1,right=10,bottom=10,score=.92)
    for output in [SimpleNamespace(txts=[],scores=[]),SimpleNamespace(txts=['0/3/0'],scores=None),
                   SimpleNamespace(txts=['0/3/0','0/3/0'],scores=[.99,.99])]:
        reader.engine=lambda *a,**k:output
        assert reader.verify_whole_triple(Image.new('RGB',(20,20)),line,(0,3,0)) is None


def test_reread_preserves_mode_and_manual_stats(tmp_path):
    from tracker.store import Store
    from tracker.session import reconcile
    from tests.test_discovery import frame
    store=Store(tmp_path);sid=store.start_session(mode=3)
    original=frame(('A','B','C'),(100,200,300))
    original.update(match_key='one',attribution='start_observed',game_mode='wildcard',observation_version=14)
    cid=store.add_capture(sid,'synthetic.png','one',original);reconcile(store,sid)
    manual=[dict(card=i,player_id=i+1,damage=450+i,kills=1,assists=2) for i in range(3)]
    store.confirm(cid,manual,manual=True)
    before=store.capture(cid)['stats'];totals=store.totals(sid)
    store.update_recognition(cid,dict(frame(('A','B','C'),(1,2,3)),recognition_version=7))
    reconcile(store,sid)
    assert store.capture(cid)['result']['game_mode']=='wildcard'
    assert store.capture(cid)['stats']==before and store.totals(sid)==totals
    store.close()


def test_history_upgrade_targets_pending_v6_without_rereading_confirmed_v6(tmp_path):
    from tracker.app import App
    path=tmp_path/'synthetic.png';path.touch()
    from tracker.ocr import RECOGNITION_VERSION
    states=[('pending',6),('confirmed',6),('pending',RECOGNITION_VERSION),('confirmed',5)]
    captures=[dict(id=i,image=str(path),status=status,result=dict(recognition_version=v)) for i,(status,v) in enumerate(states)]
    app=App.__new__(App);app.selected_session=1;app.busy=False;app.engine_ready=True;app.repair_seen=set()
    app.store=SimpleNamespace(visible_captures=lambda _:captures,capture=lambda i:captures[i])
    app.status=SimpleNamespace(set=lambda _:None);calls=[]
    app.executor=SimpleNamespace(submit=lambda fn,sid,items:calls.append(items))
    app.repair_history()
    assert [c['id'] for c in calls[0]]==[0,3]
