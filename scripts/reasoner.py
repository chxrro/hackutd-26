#!/usr/bin/env python3
"""Inspect a private Cosmos3-Edge endpoint or send one image. Standard library only."""
from __future__ import annotations

import argparse
import base64
import json
import mimetypes
import os
import sys
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

DEFAULT_PROMPT = (
    'Assess only whether a visible object may collide with the camera. '
    'Use one short line: POSSIBLE COLLISION — object and side, or '
    'UNKNOWN — insufficient evidence. Do not describe the scene. '
    'A single frame cannot establish motion or reliable distance. '
    'Do not estimate meters, certify a clear path, or give movement instructions.'
)


def request_json(base_url: str, path: str, payload: dict | None, timeout: float) -> dict:
    headers = {'Content-Type': 'application/json'}
    token = os.environ.get('REASONER_API_KEY')
    if token:
        headers['Authorization'] = f'Bearer {token}'
    request = Request(
        base_url.rstrip('/') + path,
        data=None if payload is None else json.dumps(payload).encode(),
        headers=headers,
    )
    with urlopen(request, timeout=timeout) as response:
        return json.load(response)


def image_url(path: Path) -> str:
    media_type = mimetypes.guess_type(path.name)[0]
    if media_type not in {'image/jpeg', 'image/png', 'image/webp'}:
        raise ValueError('Use a JPEG, PNG, or WebP image.')
    if path.stat().st_size > 10 * 1024 * 1024:
        raise ValueError('Image exceeds the 10 MiB request limit; resize it first.')
    return f'data:{media_type};base64,' + base64.b64encode(path.read_bytes()).decode('ascii')


def interpret_image(base_url: str, model: str, data_url: str,
                    prompt: str = DEFAULT_PROMPT, max_tokens: int = 192,
                    timeout: float = 60) -> dict:
    """Interpret an in-memory image; reject incomplete or empty observations."""
    payload = {
        'model': model,
        'messages': [{'role': 'user', 'content': [
            {'type': 'image_url', 'image_url': {'url': data_url}},
            {'type': 'text', 'text': prompt},
        ]}],
        'max_tokens': max_tokens,
        'chat_template_kwargs': {'enable_thinking': False},
    }
    started = time.monotonic()
    result = request_json(base_url, '/chat/completions', payload, timeout)
    choice = result['choices'][0]
    if choice.get('finish_reason') != 'stop':
        raise ValueError(f"Incomplete response: {choice.get('finish_reason')!r}")
    content = choice['message'].get('content')
    if not isinstance(content, str) or not content.strip():
        raise ValueError('Model returned no observation.')
    return {
        'model': model,
        'request_latency_ms': round((time.monotonic() - started) * 1000),
        'observation': content,
        'usage': result.get('usage'),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url', default='http://127.0.0.1:8000/v1')
    parser.add_argument('--timeout', type=float, default=60)
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('health', help='Verify that the model is listed by the API')
    image = commands.add_parser('image', help='Interpret one image without recording it')
    image.add_argument('path', type=Path)
    image.add_argument('--prompt', default=DEFAULT_PROMPT)
    image.add_argument('--max-tokens', type=int, default=192)
    args = parser.parse_args()
    parsed = urlparse(args.base_url)
    if parsed.scheme not in {'http', 'https'} or not parsed.hostname:
        parser.error('base URL must use HTTP or HTTPS')
    if parsed.scheme == 'http' and parsed.hostname not in {'127.0.0.1', 'localhost', '::1'}:
        parser.error('Use an SSH tunnel or HTTPS for remote connections.')
    if args.timeout <= 0:
        parser.error('timeout must be positive')
    if args.command == 'image' and not 1 <= args.max_tokens <= 1024:
        parser.error('max-tokens must be between 1 and 1024')
    try:
        listing = request_json(args.base_url, '/models', None, args.timeout)
        models = listing.get('data', [])
        if not models or not isinstance(models[0].get('id'), str):
            raise ValueError('API did not return a model ID.')
        model = models[0]['id']
        if args.command == 'health':
            print(json.dumps({'status': 'ready', 'model': model}))
            return 0
        print(json.dumps(interpret_image(
            args.base_url, model, image_url(args.path), args.prompt,
            args.max_tokens, args.timeout,
        ), indent=2))
        return 0
    except HTTPError as error:
        print(f'API returned HTTP {error.code}. Check model logs and endpoint access.', file=sys.stderr)
    except (URLError, TimeoutError, OSError, ValueError, KeyError, IndexError, TypeError) as error:
        print(f'Reasoner request failed: {error}', file=sys.stderr)
    return 1


if __name__ == '__main__':
    raise SystemExit(main())
