"""Offline founder profile and classification contracts."""
import copy
import tempfile
import unittest
from pathlib import Path
from scripts import app
from scripts.founder import EXAMPLE, load_profile as load_real, validate_profile, familiarity, audit, candidate_errors


def load_profile():
    """Tests run on the public example, never on the owner's private profile."""
    return load_real(EXAMPLE)


class FounderTests(unittest.TestCase):
    def test_invalid_profile_is_rejected(self):
        for path, value in [(('constraints','weekly_available_hours'),-1),
                            (('experience','confirmed_industries'),'all technology'),
                            (('confirmed_preferences','low_public_visibility'),'yes')]:
            profile=load_profile()
            profile[path[0]][path[1]]=value
            self.assertTrue(validate_profile(profile))
            with tempfile.TemporaryDirectory() as tmp:
                with self.assertRaisesRegex(ValueError,'founder_profile'):
                    app.create_run('bad-profile','synthetic','targeted','stop',config={'founder_profile':profile},root=Path(tmp))
                self.assertFalse((Path(tmp)/'runs/bad-profile').exists())

    def test_four_states_do_not_require_unfamiliar_industry_inventory(self):
        profile=load_profile()
        self.assertEqual(familiarity('devops',profile),'confirmed_familiar')
        self.assertEqual(familiarity('construction_trades',profile),'outside_confirmed_familiarity')
        self.assertEqual(familiarity(None,profile),'unknown')
        profile['experience']['unfamiliar_industries']=['construction_trades']
        self.assertEqual(familiarity('construction_trades',profile),'confirmed_unfamiliar')
        profile['experience']['unfamiliar_industries']=['devops']
        self.assertTrue(validate_profile(profile))

    def test_exploration_reports_outside_separately(self):
        result=audit([{'id':'a','industry':'devops'},{'id':'b','industry':'construction_trades'}],load_profile())
        self.assertEqual(result['counts']['confirmed_unfamiliar'],0)
        self.assertEqual(result['counts']['outside_confirmed_familiarity'],1)
        self.assertEqual(result['exploration_count'],1)

    def test_new_run_freezes_founder_profile(self):
        with tempfile.TemporaryDirectory() as tmp:
            profile=load_profile()
            app.create_run('profile-snapshot','synthetic','targeted','stop',config={'founder_profile':profile},root=Path(tmp))
            profile['experience']['confirmed_industries'].append('construction_trades')
            from scripts.validate import read_data
            snapshot=read_data(Path(tmp)/'runs/profile-snapshot/config.json')['founder_profile']
            self.assertNotIn('construction_trades',snapshot['experience']['confirmed_industries'])

    def test_missing_profile_points_to_the_workspace_setup(self):
        with self.assertRaisesRegex(ValueError, 'workspace.py link'):
            load_real(Path('/nonexistent/founder-profile.yaml'))

    def test_candidate_boolean_cannot_call_outside_familiarity_unfamiliar(self):
        candidate = {'industry': 'construction_trades',
                     'founder_familiarity': 'outside_confirmed_familiarity',
                     'category_flags': {'unfamiliar_to_founder': None}}
        self.assertEqual([], candidate_errors(candidate, load_profile(), require_explicit=True))
        candidate['category_flags']['unfamiliar_to_founder'] = True
        self.assertTrue(candidate_errors(candidate, load_profile(), require_explicit=True))
