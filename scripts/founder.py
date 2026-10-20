"""Validated founder context and explicit familiarity; no inferred experience."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROFILE = ROOT / 'config/founder-profile.yaml'
EXAMPLE = ROOT / 'config/founder-profile.example.yaml'
STATES = ('confirmed_familiar', 'confirmed_unfamiliar', 'unknown', 'outside_confirmed_familiarity')


def validate_profile(profile):
    from scripts.validate import schema_errors
    errors = schema_errors(profile, 'founder-profile.schema.json', 'founder_profile')
    if errors:
        return errors
    experience = profile['experience']
    overlap = set(experience['confirmed_industries'] or []) & set(experience['unfamiliar_industries'] or [])
    if overlap:
        errors.append('founder_profile: conflicting familiarity: ' + ', '.join(sorted(overlap)))
    return errors


def load_profile(path=None):
    from scripts.validate import read_data
    path = Path(path or PROFILE)
    if not path.is_file():
        raise ValueError(f'founder profile not found: {path}. Link the private data repository '
                         '(python3 scripts/workspace.py link) or copy config/founder-profile.example.yaml; '
                         'see docs/workspace.md')
    profile = read_data(path)
    errors = validate_profile(profile)
    if errors:
        raise ValueError('; '.join(errors))
    return profile


def familiarity(industry, profile):
    """Exact confirmed labels only. Absence never establishes unfamiliarity."""
    errors = validate_profile(profile)
    if errors:
        raise ValueError('; '.join(errors))
    experience = profile['experience']
    if industry in (experience['confirmed_industries'] or []):
        return 'confirmed_familiar'
    if industry in (experience['unfamiliar_industries'] or []):
        return 'confirmed_unfamiliar'
    if not industry or not experience['confirmed_industries']:
        return 'unknown'
    return 'outside_confirmed_familiarity'


def audit(candidates, profile):
    classifications = {c['id']: familiarity(c['industry'], profile) for c in candidates}
    counts = {state: sum(value == state for value in classifications.values()) for state in STATES}
    return {'denominator': len(candidates), 'counts': counts, 'classifications': classifications,
            'exploration_count': counts['confirmed_unfamiliar'] + counts['outside_confirmed_familiarity'],
            'rule': 'Exact confirmed labels only; outside confirmed familiarity is not confirmed unfamiliar.'}


def candidate_errors(candidate, profile, *, require_explicit=False):
    """Keep the legacy boolean quota flag consistent with the four-state classification."""
    errors = []
    derived = familiarity(candidate.get('industry'), profile)
    stated = candidate.get('founder_familiarity')
    if require_explicit and stated is None:
        errors.append('founder_familiarity is required for new candidates')
    elif stated is not None and stated != derived:
        errors.append(f'founder_familiarity must be {derived} from the frozen profile')
    expected_flag = True if derived == 'confirmed_unfamiliar' else False if derived == 'confirmed_familiar' else None
    actual_flag = candidate.get('category_flags', {}).get('unfamiliar_to_founder')
    if actual_flag != expected_flag:
        errors.append(f'unfamiliar_to_founder must be {expected_flag!r} for {derived}')
    return errors
