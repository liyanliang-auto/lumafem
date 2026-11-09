"""R1 real native/CLI/storage boundary; all mutations use dedicated copies.

The SQL fixtures below are constructed diagnostics, NOT historical databases:
pre-repair storage already rejected malformed provenance. Frozen JSON is the
actual pre-repair producer's sealed output, never regenerated or relabelled.
"""
from __future__ import annotations
import copy, hashlib, json, os, sqlite3, subprocess, sys, tempfile, unittest
from pathlib import Path
from uuid import uuid4
from contextlib import closing

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'python'), str(ROOT/'build/python')]
from lumafem import native
from lumafem.contracts import ContractError, KERNEL_VERSION
from lumafem.storage import ProjectStore

FIX = Path(os.environ.get('LUMAFEM_R1_FIXTURES', ROOT/'specs/20261108-155946-4num'))
CLI = Path(os.environ.get('LUMAFEM_R1_CLI', ROOT/'build/bin/lumafem_cli.exe'))
KEYS = ('category', 'reference', 'quality', 'version')

def read(path): return json.loads(Path(path).read_text(encoding='utf-8-sig'))
def sha(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def base(): return read(ROOT/'development_doc/runtime/CR-NUM-002/NC0.json')
def prepared(d):
    r = native.prepare(d)
    if not r['ok']: raise AssertionError(r['diagnostics'])
    return r['prepared']
def encoded(d):
    p = json.dumps(d, sort_keys=True, separators=(',', ':'), ensure_ascii=False)
    return p, hashlib.sha256(p.encode('utf-8')).hexdigest()

class NumberLexeme(str): pass
def canonical_lexemes(v):
    """Retain original CLI float lexemes; verify our hash against its real hash."""
    if isinstance(v,NumberLexeme): return str(v)
    if isinstance(v,dict): return '{'+','.join(json.dumps(k,ensure_ascii=False)+':'+canonical_lexemes(v[k]) for k in sorted(v))+'}'
    if isinstance(v,list): return '['+','.join(canonical_lexemes(x) for x in v)+']'
    return json.dumps(v,ensure_ascii=False,separators=(',',':'))

class R1ProvenanceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.out = Path(self.tmp.name)
        self.cli_count = 0

    def cli(self, payload, mode='prepare'):
        self.cli_count += 1
        source = self.out/f'input-{self.cli_count}.json'
        output = self.out/f'cli-{self.cli_count}'
        source.write_text(json.dumps(payload), encoding='utf-8')
        r = subprocess.run([str(CLI), '--input', str(source), '--output', str(output), '--mode', mode], capture_output=True, text=True)
        return r, output

    def reject(self, d, location, cli=True):
        before = copy.deepcopy(d)
        r = native.prepare(d)
        self.assertFalse(r['ok'], r)
        self.assertFalse(r.get('prepared'), r)
        self.assertTrue(any(location in e.get('field', '') and e['severity']=='error' for e in r['diagnostics']), r)
        file = self.out/('reject-'+uuid4().hex+'.lumafem')
        with ProjectStore(file) as store:
            original = store._check().execute('SELECT * FROM revisions').fetchall()
            with self.assertRaises(ContractError) as caught: store.save_document(d)
            self.assertTrue(location in caught.exception.diagnostic.get('field', ''), caught.exception.diagnostic)
            self.assertEqual(store._check().execute('SELECT * FROM revisions').fetchall(), original)
        if cli:
            process, output = self.cli(d)
            self.assertNotEqual(process.returncode, 0)
            if (output/'preparation.json').exists():
                self.assertFalse(read(output/'preparation.json')['ok'])
            self.assertFalse((output/'prepared.json').exists())
        self.assertEqual(d, before, 'validation rewrote caller input')

    def test_exact_keys_shapes_types_and_locations_both_modes(self):
        for mode in ('physical_gauge_mean_v1','route_c_point_v1'):
            valid = base(); valid['model']['compression_criterion']['mode'] = mode
            variants = []
            for key in KEYS:
                d = copy.deepcopy(valid); del d['model']['compression_criterion']['source'][key]
                variants.append(('missing-'+key,d,'.'+key))
                for value in (None, 0, True, [], {}):
                    d = copy.deepcopy(valid); d['model']['compression_criterion']['source'][key] = value
                    variants.append(('type-'+key+repr(value),d,'.'+key))
            for value in (None, [], '', 1, True):
                d = copy.deepcopy(valid); d['model']['compression_criterion']['source'] = value
                variants.append(('shape'+repr(value),d,''))
            d = copy.deepcopy(valid); d['model']['compression_criterion']['source']['unexpected'] = 'x'
            variants.append(('extra',d,'.unexpected'))
            for value in ('','unknown'):
                d = copy.deepcopy(valid); d['model']['compression_criterion']['source']['category'] = value
                variants.append(('category'+value,d,'.category'))
            for name,d,suffix in variants:
                with self.subTest(mode=mode,case=name): self.reject(d,'compression_criterion.source'+suffix)

    def test_complete_categories_empty_boundaries_and_both_mode_roundtrip(self):
        for mode in ('physical_gauge_mean_v1','route_c_point_v1'):
            for category in ('assumption','paper','measured','calibrated','synthetic','derived'):
                with self.subTest(mode=mode,category=category):
                    d = base(); d['model']['compression_criterion']['mode'] = mode
                    d['model']['compression_criterion']['source'] = dict(category=category,reference='',quality='',version='')
                    p = prepared(d)
                    self.assertEqual(p['document'],d)
                    self.assertEqual(native.verify_prepared(p,for_analysis=True)['prepared'],p)
                    file = self.out/(mode+category+'.lumafem')
                    with ProjectStore(file) as s: s.begin_run(p)
                    before = sha(file)
                    with ProjectStore(file,readonly=True) as s:
                        self.assertEqual(s.load_document(),d)
                        self.assertEqual(s.load_run()['prepared'],p)
                        self.assertFalse(s.load_run()['analysis_read_only'])
                    self.assertEqual(sha(file),before)
        r,output = self.cli(base())
        self.assertEqual(r.returncode,0,r.stderr)
        p = read(output/'prepared.json')
        self.assertEqual(p['document'],base())

    def test_all_actual_pre_repair_frozen_payloads_hash_and_policy_preserved(self):
        manifest = read(FIX/'baseline-r1-fixtures.json')
        self.assertTrue(manifest['actual_pre_repair'])
        for name,digest in manifest['fixtures_sha256'].items(): self.assertEqual(sha(FIX/name),digest,name)
        self.assertEqual(len(manifest['cases']),7)
        for case in manifest['cases']:
            with self.subTest(case=case['name']):
                p = read(FIX/case['prepared']); d = read(FIX/case['source'])
                original = copy.deepcopy(p)
                for analysis in (False,True):
                    result = native.verify_prepared(p,for_analysis=analysis)
                    self.assertTrue(result['ok'],result)
                    self.assertFalse(result['analysis_read_only'])
                    self.assertEqual(result['prepared'],original)
                self.assertEqual(p,original)
                r,output = self.cli(p,'view')
                self.assertEqual(r.returncode,0,r.stderr)
                self.assertEqual(read(output/'prepared.json'),p)
                self.assertEqual(native.prepare(d)['ok'],case['name']=='complete-default')
                # Also verify each independently prepared original CLI transport.
                cp = read(FIX/('baseline-r1-fixtures-final/'+case['name']+'-cli-prepare/prepared.json'))
                self.assertEqual(native.verify_prepared(cp)['prepared'],cp)

    def test_frozen_corruption_and_unknown_producer_never_bypass_verification(self):
        p = read(FIX/'baseline-r1-fixtures-final/extra-key-prepared.json')
        mutations = []
        q = copy.deepcopy(p); q['document']['model']['compression_criterion']['source']['reference'] = 'tampered'; mutations.append(q)
        q = copy.deepcopy(p); q['prepared_hash'] = '0'*len(q['prepared_hash']); mutations.append(q)
        q = copy.deepcopy(p); q['kernel_version'] = '99.0-unknown'; mutations.append(q)
        q = copy.deepcopy(p); q['compression_criterion']['threshold'] *= 2; mutations.append(q)
        for q in mutations:
            with self.subTest(keys=q.keys()):
                self.assertFalse(native.verify_prepared(q)['ok'])
                r,output = self.cli(q,'view'); self.assertNotEqual(r.returncode,0)
                self.assertFalse((output/'prepared.json').exists())
        raw = json.loads((FIX/'baseline-r1-fixtures-final/extra-key-cli-prepare/prepared.json').read_text(encoding='utf-8'),parse_float=NumberLexeme)
        claimed = raw.pop('prepared_hash')
        self.assertEqual(hashlib.sha256(canonical_lexemes(raw).encode()).hexdigest(),claimed)
        for kind in ('document-source','descriptor','unknown-producer'):
            q=copy.deepcopy(raw)
            if kind=='document-source': q['document']['model']['compression_criterion']['source']['reference']='altered-known-member'
            elif kind=='descriptor': q['compression_criterion']['threshold']=0.004
            else: q['kernel_version']='99.0-unknown'
            q['prepared_hash']=hashlib.sha256(canonical_lexemes(q).encode()).hexdigest()
            q=json.loads(canonical_lexemes(q))
            with self.subTest(valid_outer_hash_tamper=kind):
                self.assertFalse(native.verify_prepared(q)['ok'])
                r,output=self.cli(q,'view'); self.assertNotEqual(r.returncode,0)
                self.assertFalse((output/'prepared.json').exists())

    def diagnostic_database(self,p,path):
        # Construct diagnostic SQL from the REAL frozen transport. No claim
        # that old storage produced this combination; its new write rejects it.
        clean = copy.deepcopy(p['document'])
        clean['model']['compression_criterion']['source'] = base()['model']['compression_criterion']['source']
        valid = prepared(clean)
        with ProjectStore(path) as s: s.begin_run(valid)
        dp,dh = encoded(p['document']); pp,ph = encoded(p)
        with closing(sqlite3.connect(path)) as db, db:
            db.execute('UPDATE revisions SET payload=?,sha256=?',(dp,dh))
            db.execute('UPDATE runs SET run_id=?,snapshot_id=?,prepared=?,prepared_sha256=?,status=?',
                       (p['identity']['run_id'],p['identity']['snapshot_id'],pp,ph,'running'))

    def test_constructed_sql_requires_exact_native_verified_matching_run(self):
        for name in ('missing-category','missing-reference','missing-quality','missing-version','extra-key','sparse-category-only'):
            with self.subTest(name=name):
                p = read(FIX/f'baseline-r1-fixtures-final/{name}-prepared.json')
                file = self.out/(name+'.lumafem'); self.diagnostic_database(p,file)
                original = sha(file)
                with ProjectStore(file,readonly=True) as s:
                    self.assertEqual(s.load_document(),p['document'])
                    self.assertEqual(s.load_run()['prepared'],p)
                    copyfile = self.out/(name+'-copy.lumafem'); s.save_as(copyfile)
                    with ProjectStore(copyfile,readonly=True) as c:
                        self.assertEqual(c.load_document(),p['document']); self.assertEqual(c.load_run()['prepared'],p)
                self.assertEqual(sha(file),original)
                # Rechecks exact document equality, not just identity/hash.
                changed = copy.deepcopy(p['document']); changed['model']['compression_criterion']['source']['reference']='different'
                payload,digest = encoded(changed)
                with closing(sqlite3.connect(file)) as db, db: db.execute('UPDATE revisions SET payload=?,sha256=?',(payload,digest))
                with ProjectStore(file,readonly=True) as s:
                    with self.assertRaises(ContractError): s.load_document()
                # Restore the original malformed source before removing its
                # only qualifying frozen context (reference may be missing).
                payload,digest = encoded(p['document'])
                with closing(sqlite3.connect(file)) as db, db:
                    db.execute('UPDATE revisions SET payload=?,sha256=?',(payload,digest))
                    db.execute('DELETE FROM runs')
                with ProjectStore(file,readonly=True) as s:
                    with self.assertRaises(ContractError): s.load_document()

    def test_frozen_context_cannot_leak_to_new_writes_saveas_or_recovery(self):
        p = read(FIX/'baseline-r1-fixtures-final/extra-key-prepared.json')
        file = self.out/'constructed.lumafem'; self.diagnostic_database(p,file)
        target = self.out/'existing-target'; target.write_bytes(b'previous-target-preserved')
        with ProjectStore(file) as s:
            before = s._check().execute('SELECT * FROM revisions').fetchall()
            actions = (lambda:s.save_document(p['document']),lambda:s.begin_run(p),
                       lambda:s.save_as(target,document=p['document']),lambda:s.write_recovery(p['document'],target))
            for action in actions:
                with self.assertRaises(ContractError): action()
                self.assertEqual(target.read_bytes(),b'previous-target-preserved')
                self.assertEqual(s._check().execute('SELECT * FROM revisions').fetchall(),before)

    def test_absent_criterion_and_unrelated_existing_guards(self):
        d = base(); del d['model']['compression_criterion']
        p = prepared(d)
        self.assertNotIn('compression_criterion',p['document']['model'])
        self.assertEqual(p['compression_criterion']['mode'],'route_c_point_v1')
        with ProjectStore(self.out/'legacy-source.lumafem') as s:
            s.begin_run(p); self.assertEqual(s.load_document(),d)
        for kind in ('nonfinite','unsupported-schema','outer','mode','domain'):
            q = base()
            if kind=='nonfinite': q['model']['compression_criterion']['threshold']=float('inf')
            elif kind=='unsupported-schema': q['schema_version']=999
            elif kind=='outer': q['model']['compression_criterion']=[]
            elif kind=='mode': q['model']['compression_criterion']['mode']='unknown'
            else: q['model']['compression_criterion']['gauge_length_mm']=1e9
            with self.subTest(kind=kind):
                if kind=='nonfinite':
                    with self.assertRaises(ContractError) as caught: native.prepare(q)
                    self.assertEqual(caught.exception.diagnostic['code'],'nonfinite')
                else: self.assertFalse(native.prepare(q)['ok'])
                with self.assertRaises(ContractError): ProjectStore._document(q)

if __name__=='__main__': unittest.main(verbosity=2)

