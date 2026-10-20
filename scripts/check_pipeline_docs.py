"""Check supported documentation links, configuration parity and safe env examples."""
from pathlib import Path
import re
from urllib.parse import unquote,urlparse

ROOT=Path(__file__).resolve().parents[1]
ACTIVE=('README.md','AGENTS.md','docs/ARCHITECTURE.md','docs/WORKFLOWS.md',
        'docs/get-human-data.md','docs/import-migration.md','docs/census-sources.md','docs/product-scope.md','docs/final-delivery.md','docs/static-delivery.md')


def anchors(text):
    result=set()
    for title in re.findall(r'^#{1,6}\s+(.+?)\s*#*$',text,re.M):
        slug=re.sub(r'[^\w\- ]','',title.lower()).replace(' ','-')
        result.add(slug)
    return result


def main():
    checked=0;errors=[]
    for relative in ACTIVE:
        path=ROOT/relative;text=path.read_text()
        # Inline links in supported guides; historical code blocks are not link syntax.
        for destination in re.findall(r'\]\((<[^>]+>|[^)]+)\)',text):
            destination=destination.strip('<>');parts=urlparse(destination)
            if parts.scheme:continue
            target=path if not parts.path else (path.parent/unquote(parts.path)).resolve()
            if not target.exists():errors.append(f'{relative}: unavailable local link {destination}')
            elif parts.fragment and target.suffix=='.md' and unquote(parts.fragment) not in anchors(target.read_text()):
                errors.append(f'{relative}: unavailable heading {destination}')
            checked+=1
    config=ROOT/'config';resource=ROOT/'src/human/resources/config'
    for source in config.rglob('*'):
        if source.is_file() and (not (resource/source.relative_to(config)).exists() or source.read_bytes()!=(resource/source.relative_to(config)).read_bytes()):
            errors.append('Configuration/resource mismatch: '+source.relative_to(ROOT).as_posix())
    if (ROOT/'.env.example').read_bytes()!=(ROOT/'src/human/resources/env.example').read_bytes():errors.append('Environment example/resource mismatch')
    env=(ROOT/'.env.example').read_text()
    if set(re.findall(r'^([A-Z_]+)=',env,re.M))!={'START_DATE','END_DATE','DATA_DIR','VERBOSITY'}:errors.append('Unexpected environment example settings')
    if errors:raise SystemExit('\n'.join(errors))
    print(f'Supported docs: {len(ACTIVE)} files, {checked} local links/headings, config/resource and env-example parity passed')


if __name__=='__main__':main()
