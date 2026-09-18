"""Snapshot Go module notices for offline binary redistribution; run after go mod tidy."""
import json
import shutil
import subprocess
from pathlib import Path
root=Path(__file__).resolve().parents[1]
output=root/'third_party'
output.mkdir(exist_ok=True)
raw=subprocess.check_output(['go','list','-m','-json','all'],cwd=root,text=True)
decoder=json.JSONDecoder();modules=[]
while raw.strip():
    item,end=decoder.raw_decode(raw.lstrip());raw=raw.lstrip()[end:]
    if item.get('Main'):continue
    modules.append(item['Path']+' '+item['Version'])
    directory=Path(item['Dir']) if item.get('Dir') else None
    if not directory:continue
    target=output/item['Path'].replace('/','_')
    target.mkdir(exist_ok=True)
    for file in directory.iterdir():
        if file.is_file() and file.name.lower().startswith(('license','licence','copying','notice')):
            shutil.copyfile(file,target/file.name)
go_root=Path(subprocess.check_output(['go','env','GOROOT'],text=True).strip())
license_file = go_root/'LICENSE'
if not license_file.exists(): license_file=go_root.parent/'LICENSE'
shutil.copyfile(license_file,output/'Go-LICENSE')
(output/'go-modules.txt').write_text('\n'.join(modules)+'\n')
