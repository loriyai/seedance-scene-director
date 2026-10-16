"""Prepare pending writer checks for exact rendered text and backend state."""
import argparse
import json
from pathlib import Path
import director
from authoring_gates import staging_pending, staging_material

def main():
    p=argparse.ArgumentParser(description=__doc__)
    source=p.add_mutually_exclusive_group(required=True)
    source.add_argument('--project',type=Path); source.add_argument('--state',type=Path)
    p.add_argument('--request',type=Path,required=True); p.add_argument('--output',type=Path,required=True)
    a=p.parse_args()
    state=director.load(a.project) if a.project else director.read_json(a.state)
    request=director.read_json(a.request)
    result={'status':'pending','units':[{'unit':u.get('id',i+1), 'staging_review':staging_pending(state,u),
                                       'actual':staging_material(state,u)} for i,u in enumerate(request['units'])]}
    root=Path(__file__).resolve().parent.parent
    output=a.output.resolve()
    if output == root or root in output.parents:
        raise ValueError('工作记录不能写入Skill包内')
    a.output.parent.mkdir(parents=True,exist_ok=True)
    with a.output.open('x',encoding='utf-8') as f: json.dump(result,f,ensure_ascii=False,indent=2)
    print(json.dumps({'status':'pending','output':str(a.output)},ensure_ascii=False))
if __name__=='__main__': main()
