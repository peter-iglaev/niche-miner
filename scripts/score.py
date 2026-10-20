#!/usr/bin/env python3
"""Evidence-linked scoring; no research is performed.

python scripts/score.py assessment.yaml --claims claims.jsonl
API invalid input raises ValueError; the CLI emits invalid_input and exits 2.
Role completion and semantic truth additionally require validate.py and review.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from itertools import product
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parents[1]
DIMENSIONS = {
    'problem': {'impact_per_event', 'recurring_burden'},
    'business': {'monetization_economics', 'accessible_distribution', 'switching_opportunity'},
}
RATING_ORDER = ('impact_per_event', 'recurring_burden', 'monetization_economics',
                'accessible_distribution', 'switching_opportunity')


class UniqueSafeLoader(yaml.SafeLoader):
    pass


def _unique_mapping(loader, node, deep=False):
    loader.flatten_mapping(node)
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        try:
            if key in result:
                raise ValueError(f'duplicate YAML key: {key!r}')
            result[key] = loader.construct_object(value_node, deep=deep)
        except TypeError as error:
            raise ValueError('mapping keys must be scalar') from error
    return result


UniqueSafeLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _unique_mapping)


def load_yaml(path):
    try:
        return yaml.load(Path(path).read_text(encoding='utf-8'), Loader=UniqueSafeLoader)
    except (OSError, yaml.YAMLError) as error:
        raise ValueError(f'cannot load YAML: {error}') from error


def _hash(value):
    try:
        canonical = json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)
    except (TypeError, ValueError) as error:
        raise ValueError(f'expected finite JSON-compatible data: {error}') from error
    return hashlib.sha256(canonical.encode()).hexdigest()


def _schema_validate(record, name):
    schema = json.loads((ROOT / 'schemas' / name).read_text())
    errors = list(Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(record))
    if errors:
        error = errors[0]
        location = '.'.join(map(str, error.absolute_path)) or 'record'
        raise ValueError(f'{location}: {error.message}')


def _normalize_anchors(mapping, minimum, maximum, message):
    """Accept anchor keys as integers or digit strings; JSON round-trips turn integer keys into strings."""
    if not isinstance(mapping, dict): raise ValueError(message)
    result = {}
    for key, value in mapping.items():
        if type(key) is int and not isinstance(key, bool): number = key
        elif isinstance(key, str) and key.isdigit(): number = int(key)
        else: raise ValueError(message)
        if not isinstance(value, str) or not value.strip(): raise ValueError(message)
        result[number] = value
    if set(result) != set(range(minimum, maximum + 1)): raise ValueError(message)
    return result


def _validate_config(config):
    keys = {'version','policy_status','score_scale','hard_gates','indices','payment_evidence_levels','anchors','rules','ranking'}
    if not isinstance(config, dict) or set(config) != keys:
        raise ValueError('scoring config has missing or unknown keys')
    if not isinstance(config['version'], str) or not config['version'].strip():
        raise ValueError('config version must be a nonempty string')
    scale = config['score_scale']
    if not isinstance(scale, dict) or scale != {'min':0,'max':4} or any(type(v) is not int for v in scale.values()):
        raise ValueError('assessment schema requires integer scale min=0, max=4')
    gates = config['hard_gates']
    if not isinstance(gates, dict) or set(gates) != {'investigate_minimum_payment','commercial_validation_minimum_payment','distribution_pass_minimum_rating'}:
        raise ValueError('hard_gates has missing or unknown keys')
    if any(type(gates[k]) is not int or not 0 <= gates[k] <= 5 for k in ('investigate_minimum_payment','commercial_validation_minimum_payment')):
        raise ValueError('payment thresholds must be integers 0 through 5')
    if type(gates['distribution_pass_minimum_rating']) is not int or not 0 <= gates['distribution_pass_minimum_rating'] <= scale['max']:
        raise ValueError('distribution rating threshold must be an integer within the rating scale')
    if gates['investigate_minimum_payment'] > gates['commercial_validation_minimum_payment']:
        raise ValueError('commercial_validation threshold must not be weaker than Investigation')
    if not isinstance(config['indices'], dict) or set(config['indices']) != set(DIMENSIONS):
        raise ValueError('indices must define problem and business')
    for index, names in DIMENSIONS.items():
        weights = config['indices'][index]
        if not isinstance(weights, dict) or set(weights) != names:
            raise ValueError(f'{index} has missing or unknown dimensions')
        if any(type(v) not in (int,float) or not math.isfinite(v) or v < 0 for v in weights.values()):
            raise ValueError('weights must be finite nonnegative numbers')
        if not math.isclose(sum(weights.values()), 100, rel_tol=0, abs_tol=1e-9):
            raise ValueError(f'{index} weights must sum to 100')
    levels = config['payment_evidence_levels']
    config['payment_evidence_levels'] = _normalize_anchors(levels,0,5,'payment evidence anchors 0 through 5 are required')
    anchors = config['anchors']
    if not isinstance(anchors,dict) or set(anchors) != set().union(*DIMENSIONS.values()):
        raise ValueError('every rating dimension needs anchors')
    for name, values in anchors.items():
        anchors[name] = _normalize_anchors(values,0,4,'rating anchors 0 through 4 must be nonempty')
    ranking = config['ranking']
    ranking_keys = {'status','applies_to','founder_fit_included','scale_max','weights','components',
                    'unweighted_gaps','rationale'}
    if not isinstance(ranking,dict) or set(ranking) != ranking_keys:
        raise ValueError('ranking has missing or unknown keys')
    if ranking['applies_to'] != 'eligible_candidates_only':
        raise ValueError('ranking must apply to eligible candidates only')
    if ranking['founder_fit_included'] is not False:
        raise ValueError('ranking must not include founder fit')
    weights, components = ranking['weights'], ranking['components']
    known_dimensions = set().union(*DIMENSIONS.values())
    if not isinstance(weights,dict) or not weights or not isinstance(components,dict) or set(components) != set(weights):
        raise ValueError('every ranking weight needs a component definition')
    if any(type(v) not in (int,float) or not math.isfinite(v) or v < 0 for v in weights.values()):
        raise ValueError('ranking weights must be finite nonnegative numbers')
    for name, component in components.items():
        if not isinstance(component,dict):
            raise ValueError(f'ranking component {name} must be a mapping')
        if set(component) == {'source','max'} and component['source'] == 'payment_level':
            if type(component['max']) is not int or component['max'] < 1:
                raise ValueError('payment_level ranking component needs a positive integer max')
        elif set(component) == {'rating'} and component['rating'] in known_dimensions:
            continue
        else:
            raise ValueError(f'unsupported ranking component: {name}')
    if type(ranking['scale_max']) is not int or not math.isclose(sum(weights.values()),ranking['scale_max'],
                                                                  rel_tol=0,abs_tol=1e-9):
        raise ValueError('ranking weights must sum to scale_max')
    if not isinstance(ranking['unweighted_gaps'],dict) or any(
            type(v) not in (int,float) or not math.isfinite(v) or v < 0 for v in ranking['unweighted_gaps'].values()):
        raise ValueError('unweighted_gaps must map gap names to nonnegative numbers')
    if not isinstance(ranking['rationale'],str) or not ranking['rationale'].strip():
        raise ValueError('ranking needs a rationale')
    if not isinstance(config['rules'],list) or not config['rules'] or not all(isinstance(v,str) and v.strip() for v in config['rules']):
        raise ValueError('config rules must be nonempty strings')
    _hash(config)
    return config


def load_config(path=None):
    return _validate_config(load_yaml(path or ROOT / 'config/scoring.yaml'))


def _claim_records(claims):
    if claims is None:
        return {}
    if isinstance(claims,dict):
        items = list(claims.items())
    elif isinstance(claims,list):
        items = [(None,c) for c in claims]
    else:
        raise ValueError('claims must be a list or mapping keyed by ID')
    result = {}
    for key, claim in items:
        _schema_validate(claim,'claim.schema.json')
        identifier = claim['id']
        if key is not None and key != identifier:
            raise ValueError('claim mapping key does not match id')
        if identifier in result:
            raise ValueError(f'duplicate claim ID: {identifier}')
        result[identifier] = claim
    _hash(result)
    return result


def load_claims(path):
    try:
        records = []
        for number, line in enumerate(Path(path).read_text().splitlines(),1):
            if line.strip():
                try:
                    records.append(json.loads(line, object_pairs_hook=_json_unique))
                except (ValueError, TypeError) as error:
                    raise ValueError(f'claims line {number}: {error}') from error
        return _claim_records(records)
    except OSError as error:
        raise ValueError(f'cannot read claims: {error}') from error


def _json_unique(pairs):
    result = {}
    for key,value in pairs:
        if key in result:
            raise ValueError(f'duplicate JSON key: {key}')
        result[key] = value
    return result


def _validate_assessment(record, claims, *, legacy=False):
    _schema_validate(record,'assessment.schema.json')
    _hash(record)
    if record['author_id'] == record['discoverer_id']:
        raise ValueError('discoverer cannot author its own assessment')
    level = record['payment']['level']
    if level is not None and type(level) is not int:
        raise ValueError('payment.level must be a strict integer or null')
    for rating in record['ratings'].values():
        if rating['value'] is not None and type(rating['value']) is not int:
            raise ValueError('rating values must be strict integers or null')
    evidence = _claim_records(claims)
    cited = set(record['unresolved_fatal_contradictions'])
    for section in [record['payment'],record['distribution'],record['feasibility'],*record['ratings'].values()]:
        cited.update(section['claim_ids'])
    for identifier in sorted(cited):
        if identifier not in evidence:
            raise ValueError(f'unresolved claim reference: {identifier}')
        if evidence[identifier]['candidate_id'] != record['candidate_id']:
            raise ValueError(f'claim {identifier} belongs to another candidate')
    def material(ids):
        return [evidence[c] for c in ids if evidence[c]['quality'] != 'D']
    for name,rating in record['ratings'].items():
        if rating['value'] is not None and not material(rating['claim_ids']):
            raise ValueError(f'known rating {name} needs non-speculative evidence')
    for name in ('distribution','feasibility'):
        gate = record[name]
        observations = material(gate['claim_ids'])
        if gate['status'] != 'unknown' and not observations:
            raise ValueError(f'known {name} needs non-speculative evidence')
        if gate['status'] == 'pass' and not any(c['relationship'] in {'supports','qualifies'} for c in observations):
            raise ValueError(f'passing {name} needs supporting evidence')
    if level is not None and level > 0:
        observations = material(record['payment']['claim_ids'])
        if not any(c['relationship'] in {'supports','qualifies'} for c in observations):
            raise ValueError('positive payment level needs supporting evidence')
        if level >= 3 and not legacy:
            paid = _paid_observations(observations)
            scoped_paid = [c for c in paid if level < 4 or c['payment_scope'] == 'exact']
            convergent = _convergent_spend(observations, exact=level >= 4)
            if not scoped_paid and not convergent:
                raise ValueError('payment level needs scoped paid use or independent convergent existing-spend evidence')
            if level == 5:
                terms = [c for c in scoped_paid if c.get('commercial_terms_explicit') is True]
                if not any(a['buyer_group'] != b['buyer_group'] and
                           a['independence_group'] != b['independence_group']
                           for a, b in product(terms, terms)):
                    raise ValueError('level 5 requires two independent paid buyer groups and origins with attributable terms')
        if level >= 3 and legacy:
            paid = [c for c in observations if c.get('paid_use_explicit') is True and c.get('payment_scope') in {'adjacent','exact'} and c['quality'] in {'A','B'} and c['relationship'] in {'supports','qualifies'} and c.get('buyer_group')]
            if level >= 4:
                paid = [c for c in paid if c['payment_scope'] == 'exact']
            if not paid:
                raise ValueError('payment level requires explicit paid use, A/B quality, required scope, and buyer mapping')
            if level == 5 and (len({c['buyer_group'] for c in paid}) < 2 or len({c['independence_group'] for c in paid}) < 2):
                raise ValueError('level 5 requires two independent buyer groups and origins')
    for identifier in record['unresolved_fatal_contradictions']:
        if evidence[identifier]['relationship'] != 'contradicts' or evidence[identifier]['quality'] == 'D':
            raise ValueError('fatal contradiction must cite a non-speculative contradicting claim')
    return {identifier:evidence[identifier] for identifier in sorted(cited)}


def _paid_observations(observations):
    return [c for c in observations if c.get('paid_use_explicit') is True
            and c.get('payment_scope') in {'adjacent', 'exact'} and c['quality'] in {'A', 'B'}
            and c['relationship'] in {'supports', 'qualifies'} and c.get('buyer_group')
            and c.get('spend_signal') not in {'public_pricing', 'recurring_workflow'}]


def _convergent_spend(observations, *, exact=False):
    """Signals describe observed spending on work, never automatic software budget transfer.

    Three distinct source origins must support three different signal families.
    Repetition from one vendor, salary offers and generic salary benchmarks cannot count
    as observed labor_spend; semantic correctness remains the reviewer's responsibility.
    """
    eligible = [c for c in observations if c['quality'] in {'A', 'B'}
                and c['relationship'] in {'supports', 'qualifies'}
                and c.get('payment_scope') in ({'exact'} if exact else {'exact', 'adjacent'})]
    buyers = [c for c in eligible if c.get('spend_signal') in {'buyer_report', 'practitioner_report'} and c.get('buyer_group')]
    spend = [c for c in eligible if c.get('spend_signal') in {'labor_spend', 'outsourcing_spend'} and c.get('buyer_group')]
    offers = [c for c in eligible if c.get('spend_signal') in {'public_pricing', 'paid_competitor'}]
    recurrence = any(c.get('spend_signal') == 'recurring_workflow' for c in eligible)
    return any(len({a['independence_group'], b['independence_group'], c['independence_group']}) == 3
               and (not exact or (recurrence and a['buyer_group'] != b['buyer_group']))
               for a, b, c in product(buyers, spend, offers))


def transaction_evidence_strength(observations):
    """Directness of concrete paid-use evidence, independent of spend confidence (0–3)."""
    paid = _paid_observations(observations)
    if any(c.get('spend_signal') == 'transaction_record' and c.get('commercial_terms_explicit') for c in paid):
        return 3
    if any(a['buyer_group'] != b['buyer_group'] and a['independence_group'] != b['independence_group']
           for a, b in product(paid, paid)):
        return 2
    return 1 if paid else 0


def investigation_priority(record):
    """Ordinal research allocation; unknown commercial gates are explicit gaps, not vetoes."""
    level = record['payment']['level']
    impact = record['ratings']['impact_per_event']['value']
    frequency = record['ratings']['recurring_burden']['value']
    missing = [name for name in ('distribution', 'feasibility') if record[name]['status'] == 'unknown']
    missing += [name for name, value in (('payment', level), ('impact_per_event', impact),
                                        ('recurring_burden', frequency)) if value is None]
    blocked = bool(record['unresolved_fatal_contradictions']) or any(
        record[name]['status'] == 'fail' for name in ('distribution', 'feasibility'))
    if blocked:
        tier, reason = 'blocked', 'known_fatal_or_failed_commercial_condition'
    elif level is not None and level >= 3 and impact is not None and impact >= 2 and frequency is not None and frequency >= 2:
        tier, reason = 'high', 'meaningful_spend_and_recurring_material_pain'
    elif level is not None and level >= 3:
        tier, reason = 'medium', 'meaningful_spend_needs_pain_or_frequency_check'
    elif level is not None and level >= 2 and impact is not None and impact >= 2 and frequency is not None and frequency >= 2:
        tier, reason = 'medium', 'priced_workflow_with_recurring_material_pain_needs_spend_check'
    elif level is None:
        tier, reason = 'unknown', 'payment_not_assessed'
    else:
        tier, reason = 'low', 'limited_existing_spend_support'
    return {'tier': tier, 'reason_codes': [reason], 'missing_evidence': missing,
            'commercial_recommendation': False}


def investigation_priority_from_claims(candidate_id, claims, config=None):
    """Cheap triage over accepted claims; no Judge, network or commercial recommendation.

    This infers only a supported ceiling from structured signals. It does not infer
    pain ratings or qualify the candidate, and it cites the complete input evidence.
    """
    policy = load_config() if config is None else _validate_config(config)
    observations = [c for c in _claim_records(claims).values() if c['candidate_id'] == candidate_id]
    support = [c for c in observations if c['quality'] in {'A', 'B'} and c['relationship'] in {'supports', 'qualifies'}]
    paid = _paid_observations(support)
    exact = [c for c in paid if c['payment_scope'] == 'exact']
    terms = [c for c in exact if c.get('commercial_terms_explicit')]
    if any(a['buyer_group'] != b['buyer_group'] and a['independence_group'] != b['independence_group']
           for a, b in product(terms, terms)):
        level = 5
    elif exact or _convergent_spend(support, exact=True):
        level = 4
    elif paid or _convergent_spend(support):
        level = 3
    elif any(c.get('spend_signal') in {'public_pricing', 'paid_competitor'} for c in support):
        level = 2
    else:
        level = 1 if support else None
    recurring = any(c.get('spend_signal') == 'recurring_workflow' and
                    c.get('payment_scope') in {'adjacent', 'exact'} for c in support)
    counterevidence = [c['id'] for c in observations if c['relationship'] == 'contradicts' and c['quality'] != 'D']
    if counterevidence:
        tier, reason = 'review_required', 'counterevidence_needs_review_before_more_budget'
    elif level is not None and level >= 4 and recurring:
        tier, reason = 'high', 'strong_scoped_spend_and_recurring_workflow'
    elif level is not None and (level >= 3 or level >= 2 and recurring):
        tier, reason = 'medium', 'spend_or_recurring_priced_workflow_needs_targeted_check'
    else:
        tier, reason = ('unknown' if level is None else 'low'), 'limited_existing_spend_support'
    return {'tier': tier, 'reason_codes': [reason], 'payment_confidence': level,
            'transaction_evidence_strength': transaction_evidence_strength(support),
            'claim_ids': sorted(c['id'] for c in observations), 'counterevidence_claim_ids': counterevidence,
            'missing_evidence': ['pain_severity', 'distribution', 'feasibility', 'independent_review'],
            'cheapest_next_decisive_test': ('Проверить противоречащие утверждения.' if counterevidence else
                'Проверить один конкретный inbound-канал и стоимость выполнения задачи.' if level is not None and level >= 3 else
                'Найти независимый рассказ покупателя и подтверждение расходов на эту работу.'),
            'commercial_recommendation': False, 'config_version': policy['version']}


def score(record, config=None, claims=None):
    policy = load_config() if config is None else _validate_config(config)
    if policy['version'] == '2.2':
        return _score_v2(record, config=policy, claims=claims)
    evidence = _validate_assessment(record,claims)
    access_rating = record['ratings']['accessible_distribution']['value']
    if record['distribution']['status'] == 'pass' and access_rating is not None and access_rating < policy['hard_gates']['distribution_pass_minimum_rating']:
        raise ValueError('passing distribution gate conflicts with the known accessible_distribution rating')
    context, level = record['decision_context'], record['payment']['level']
    minimum = policy['hard_gates']['commercial_validation_minimum_payment']
    failures, unknowns = [], []
    if level is None:
        unknowns.append('payment_unknown')
    elif level < minimum:
        failures.append('payment_below_context_minimum')
    if record['distribution']['status'] == 'fail':
        failures.append('distribution_failed')
    elif record['distribution']['status'] == 'unknown':
        unknowns.append('distribution_unknown')
    if record['feasibility']['status'] == 'fail':
        failures.append('feasibility_failed')
    elif record['feasibility']['status'] == 'unknown':
        unknowns.append('feasibility_unknown')
    if record['unresolved_fatal_contradictions']:
        failures.append('unresolved_fatal_contradiction')
    commercial_outcome = 'ineligible' if failures else 'deferred' if unknowns else 'eligible'
    commercial = {'status': 'qualified' if commercial_outcome == 'eligible' else commercial_outcome,
                  'qualified': commercial_outcome == 'eligible', 'reason_codes': failures + unknowns}
    priority = investigation_priority(record)
    if context == 'investigate':
        outcome = 'ineligible' if priority['tier'] == 'blocked' else 'eligible'
        reasons = priority['reason_codes'] if outcome == 'ineligible' else []
    else:
        outcome, reasons = commercial_outcome, failures + unknowns
    result = {
        'candidate_id':record['candidate_id'],'decision_context':context,'outcome':outcome,
        'eligible':outcome=='eligible','rankable':False,
        'problem_index':None,'business_index':None,'reason_codes':reasons.copy(),'reasons':reasons.copy(),
        'payment_level':level,'payment_confidence':level,'minimum_payment_level':minimum,
        'transaction_evidence_strength':transaction_evidence_strength(
            [evidence[c] for c in record['payment']['claim_ids']]),
        'investigation_priority':priority, 'commercial_qualification':commercial,
        'missing_ratings':[k for k in RATING_ORDER if record['ratings'][k]['value'] is None],
        'ranking_score':None,'ranking_scale_max':policy['ranking']['scale_max'],
        'ranking_components':{},'founder_fit_included':False,
        'switching_signal_rating':record['ratings']['switching_opportunity']['value'],
        'config_version':policy['version'],'config_hash':_hash(policy),
        'assessment_hash':_hash(record),'evidence_hash':_hash(evidence),
    }
    if result['eligible']:
        scale = policy['score_scale']
        for index,weights in policy['indices'].items():
            if all(record['ratings'][name]['value'] is not None for name in weights):
                total = sum(weight*(record['ratings'][name]['value']-scale['min'])/(scale['max']-scale['min']) for name,weight in weights.items())
                result[f'{index}_index'] = round(total)
        ranking = policy['ranking']
        components = {}
        for name, component in ranking['components'].items():
            if 'source' in component:
                value, maximum = level, component['max']
            else:
                value, maximum = record['ratings'][component['rating']]['value'], scale['max']
            components[name] = None if value is None else round(value/maximum*100)
        result['ranking_components'] = components
        result['ranking_score'] = (round(sum(ranking['weights'][name]*components[name]/100 for name in components))
                                   if commercial['qualified'] and all(value is not None for value in components.values()) else None)
        result['rankable'] = (context=='commercial_validation' and result['ranking_score'] is not None
                              and all(result[f'{index}_index'] is not None for index in DIMENSIONS))
        if result['ranking_score'] is None:
            result['reason_codes'].append('ranking_incomplete')
            result['reasons'].append('ranking_incomplete')
        if result['missing_ratings']:
            result['reason_codes'].append('ratings_unknown_unranked')
            result['reasons'].append('ratings_unknown_unranked')
    return result



# Frozen v2.2 snapshots must reproduce historical score.json byte semantics.
# New runs use the v3 policy; never rewrite accepted historical scores in place.
def _score_v2(record, config=None, claims=None):
    policy = load_config() if config is None else _validate_config(config)
    evidence = _validate_assessment(record,claims, legacy=True)
    access_rating = record['ratings']['accessible_distribution']['value']
    if record['distribution']['status'] == 'pass' and access_rating is not None and access_rating < policy['hard_gates']['distribution_pass_minimum_rating']:
        raise ValueError('passing distribution gate conflicts with the known accessible_distribution rating')
    context, level = record['decision_context'], record['payment']['level']
    minimum = policy['hard_gates'][f'{context}_minimum_payment']
    failures, unknowns = [], []
    if level is None:
        unknowns.append('payment_unknown')
    elif level < minimum:
        failures.append('payment_below_context_minimum')
    if record['distribution']['status'] == 'fail':
        failures.append('distribution_failed')
    elif record['distribution']['status'] == 'unknown':
        unknowns.append('distribution_unknown')
    if record['feasibility']['status'] == 'fail':
        failures.append('feasibility_failed')
    elif context == 'commercial_validation' and record['feasibility']['status'] == 'unknown':
        unknowns.append('feasibility_unknown')
    if record['unresolved_fatal_contradictions']:
        failures.append('unresolved_fatal_contradiction')
    outcome = 'ineligible' if failures else 'deferred' if unknowns else 'eligible'
    reasons = failures + unknowns
    result = {
        'candidate_id':record['candidate_id'],'decision_context':context,'outcome':outcome,
        'eligible':outcome=='eligible','rankable':False,
        'problem_index':None,'business_index':None,'reason_codes':reasons.copy(),'reasons':reasons.copy(),
        'payment_level':level,'minimum_payment_level':minimum,
        'missing_ratings':[k for k in RATING_ORDER if record['ratings'][k]['value'] is None],
        'ranking_score':None,'ranking_scale_max':policy['ranking']['scale_max'],
        'ranking_components':{},'founder_fit_included':False,
        'switching_signal_rating':record['ratings']['switching_opportunity']['value'],
        'config_version':policy['version'],'config_hash':_hash(policy),
        'assessment_hash':_hash(record),'evidence_hash':_hash(evidence),
    }
    if result['eligible']:
        scale = policy['score_scale']
        for index,weights in policy['indices'].items():
            if all(record['ratings'][name]['value'] is not None for name in weights):
                total = sum(weight*(record['ratings'][name]['value']-scale['min'])/(scale['max']-scale['min']) for name,weight in weights.items())
                result[f'{index}_index'] = round(total)
        ranking = policy['ranking']
        components = {}
        for name, component in ranking['components'].items():
            if 'source' in component:
                value, maximum = level, component['max']
            else:
                value, maximum = record['ratings'][component['rating']]['value'], scale['max']
            components[name] = None if value is None else round(value/maximum*100)
        result['ranking_components'] = components
        result['ranking_score'] = (round(sum(ranking['weights'][name]*components[name]/100 for name in components))
                                   if all(value is not None for value in components.values()) else None)
        result['rankable'] = (context=='commercial_validation' and result['ranking_score'] is not None
                              and all(result[f'{index}_index'] is not None for index in DIMENSIONS))
        if result['ranking_score'] is None:
            result['reason_codes'].append('ranking_incomplete')
            result['reasons'].append('ranking_incomplete')
        if result['missing_ratings']:
            result['reason_codes'].append('ratings_unknown_unranked')
            result['reasons'].append('ratings_unknown_unranked')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('assessment')
    parser.add_argument('--claims',required=True)
    parser.add_argument('--config')
    args = parser.parse_args()
    try:
        result = score(load_yaml(args.assessment),load_config(args.config),load_claims(args.claims))
    except (ValueError,OSError) as error:
        print(json.dumps({'outcome':'invalid_input','eligible':False,'rankable':False,'problem_index':None,'business_index':None,'error':str(error)},indent=2))
        raise SystemExit(2) from None
    print(json.dumps(result,indent=2,allow_nan=False))


if __name__ == '__main__':
    main()
