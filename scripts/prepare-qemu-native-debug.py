#!/usr/bin/env python3
"""Back up and install matching native debugging components into the stopped desktop."""
import argparse
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from prepare import ROOT, sha256
from source_patches import expected_patch
from initfs_image import Initfs


def payloads(image):
    result={}
    def walk(node,path):
        kind,length,offset=image.inodes[node]
        if kind==2:
            result[path]=(kind,b'')
            for name,child in image.entries(node).items():walk(child,path+'/'+name)
        else:result[path]=(kind,image.slice(offset,length))
    walk(image.root,'')
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--debug',type=Path,required=True)
    parser.add_argument('--kernel',type=Path,required=True)
    parser.add_argument('--libc',type=Path,required=True)
    args=parser.parse_args()
    debug,kernel,libc=(p.resolve() for p in (args.debug,args.kernel,args.libc))
    source=json.loads((ROOT/'qemu/sources.lock.json').read_text())
    for build in (debug,kernel,libc):
        if (build/'exit-code.txt').read_text().strip()!='0':raise RuntimeError('Unsuccessful build: '+str(build))
        subprocess.run(['shasum','-a','256','-c','SHA256SUMS'],cwd=build,check=True,stdout=subprocess.DEVNULL)
    for build,name,pin in ((debug,'gdb',source['native_gdb']),(debug,'base',source['base']),
                           (debug,'relibc',source['relibc']),(kernel,'kernel',source['repositories']['kernel']),
                           (libc,'relibc',source['relibc'])):
        if (build/(name+'.patch')).read_bytes()!=expected_patch(pin):raise RuntimeError('Source patch differs: '+name)
    if json.loads((debug/'build-inputs.json').read_text())['libc_a_sha256']!=sha256(libc/'libc.a'):
        raise RuntimeError('GDB linked against another libc')
    path=ROOT/'work/qemu-arm64/persistent-desktop-image.json'
    metadata=json.loads(path.read_text());disk=Path(metadata['root_disk']);boot=Path(metadata['image'])
    tool=ROOT/'tools/redox-image/target/release/redox-image'
    with disk.with_suffix('.lock').open('a+') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        if sha256(boot)!=metadata['image_sha256']:raise RuntimeError('Boot image hash differs')
        parent=ROOT/'work/qemu-arm64/native-debug-backups';parent.mkdir(parents=True,exist_ok=True)
        backup=Path(tempfile.mkdtemp(prefix=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ-'),dir=parent))
        for image in (boot,disk):
            if subprocess.run(['cp','-c',str(image),str(backup/image.name)],stderr=subprocess.DEVNULL).returncode:
                shutil.copyfile(image,backup/image.name)
        shutil.copy2(path,backup/'image.json')
        def read(image,guest):return subprocess.check_output([str(tool),'cat',str(image),guest])
        original=Initfs(read(boot,'/usr/lib/boot/initfs'));files=payloads(original)
        tree=backup/'initfs-root';tree.mkdir()
        for name,(kind,data) in files.items():
            target=tree/name.lstrip('/');target.parent.mkdir(parents=True,exist_ok=True)
            if kind==2:target.mkdir(exist_ok=True)
            elif kind==3:target.symlink_to(data.decode())
            else:target.write_bytes(data);target.chmod(0o755 if kind==1 else 0o644)
        new=backup/'initfs'
        subprocess.run([str(debug/'redox-initfs-ar'),str(tree),str(debug/'bootstrap'),'-o',str(new)],check=True)
        if payloads(Initfs(new.read_bytes()))!=files:raise RuntimeError('Unrelated initfs payload changed')
        def put(image,guest,host,executable=False):
            subprocess.run([str(tool),'put-exec' if executable else 'put',str(image),guest,str(host)],check=True)
            if read(image,guest)!=host.read_bytes():raise RuntimeError('Readback differs: '+guest)
        for image in (boot,disk):
            put(image,'/usr/lib/boot/initfs',new)
            for name in ('kernel','kernel.all','kernel.sym'):put(image,'/usr/lib/boot/'+name,kernel/name)
        for guest,host in [('/usr/bin/gdb',debug/'gdb'),('/usr/bin/ptrace-smoke',debug/'ptrace-smoke'),
                           ('/usr/bin/signal-registers',libc/'signal-registers'),
                           ('/usr/bin/wait-continued',debug/'wait-continued'),
                           ('/usr/bin/stdio-unbuffered',debug/'stdio-unbuffered'),
                           ('/usr/bin/tty-drain',debug/'tty-drain'),
                           ('/usr/bin/exec-saved-ids',debug/'exec-saved-ids'),
                           ('/usr/lib/libc.so',libc/'libc.so'),('/usr/lib/devkit/libc.a',libc/'libc.a'),
                           ('/usr/lib/devkit/crt0.o',libc/'crt0.o'),
                           ('/usr/lib/devkit/native-debug.gdb',ROOT/'qemu/development/native-debug.gdb')]:
            put(disk,guest,host,guest.startswith('/usr/bin/'))
        metadata.update(components=str(kernel),kernel_sha256=sha256(kernel/'kernel'),kernel_patch=source['repositories']['kernel']['patch'],
            relibc_components=str(libc),relibc_sha256=sha256(libc/'libc.so'),relibc_source=source['relibc'],
            initfs_sha256=sha256(new),image_sha256=sha256(boot),sources=source,
            native_debug_components=str(debug),gdb_sha256=sha256(debug/'gdb'),bootstrap_sha256=sha256(debug/'bootstrap'))
        for target in (path,disk.parent/'image.json'):target.write_text(json.dumps(metadata,indent=2)+'\n')
        shutil.copy2(new,disk.parent/'initfs')
        print(f'Installed native GNU GDB and ARM64 process debugging. Backup: {backup}')


if __name__=='__main__':main()
