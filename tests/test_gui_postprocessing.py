"""GUI-POST acceptance tests: real native short runs and actual Qt/VTK data.

Invalid copies below are display-boundary fault injections, never original data.
Full M50 and relocated-package evidence supplement this fast permanent target.
"""
from __future__ import annotations
import copy
import hashlib
import json
import math
from pathlib import Path
import re
import unittest

import test_gui_workflow as base
import test_gui_themes as themes
from PySide6.QtCore import Qt, QPoint, QRectF
from PySide6.QtGui import QImage, QPainter
from PySide6.QtTest import QTest
from lumafem.contracts import ContractError
from lumafem.gui.formatting import scientific, full_precision
from lumafem.gui.results import _table, _rows, _Curve
from lumafem.gui.results import ResultsPanel
from lumafem.observations import import_csv, compare_curves
from lumafem.storage import ProjectStore

OUT = base.OUT / 'postprocessing'
OUT.mkdir(parents=True, exist_ok=True)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                     ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def inspect_mapper(test, view, step, mode):
    """Inspect real cell arrays independently of the renderer's source values."""
    field = {'concrete_stress': 'stress', 'concrete_strain': 'strain'}[mode]
    source = {f['id']: f for f in step['fibers'] if f['role'] == 'concrete'}
    expected = {f['id'] for s in view._prepared['sections'] for f in s['fibers'] if f['role'] == 'concrete'}
    test.assertEqual(set(source), expected)
    test.assertEqual({s['fiber_id'] for s in view._fiber_samples}, expected, view._fiber_error)
    test.assertEqual(len(view._fiber_samples), len(expected))
    values = [f['state'][field] for f in source.values()]
    test.assertEqual(view._fiber_range, (min(values), max(values)))
    represented = set()
    cells = 0
    for sid, actor in view._fiber_actors.items():
        dataset = actor.GetMapper().GetInput()
        scalars = dataset.GetCellData().GetScalars()
        test.assertEqual(scalars.GetDataTypeAsString(), 'double')
        test.assertEqual(scalars.GetName(), mode)
        ids = view._fiber_cell_sources[sid]
        test.assertEqual(len(ids), dataset.GetNumberOfCells())
        test.assertEqual(scalars.GetNumberOfTuples(), len(ids))
        test.assertEqual(view._actor_ids[actor.GetAddressAsString('')], sid)
        for cell, fid in enumerate(ids):
            test.assertEqual(source[fid]['section_id'], sid)
            test.assertEqual(scalars.GetTuple1(cell), source[fid]['state'][field])
            represented.add(fid)
        cells += len(ids)
    test.assertEqual(represented, expected)
    return {'samples': len(expected), 'cells': cells, 'range': view._fiber_range}


class PostprocessingTests(unittest.TestCase):
    setUpClass = classmethod(base.GuiWorkflowTests.setUpClass.__func__)
    setUp = base.GuiWorkflowTests.setUp
    tearDown = base.GuiWorkflowTests.tearDown
    _close_unexpected_dialog = base.GuiWorkflowTests._close_unexpected_dialog
    pump = base.GuiWorkflowTests.pump
    choose = base.GuiWorkflowTests.choose
    tree = base.GuiWorkflowTests.tree
    field = base.GuiWorkflowTests.field
    replace_text = base.GuiWorkflowTests.replace_text
    run_short_fixture = themes.GuiThemeTests.run_short_fixture

    def camera(self):
        c = self.window.viewport.renderer.GetActiveCamera()
        return c.GetPosition(), c.GetFocalPoint(), c.GetViewUp(), c.GetParallelScale(), c.GetParallelProjection()

    def test_formatter_boundaries_and_rounded_threshold_pair(self):
        for value, expected in ((0, '0.000E+00'), (-0.0, '0.000E+00'), (1234, '1.234E+03'),
                                (-.00025, '-2.500E-04'), (1e-99, '1.000E-99'), (10**400, '1.000E+400')):
            self.assertEqual(scientific(value), expected)
        for value in (None, True, False, '1.0', float('nan'), float('inf'), -float('inf')):
            self.assertEqual(scientific(value, unavailable='NA'), 'NA')
            self.assertEqual(full_precision(value, unavailable='NA'), 'NA')
        for precision in (-1, True, 1.5):
            with self.assertRaises(ValueError): scientific(1, precision=precision)
        threshold = .0035
        below, above = math.nextafter(threshold, -math.inf), math.nextafter(threshold, math.inf)
        self.assertEqual(scientific(below), scientific(above))
        self.assertLess(below, threshold)
        self.assertGreater(above, threshold)
        self.assertNotEqual(full_precision(below), full_precision(above))
        self.assertTrue(scientific(-1e-320).startswith('-'))

    def test_physical_table_retains_raw_values_and_semantic_counts(self):
        t = _table(['sequence', 'P [N]', 'flag', 'identity'], physical_columns=(1,))
        rows = [[152, 0, True, 'run-152'], [269, .0035, False, 'section-1']]
        before = copy.deepcopy(rows)
        _rows(t, rows)
        for language in ('zh_CN', 'en'):
            t.setProperty('result_language', language)
            _rows(t, t.property('source_rows'))
            self.assertEqual(t.item(0, 0).text(), '152')
            self.assertEqual(t.item(0, 1).text(), '0.000E+00')
            self.assertEqual(t.item(1, 1).toolTip(), full_precision(.0035))
            self.assertEqual(t.item(0, 3).text(), 'run-152')
            for r, row in enumerate(rows):
                for c, value in enumerate(row): self.assertEqual(t.item(r, c).data(Qt.ItemDataRole.UserRole), value)
        self.assertEqual(rows, before)
        t.deleteLater()

    def test_actual_saved_field_mappers_source_mapping_and_camera_invariance(self):
        self.run_short_fixture()
        v = self.window.viewport
        step = self.controller.get_step()
        original = digest(step), digest(v._prepared)
        camera = self.camera()
        observations = {}
        for mode in ('concrete_stress', 'concrete_strain'):
            self.choose(v.scalar_mode, mode)
            observations[mode] = inspect_mapper(self, v, step, mode)
            self.assertTrue(v.legend.GetVisibility())
            self.assertEqual(v.legend.GetLabelFormat(), '%.3E')
            self.assertEqual(self.camera(), camera)
            v.export_png(str(OUT / (mode + '-real-vtk.png')))
            self.assertEqual((digest(v._step), digest(v._prepared)), original)
        # Select a real colored section with actual Qt input and VTK picking.
        QTest.mouseClick(v.visibility_button, Qt.MouseButton.LeftButton)
        for key in ('stirrups', 'rebar', 'original', 'events'):
            if v._visible[key].isChecked(): QTest.mouseClick(v._visible[key], Qt.MouseButton.LeftButton)
        QTest.mouseClick(v.visibility_button, Qt.MouseButton.LeftButton)
        QTest.mouseClick(v.front_button, Qt.MouseButton.LeftButton)
        self.pump(150)
        actor = list(v._fiber_actors.values())[len(v._fiber_actors)//2]
        bounds = actor.GetBounds()
        world = [(bounds[i*2] + bounds[i*2+1])/2 for i in range(3)]
        v.renderer.SetWorldPoint(*world, 1); v.renderer.WorldToDisplay()
        x, y, _ = v.renderer.GetDisplayPoint(); rw, rh = v.render_window.GetSize()
        point = QPoint(round(x*v.vtk_widget.width()/rw), round((rh-y)*v.vtk_widget.height()/rh))
        QTest.mouseClick(v.vtk_widget, Qt.MouseButton.LeftButton, pos=point); self.pump()
        event = v.interactor.GetEventPosition()
        self.assertTrue(v.picker.Pick(event[0], event[1], 0, v.renderer))
        picked = v.picker.GetActor(); sid = v._actor_ids[picked.GetAddressAsString('')]
        self.assertIn(sid, v._fiber_actors)
        self.assertEqual(v._selection.get('section_id') or v._selection.get('object_id'), sid)
        (OUT/'mapper-and-selection.json').write_text(json.dumps(observations, indent=2))

    def test_incomplete_foreign_duplicate_and_nonfinite_frames_clear_old_cloud(self):
        self.run_short_fixture()
        v = self.window.viewport
        valid = self.controller.get_step()
        self.choose(v.scalar_mode, 'concrete_stress')
        variants = []
        bad = copy.deepcopy(valid); bad['fibers'] = bad['fibers'][1:]; variants.append(bad)
        bad = copy.deepcopy(valid); bad['fibers'].append(copy.deepcopy(bad['fibers'][0])); variants.append(bad)
        bad = copy.deepcopy(valid); next(f for f in bad['fibers'] if f['role']=='concrete')['state']['stress'] += 1; variants.append(bad)
        bad = copy.deepcopy(valid); bad['sections'][0]['id'] += '-foreign'; variants.append(bad)
        for bad in variants:
            v.set_step(valid); self.assertTrue(v._fiber_actors)
            v.set_step(bad); self.pump(20)
            self.assertFalse(v._fiber_actors)
            self.assertIsNone(v._fiber_range)
            self.assertFalse(v.legend.GetVisibility())
            self.assertTrue(v._fiber_error)
        for kind in ('nonfinite', 'identity', 'shape'):
            bad = copy.deepcopy(valid)
            if kind == 'nonfinite': bad['P'] = float('nan')
            elif kind == 'identity': bad['identity']['run_id'] += '-foreign'
            else: bad['displacement'] = []
            v.set_step(valid)
            with self.assertRaises(ContractError): v.set_step(bad)
            self.assertIsNone(v._step)
            self.assertFalse(v._fiber_actors)
            self.assertFalse(v.legend.GetVisibility())
        v.set_step(None)
        self.assertFalse(v._fiber_actors)

    def test_real_zero_constant_field_and_user_beam_visibility(self):
        self.run_short_fixture()
        v = self.window.viewport
        initial = self.controller.get_step(0)
        v.set_step(initial)
        self.choose(v.scalar_mode, 'concrete_stress')
        inspect_mapper(self, v, initial, 'concrete_stress')
        self.assertEqual(v._fiber_range, (0.0, 0.0))
        self.assertTrue(v.legend.GetUseCustomLabels())
        self.assertEqual(v.legend.GetCustomLabels().GetNumberOfTuples(), 1)
        self.assertEqual(v.legend.GetCustomLabels().GetValue(0), 0)
        self.assertIn('constant', v.legend.GetTitle())
        self.assertEqual(len({v.lookup.GetTableValue(i) for i in range(v.lookup.GetNumberOfTableValues())}), 1)
        QTest.mouseClick(v.visibility_button, Qt.MouseButton.LeftButton)
        box = v._visible['beam']
        QTest.mouseClick(box, Qt.MouseButton.LeftButton, pos=QPoint(8, box.height()//2))
        self.assertFalse(box.isChecked(), 'Actual checkbox indicator click must toggle beam visibility')
        QTest.mouseClick(v.visibility_button, Qt.MouseButton.LeftButton)
        self.assertFalse(v.legend.GetVisibility()); self.assertFalse(v._fiber_actors)
        self.choose(v.scalar_mode, 'concrete_strain')
        self.assertFalse(v._visible['beam'].isChecked()); self.assertFalse(v.legend.GetVisibility())

    def test_xyz_real_camera_resize_hide_show_and_close_lifecycle(self):
        w = self.window; v = w.viewport
        QTest.keyClick(w, Qt.Key.Key_F6); self.pump(100)
        marker = v.orientation_marker
        self.assertTrue(marker.GetEnabled())
        self.assertFalse(marker.GetInteractive())
        self.assertEqual(marker.GetInteractor(), v.interactor)
        self.assertFalse(v.orientation_axes.GetPickable())
        for prop in (v.orientation_axes.GetXAxisTipProperty(), v.orientation_axes.GetYAxisTipProperty(), v.orientation_axes.GetZAxisTipProperty()):
            self.assertGreater(max(prop.GetColor()), .7)
        renderer_count = v.render_window.GetRenderers().GetNumberOfItems()
        cam = v.renderer.GetActiveCamera(); cam.Azimuth(21); cam.Elevation(13); v.render_window.Render()
        renderers = v.render_window.GetRenderers()
        overlay = [renderers.GetItemAsObject(i) for i in range(renderers.GetNumberOfItems())
                   if renderers.GetItemAsObject(i).HasViewProp(v.orientation_axes)]
        self.assertEqual(len(overlay), 1)
        overlay_camera = overlay[0].GetActiveCamera()
        for a, b in zip(cam.GetDirectionOfProjection(), overlay_camera.GetDirectionOfProjection()): self.assertAlmostEqual(a, b, places=9)
        for width, height in ((1280,800),(1500,980)):
            w.resize(width,height); w.restore_layout(); self.pump()
            box = marker.GetViewport(); rw, rh = v.render_window.GetSize()
            self.assertAlmostEqual((box[2]-box[0])*rw, (box[3]-box[1])*rh, delta=1)
            self.assertGreaterEqual(v.annotation.GetPosition()[0], box[2]*rw)
            QTest.mouseClick(v.front_button, Qt.MouseButton.LeftButton); self.pump()
            QTest.mouseClick(v.fit_button, Qt.MouseButton.LeftButton); self.pump()
        for _ in range(2):
            v.hide(); self.pump(20); self.assertFalse(marker.GetEnabled())
            v.show(); self.pump(20); self.assertTrue(marker.GetEnabled())
        self.assertEqual(v.render_window.GetRenderers().GetNumberOfItems(), renderer_count)
        v.export_png(str(OUT/'xyz-lifecycle-real-vtk.png'))
        vtk_errors = []
        marker.AddObserver('ErrorEvent', lambda *args: vtk_errors.append('marker ErrorEvent'))
        v.close(); self.pump(20)
        self.assertFalse(marker.GetEnabled()); self.assertIsNone(marker.GetInteractor())
        self.assertIsNone(marker.GetOrientationMarker())
        self.assertEqual(vtk_errors, [], 'Normal viewport close must not emit a VTK marker lifecycle error')

    def test_actual_curve_scientific_tick_rectangles_fit_painted_image(self):
        class RecordingPainter(QPainter):
            def __init__(self, image):
                super().__init__(image)
                self.labels = []
            def drawText(self, *args):
                if isinstance(args[0], QRectF) and isinstance(args[-1], str) and re.fullmatch(r'-?\d\.\d{3}E[+-]\d+', args[-1]):
                    self.labels.append((QRectF(args[0]), args[-1]))
                return super().drawText(*args)
        curve = _Curve()
        curve.records = [{'w': w, 'P': p, 'identity': {'step_id': str(n)}, 'sequence': n}
                         for n, (w,p) in enumerate(((-1e-12,-3e99),(0,0),(1e-12,3e99)))]
        original = copy.deepcopy(curve.records)
        for width,height in ((400,220),(987,300)):
            image = QImage(width,height,QImage.Format.Format_ARGB32)
            painter = RecordingPainter(image)
            curve.draw(painter,QRectF(0,0,width,height))
            labels = painter.labels
            painter.end()
            self.assertGreaterEqual(len(labels),4)
            for rectangle,text in labels:
                self.assertGreaterEqual(rectangle.left(),0,text)
                self.assertLessEqual(rectangle.right(),width,text)
                self.assertGreaterEqual(rectangle.top(),0,text)
                self.assertLessEqual(rectangle.bottom(),height,text)
            image.save(str(OUT/f'scientific-curve-{width}x{height}.png'))
        self.assertEqual(curve.records,original)
        curve.deleteLater()

    def test_real_saved_search_domain_wire_table_summary_and_retranslation(self):
        """The actual descriptor/window key drives formatting, not test columns."""
        self.run_short_fixture()
        saved = OUT/'semantic-domain-saved.lumafem'
        self.assertTrue(self.controller.save_as(str(saved)), self.errors)
        source_sha = hashlib.sha256(saved.read_bytes()).hexdigest()
        run_id = self.controller.current_run['prepared']['identity']['run_id']
        sequence = self.controller.get_step()['sequence']
        with ProjectStore(saved, readonly=True) as store:
            prepared = store.load_run(run_id, include_steps=False)['prepared']
            step = store.load_step(run_id, sequence)
        self.assertEqual(self.window.results._prepared, prepared)
        descriptor = prepared['compression_criterion']
        self.assertIn('search_domain_mm', descriptor)
        domains = [descriptor['search_domain_mm']]
        domains += [step['gauge'][status]['search_domain_mm'] for status in ('current','historical')]
        domains += [value['search_domain_mm'] for value in step['localization'].values()
                    if isinstance(value, dict) and 'search_domain_mm' in value]
        original = copy.deepcopy((prepared, step, domains, self.controller.document))
        observed = []
        for language in ('zh_CN','en','zh_CN'):
            self.choose(self.window.language_combo, language)
            table = self.window.results.gauge_table
            raw_rows = table.property('source_rows')
            indices = [index for index,row in enumerate(raw_rows) if row[1]=='search_domain_mm']
            self.assertEqual(len(indices), len(domains))
            self.assertEqual([raw_rows[index][2] for index in indices], domains)
            texts = []
            for index,domain in zip(indices,domains):
                self.assertEqual(table.item(index,1).text(), 'search_domain_mm [mm]')
                text='['+', '.join(scientific(value) for value in domain)+']'
                exact='['+', '.join(full_precision(value) for value in domain)+']'
                self.assertEqual(table.item(index,2).text(), text)
                self.assertEqual(table.item(index,2).toolTip(), exact)
                self.assertEqual(table.item(index,2).data(Qt.ItemDataRole.UserRole), domain)
                texts.append(text)
            line='search_domain_mm [mm]: ['+', '.join(scientific(v) for v in domains[0])+']'
            exact_line='search_domain_mm [mm]: ['+', '.join(full_precision(v) for v in domains[0])+']'
            self.assertIn(line,self.window.results.summary_text.toPlainText())
            self.assertIn(exact_line,self.window.results.summary_text.toolTip())
            self.assertEqual(self.window.results._prepared,prepared)
            self.assertEqual(self.controller.get_step(),step)
            self.assertEqual(self.controller.document,original[3])
            observed.append({'language':language,'domains':texts,'summary':line,'summary_full_precision':exact_line})
        self.assertEqual((prepared,step,domains),original[:3])
        self.assertEqual(hashlib.sha256(saved.read_bytes()).hexdigest(),source_sha)
        (OUT/'semantic-search-domain.json').write_text(json.dumps(dict(status='PASS',source=str(saved),source_sha256=source_sha,
             prepared_hash=prepared['prepared_hash'],sequence=sequence,raw_domains=domains,observed=observed),indent=2))

    def test_real_csv_dotted_channel_metric_units_and_raw_source_preservation(self):
        """Real import+comparison wire drives the production Qt metric renderer."""
        folder=OUT/'dotted-semantic-inputs';folder.mkdir(exist_ok=False)
        paths=[]
        for name,shift in (('experiment',0),('simulation',1)):
            path=folder/(name+'.csv')
            lines=['w,P,e,d']+[f'{x},{100*x+5*shift},{.001*x+.0001*shift},{.1*x+.02*shift}' for x in range(4)]
            path.write_text('\n'.join(lines)+'\n',encoding='utf-8')
            metadata=dict(synthetic=True,category='synthetic',purpose='GUI semantic regression only; not experiment',
                          csv_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),count=7,flag=False)
            path.with_suffix('.metadata.json').write_text(json.dumps(metadata),encoding='utf-8')
            paths += [path,path.with_suffix('.metadata.json')]
        mapping={'synthetic':True,'load_source':'total','channels':{
            'P':{'column':'P','quantity':'force','unit':'N'},'w':{'column':'w','quantity':'displacement','unit':'mm'},
            'strain.mid':{'column':'e','quantity':'strain','unit':'1'},
            'disp.mid.gauge':{'column':'d','quantity':'displacement','unit':'mm'}}}
        dataset=import_csv(str(folder/'experiment.csv'),mapping)
        simulation=import_csv(str(folder/'simulation.csv'),mapping)
        self.assertEqual(dataset['units']['strain.mid'],'1')
        self.assertEqual(dataset['units']['disp.mid.gauge'],'mm')
        self.assertTrue(dataset['synthetic'])
        self.assertEqual(dataset['physical_validation'],'UNVERIFIED_SYNTHETIC_DATA')
        options={'channel_scales':{'strain.mid':.01,'disp.mid.gauge':2.0},
                 'noise_scales':{'strain.mid':1e-6,'disp.mid.gauge':.001},
                 'raw_count':7,'raw_flag':False,'raw_label':'source.unmodified'}
        comparison=compare_curves(simulation['rows'],dataset,options)
        self.assertEqual(set(comparison['channels']),{'strain.mid','disp.mid.gauge'})
        original=copy.deepcopy((dataset,simulation,mapping,options,comparison))
        hashes={str(path):hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}
        panel=ResultsPanel();panel.show();self.pump(20);observed=[]
        try:
            for language in ('zh_CN','en','zh_CN'):
                panel.retranslate_ui(language)
                rows=[];physical_cells=[]
                # The same production recursion/semantic metadata used by
                # _refresh_comparison consumes a real compare_curves result.
                # No test assigns physical_columns or invents metric values.
                panel._metric_rows(rows,dataset['name'],dataset['source']['category'],comparison,
                                   units=dataset['units'],physical_cells=physical_cells)
                _rows(panel.comparison_table,rows,physical_cells=tuple(physical_cells))
                self.assertEqual(panel.comparison_table.property('physical_columns'),[])
                found={row[2]:index for index,row in enumerate(rows)}
                labels=[]
                for channel,unit in (('strain.mid','1'),('disp.mid.gauge','mm')):
                    for metric in ('maximum_absolute_error','scale','noise_scale'):
                        key=f'channels.{channel}.{metric} [{unit}]'
                        self.assertIn(key,found)
                        index=found[key];raw=comparison['channels'][channel][metric]
                        item=panel.comparison_table.item(index,3)
                        self.assertEqual(item.text(),scientific(raw))
                        self.assertEqual(item.toolTip(),full_precision(raw))
                        self.assertEqual(item.data(Qt.ItemDataRole.UserRole),raw)
                        labels.append(key)
                self.assertIn('options.raw_count',found)
                self.assertEqual(panel.comparison_table.item(found['options.raw_count'],3).text(),'7')
                self.assertIs(panel.comparison_table.item(found['options.raw_flag'],3).data(Qt.ItemDataRole.UserRole),False)
                self.assertEqual(panel.comparison_table.item(found['options.raw_label'],3).text(),'source.unmodified')
                self.assertEqual(panel.comparison_table.item(found['sample_count'],3).text(),'200')
                self.assertEqual(panel.comparison_table.item(found['source.sha256'],3).text(),dataset['source']['sha256'])
                self.assertNotIn('E',panel.comparison_table.item(found['options.channel_scales.strain.mid'],3).text())
                self.assertEqual((dataset,simulation,mapping,options,comparison),original)
                self.assertEqual(panel.comparison_table.property('source_rows'),rows)
                observed.append(dict(language=language,metric_labels=labels))
        finally:panel.deleteLater();self.pump(20)
        for path in paths:self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(),hashes[str(path)])
        (OUT/'semantic-dotted-units.json').write_text(json.dumps(dict(status='PASS',source_hashes=hashes,
            source_mapping=mapping,raw_metadata=dataset['metadata'],raw_options=options,observed=observed,
            synthetic_only=True,comparison_input_and_raw_values_unchanged=True),indent=2))

    def test_actual_mainwindow_fields_matrix_exact_tables_csv_and_save_reopen(self):
        self.run_short_fixture()
        w = self.window; v = w.viewport
        self.choose(w.module_combo, 'results')
        self.choose(v.scalar_mode, 'concrete_strain')
        before_step = digest(v._step); before_prepared = digest(v._prepared); camera = self.camera()
        csv1, csv2 = OUT/'before-presentation.csv', OUT/'after-presentation.csv'
        self.assertTrue(self.controller.export_data(str(csv1), 'curves'))
        records = []
        for language in ('zh_CN','en'):
            self.choose(w.language_combo, language)
            for theme in ('dark','light'):
                self.choose(w.theme_combo, theme)
                for width,height in ((1280,800),(1500,980)):
                    w.resize(width,height); w.restore_layout(); self.pump(100)
                    self.assertEqual((w.width(),w.height()), (width,height))
                    self.assertEqual((digest(v._step),digest(v._prepared)), (before_step,before_prepared))
                    self.assertEqual(self.camera(),camera)
                    self.assertTrue(v._fiber_actors)
                    self.assertGreaterEqual(v.vtk_widget.height(),220)
                    name=f'main-{language}-{theme}-{width}x{height}'
                    v.export_png(str(OUT/(name+'-true-vtk.png')))
                    w.grab().save(str(OUT/(name+'-client-layout-only.png')))
                    records.append({'language':language,'theme':theme,'window':[w.width(),w.height()], 'vtk':[v.vtk_widget.width(),v.vtk_widget.height()]})
        table=w.results.points_table
        self.assertGreater(table.rowCount(),0)
        for row in range(table.rowCount()):
            for column in (2,3,4,5,6):
                item=table.item(row,column); raw=item.data(Qt.ItemDataRole.UserRole)
                if isinstance(raw,(int,float)) and not isinstance(raw,bool):
                    self.assertEqual(item.text(),scientific(raw)); self.assertEqual(item.toolTip(),full_precision(raw))
        self.assertTrue(self.controller.export_data(str(csv2),'curves'))
        self.assertEqual(csv1.read_bytes(),csv2.read_bytes())
        path=Path(self.folder.name)/'post-saved.lumafem'
        self.assertTrue(self.controller.save_as(str(path)), self.errors)
        before_file=hashlib.sha256(path.read_bytes()).hexdigest()
        self.assertTrue(self.controller.open_project(str(path)),self.errors)
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(),before_file)
        self.choose(v.scalar_mode,'concrete_stress')
        inspect_mapper(self,v,self.controller.get_step(),'concrete_stress')
        (OUT/'main-matrix.json').write_text(json.dumps(records,indent=2))
        self.assertEqual(self.dialogs,[])


if __name__ == '__main__':
    unittest.main(verbosity=2)
