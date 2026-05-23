import os
import hashlib
from pathlib import Path

def hash_dir(dir_path, exclude_name):
    h = hashlib.sha3_256()
    in_path = Path(dir_path)
    files = []
    
    def hash_file(f_path):
        with open(f_path, 'rb') as f:
            for chunk in iter(lambda: f.read(4096), b""):
                h.update(chunk)
                
    for p in sorted(in_path.rglob('*')):
        if p.is_file() and p.name != exclude_name:
            h.update(p.name.encode('utf-8'))
            hash_file(p)
            files.append(p.name)
            
    return h.hexdigest(), files

h1, f1 = hash_dir('tmp_edge/study_0', 'hash_0.json')
h2, f2 = hash_dir('tmp_fog/decompressed_0', 'hash_0.json')

print("Edge hash:", h1)
print("Fog hash: ", h2)
print("Files match?", f1 == f2)

if f1 != f2:
    print("Files in edge not in fog:", set(f1) - set(f2))
    print("Files in fog not in edge:", set(f2) - set(f1))
else:
    for f in f1:
        p1 = Path('tmp_edge/study_0') / f
        p2 = Path('tmp_fog/decompressed_0') / f
        c1 = p1.read_bytes()
        c2 = p2.read_bytes()
        if c1 != c2:
            print(f"Content mismatch for {f}!")
