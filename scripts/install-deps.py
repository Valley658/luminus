"""Keep portable runtime tools in bin; download only missing executables."""
import hashlib
import io
import json
import os
from pathlib import Path
import platform
import tempfile
import subprocess
import shutil
import zipfile

ROOT = Path(__file__).resolve().parent.parent
BIN = ROOT / 'bin'


def request(url):
    result = subprocess.run([curl(), '--fail', '--location', '--silent', '--show-error',
                             '--connect-timeout', '10', '--max-time', '30',
                             '--user-agent', 'Pastellive-Setup', url],
                            stdout=subprocess.PIPE, timeout=35, check=True)
    return io.BytesIO(result.stdout)


def curl():
    command = shutil.which('curl.exe')
    if not command:
        raise RuntimeError('curl.exe is required (included with Windows 10/11).')
    return command


def download(url, target):
    print('Connecting (10s timeout); download progress follows:', flush=True)
    print(url, flush=True)
    subprocess.run([curl(), '--fail', '--location', '--progress-bar',
                    '--connect-timeout', '10', '--max-time', '600',
                    '--speed-time', '20', '--speed-limit', '1024',
                    '--output', str(target), url], timeout=610, check=True)
    digest = hashlib.sha256()
    with target.open('rb') as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def install():
    BIN.mkdir(exist_ok=True)
    if all((BIN / name).is_file() for name in ('ffmpeg.exe', 'meilisearch.exe')):
        print('FFmpeg and Meilisearch are already installed.', flush=True)
        return
    if platform.machine().lower() not in ('amd64', 'x86_64'):
        raise RuntimeError('These portable downloads require Windows x64.')
    with tempfile.TemporaryDirectory(prefix='pastellive-setup-') as temporary:
        temp = Path(temporary)
        if not (BIN / 'ffmpeg.exe').is_file():
            print('Downloading FFmpeg essentials (first run only)...', flush=True)
            archive = temp / 'ffmpeg.zip'
            with request('https://api.github.com/repos/GyanD/codexffmpeg/releases/latest') as response:
                release = json.load(response)
            asset = next(item for item in release['assets'] if item['name'].endswith('-essentials_build.zip'))
            try:
                actual = download(asset['browser_download_url'], archive)
                expected = asset.get('digest')
            except (subprocess.SubprocessError, OSError):
                print('GitHub download failed. Trying Gyan official server...', flush=True)
                url = 'https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip'
                with request(url + '.sha256') as response:
                    expected = 'sha256:' + response.read().decode().split()[0].lower()
                actual = download(url, archive)
            if expected != 'sha256:' + actual:
                raise RuntimeError('FFmpeg SHA256 verification failed. Run again to retry.')
            with zipfile.ZipFile(archive) as package:
                entry = next(name for name in package.namelist() if name.endswith('/bin/ffmpeg.exe'))
                staged = temp / 'ffmpeg.exe'
                with package.open(entry) as source, staged.open('wb') as target:
                    while chunk := source.read(1024 * 1024):
                        target.write(chunk)
                for name in package.namelist():
                    if Path(name).name.lower() in ('license', 'license.txt', 'copying', 'copying.txt'):
                        (BIN / 'ffmpeg-LICENSE.txt').write_bytes(package.read(name))
                os.replace(staged, BIN / 'ffmpeg.exe')
            print('Installed bin/ffmpeg.exe', flush=True)
        if not (BIN / 'meilisearch.exe').is_file():
            print('Downloading Meilisearch from its official release...', flush=True)
            with request('https://api.github.com/repos/meilisearch/meilisearch/releases/latest') as response:
                release = json.load(response)
            asset = next(item for item in release['assets'] if item['name'] == 'meilisearch-windows-amd64.exe')
            staged = temp / 'meilisearch.exe'
            actual = download(asset['browser_download_url'], staged)
            expected = asset.get('digest')
            if expected and expected != 'sha256:' + actual:
                raise RuntimeError('Meilisearch SHA256 verification failed. Run again to retry.')
            os.replace(staged, BIN / 'meilisearch.exe')
            print('Installed bin/meilisearch.exe', flush=True)


if __name__ == '__main__':
    try:
        install()
    except Exception as error:
        raise SystemExit(f'Setup failed: {error}')
