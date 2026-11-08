#!/usr/bin/env python3
"""Compare replay target ranges with independent tape-measured references."""
import argparse
import json
import math
from pathlib import Path
from session_recording import private_write


def evaluate(predictions, references):
    seen = set()
    for ref in references:
        key = (ref['frame_id'], ref['label'])
        truth = ref['meters']
        if key in seen or isinstance(truth, bool) or not isinstance(truth, (int, float)) or not math.isfinite(truth) or truth <= 0:
            raise ValueError('References need unique frame/label keys and positive finite measured meters')
        seen.add(key)
    by_key = {}
    for row in predictions:
        if row.get('type') != 'frame_perception':
            continue
        for obj in row['objects']:
            key = (row['frame_id'], obj['label'])
            if key not in seen:
                continue
            if key in by_key:
                raise ValueError('Ambiguous duplicate frame/label prediction')
            by_key[key] = obj['range'].get('meters')
    errors, missing = [], []
    for ref in references:
        key = (ref['frame_id'], ref['label'])
        truth = ref['meters']
        estimate = by_key.get(key)
        if estimate is None:
            missing.append({'frame_id': key[0], 'label': key[1]})
            continue
        if not math.isfinite(estimate) or estimate <= 0:
            raise ValueError('Invalid predicted range')
        errors.append({'frame_id': key[0], 'label': key[1], 'measured_m': truth,
                       'estimated_m': estimate, 'absolute_error_m': abs(estimate - truth),
                       'relative_error': abs(estimate-truth)/truth})
    if not seen:
        raise ValueError('At least one independent measured reference is required')
    return {'schema_version': 1, 'reference_count': len(seen), 'estimated_count': len(errors),
            'coverage': len(errors)/len(seen), 'unknown_or_missing': missing,
            'mean_absolute_error_m': sum(e['absolute_error_m'] for e in errors)/len(errors) if errors else None,
            'max_absolute_error_m': max((e['absolute_error_m'] for e in errors), default=None),
            'samples': errors, 'safety_validated': False,
            'limitations': 'Conditional error over estimated targets; coverage includes unavailable targets. '
                           'Requires independent real measurements. No navigation safety claim.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('perception', type=Path)
    parser.add_argument('references', type=Path, help='JSON array: frame_id, label, meters (measured independently)')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    rows = [json.loads(line) for line in args.perception.read_text().splitlines()]
    report = evaluate(rows, json.loads(args.references.read_text()))
    text = json.dumps(report, indent=2, allow_nan=False) + '\n'
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        private_write(args.output, text.encode())
    print(text, end='')


if __name__ == '__main__':
    main()
