"""Read-only Human run settings: arguments > process environment > .env > recipe."""
from dataclasses import dataclass
from datetime import date, datetime, timezone
import os
from pathlib import Path
import shlex
import sys

KEYS = {'START_DATE', 'END_DATE', 'DATA_DIR', 'VERBOSITY'}
REQUIRED = {'START_DATE', 'END_DATE', 'DATA_DIR'}


@dataclass(frozen=True)
class RunSettings:
    start_date: str
    end_date: str
    output_dir: Path
    verbosity: bool

    def public(self):
        return {'START_DATE': self.start_date, 'END_DATE': self.end_date,
            'DATA_DIR': str(self.output_dir), 'VERBOSITY': self.verbosity}


def preflight(start_date=None, end_date=None, output_dir=None, verbosity=None, env_file=None,
              *, workspace=None, defaults=None):
    """Validate known keys without altering os.environ, creating files or contacting sources.

    Existing/explicit .env files require all three settings from file/environment/
    arguments. Without a file, the Python pipeline may use its explicit recipe defaults.
    Relative DATA_DIR in .env resolves beside that file; other paths use workspace.
    Unknown keys, including credentials, are never loaded or echoed.
    """
    root = Path(workspace or os.environ.get('HUMAN_WORKSPACE', Path.cwd())).expanduser().resolve()
    path = (root / Path(env_file).expanduser()).resolve() if env_file is not None else root / '.env'
    values, errors = {}, []
    exists = False
    try:
        exists = path.exists()
        if exists and not path.is_file():
            errors.append('env_file must be a regular .env file.')
        elif exists:
            seen = set()
            for number, line in enumerate(path.read_text(encoding='utf-8').splitlines(), 1):
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                if line.startswith('export '):
                    line = line[7:].lstrip()
                key, separator, raw = line.partition('=')
                key = key.strip()
                if key not in KEYS:
                    continue
                if key in seen:
                    errors.append(f'{key} is repeated on .env line {number}.')
                seen.add(key)
                try:
                    tokens = shlex.split(raw, comments=True)
                    if not separator or len(tokens) != 1:
                        raise ValueError
                    values[key] = tokens[0]
                except ValueError:
                    errors.append(f'{key} has invalid syntax on .env line {number}.')
        elif env_file is not None:
            errors.append('Explicit env_file does not exist.')
    except (OSError, UnicodeError):
        errors.append('env_file cannot be read as UTF-8.')
    file_output = 'DATA_DIR' in values and 'DATA_DIR' not in os.environ and output_dir is None
    values.update({key: os.environ[key] for key in KEYS if key in os.environ})
    for key, value in [('START_DATE', start_date), ('END_DATE', end_date), ('DATA_DIR', output_dir), ('VERBOSITY', verbosity)]:
        if value is not None:
            values[key] = value
    if not exists and env_file is None:
        for key, value in (defaults or {}).items():
            if key in KEYS:
                values.setdefault(key, value)
    values.setdefault('VERBOSITY', False)
    missing = {key for key in REQUIRED if key not in values or values[key] is None
        or (isinstance(values[key], str) and not values[key].strip())}
    errors.extend(f'{key} must be set.' for key in sorted(missing))
    dates = {}
    for key in ('START_DATE', 'END_DATE'):
        if key in missing:
            continue
        try:
            dates[key] = date.fromisoformat(str(values[key]))
            if dates[key].isoformat() != str(values[key]):
                raise ValueError
        except (TypeError, ValueError):
            dates.pop(key, None)
            errors.append(f'{key} must be a valid YYYY-MM-DD date.')
    if len(dates) == 2 and dates['START_DATE'] > dates['END_DATE']:
        errors.append('START_DATE must be on or before END_DATE.')
    if dates.get('END_DATE') == date.max:
        errors.append('END_DATE must precede 9999-12-31 so its local-day end is representable.')
    verbose = values['VERBOSITY']
    if isinstance(verbose, str) and verbose.lower() in {'true', 'false'}:
        verbose = verbose.lower() == 'true'
    if type(verbose) is not bool:
        errors.append('VERBOSITY must be True or False.')
    target = None
    if 'DATA_DIR' not in missing:
        try:
            raw = os.fspath(values['DATA_DIR'])
            if not isinstance(raw, str) or not raw.strip() or '\x00' in raw:
                raise ValueError
            target = Path(raw).expanduser()
            if not target.is_absolute():
                target = (path.parent if file_output else root) / target
            target = target.resolve()
            ancestor = target
            while not ancestor.exists():
                ancestor = ancestor.parent
            if not ancestor.is_dir():
                errors.append('DATA_DIR or its existing ancestor must be a directory.')
            elif not os.access(ancestor, os.W_OK | os.X_OK):
                errors.append('DATA_DIR or its existing parent must be writable and searchable.')
        except (TypeError, ValueError, OSError, RuntimeError):
            errors.append('DATA_DIR must be a usable directory path.')
    if errors:
        raise ValueError('Human preflight failed:\n- ' + '\n- '.join(errors))
    return RunSettings(dates['START_DATE'].isoformat(), dates['END_DATE'].isoformat(), target, verbose)


def log(verbosity, message):
    if verbosity:
        stamp = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
        sys.stderr.write(f'[human {stamp}] {message}\n')
        sys.stderr.flush()
