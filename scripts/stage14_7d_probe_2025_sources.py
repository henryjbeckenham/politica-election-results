#!/usr/bin/env python3
from __future__ import annotations
import subprocess
from pathlib import Path
import yaml

root = Path(__file__).resolve().parents[1]
catalogue = yaml.safe_load((root / 'config' / 'source_catalogue_2025.yml').read_text())
base = catalogue['base_download_url'].rstrip('/')
for index, source in enumerate(catalogue['sources'], 1):
    url = source.get('url') or f"{base}/{source['file']}"
    command = ['curl', '--location', '--silent', '--show-error', '--head', '--http1.1', '--max-time', '60', '--user-agent', 'PoliticaElectionResults/1.0', url]
    result = subprocess.run(command, text=True, capture_output=True)
    headers = result.stdout.replace('\r', '').splitlines()
    status = next((line for line in reversed(headers) if line.startswith('HTTP/')), '')
    length = next((line.split(':',1)[1].strip() for line in reversed(headers) if line.lower().startswith('content-length:')), '')
    ctype = next((line.split(':',1)[1].strip() for line in reversed(headers) if line.lower().startswith('content-type:')), '')
    print(f"{index:02d}\t{source['key']}\t{source['file']}\t{status}\t{length}\t{ctype}\t{url}")
