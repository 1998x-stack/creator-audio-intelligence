import pytest
from creator_audio_intel.models import Segment
from creator_audio_intel.llm import validate_output

S=[Segment(0,0,1000,'现在房价可能会继续下跌。'),Segment(1,1000,2000,'这只是一个判断，并不是保证。')]


def claim(quote='房价可能会继续下跌',ids=None,kind='forecast'):
    return {'kind':kind,'statement':'未来房价可能继续下跌',
            'segment_ids':ids if ids is not None else [0],
            'evidence_quote':quote,
            'forecast':{'metric':'房价','direction':'decrease','horizon':'','conditions':''} if kind=='forecast' else {}}


def test_validate_evidence_and_forecast():
    data=validate_output({'summary':'房地产观点','claims':[claim()]},S)
    assert data['claims'][0]['forecast']['horizon']==''


def test_reject_hallucinated_quote():
    with pytest.raises(ValueError,match='not present'):
        validate_output({'summary':'','claims':[claim(quote='房价必然上涨')]},S)


def test_reject_missing_segment():
    with pytest.raises(ValueError,match='segment'):
        validate_output({'summary':'','claims':[claim(ids=[19])]},S)


def test_reject_wrong_type_and_bad_forecast_fields():
    with pytest.raises(ValueError):validate_output({'summary':'','claims':[claim(kind='new_tag')]},S)
    a=claim(kind='fact');a['forecast']={'direction':'increase'}
    with pytest.raises(ValueError):validate_output({'summary':'','claims':[a]},S)


def test_empty_forecasts_ok():
    assert validate_output({'summary':'总结','claims':[]},S)['claims']==[]
