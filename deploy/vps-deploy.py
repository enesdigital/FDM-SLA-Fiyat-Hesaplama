#!/usr/bin/python3
"""Root-owned deployment helper. Receives static dist files only over forced SSH."""
import datetime, fcntl, json, os, pathlib, re, shutil, subprocess, sys, tarfile, tempfile, time, urllib.request
os.umask(0o077)
ROOT=pathlib.Path('/srv/apps/3d-pricing')
CONFIG=pathlib.Path('/docker/3d-pricing/docker-compose.yml')
TRUSTED=pathlib.Path('/docker/3d-pricing/runtime')
if len(sys.argv)!=2 or not re.fullmatch('[0-9a-f]{40}',sys.argv[1]):
    sys.exit('A full commit SHA is required')
sha=sys.argv[1]
lock=open(ROOT/'deploy.lock','w')
fcntl.flock(lock,fcntl.LOCK_EX)
release=pathlib.Path(tempfile.mkdtemp(prefix=sha+'-',dir=ROOT/'releases'))
archive=release/'upload.tar.gz'
try:
    with archive.open('wb') as out:
        total=0
        while chunk:=sys.stdin.buffer.read(65536):
            total+=len(chunk)
            if total>64*1024*1024: raise ValueError('Archive too large')
            out.write(chunk)
    with tarfile.open(archive,'r:gz') as bundle:
        members=bundle.getmembers()
        if sum(m.size for m in members)>256*1024*1024 or len(members)>5000:
            raise ValueError('Expanded archive too large')
        for item in members:
            name=pathlib.PurePosixPath(item.name)
            if name.is_absolute() or '..' in name.parts or not name.parts or name.parts[0]!='dist' or not (item.isfile() or item.isdir()):
                raise ValueError('Only regular dist files are allowed')
        bundle.extractall(release,members=members,filter='data')
    if not (release/'dist/index.html').is_file(): raise ValueError('index.html missing')
    archive.unlink()
    (release/'dist/deployment.json').write_text(json.dumps({'commit':sha}))
    shutil.copyfile(TRUSTED/'Dockerfile.runtime',release/'Dockerfile')
    shutil.copyfile(TRUSTED/'nginx.conf',release/'nginx.conf')
    image='local/3d-pricing:'+sha
    subprocess.run(['docker','build','--label','org.opencontainers.image.revision='+sha,'-t',image,str(release)],check=True)
    original=CONFIG.read_text()
    previous=ROOT/'deployment.json'
    had_previous=previous.exists()
    stamp=datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    backup=pathlib.Path('/srv/platform/backups/3d-pricing')/stamp
    backup.mkdir(parents=True,mode=0o700)
    (backup/'docker-compose.yml').write_text(original)
    if had_previous: shutil.copyfile(previous,backup/'deployment.json')
    updated,n=re.subn(r'^    image: .*$', '    image: '+image,original,flags=re.M)
    assert n==1
    updated,n=re.subn(r'^    build: .*$', '    build: '+str(release),updated,flags=re.M)
    assert n==1
    def replace_config(text):
        tmp=CONFIG.with_suffix('.pending');tmp.write_text(text);os.replace(tmp,CONFIG)
    compose=['docker','compose','-f',str(CONFIG)]
    try:
        replace_config(updated)
        subprocess.run(compose+['config','--quiet'],check=True)
        subprocess.run(compose+['up','-d','--no-build','--wait','--wait-timeout','90'],check=True,timeout=150)
        for attempt in range(30):
            try:
                with urllib.request.urlopen('https://3d-pricing.srv2047910.hstgr.cloud/deployment.json',timeout=10) as response:
                    assert json.load(response)['commit']==sha
                break
            except Exception:
                if attempt==29: raise
                time.sleep(2)
    except BaseException:
        replace_config(original)
        if had_previous:
            subprocess.run(compose+['up','-d','--no-build','--wait','--wait-timeout','90'],check=True,timeout=150)
            print('PREVIOUS_VERSION_RESTORED',flush=True)
        else:
            subprocess.run(compose+['stop','web'],check=False)
        raise
    state={'commit':sha,'image':image,'source':str(release),'deployed_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'status':'healthy'}
    previous.write_text(json.dumps(state,indent=2)+'\n')
    print('DEPLOY_OK '+sha,flush=True)
except BaseException as error:
    print('DEPLOY_FAILED '+str(error),file=sys.stderr)
    sys.exit(1)
