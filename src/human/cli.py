"""One supported Human collection and delivery pipeline."""
from __future__ import annotations

import argparse
from importlib.resources import files
import json
import os
from pathlib import Path

# Kept as an empty compatibility constant for code checking active registrations.
FAMILIES = {}


def initialize_workspace(root: Path) -> list[Path]:
    """Install only the supported pipeline recipe, preserving existing user edits."""
    created=[]
    for relative,resource in [('config/human.yaml','resources/config/human.yaml'),
            ('config/census-sources.v1.json','resources/config/census-sources.v1.json'),
            ('.env.example','resources/env.example')]:
        target=Path(root)/relative
        if target.exists():continue
        target.parent.mkdir(parents=True,exist_ok=True)
        with target.open('xb') as handle:handle.write(files('human').joinpath(resource).read_bytes())
        created.append(target)
    return created


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace',type=Path,help='Explicit configuration/data root; defaults to HUMAN_WORKSPACE or cwd')
    sub=parser.add_subparsers(dest='command')
    sub.add_parser('init',help='Create pipeline/census configs and .env.example without overwriting edits')
    validate=sub.add_parser('validate',help='Validate the three final products and their receipts')
    validate.add_argument('directory',type=Path)
    run=sub.add_parser('get-human-data',help='Collect inputs and produce only the three final Human Parquets')
    run.add_argument('--config',type=Path)
    run.add_argument('--output-dir',type=Path)
    run.add_argument('--mode',choices=('offline','live'),default='live')
    run.add_argument('--workers',type=int,default=1)
    pre=sub.add_parser('preflight',help='Validate date/output settings without study, sources or writes')
    for command in (run,pre):
        command.add_argument('--start-date')
        command.add_argument('--end-date')
        command.add_argument('--env-file',type=Path)
        command.add_argument('--verbose',action=argparse.BooleanOptionalAction,default=None)
    pre.add_argument('--output-dir',type=Path)
    run.add_argument('--no-overwrite',action='store_true',help='Require an empty destination')
    args=parser.parse_args(argv)
    root=Path(args.workspace or os.environ.get('HUMAN_WORKSPACE',Path.cwd())).expanduser().resolve()
    if args.command=='init':
        print(f'Created {len(initialize_workspace(root))} pipeline configuration files in {root}')
        return 0
    if args.command=='validate':
        from human.pipelines.delivery.validation import validate_delivery
        print(json.dumps(validate_delivery(args.directory),indent=2))
        return 0
    if args.command=='preflight':
        from human.pipelines.delivery.settings import preflight
        result=preflight(args.start_date,args.end_date,args.output_dir,args.verbose,args.env_file,workspace=root)
        print(json.dumps(result.public(),indent=2))
        return 0
    from human import get_human_data
    result=get_human_data(getattr(args,'config',None),workspace=root,output_dir=getattr(args,'output_dir',None),
        mode=getattr(args,'mode','live'),workers=getattr(args,'workers',1),overwrite=not getattr(args,'no_overwrite',False),
        start_date=getattr(args,'start_date',None),end_date=getattr(args,'end_date',None),
        env_file=getattr(args,'env_file',None),verbosity=getattr(args,'verbose',None))
    print(json.dumps({'directory':str(result.directory),'receipt':str(result.receipt),'products':result.validation},indent=2))
    return 0
