"""Record measured post-photography speech rework, without estimating savings."""
import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from scene_review import digest

def speech(unit):
    return {k:unit.get(k) for k in ('audio','audio_plan','action_plan','duration')} | {
        'parts':[s.get('dialogue_parts',[]) for s in unit.get('shots',[])]}

def measure(before, after, proof, started_at, finished_at):
    elapsed=(datetime.fromisoformat(finished_at)-datetime.fromisoformat(started_at)).total_seconds()
    if elapsed<0: raise ValueError('结束时间早于开始时间')
    if proof.get('request_sha256')!=digest(after): raise ValueError('复核影响报告不属于当前请求')
    if proof.get('old_request_sha256')!=digest(before): raise ValueError('旧请求不属于本轮复核影响报告')
    changed=[]
    for i,(a,b) in enumerate(zip(before['units'],after['units']),1):
        if ((a.get('audio') or b.get('audio')) and a.get('span')==b.get('span') and all(s.get('camera') and s.get('composition') for s in a.get('shots',[]))
                and a.get('shots') and speech(a)!=speech(b)):
            changed.append(i)
    return {'event':'post_photography_audio_rework','changed_units':changed,
            'replan_count':len(changed),'invalidated_previously_reviewed_shots':sum(
                p['status']=='pending' and int(p['key'].split(':')[0]) in changed and
                p.get('previous_was_reviewed',False) for p in proof['shot_provenance']),
            'started_at':started_at,'finished_at':finished_at,'measured_elapsed_seconds':elapsed,
            'before_sha256':digest(before),'after_sha256':digest(after),'provenance_sha256':digest(proof),
            'limits':'本次实际返修区间耗时；不推算节省比例，不等同纯模型生成时间'}

def main():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8')
    p=argparse.ArgumentParser(description=__doc__)
    for k in ('before','after','provenance','output'): p.add_argument('--'+k,type=Path,required=True)
    for k in ('started-at','finished-at'): p.add_argument('--'+k,required=True)
    a=p.parse_args(); read=lambda path:json.loads(path.read_text(encoding='utf-8-sig'))
    result=measure(read(a.before),read(a.after),read(a.provenance),a.started_at,a.finished_at)
    result['event_id']=digest({k:result[k] for k in ('before_sha256','after_sha256')})
    a.output.parent.mkdir(parents=True,exist_ok=True)
    if a.output.exists() and any(json.loads(line).get('event_id')==result['event_id']
                                for line in a.output.read_text(encoding='utf-8').splitlines() if line.strip()):
        raise ValueError('本次声音返修版本已经登记，不能重复计数')
    with a.output.open('a',encoding='utf-8') as f:f.write(json.dumps(result,ensure_ascii=False)+'\n')
    print(json.dumps(result,ensure_ascii=False))
if __name__=='__main__':main()
