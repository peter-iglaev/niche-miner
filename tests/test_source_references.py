"""Canonical source references must resolve to archived, hash-checked run bytes."""
import hashlib
import tempfile
import unittest
from pathlib import Path
from scripts import sources
from scripts.validate import source_reference_errors


class SourceReferenceTests(unittest.TestCase):
    def test_archived_pdf_reference_verifies_original_bytes_and_detects_tampering(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            folder=root/'runs/synthetic-sources'
            folder.mkdir(parents=True)
            raw=b'%PDF-1.7\n\xff\xfe\x00'
            meta=sources.archive('synthetic-sources','synthetic','https://example.test/doc.pdf',body=raw,status=200,content_type='application/pdf',root=root)
            record={'claims':[{'source_id':meta['source_id']}], 'search_log':[]}
            self.assertEqual(source_reference_errors(folder,record),[])
            (folder/meta['archive_path']).write_bytes(raw+b'changed')
            self.assertTrue(any('SHA/length' in e for e in source_reference_errors(folder,record)))

    def test_tmp_paths_and_missing_archives_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder=Path(tmp)/'runs/ref-run'
            folder.mkdir(parents=True)
            for ref in ['/tmp/raw.pdf','../../raw.pdf','work/raw.pdf','sources/missing.pdf']:
                with self.subTest(ref=ref):
                    record={'claims':[{'metrics':[{'raw_response_ref':ref,'collection_status':'collected'}]}]}
                    self.assertTrue(source_reference_errors(folder,record))

    def test_search_failure_can_cite_metadata_without_claiming_collected_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            folder=root/'runs/ref-run'
            folder.mkdir(parents=True)
            meta=sources.archive('ref-run','synthetic','https://example.test',error='offline transport fixture',root=root)
            record={'search_log':[{'raw_response_ref':meta['source_id'],'status':'failed'}]}
            self.assertEqual(source_reference_errors(folder,record),[])
            record['search_log'][0]['status']='results'
            self.assertTrue(source_reference_errors(folder,record))

    def test_new_external_claim_requires_canonical_source_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / 'runs/ref-run'
            folder.mkdir(parents=True)
            record = {'claims': [{'id': 'external-1', 'source_type': 'pricing_page'}]}
            self.assertTrue(any('source_id is required' in error
                                for error in source_reference_errors(folder, record, strict=True)))
            synthetic = {'claims': [{'id': 'fixture-1', 'source_type': 'synthetic_fixture'}]}
            self.assertEqual([], source_reference_errors(folder, synthetic, strict=True))

    def test_new_provider_attempt_requires_stable_source_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / 'runs/ref-run'
            folder.mkdir(parents=True)
            record = {'search_log': [{'query': 'synthetic query', 'status': 'failed',
                                      'raw_response_ref': None}]}
            self.assertTrue(any('source_id is required' in error
                                for error in source_reference_errors(folder, record, strict=True)))
