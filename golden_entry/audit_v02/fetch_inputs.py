"""Read public, commit-pinned inputs. Verify Git hashes; never overwrite a different file."""
import argparse
import hashlib
import json
from pathlib import Path
import urllib.request

COMMIT = '359ce852283c91c8fd59a7cd4fdc3e485a092cea'
BASE = 'https://raw.githubusercontent.com/gatbatanic-eng/sepa-screener/' + COMMIT + '/'


def fetch(out, tree_path):
    out.mkdir(parents=True, exist_ok=True)
    tree=json.loads(tree_path.read_text())
    if tree['sha']!=COMMIT:raise ValueError('unexpected source commit')
    entries=list(tree['tree'])
    entries.append({'path':'golden_entry/reports/phase1_dart_events.csv','type':'blob',
                    **tree['event_input']})
    for e in entries:
        target=out/Path(e['path']).name
        raw=target.read_bytes() if target.exists() else urllib.request.urlopen(BASE+e['path'],timeout=60).read()
        sha=hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest()
        if len(raw)!=e['size'] or sha!=e['sha']:raise ValueError('source hash mismatch: '+target.name)
        if not target.exists():
            with target.open('xb') as f:f.write(raw)
        print(target.name,len(raw),'verified',flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--source-tree',type=Path,required=True)
    a=p.parse_args();fetch(a.output,a.source_tree)
