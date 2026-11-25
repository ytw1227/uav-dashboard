"""Reuse exact sealed comparison samples, never interpolate or hide bad pins."""
from copy import deepcopy
import unittest

try:
    import numpy as np
except ImportError:
    np = None

from nicegui_dashboard.team_evaluation import restore_sealed_scores, score_reuse_record


@unittest.skipIf(np is None, 'Score restoration is tested with the research NumPy environment')
class SealedScoreTests(unittest.TestCase):
    def setUp(self):
        self.row = dict(duration_s=14.5,spec={"region":"fixture"},deployment="v8")
        self.arrays = dict(network=np.ones(30,dtype=bool),plan_frames=np.arange(1,30,dtype=np.int32))
        values = [None]+[float(np.float32(i/30)) for i in range(1,16)]+[None]*14
        self.prior = dict(evaluation=dict(metadata=dict(self.row),series=dict(
            times=(np.arange(30)*.5).tolist(),scores=values,valid=[v is not None for v in values])))

    def test_samples_and_terminal_mask_are_exact(self):
        before=deepcopy(self.prior)
        restore_sealed_scores(self.prior,self.row,self.arrays)
        expected=np.asarray(self.prior['evaluation']['series']['scores'][1:16],dtype=np.float32)
        np.testing.assert_array_equal(self.arrays['comparison_scores'][:15],expected)
        self.assertTrue(np.isnan(self.arrays['comparison_scores'][15:]).all())
        self.assertEqual(self.prior,before)
        self.assertTrue(self.arrays['network'].all())

    def test_missing_eligible_frame_is_rejected(self):
        self.arrays['plan_frames']=self.arrays['plan_frames'][1:]
        with self.assertRaises(ValueError):restore_sealed_scores(self.prior,self.row,self.arrays)

    def test_null_eligible_score_is_rejected(self):
        self.prior['evaluation']['series']['scores'][2]=None
        self.prior['evaluation']['series']['valid'][2]=False
        with self.assertRaises(ValueError):restore_sealed_scores(self.prior,self.row,self.arrays)

    def test_changed_duration_is_rejected(self):
        self.row['duration_s']=14
        with self.assertRaises(ValueError):restore_sealed_scores(self.prior,self.row,self.arrays)

    def test_precision_loss_is_rejected(self):
        self.prior['evaluation']['series']['scores'][2]=1/3
        with self.assertRaises(ValueError):restore_sealed_scores(self.prior,self.row,self.arrays)

    def test_unknown_commit_keeps_original_path(self):
        nominal=dict(team_commit='future',model_sha256='other')
        self.assertIsNone(score_reuse_record(None,nominal,None,None,{},None,None,None,None,{}))


if __name__=='__main__':unittest.main()
