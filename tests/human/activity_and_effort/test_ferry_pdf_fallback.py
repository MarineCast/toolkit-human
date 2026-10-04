"""Exercise rotated-report fallback without requiring live reports or optional tools."""
from types import SimpleNamespace
import sys
import pytest
from human.activity_and_effort.ferry.sources import parse_bc_report

TEXT = '''Total Vehicle and Passenger Counts by Route for January 2023
01 TERMINAL
10 1 2 3 4 5 6 20 8 9 10 11 12 13
'''


def fake_reader(monkeypatch):
    monkeypatch.setitem(sys.modules, 'pypdf', SimpleNamespace(PdfReader=lambda path:
        SimpleNamespace(pages=[SimpleNamespace(extract_text=lambda **kwargs: '')])))


def test_empty_layout_requires_explicit_tool(monkeypatch, tmp_path):
    fake_reader(monkeypatch)
    monkeypatch.setattr('human.activity_and_effort.ferry.sources.shutil.which', lambda name: None)
    with pytest.raises(RuntimeError, match='Poppler'):
        parse_bc_report(tmp_path / 'fixture.pdf', 'https://example.org/fixture.pdf')


def test_fallback_retains_separated_current_month_columns(monkeypatch, tmp_path):
    fake_reader(monkeypatch)
    monkeypatch.setattr('human.activity_and_effort.ferry.sources.shutil.which', lambda name: '/fixture/pdftotext')
    def run(args, **kwargs):
        assert args[1:4] == ['-layout', '-enc', 'UTF-8']
        assert kwargs['check'] is True
        return SimpleNamespace(stdout=TEXT + '\f')
    monkeypatch.setattr('human.activity_and_effort.ferry.sources.subprocess.run', run)
    path = tmp_path / 'fixture.pdf';path.write_bytes(b'synthetic source')
    frame = parse_bc_report(path, 'https://example.org/fixture.pdf')
    assert frame['published_monthly_vehicles'].tolist() == [10.0]
    assert frame['published_monthly_passengers'].tolist() == [20.0]


def test_fallback_rejects_missing_pages(monkeypatch, tmp_path):
    fake_reader(monkeypatch)
    monkeypatch.setattr('human.activity_and_effort.ferry.sources.shutil.which', lambda name: '/fixture/pdftotext')
    monkeypatch.setattr('human.activity_and_effort.ferry.sources.subprocess.run', lambda *a, **k: SimpleNamespace(stdout=''))
    with pytest.raises(RuntimeError, match='every report page'):
        parse_bc_report(tmp_path / 'fixture.pdf', 'https://example.org/fixture.pdf')
