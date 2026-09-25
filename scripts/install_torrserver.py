"""Install TorrServer inside this project; never changes system packages."""
import hashlib
import json
import os
import platform
import urllib.request
from pathlib import Path

root = Path(__file__).resolve().parents[1]
systems = {'Darwin': 'darwin', 'Linux': 'linux', 'Windows': 'windows'}
arches = {'arm64': 'arm64', 'aarch64': 'arm64', 'x86_64': 'amd64', 'AMD64': 'amd64'}
try:
    asset_name = f'TorrServer-{systems[platform.system()]}-{arches[platform.machine()]}'
except KeyError:
    raise SystemExit('No bundled installer for this platform; set JF_TORRSERVER_URL to an existing TorrServer.')
if platform.system() == 'Windows':
    asset_name += '.exe'
headers = {'User-Agent': 'jf-resolve-torrserver-installer'}
with urllib.request.urlopen(urllib.request.Request('https://api.github.com/repos/YouROK/TorrServer/releases/latest', headers=headers), timeout=30) as response:
    release = json.load(response)
asset = next((a for a in release['assets'] if a['name'] == asset_name), None)
if not asset:
    raise SystemExit(f'No {asset_name} asset in release {release["tag_name"]}')
folder = root / 'data' / 'torrserver' / 'bin'
folder.mkdir(parents=True, exist_ok=True)
target = folder / ('TorrServer.exe' if platform.system() == 'Windows' else 'TorrServer')
with urllib.request.urlopen(urllib.request.Request(asset['browser_download_url'], headers=headers), timeout=120) as response:
    content = response.read()
if len(content) != asset['size']:
    raise SystemExit('Incomplete download')
digest = asset.get('digest')
if digest and digest.startswith('sha256:') and hashlib.sha256(content).hexdigest() != digest[7:]:
    raise SystemExit('Download checksum did not match')
temporary = target.with_suffix('.download')
temporary.write_bytes(content)
temporary.chmod(0o755)
os.replace(temporary, target)
(folder / 'version.txt').write_text(release['tag_name'] + '\n')
print(f'Installed {release["tag_name"]} at {target}')
