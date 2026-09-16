from pathlib import Path
import json
from vea.detailed_review import interval_transcript, review, attach_previews

def test_timed_transcript_overlap():
    text='(00:45) first\n(01:15) second\n(02:00) third'
    assert 'first' in interval_transcript(text,60,120)
    assert 'second' in interval_transcript(text,60,120)
    assert 'third' not in interval_transcript(text,60,120)
    assert interval_transcript('untimed text',0,60) is None

def test_checkpoint_stops_on_failure_and_passes_key(tmp_path):
    image=tmp_path/'image.jpg';image.write_bytes(b'jpeg-test')
    def extract(video, folder, points, **kwargs):
        return [dict(path=image,timestamp_seconds=p) for p in points],{}
    calls=[]
    def analyze(**kwargs):
        calls.append(kwargs)
        assert kwargs['api_key']=='test-key'
        if len(calls)==2: raise TimeoutError()
        return {'summary':'sample','visual_observations':[{'frame_index':0,'observation':'a','interpretation':'b'}],'limitations':[]}
    output=tmp_path/'result.json'
    data=review({'metadata':{'duration_ms':180000},'transcript':{'text':'(00:00) test'}},'video',output,analyze=analyze,extract=extract,api_key='test-key')
    assert [v['status'] for v in data['detailed_review']['intervals']]==['ok','failed','not_run']
    assert len(calls)==2
    assert json.loads(output.read_text())==data
    assert data['detailed_review']['intervals'][0]['result']['visual_observations'][0]['timestamp_ms']==7500
    assert 'test-key' not in output.read_text()

def test_previews_missing_images_preserve_indices(tmp_path):
    image=tmp_path/'a.jpg'; image.write_bytes(b'jpeg')
    data={'timeline':[{'event_type':'frame','start_ms':1000,'attributes':{'path':str(tmp_path/'missing.jpg')}},
                      {'event_type':'frame','start_ms':2000,'attributes':{'path':str(image)}}]}
    attach_previews(data)
    assert data['frame_previews'][0]['frame_index']==1

def test_real_frame_extraction_without_paid_api(tmp_path):
    import subprocess
    video=tmp_path/'clip.mp4'
    subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','testsrc2=size=160x90:rate=10:duration=2','-c:v','libx264',str(video)],check=True)
    def analyze(**kwargs):
        assert len(kwargs['frame_paths'])==4
        return {'summary':'test','visual_observations':[],'limitations':[]}
    data=review({'metadata':{'duration_ms':2000}},str(video),tmp_path/'result.json',analyze=analyze)
    item=data['detailed_review']['intervals'][0]
    assert item['status']=='ok'
    assert len(item['frame_previews'])==4
    assert all(p['image'].startswith('data:image/jpeg;base64,/9j/') for p in item['frame_previews'])
