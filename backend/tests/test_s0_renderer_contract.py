"""S0-A1: actual versions, configured capabilities and existing generation regressions."""
from contextlib import closing

from test_capabilities import _Base, API, template_ir, metadata


class RendererContractTests(_Base):
    def test_clone_preserves_declarations_and_unimplemented_candidates_are_rejected(self):
        version_id = self._template_version(['canvas2d', 'css'])
        source = self.client.get(f'{API}/template-versions/{version_id}').json()
        cloned = self.client.post(f"{API}/templates/{source['templateId']}/versions", json={
            'fromVersionId':version_id,
        }, headers={'X-CSRF-Token':self.csrf})
        self.assertEqual(cloned.status_code, 201, cloned.text)
        self.assertEqual(cloned.json()['supportedBackends'], ['canvas2d','css'])
        for declarations in [['webgl'], ['turtle'], ['css','css']]:
            rejected = self.client.patch(f'{API}/template-versions/{cloned.json()["id"]}', json={
                'expectedRevision':cloned.json()['revision'],'supportedBackends':declarations,
            }, headers={'X-CSRF-Token':self.csrf})
            self.assertEqual(rejected.status_code, 422, rejected.text)

    def test_public_filter_uses_published_backend_snapshot(self):
        import sqlite3
        version_id = self._template_version(['canvas2d','css'])
        version = self.client.get(f'{API}/template-versions/{version_id}').json()
        # A stale container declaration must not hide a newly published CSS version.
        with closing(sqlite3.connect(self.settings.database_path)) as db:
            db.execute("UPDATE template_versions SET status='published' WHERE id=?", (version_id,))
            db.execute("UPDATE templates SET published_version_id=?, supported_backends_json='[\"canvas2d\"]' WHERE id=?",
                       (version_id, version['templateId']))
            db.commit()
        listed = self.client.get(f'{API}/templates?backend=css').json()['items']
        self.assertEqual([item['id'] for item in listed], [version['templateId']])

    def _job(self, generation, *, backend=None, version=None):
        backend = backend or generation['backend']
        return self.client.post(f'{API}/render-jobs', json={
            'generationId': generation['id'], 'backend': backend, 'timesMs': [0],
            'executionMode': 'browser_upload',
            'rendererVersion': version or ('codepoet-css-1.0.0' if backend == 'css' else 'codepoet-canvas-1.0.0'),
            'clientEnvironment': {'userAgent': 'contract-test', 'deviceScaleFactor': 1},
        }, headers={'X-CSRF-Token': self.csrf})

    def test_capability_report_follows_instance_configuration(self):
        self.app.state.settings.enabled_backends = 'css,webgl,turtle'
        caps = self.client.get(f'{API}/engine/capabilities').json()['backends']
        states = {item['backend']: item for item in caps}
        self.assertFalse(states['canvas2d']['available'])
        self.assertTrue(states['css']['available'])
        self.assertEqual(states['css']['rendererVersion'], 'codepoet-css-1.0.0')
        self.assertEqual(states['css']['executionModes'], ['browser_upload'])
        self.assertFalse(states['webgl']['available'])
        self.assertFalse(states['turtle']['available'])

    def test_wrong_and_legacy_alias_versions_are_rejected(self):
        generation = self._generate_canvas()
        for version in ['p0-03.1', 'canvas-1.0.0', 'unknown', 'codepoet-css-1.0.0']:
            response = self._job(generation, version=version)
            self.assertEqual(response.status_code, 422, response.text)
            self.assertEqual(response.json()['error']['code'], 'UNSUPPORTED_CAPABILITY')
        self.assertEqual(self._job(generation).status_code, 202)

    def test_generation_backend_cannot_be_relabelled_on_job(self):
        generation = self._generate_canvas()
        self.app.state.settings.enabled_backends = 'canvas2d,css'
        response = self._job(generation, backend='css')
        self.assertEqual(response.status_code, 422, response.text)

    def test_css_candidate_has_a_real_versioned_browser_job(self):
        base = self._generate_canvas()
        self.app.state.settings.enabled_backends = 'canvas2d,css'
        response = self.client.post(f'{API}/generations', json={
            'mode': 'ir', 'baseGenerationId': base['id'], 'backend': 'css', 'ir': template_ir(),
        }, headers={'X-CSRF-Token': self.csrf})
        self.assertEqual(response.status_code, 201, response.text)
        generation = response.json()
        self.assertEqual(self._job(generation, version='codepoet-canvas-1.0.0').status_code, 422)
        self.assertEqual(self._job(generation).status_code, 202)

    def test_unknown_users_still_get_404_before_version_or_capability(self):
        generation = self._generate_canvas()
        self.csrf = self._register('other_contract_user')
        self.app.state.settings.enabled_backends = 'css'
        self.assertEqual(self._job(generation, version='unknown').status_code, 404)
        response = self.client.post(f'{API}/generations', json={
            'mode':'ir', 'baseGenerationId':generation['id'], 'backend':'css','ir':template_ir(),
        }, headers={'X-CSRF-Token': self.csrf})
        self.assertEqual(response.status_code, 404, response.text)

    def test_parameter_template_and_out_of_bounds_warning_survive(self):
        template = self.client.post(f'{API}/templates', json={'metadata':metadata()}, headers={'X-CSRF-Token': self.csrf}).json()
        created = self.client.post(f"{API}/templates/{template['id']}/versions", json={
            'metadata':metadata(), 'irTemplate':template_ir(), 'parameterSchema':[
                {'name':'radius','label':'Radius','description':'','type':'integer','default':60,'min':10,'max':100,'step':1}],
            'bindings':[{'parameter':'radius','nodeId':'c1','property':'geometry.r'}], 'constraints':[], 'supportedBackends':['canvas2d'],
        }, headers={'X-CSRF-Token': self.csrf})
        self.assertEqual(created.status_code, 201, created.text)
        generated = self.client.post(f'{API}/generations', json={
            'mode':'template','templateVersionId':created.json()['id'],'params':{'radius':70},'backend':'canvas2d',
        }, headers={'X-CSRF-Token': self.csrf})
        self.assertEqual(generated.status_code, 201, generated.text)
        self.assertEqual(generated.json()['ir']['nodes'][0]['geometry']['r'], 70)
        document = template_ir()
        document['nodes'][0]['geometry']['cx'] = 799
        response = self.client.post(f'{API}/generations', json={
            'mode':'ir','baseGenerationId':generated.json()['id'],'backend':'canvas2d','ir':document,
        }, headers={'X-CSRF-Token': self.csrf})
        self.assertEqual(response.status_code, 201, response.text)
        self.assertTrue(any(issue['code'] == 'IR_OUT_OF_BOUNDS' for issue in response.json()['warnings']))

    def test_server_creation_stays_explicitly_unavailable(self):
        generation = self._generate_canvas()
        response = self.client.post(f'{API}/render-jobs', json={
            'generationId':generation['id'],'backend':'turtle','timesMs':[0],'executionMode':'server',
        }, headers={'X-CSRF-Token':self.csrf})
        self.assertEqual(response.status_code, 422, response.text)
