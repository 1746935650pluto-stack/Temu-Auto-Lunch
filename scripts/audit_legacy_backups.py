"""Read-only legacy backup inventory and optional full verification; never deletes."""
from contextlib import closing
import argparse
import hashlib
import json
from pathlib import Path
import sqlite3

def inventory(root, verify=None):
    root=Path(root).resolve();rows=[]
    for directory in sorted(root.iterdir()):
        if not directory.is_dir() or directory.is_symlink():continue
        files=[]
        for path in directory.glob('*.sqlite3'):
            if path.is_symlink():continue
            with path.open('rb') as stream:header=stream.read(16)
            entry={'name':path.name,'bytes':path.stat().st_size,'sqliteHeader':header==b'SQLite format 3\x00'}
            if verify and directory==Path(verify).resolve():
                with path.open('rb') as stream:entry['sha256']=hashlib.file_digest(stream,'sha256').hexdigest()
                with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)) as db:
                    entry['integrity']=[r[0] for r in db.execute('PRAGMA integrity_check')]
            files.append(entry)
        rows.append({'directory':directory.name,'pinned':(directory/'PINNED').exists(),
                     'hasManifest':(directory/'manifest.json').exists(),'files':files,
                     'bytes':sum(f['bytes'] for f in files),
                     'action':'keep-pinned' if (directory/'PINNED').exists() else 'review-before-cleanup'})
    return {'readOnly':True,'deleted':0,'fullVerificationDirectory':str(verify) if verify else None,
            'directories':rows,'totalBytes':sum(r['bytes'] for r in rows)}

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--verify',type=Path)
    args=parser.parse_args()
    result=inventory(args.root,args.verify)
    args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'directories':len(result['directories']),'databaseGiB':round(result['totalBytes']/2**30,2),'deleted':0,'output':str(args.output)}))
