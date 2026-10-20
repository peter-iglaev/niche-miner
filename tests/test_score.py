"""Offline synthetic regression checks; never perform niche research."""
import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.score import DIMENSIONS, load_claims, load_config, load_yaml, score, investigation_priority_from_claims


def fixtures():
    claims = [{
        'id':'paid-1','candidate_id':'synthetic-niche','claim':'Synthetic buyer pays for the exact task.',
        'source_url':'https://example.com/synthetic-payment','source_type':'synthetic_fixture',
        'access_date':'2026-09-11','source_excerpt':'Synthetic paid-use observation.',
        'locator':'Synthetic fixture','quality':'A','relationship':'supports',
        'independence_group':'buyer-origin-1','notes':'Synthetic test only.',
        'author_id':'validator-1','payment_scope':'exact','paid_use_explicit':True,'buyer_group':'buyer-1',
    }]
    entry = {'claim_ids':['paid-1'],'rationale':'Synthetic evidence supports this assessment.'}
    record = {
        'schema_version':'2.0','candidate_id':'synthetic-niche','author_id':'judge-1',
        'discoverer_id':'scout-1','decision_context':'commercial_validation',
        'payment':{'level':4,**copy.deepcopy(entry)},
        'distribution':{'status':'pass',**copy.deepcopy(entry)},
        'feasibility':{'status':'pass',**copy.deepcopy(entry)},
        'ratings':{name:{'value':4,**copy.deepcopy(entry)} for name in sorted(set().union(*DIMENSIONS.values()))},
        'critical_unknowns':[],'unresolved_fatal_contradictions':[],
    }
    return record, claims


class ScoreTests(unittest.TestCase):
    def setUp(self):
        self.record, self.claims = fixtures()

    def run_score(self, record=None, claims=None, config=None):
        return score(self.record if record is None else record,config=config,claims=self.claims if claims is None else claims)

    def test_weighted_ranking_applies_only_after_gates(self):
        record, claims = fixtures()
        result = score(record, claims={c['id']: c for c in claims})
        self.assertTrue(result['eligible'])
        self.assertEqual(result['ranking_components'], {
            'payment_evidence': 80, 'pain_severity': 100, 'frequency': 100, 'distribution': 100})
        self.assertEqual(result['ranking_score'], 63)
        self.assertEqual(result['ranking_scale_max'], 70)
        self.assertFalse(result['founder_fit_included'])

    def test_ranking_is_absent_when_a_gate_fails(self):
        record, claims = fixtures()
        record['payment']['level'] = 0
        result = score(record, claims={c['id']: c for c in claims})
        self.assertEqual(result['outcome'], 'ineligible')
        self.assertIsNone(result['ranking_score'])
        self.assertFalse(result['rankable'])

    def test_dissatisfaction_is_a_switching_signal_not_payment_proof(self):
        record, claims = fixtures()
        record['ratings']['switching_opportunity']['value'] = 1
        result = score(record, claims={c['id']: c for c in claims})
        self.assertEqual(result['switching_signal_rating'], 1)
        self.assertEqual(result['payment_level'], 4)
        policy = load_config()
        self.assertFalse(policy['ranking']['founder_fit_included'])
        self.assertIn('founder_fit', policy['ranking']['unweighted_gaps'])
        self.assertEqual(sum(policy['ranking']['weights'].values()), policy['ranking']['scale_max'])

    def test_indices_bounds_and_mixed_vector(self):
        result = self.run_score()
        self.assertEqual((result['problem_index'],result['business_index']),(100,100))
        self.assertTrue(result['rankable'])
        for rating in self.record['ratings'].values():
            rating['value'] = 0
        self.record['ratings']['accessible_distribution']['value'] = 3
        self.assertEqual(self.run_score()['problem_index'],0)
        self.assertEqual(self.run_score()['business_index'],26)
        self.record['ratings']['impact_per_event']['value'] = 4
        self.record['ratings']['monetization_economics']['value'] = 4
        self.assertEqual((self.run_score()['problem_index'],self.run_score()['business_index']),(60,66))
        self.assertNotIn('score',result)

    def test_config_changes_arithmetic_hash_and_gate(self):
        self.record['ratings']['recurring_burden']['value'] = 0
        original = self.run_score()
        policy = load_config()
        policy['indices']['problem'] = {'impact_per_event':20,'recurring_burden':80}
        changed = self.run_score(config=policy)
        self.assertEqual(changed['problem_index'],20)
        self.assertNotEqual(original['config_hash'],changed['config_hash'])
        policy['hard_gates']['commercial_validation_minimum_payment'] = 5
        self.assertEqual(self.run_score(config=policy)['outcome'],'ineligible')

    def test_payment_strict_types_and_range(self):
        for value in (99,-1,True,'4',3.9,4.0,float('nan'),float('inf')):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.record['payment']['level'] = value
                self.run_score()

    def test_rating_strict_types_and_range(self):
        for value in (5,-1,True,'4',3.5,4.0,float('nan'),float('inf')):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.record['ratings']['impact_per_event']['value'] = value
                self.run_score()

    def test_invalid_input_precedes_commercial_rejection(self):
        for record in ({},[],None):
            with self.subTest(record=record), self.assertRaises(ValueError):
                score(record,claims=self.claims)
        self.record['payment']['level'] = 0
        del self.record['ratings']['recurring_burden']
        with self.assertRaises(ValueError):
            self.run_score()

    def test_unsupported_ratings_and_extra_fields(self):
        self.record['ratings']['impact_per_event']['claim_ids'] = []
        with self.assertRaises(ValueError):
            self.run_score()
        self.record,self.claims = fixtures()
        self.record['payment_confidnce'] = 4
        with self.assertRaises(ValueError):
            self.run_score()

    def test_adjacent_payment_is_investigate_only(self):
        self.record['payment']['level'] = 3
        self.claims[0]['payment_scope'] = 'adjacent'
        self.assertEqual(self.run_score()['outcome'],'ineligible')
        self.record['decision_context'] = 'investigate'
        self.assertEqual(self.run_score()['outcome'],'eligible')
        self.assertFalse(self.run_score()['rankable'])
        self.record['payment']['level'] = 4
        with self.assertRaises(ValueError):
            self.run_score()

    def test_pricing_weak_quality_or_missing_buyer_cannot_prove_paid_use(self):
        for key,value in [('paid_use_explicit',False),('quality','C'),('buyer_group',None),('payment_scope','none')]:
            with self.subTest(key=key):
                record,claims = fixtures()
                claims[0][key] = value
                with self.assertRaises(ValueError):
                    score(record,claims=claims)

    def test_level_five_needs_distinct_buyers_and_origins(self):
        self.record['payment']['level'] = 5
        with self.assertRaises(ValueError):
            self.run_score()
        second = {**self.claims[0],'id':'paid-2','buyer_group':'buyer-2'}
        self.claims.append(second)
        self.record['payment']['claim_ids'].append('paid-2')
        with self.assertRaises(ValueError):
            self.run_score()
        second['independence_group'] = 'buyer-origin-2'
        with self.assertRaises(ValueError):
            self.run_score()
        for claim in self.claims:
            claim['commercial_terms_explicit'] = True
        self.assertTrue(self.run_score()['eligible'])
        second['buyer_group'] = 'buyer-1'
        with self.assertRaises(ValueError):
            self.run_score()

    def test_distribution_is_noncompensatory(self):
        self.record['distribution']['status'] = 'fail'
        result = self.run_score()
        self.assertEqual(result['outcome'],'ineligible')
        self.assertIsNone(result['problem_index'])
        self.assertIsNone(result['business_index'])
        self.record['distribution']['status'] = 'unknown'
        self.assertEqual(self.run_score()['outcome'],'deferred')
        self.record['decision_context'] = 'investigate'
        self.assertEqual(self.run_score()['outcome'],'eligible')
        self.assertEqual(self.run_score()['commercial_qualification']['status'],'deferred')

    def test_passing_distribution_cannot_hide_a_low_access_rating(self):
        for value in (0,1,2):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.record['ratings']['accessible_distribution']['value'] = value
                self.run_score()
        self.record['ratings']['accessible_distribution']['value'] = 3
        self.assertTrue(self.run_score()['eligible'])
        policy = load_config()
        policy['hard_gates']['distribution_pass_minimum_rating'] = 4
        with self.assertRaises(ValueError):
            self.run_score(config=policy)

    def test_unknown_access_rating_does_not_imply_failed_access(self):
        self.record['ratings']['accessible_distribution']['value'] = None
        result = self.run_score()
        self.assertTrue(result['eligible'])
        self.assertIsNone(result['business_index'])
        self.assertFalse(result['rankable'])

    def test_decision_context_and_schema_version_are_required(self):
        self.record['schema_version'] = '1.0'
        with self.assertRaises(ValueError):
            self.run_score()
        self.record['schema_version'] = '2.0'
        self.record['decision_context'] = 'unrecognized_context'
        with self.assertRaises(ValueError):
            self.run_score()

    def test_unknown_payment_and_ratings_remain_unknown(self):
        self.record['payment']['level'] = None
        self.assertEqual(self.run_score()['outcome'],'deferred')
        self.record['payment']['level'] = 4
        self.record['ratings']['monetization_economics']['value'] = None
        result = self.run_score()
        self.assertEqual(result['outcome'],'eligible')
        self.assertEqual(result['problem_index'],100)
        self.assertIsNone(result['business_index'])
        self.assertFalse(result['rankable'])

    def test_feasibility_context_policy(self):
        self.record['feasibility']['status'] = 'unknown'
        self.assertEqual(self.run_score()['outcome'],'deferred')
        self.record['decision_context'] = 'investigate'
        self.assertEqual(self.run_score()['outcome'],'eligible')
        self.record['feasibility']['status'] = 'fail'
        self.assertEqual(self.run_score()['outcome'],'ineligible')

    def test_fatal_contradiction_blocks_both_contexts(self):
        negative = {**self.claims[0],'id':'negative-1','relationship':'contradicts'}
        self.claims.append(negative)
        self.record['unresolved_fatal_contradictions'] = ['negative-1']
        for context in ('investigate','commercial_validation'):
            self.record['decision_context'] = context
            self.assertEqual(self.run_score()['outcome'],'ineligible')

    def test_claim_references_identity_and_schema(self):
        self.assertTrue(self.run_score(claims={c['id']:c for c in self.claims})['eligible'])
        with self.assertRaises(ValueError):
            self.run_score(claims=[])
        with self.assertRaises(ValueError):
            self.run_score(claims=self.claims+self.claims)
        with self.assertRaises(ValueError):
            self.run_score(claims={'wrong-key':self.claims[0]})
        self.claims[0]['candidate_id'] = 'another-niche'
        with self.assertRaises(ValueError):
            self.run_score()
        self.claims[0]['candidate_id'] = 'synthetic-niche'
        self.claims[0]['access_date'] = 'yesterday'
        with self.assertRaises(ValueError):
            self.run_score()

    def test_self_assessment_is_invalid(self):
        self.record['author_id'] = self.record['discoverer_id']
        with self.assertRaises(ValueError):
            self.run_score()

    def test_invalid_config_is_rejected(self):
        for mutation in ('weight','dimension','gate','anchor','scale','access_threshold'):
            with self.subTest(mutation=mutation):
                policy = load_config()
                if mutation == 'weight': policy['indices']['problem']['impact_per_event'] = -1
                if mutation == 'dimension': policy['indices']['problem']['invented'] = 1
                if mutation == 'gate': policy['hard_gates']['commercial_validation_minimum_payment'] = True
                if mutation == 'anchor': del policy['anchors']['impact_per_event'][2]
                if mutation == 'scale': policy['score_scale']['max'] = 5
                if mutation == 'access_threshold': policy['hard_gates']['distribution_pass_minimum_rating'] = True
                with self.assertRaises(ValueError):
                    self.run_score(config=policy)

    def convergence(self, *, exact=False):
        record, claims = fixtures()
        signals = ['public_pricing', 'buyer_report', 'labor_spend', 'recurring_workflow']
        claims = [{**claims[0], 'id': f'signal-{i}', 'spend_signal': signal,
                   'paid_use_explicit': False, 'payment_scope': 'exact' if exact else 'adjacent',
                   'buyer_group': f'buyer-{i}' if i in (1, 2) else None,
                   'independence_group': f'origin-{i}'} for i, signal in enumerate(signals)]
        for section in [record['payment'], record['distribution'], record['feasibility'], *record['ratings'].values()]:
            section['claim_ids'] = [c['id'] for c in claims]
        record['payment']['level'] = 4 if exact else 3
        return record, claims

    def test_pricing_alone_cannot_establish_existing_spend(self):
        record, claims = self.convergence(exact=True)
        claims = [claims[0]]
        for section in [record['payment'], record['distribution'], record['feasibility'], *record['ratings'].values()]:
            section['claim_ids'] = [claims[0]['id']]
        for level in (3, 4, 5):
            record['payment']['level'] = level
            with self.subTest(level=level), self.assertRaises(ValueError):
                score(record, claims=claims)
        self.assertEqual(investigation_priority_from_claims(record['candidate_id'], claims)['payment_confidence'], 2)

    def test_independent_buyer_offer_and_labor_or_outsourcing_establish_spend(self):
        for signal in ('labor_spend', 'outsourcing_spend'):
            record, claims = self.convergence()
            claims[2]['spend_signal'] = signal
            result = score(record, claims=claims)
            self.assertEqual(result['payment_confidence'], 3)
            self.assertEqual(result['transaction_evidence_strength'], 0)
            self.assertEqual(result['investigation_priority']['tier'], 'high')
            self.assertFalse(result['commercial_qualification']['qualified'])

    def test_exact_convergence_needs_recurrence_two_buyers_and_independent_origins(self):
        record, claims = self.convergence(exact=True)
        self.assertTrue(score(record, claims=claims)['commercial_qualification']['qualified'])
        for index, field, value in ((3, 'spend_signal', 'public_pricing'),
                                    (2, 'buyer_group', 'buyer-1'),
                                    (2, 'independence_group', 'origin-1'),
                                    (2, 'payment_scope', 'adjacent'),
                                    (2, 'quality', 'C')):
            changed = copy.deepcopy(claims)
            changed[index][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                score(record, claims=changed)

    def test_strong_investigation_with_unknown_commercial_conditions(self):
        record, claims = self.convergence(exact=True)
        for context in ('investigate', 'commercial_validation'):
            record['decision_context'] = context
            for gate in ('distribution', 'feasibility'):
                record[gate]['status'] = 'unknown'
            record['ratings']['accessible_distribution']['value'] = None
            result = score(record, claims=claims)
            self.assertEqual(result['investigation_priority']['tier'], 'high')
            self.assertEqual(result['commercial_qualification']['status'], 'deferred')
            self.assertIsNone(result['ranking_score'])
            self.assertFalse(result['rankable'])

    def test_light_priority_uses_claims_before_judge_and_preserves_counterevidence(self):
        record, claims = self.convergence(exact=True)
        priority = investigation_priority_from_claims(record['candidate_id'], claims)
        self.assertEqual(priority['tier'], 'high')
        self.assertFalse(priority['commercial_recommendation'])
        self.assertIn('distribution', priority['missing_evidence'])
        self.assertEqual(priority['payment_confidence'], 4)
        original = copy.deepcopy(claims)
        self.assertEqual(investigation_priority_from_claims('different-candidate', claims)['tier'], 'unknown')
        self.assertEqual(claims, original)
        claims.append({**claims[1], 'id': 'counter-1', 'relationship': 'contradicts'})
        priority = investigation_priority_from_claims(record['candidate_id'], claims)
        self.assertEqual(priority['tier'], 'review_required')
        self.assertEqual(priority['counterevidence_claim_ids'], ['counter-1'])

    def test_transaction_strength_is_separate_and_invoice_optional(self):
        result = self.run_score()
        self.assertEqual(result['payment_confidence'], 4)
        self.assertEqual(result['transaction_evidence_strength'], 1)
        self.claims[0].update(spend_signal='transaction_record', commercial_terms_explicit=True)
        result = self.run_score()
        self.assertEqual(result['payment_confidence'], 4)
        self.assertEqual(result['transaction_evidence_strength'], 3)

    def test_frozen_v22_policy_keeps_historical_score_contract(self):
        policy = load_config()
        policy['version'] = '2.2'
        policy['hard_gates']['investigate_minimum_payment'] = 3
        result = self.run_score(config=policy)
        self.assertNotIn('commercial_qualification', result)
        self.assertNotIn('investigation_priority', result)
        self.assertEqual(result['ranking_score'], 63)
        self.record['decision_context'] = 'investigate'
        self.record['distribution']['status'] = 'unknown'
        self.assertEqual(self.run_score(config=policy)['outcome'], 'deferred')
        self.record['distribution']['status'] = 'pass'
        self.record['payment']['level'] = 5
        self.claims.append({**self.claims[0], 'id': 'paid-2', 'buyer_group': 'buyer-2',
                            'independence_group': 'origin-2'})
        self.record['payment']['claim_ids'].append('paid-2')
        self.assertTrue(self.run_score(config=policy)['eligible'])

    def test_pricing_tag_cannot_be_upgraded_by_paid_use_flag(self):
        self.claims[0]['spend_signal'] = 'public_pricing'
        with self.assertRaises(ValueError):
            self.run_score()
        priority = investigation_priority_from_claims(self.record['candidate_id'], self.claims)
        self.assertEqual(priority['payment_confidence'], 2)
        self.assertEqual(priority['transaction_evidence_strength'], 0)

    def test_yaml_cli_from_unrelated_directory_and_invalid_exit(self):
        with tempfile.TemporaryDirectory(prefix='score-regression-') as folder:
            path = Path(folder)
            assessment = path/'assessment.yaml'
            claims = path/'claims.jsonl'
            assessment.write_text(yaml.safe_dump(self.record))
            claims.write_text(''.join(json.dumps(c)+'\n' for c in self.claims))
            command = [sys.executable,'-B',str(ROOT/'scripts/score.py'),str(assessment),'--claims',str(claims)]
            process = subprocess.run(command,cwd=folder,text=True,capture_output=True)
            self.assertEqual(process.returncode,0,process.stderr)
            result = json.loads(process.stdout)
            self.assertEqual(result['problem_index'],100)
            self.assertEqual(result['config_hash'],self.run_score()['config_hash'])
            assessment.write_text('{}\n')
            process = subprocess.run(command,cwd=folder,text=True,capture_output=True)
            self.assertEqual(process.returncode,2)
            self.assertEqual(json.loads(process.stdout)['outcome'],'invalid_input')

    def test_duplicate_serialized_keys_are_rejected(self):
        with tempfile.TemporaryDirectory(prefix='score-duplicate-') as folder:
            path = Path(folder)/'duplicate.yaml'
            path.write_text('payment: 3\npayment: 4\n')
            with self.assertRaises(ValueError):
                load_yaml(path)
            path.write_text('{"id":"one","id":"two"}\n')
            with self.assertRaises(ValueError):
                load_claims(path)


if __name__ == '__main__':
    unittest.main()
