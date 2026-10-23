"""Publish the existing draft into the requested application directories."""
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parent.parent


def prepare():
    templates = ROOT / 'templates/luminus'
    frontend = ROOT / 'frontend/luminus/assets'
    static = ROOT / 'static/luminus'
    for folder in (templates, frontend, static / 'assets', static / 'profiles', ROOT / 'data'):
        folder.mkdir(parents=True, exist_ok=True)
    shutil.copy2(ROOT / 'drafts/index2.html', templates / 'index2.html')
    legacy=templates/'legacy'
    legacy.mkdir(exist_ok=True)
    for name in ('profile.html','watch.html'):
        shutil.copy2(ROOT/'templates'/name,legacy/name)
    for name in ('auth-complete.html', 'not-found.html'):
        shutil.copy2(ROOT / 'public' / name, templates / name)
    # The preparation homepage remains public/index.html, never the draft.
    for source in (ROOT / 'drafts/assets').iterdir():
        if source.is_file():
            if not (frontend / source.name).exists():
                shutil.copy2(source, frontend / source.name)
    for source in frontend.iterdir():
        if source.is_file():
            shutil.copy2(frontend / source.name, static / 'assets' / source.name)
    imported = ROOT / 'frontend/luminus/imported'
    if imported.exists():
        shutil.copytree(imported, static / 'imported', dirs_exist_ok=True)
    for source in (ROOT / 'drafts/profiles').iterdir():
        if source.is_file():
            shutil.copy2(source, static / 'profiles' / source.name)
    print('Prepared templates/luminus, frontend/luminus, static/luminus and data.')


if __name__ == '__main__':
    prepare()
