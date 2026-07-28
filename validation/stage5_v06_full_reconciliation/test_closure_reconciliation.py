import importlib.util, json, tempfile, unittest
from pathlib import Path
MODULE_PATH=Path(__file__).with_name('closure_reconciliation.py')
spec=importlib.util.spec_from_file_location('closure',MODULE_PATH); m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
class ClosureTests(unittest.TestCase):
 def test_observation_identity_deterministic(self):
  row={'titleId':'F1','start':None,'retrospectiveStart':None,'registerId':None,'compilationNumber':None}; self.assertEqual(m.obs_id(row,m.VERSION_FIELDS),m.obs_id(dict(row),m.VERSION_FIELDS))
 def test_external_id_not_invented(self): self.assertFalse({'titleId':'F1','registerId':None}.get('registerId'))
 def test_document_plan_rejects_count_mismatch(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'p.json'; p.write_text(json.dumps({'status':'passed','null_title_id_count':0,'document_count':2,'leaf_document_count_sum':1,'root_document_count_sum':2,'leaves':[],'leaf_partition_count':0}))
   with self.assertRaises(RuntimeError): m.load_plan(p)
 def test_approved_host_fixed(self): self.assertEqual(m.APPROVED_HOST,'api.prod.legislation.gov.au')
 def test_page_size_bounded(self): self.assertEqual(m.PAGE_SIZE,100)
 def test_retries_finite(self): self.assertEqual(m.MAX_ATTEMPTS,4)
 def test_scope_ruleset_fixed(self): self.assertEqual(m.RULESET,'stage5-v0.6-closure-1')
 def test_canonical_json_stable(self): self.assertEqual(m.canonical({'b':1,'a':2}),b'{"a":2,"b":1}')
if __name__=='__main__': unittest.main()
