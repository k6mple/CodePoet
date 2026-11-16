"""True Chrome PNG calibration data plus HTTP persistence/failure boundaries."""
import hashlib
import json
import unittest
from pathlib import Path
from unittest.mock import patch
from PIL import Image
from app.engine.assessment import assess
from app.engine.validate import validate_ir
from app.schemas.ir import DrawingIR
import test_quality as quality_helpers
from test_quality import drawn_png

ROOT=Path(__file__).resolve().parents[2]
DATA=ROOT/'shared/demo/quality'


class CalibrationTests(unittest.TestCase):
    def test_twelve_labels_six_bad_and_corrected_outputs(self):
        dataset=json.loads((DATA/'cases.json').read_text(encoding='utf-8'))
        self.assertEqual(len(dataset['cases']),12)
        self.assertEqual(sum(case['bad'] for case in dataset['cases']),6)
        manifest=json.loads((DATA/'png/manifest.json').read_text(encoding='utf-8'))
        for frame in manifest['frames']:
            self.assertEqual(hashlib.sha256((DATA/'png'/frame['file']).read_bytes()).hexdigest(),frame['sha256'])
        for case in dataset['cases']:
            for stage in ['before']+(['after'] if 'afterIR' in case else []):
                with self.subTest(case=case['id'],stage=stage):
                    ir=DrawingIR.model_validate(case['ir' if stage=='before' else 'afterIR'])
                    times=[0,ir.durationMs//2,ir.durationMs] if ir.durationMs else [0]
                    images={}
                    for time in times:
                        with Image.open(DATA/'png'/f"{case['id']}-{stage}-{time}.png") as image:
                            images[time]=image.convert('RGBA')
                    try:
                        report=assess(ir,images,{'paletteDecision':{'value':','.join(case['palette'])}},validate_ir(ir).model_dump())
                    finally:
                        for image in images.values():image.close()
                    actual={metric.name:metric.status for metric in report.metrics}
                    self.assertEqual(actual,case['expected'] if stage=='before' else {name:'pass' for name in actual})
                    self.assertTrue(all(metric.score is None for metric in report.metrics))
                    self.assertLessEqual(sum(len(metric.evidence) for metric in report.metrics),100)
                    self.assertLessEqual(len(report.recommendations),20)
                    ids={node.id for node in ir.nodes}
                    for metric in report.metrics:
                        for item in metric.evidence:
                            self.assertTrue(set(item.nodeIds)<=ids)
                            self.assertTrue(item.timeMs is None or 0<=item.timeMs<=ir.durationMs)
                    if stage=='before' and case.get('expectedEvidenceTimeMs') is not None:
                        self.assertTrue(any(item.timeMs==case['expectedEvidenceTimeMs'] for item in report.recommendations))

    def test_no_historical_palette_is_not_applicable(self):
        case=json.loads((DATA/'cases.json').read_text(encoding='utf-8'))['cases'][0]
        ir=DrawingIR.model_validate(case['ir'])
        with Image.open(DATA/'png/normal-separated-before-0.png') as image:
            report=assess(ir,{0:image},{},validate_ir(ir).model_dump())
        color=next(metric for metric in report.metrics if metric.name=='colorSemantic')
        self.assertEqual(color.status,'not_applicable')
        self.assertIsNone(color.score)

    def test_parameter_failure_and_missing_validation_are_not_passes(self):
        case=json.loads((DATA/'cases.json').read_text(encoding='utf-8'))['cases'][0]
        ir=DrawingIR.model_validate(case['ir'])
        with Image.open(DATA/'png/normal-separated-before-0.png') as image:
            for validation,expected in [({'parameterValid':False},'fail'),({},'unavailable')]:
                report=assess(ir,{0:image},{},validation)
                self.assertEqual(report.metrics[0].status,expected)
                self.assertEqual(report.status,expected)

    def test_high_color_diversity_exhausts_bounded_color_analysis_explicitly(self):
        import copy
        import random
        case=json.loads((DATA/'cases.json').read_text(encoding='utf-8'))['cases'][0]
        data=copy.deepcopy(case['ir'])
        data['nodes']=[copy.deepcopy(data['nodes'][0]) for _ in range(3)]
        for index,node in enumerate(data['nodes']):
            node['id']=f'n{index}';node['geometry']['r']=1000;node['style']['opacity']=0.3+index*0.1
        image=Image.frombytes('RGB',(800,600),random.Random(42).randbytes(800*600*3))
        try:
            report=assess(DrawingIR.model_validate(data),{0:image},{'paletteDecision':{'value':case['palette']}},{'parameterValid':True})
        finally:image.close()
        color=next(metric for metric in report.metrics if metric.name=='colorSemantic')
        self.assertEqual(color.status,'unavailable')
        self.assertIsNone(color.value)


class AssessmentHttpTests(unittest.TestCase):
    setUp=quality_helpers.QualityTests.setUp
    _register=quality_helpers.QualityTests._register
    _seed_generation=quality_helpers.QualityTests._seed_generation
    _create_job=quality_helpers.QualityTests._create_job
    _upload=quality_helpers.QualityTests._upload

    def test_ir_and_subgraph_keep_historical_palette_and_rule_snapshots(self):
        from contextlib import closing
        import sqlite3
        self._register('q1_inherit')
        base=self._seed_generation()
        source={'kind':'semantic','baseGenerationId':None,'matchedEntities':[],
                'matchedRules':[{'ruleId':'r','version':1,'content':{'effects':{'palette':['#3B6D11']}}}],
                'paletteDecision':{'value':'#3B6D11','ruleIds':['r'],'reason':'historical'},
                'engineVersion':'old-engine','ontologyVersion':1,'policyRevision':1}
        with closing(sqlite3.connect(self.settings.database_path)) as db:
            db.execute('INSERT INTO generations(id,owner_id,mode,backend,template_version_id,params_json,seed,ir_json,ir_hash,provenance_json,validation_report_json,warnings_json,unpublished_source,request_id,created_at) '
                       'SELECT ?,owner_id,mode,backend,template_version_id,params_json,seed,ir_json,ir_hash,?,validation_report_json,warnings_json,unpublished_source,request_id,created_at FROM generations WHERE id=?',
                       ('gen-q1-palette',json.dumps(source),base));db.commit()
        base='gen-q1-palette'
        ir=quality_helpers.ir_doc()
        edited=self.client.post('/api/v1/generations',json={'mode':'ir','baseGenerationId':base,'ir':ir,'backend':'canvas2d'},headers={'X-CSRF-Token':self.csrf})
        self.assertEqual(edited.status_code,201,edited.text)
        subgraph=self.client.post(f'/api/v1/generations/{base}/subgraph',json={'rootNodeId':'c1','replacementNodes':ir['nodes'],'viewport':ir['viewport']},headers={'X-CSRF-Token':self.csrf})
        self.assertEqual(subgraph.status_code,201,subgraph.text)
        for generation in [edited.json(),subgraph.json()['generation']]:
            self.assertEqual(generation['provenance']['paletteDecision'],source['paletteDecision'])
            self.assertEqual(generation['provenance']['matchedRules'],source['matchedRules'])
            self.assertEqual(generation['provenance']['baseGenerationId'],base)
    def test_actual_upload_persists_report_and_legacy_reports_still_parse(self):
        self._register('q1_persist')
        generation=self._seed_generation()
        job=self._create_job([0],generation).json()
        uploaded=self._upload(job['id'],0,drawn_png()).json()
        assessment=uploaded['qualityReport']['assessment']
        self.assertEqual(assessment['algorithmVersion'],'codepoet-quality-1.0.0')
        self.assertEqual(assessment['evaluatedTimesMs'],[0])
        with patch('app.engine.assessment.VERSION','changed-after-upload'):
            fetched=self.client.get(f"/api/v1/render-jobs/{job['id']}").json()
        self.assertEqual(fetched['qualityReport'],uploaded['qualityReport'])
        from app.schemas.works import QualityReport
        legacy={key:value for key,value in uploaded['qualityReport'].items() if key!='assessment'}
        self.assertIsNone(QualityReport.model_validate(legacy).assessment)

    def test_algorithm_exception_preserves_successful_upload_and_is_explicit(self):
        self._register('q1_failure')
        generation=self._seed_generation()
        job=self._create_job([0],generation).json()
        with patch('app.services.quality.assess',side_effect=RuntimeError('internal-test-message')):
            with self.assertLogs('app.services.quality',level='ERROR'):
                response=self._upload(job['id'],0,drawn_png())
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(response.json()['status'],'succeeded')
        self.assertEqual(response.json()['qualityReport']['assessment']['status'],'unavailable')
        self.assertNotIn('internal-test-message',response.text)

    def test_unreadable_one_of_three_frames_is_not_a_partial_pass(self):
        from contextlib import closing
        import sqlite3
        from app.services import uploads
        self._register('q1_partial')
        generation=self._seed_generation(4000)
        job=self._create_job([0,2000,4000],generation).json()
        for time in [0,2000]:
            self.assertEqual(self._upload(job['id'],time,drawn_png()).status_code,200)
        with closing(sqlite3.connect(self.settings.database_path)) as db:
            path=db.execute('SELECT storage_path FROM artifacts WHERE render_job_id=? AND time_ms=0',(job['id'],)).fetchone()[0]
        target=uploads.artifact_path(self.settings,path)
        original=Image.open
        def read(frame,*args,**kwargs):
            if isinstance(frame,Path) and frame==target:
                raise OSError('test unreadable frame')
            return original(frame,*args,**kwargs)
        with patch('app.services.quality.Image.open',side_effect=read):
            response=self._upload(job['id'],4000,drawn_png())
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(response.json()['status'],'succeeded')
        self.assertEqual(response.json()['qualityReport']['assessment']['status'],'unavailable')
