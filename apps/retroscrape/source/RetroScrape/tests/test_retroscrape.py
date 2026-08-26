import json, os, sys, tempfile, threading, time, unittest
from unittest.mock import patch
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]; sys.path.insert(0,str(ROOT))
from app.thegamesdb import TheGamesDBScraper
from app.config import load_config
from app.storage import durable_json, write_bytes_if_changed
from app.reports import issue_lines
from app.xmame import ArcadeOrganiser
from app.ui import GUI, LEFT, RIGHT, UP, DOWN, Y, B, L1, R1

class RetroScrapeTests(unittest.TestCase):
 def cfg(self,r):
  return {'selected_system':'snes','show_missing_systems':False,'rom_roots':[str(r/'Roms')],'bios_roots':[],
   'xmame':{'executable':'/missing','rom_dir':str(r/'Roms/MAME'),'xml_path':str(r/'mame.xml'),'expected_build':'x'},
   'databases':{'url':'https://example.invalid/db.zip','timeout':5},
   'thegamesdb':{'enabled':True,'api_key_file':str(r/'key'),'timeout':5,'cache_days':30,'download_boxart':True,'max_hash_mib':128,'reserve_requests':0,'max_retries':0,'backoff_seconds':1,'max_backoff_seconds':5}}
 def scraper(self,r): return TheGamesDBScraper(r,self.cfg(r),{'name':'SNES'},'snes',r/'Roms/SFC',lambda *a:None,threading.Event())
 def test_cache_statistics_and_expired_cleanup(self):
  with tempfile.TemporaryDirectory() as d:
   r=Path(d); s=self.scraper(r); fresh=s.cache/'fresh.json'; old=s.cache/'nested/old.json'; old.parent.mkdir(parents=True); fresh.write_text('{}'); old.write_text('{}')
   expired=time.time()-31*86400; os.utime(old,(expired,expired))
   status=s.cache_status(); self.assertEqual(status['cache_entries'],2); self.assertEqual(status['cache_expired'],1); self.assertGreaterEqual(status['cache_bytes'],4)
   self.assertEqual(s.cleanup_expired_cache(),1); self.assertTrue(fresh.is_file()); self.assertFalse(old.exists())
 def test_report_history_latest_first_and_navigation(self):
  with tempfile.TemporaryDirectory() as d:
   r=Path(d); data=r/'data'; data.mkdir(); a=data/'report-a.json'; b=data/'report-b.json'
   a.write_text(json.dumps({'type':'dat','system':'snes','results':[],'summary':{'OK':1}})); b.write_text(json.dumps({'type':'dat','system':'snes','results':[],'summary':{'OK':2}}))
   os.utime(a,(100,100)); os.utime(b,(200,200)); gui=GUI(r,self.cfg(r),{'snes':{'name':'SNES'}}); gui.show_last_report()
   self.assertEqual(gui.report_path,b); gui.button(LEFT); self.assertEqual(gui.report_path,a); gui.button(RIGHT); self.assertEqual(gui.report_path,b)
 def test_scrape_only_summary_uses_scraper_counts(self):
  with tempfile.TemporaryDirectory() as d:
   r=Path(d); data=r/'data'; data.mkdir(); p=data/'report-s.json'; p.write_text(json.dumps({'type':'scrape','system':'snes','results':[],'summary':{},'scraping':{'matched':7,'not_found':2,'lookup_failures':1,'media_pending_details':{'./x.sfc':{}}}}))
   gui=GUI(r,self.cfg(r),{'snes':{'name':'SNES'}}); gui.show_last_report(); self.assertEqual(gui.summary,{'MATCHED':7,'NOT_FOUND':2,'LOOKUP_FAILED':1,'ARTWORK_PENDING':1})
 def test_database_status_reads_manifest(self):
  with tempfile.TemporaryDirectory() as d:
   r=Path(d); current=r/'dats/current'; current.mkdir(parents=True); (current/'.retroscrape-manifest.json').write_text(json.dumps({'downloaded_at':'2026-08-24T11:00:00+0200','dat_count':42}))
   gui=GUI(r,self.cfg(r),{'snes':{'name':'SNES'}}); gui.show_database_status(); self.assertIn(('Downloaded','2026-08-24T11:00:00+0200'),gui.detail_rows); self.assertIn(('Source DAT files',42),gui.detail_rows)
 def test_advanced_uses_shared_scrolling(self):
  with tempfile.TemporaryDirectory() as d:
   r=Path(d); gui=GUI(r,self.cfg(r),{'snes':{'name':'SNES'}}); gui.page='advanced'; gui.advanced=['A','B','C','D','E','F']; gui.selection=4; gui.navigate(4); self.assertEqual(gui.selection,5); self.assertEqual(gui.menu_scroll,1)
 def test_capability_colours_and_wrapped_logs_present(self):
  source=(ROOT/'app/ui.py').read_text(); self.assertIn('capability_colours',source); self.assertIn('visual_logs',source); self.assertIn('self._wrap_text(str(value)',source)
 def test_single_dedicated_test_suite(self):
  files=sorted(path.name for path in (ROOT/'tests').glob('test_*.py')); self.assertEqual(files,['test_retroscrape.py'])


class BugfixRegressionTests(unittest.TestCase):
 def cfg(self,r):
  return {'rom_roots':[str(r/'Roms')],'bios_roots':[],'selected_system':'snes','show_missing_systems':False,
  'xmame':{'executable':'/missing','rom_dir':str(r/'Roms/MAME'),'xml_path':str(r/'mame.xml'),'expected_build':'x'},
  'databases':{'url':'x','timeout':5},'thegamesdb':{'enabled':True,'api_key_file':str(r/'key'),'timeout':5,'cache_days':30,'download_boxart':True,'max_hash_mib':128,'reserve_requests':0,'max_retries':0,'backoff_seconds':1,'max_backoff_seconds':5}}
 def scraper(self,r):
  return TheGamesDBScraper(r,self.cfg(r),{'name':'SNES'},'snes',r/'Roms/SFC',lambda *a:None,threading.Event())
 def test_platform_requires_exact_configured_alias(self):
  with tempfile.TemporaryDirectory() as d:
   r=Path(d); s=self.scraper(r)
   s._request=lambda *a,**k:{'data':{'platforms':{'1':{'id':1,'name':'Super Nintendo (SNES)'},'2':{'id':2,'name':'Super Nintendo Entertainment System Plus'}}}}
   match=s._platform_id(); self.assertEqual(match['id'],'1'); self.assertEqual(match['match'],'exact')
 def test_fuzzy_platform_is_not_accepted(self):
  with tempfile.TemporaryDirectory() as d:
   r=Path(d); s=self.scraper(r)
   s._request=lambda *a,**k:{'data':{'platforms':{'2':{'id':2,'name':'Super Nintendo Entertainment System Plus'}}}}
   with self.assertRaisesRegex(RuntimeError,'No exact TheGamesDB platform match'):
    s._platform_id()
 def test_artwork_target_mirrors_rom_subdirectories(self):
  with tempfile.TemporaryDirectory() as d:
   r=Path(d); s=self.scraper(r); rom=s.rom_dir/'Japan'/'Game.sfc'; rom.parent.mkdir(parents=True)
   self.assertEqual(s._artwork_target(rom,'.jpg'),s.rom_dir/'images/Japan/Game.jpg')
   usa=s.rom_dir/'USA'/'Game.sfc'; self.assertNotEqual(s._artwork_target(rom,'.jpg'),s._artwork_target(usa,'.jpg'))
 def test_first_run_is_silent_but_creates_config(self):
  with tempfile.TemporaryDirectory() as d:
   r=Path(d); c=r/'config'; c.mkdir(); defaults=self.cfg(r); defaults['config_version']=6
   (c/'defaults.json').write_text(json.dumps(defaults))
   config,warning=load_config(r); self.assertIsNone(warning); self.assertTrue((c/'config.json').is_file())
 def test_invalid_config_is_backed_up_and_warned(self):
  with tempfile.TemporaryDirectory() as d:
   r=Path(d); c=r/'config'; c.mkdir(); defaults=self.cfg(r); defaults['config_version']=6
   (c/'defaults.json').write_text(json.dumps(defaults)); (c/'config.json').write_text('{bad')
   config,warning=load_config(r); self.assertIn('Invalid configuration',warning); self.assertEqual(len(list(c.glob('config.invalid-*.json'))),1)
 def test_lookup_failures_appear_in_issue_lines(self):
  payload={'results':[],'scraping':{'lookup_failure_details':{'./bad.sfc':'timeout'},'media_pending_details':{'./art.sfc':{}}}}
  lines=issue_lines(payload); self.assertIn('LOOKUP_FAILED: ./bad.sfc | timeout',lines); self.assertIn('ARTWORK_PENDING: ./art.sfc',lines)
 def test_playlist_path_identity_is_shared(self):
  with tempfile.TemporaryDirectory() as d:
   r=Path(d); o=ArcadeOrganiser(r,self.cfg(r),lambda *a:None,threading.Event()); folder=r/'Roms/MAME'; folder.mkdir(parents=True); game=folder/'game.zip'; game.write_bytes(b'x')
   equivalent=folder/'sub/../game.zip'
   self.assertEqual(o._path_identity(game),o._path_identity(equivalent))
 def test_apply_accepts_equivalent_playlist_path_and_rewrites(self):
  with tempfile.TemporaryDirectory() as d:
   r=Path(d); source=r/'Roms/MAME/game.zip'; source.parent.mkdir(parents=True); source.write_bytes(b'x'); target=r/'Roms/FBNEO/game.zip'
   playlist=r/'playlists/FBNeo.lpl'; playlist.parent.mkdir(); playlist.write_text(json.dumps({'items':[{'path':str(source.parent/'sub/../game.zip'),'db_name':'FBNeo - Arcade Games.lpl'}]}))
   o=ArcadeOrganiser(r,self.cfg(r),lambda *a:None,threading.Event())
   o._retroarch_process=lambda:(None,None); o._retroarch_paths=lambda:(r/'retroarch.cfg',r/'retroarch',playlist.parent)
   payload={'results':[{'status':'MOVE_RECOMMENDED','path':str(source),'target':str(target)}]}
   result=o.apply(payload); self.assertEqual(result['moved'],1); self.assertTrue(target.is_file())
   saved=json.loads(playlist.read_text()); self.assertEqual(saved['items'][0]['path'],str(target))



class Reliability137Tests(unittest.TestCase):
 def cfg(self,r):
  return {'selected_system':'snes','show_missing_systems':False,'rom_roots':[str(r/'Roms')],'bios_roots':[], 'xmame':{'executable':'/missing','rom_dir':str(r/'Roms/MAME'),'xml_path':str(r/'mame.xml'),'expected_build':'x'}, 'databases':{'url':'x','timeout':5}, 'thegamesdb':{'enabled':True,'api_key_file':str(r/'key'),'timeout':5,'cache_days':30,'download_boxart':True,'max_hash_mib':128,'reserve_requests':0,'max_retries':0,'backoff_seconds':1,'max_backoff_seconds':5}}
 def scraper(self,r): return TheGamesDBScraper(r,self.cfg(r),{'name':'SNES'},'snes',r/'Roms/SFC',lambda *a:None,threading.Event())
 def test_bios_duplicate_keeps_canonical_copy(self):
  from app.generic import BiosValidator
  results=[{'path':'wrong/fw.bin','source':'/bios','status':'BADPATH','expected':'fw.bin'},{'path':'fw.bin','source':'/bios','status':'VERIFIED','expected':'fw.bin'}]
  BiosValidator._mark_duplicates(results)
  self.assertEqual(results[1]['status'],'VERIFIED'); self.assertEqual(results[0]['status'],'DUPLICATE'); self.assertEqual(results[0]['duplicate_of'],'fw.bin'); self.assertEqual(results[0]['duplicate_of_source'],'/bios')
 def test_name_lookup_rejects_non_exact_natural_result(self):
  with tempfile.TemporaryDirectory() as d:
   r=Path(d); rom=r/'Roms/SFC/Game.sfc'; rom.parent.mkdir(parents=True); rom.write_bytes(b'x'); scraper=self.scraper(r)
   scraper._rom_identity=lambda path:None; scraper._request=lambda *a,**k:{'data':{'games':[{'id':1,'game_title':'Game 2'}]}}
   game,payload,method=scraper._lookup(rom,'6'); self.assertIsNone(game); self.assertEqual(method,'name')
 def test_no_scrapeable_files_skip_provider_setup(self):
  with tempfile.TemporaryDirectory() as d:
   r=Path(d); rom=r/'Roms/SFC'; rom.mkdir(parents=True); (rom/'games.7z').write_bytes(b'x'); (r/'key').write_text('key'); scraper=self.scraper(r)
   scraper._platform_id=lambda: self.fail('provider setup should not run')
   result=scraper.run(); self.assertEqual(result['total'],0); self.assertIn('no scrapeable files',result['status'])
 def test_cache_hit_uses_checkpoint_not_direct_save(self):
  with tempfile.TemporaryDirectory() as d:
   r=Path(d); scraper=self.scraper(r); cached=scraper.cache/'hit.json'; cached.parent.mkdir(parents=True); cached.write_text('{}'); calls=[]
   scraper._checkpoint_state=lambda force=False:calls.append(force); scraper._save_state=lambda:self.fail('direct save used')
   self.assertEqual(scraper._request('/x',{},'hit.json'),{}); self.assertEqual(calls,[False])
 def test_unmatched_details_are_issues(self):
  lines=issue_lines({'results':[],'scraping':{'not_found_details':['./Unknown.sfc']}}); self.assertEqual(lines,['NO_MATCH: ./Unknown.sfc'])
 def test_metadata_names_are_ordered_and_unique(self):
  self.assertEqual(TheGamesDBScraper._unique_names(['Sega','sega','Nintendo','Sega']),['Sega','Nintendo'])
 def test_137_ui_and_tf1_cleanup_guards(self):
  ui=(ROOT/'app/ui.py').read_text(); tg=(ROOT/'app/thegamesdb.py').read_text(); xm=(ROOT/'app/xmame.py').read_text()
  self.assertIn('visual_issues',ui); self.assertIn('progress_width',ui); self.assertIn('Installed payload',ui)
  self.assertNotIn('"rating", "image"',tg); self.assertNotIn('Close RetroArch before moving ROMs',xm); self.assertNotIn('Path("/proc")',tg)


class UI138RegressionTests(unittest.TestCase):
 def cfg(self, root):
  return {'selected_system':'snes','show_missing_systems':False,'rom_roots':[str(root/'Roms')],'bios_roots':[], 'xmame':{'executable':'/missing','rom_dir':str(root/'Roms/MAME'),'xml_path':str(root/'mame.xml'),'expected_build':'x'}, 'databases':{'url':'x','timeout':5}, 'thegamesdb':{'enabled':True,'api_key_file':str(root/'key'),'timeout':5,'cache_days':30,'download_boxart':True,'max_hash_mib':128,'reserve_requests':0,'max_retries':0,'backoff_seconds':1,'max_backoff_seconds':5}}
 def gui(self, root): return GUI(root,self.cfg(root),{'snes':{'name':'SNES'},'gb':{'name':'Game Boy'}})
 def test_details_scroll_reaches_ninth_row(self):
  with tempfile.TemporaryDirectory() as d:
   gui=self.gui(Path(d)); gui.page='details'; gui.detail_rows=[('Row %d'%n,n) for n in range(9)]; gui.navigate(DOWN)
   self.assertEqual(gui.detail_scroll,1); self.assertEqual(gui.detail_rows[gui.detail_scroll+7][0],'Row 8')
 def test_header_follows_report_and_page_context(self):
  with tempfile.TemporaryDirectory() as d:
   gui=self.gui(Path(d)); gui.page='report'; gui.report_data={'system':'gb'}; self.assertEqual(gui._header_context(),'Game Boy')
   gui.page='api_status'; self.assertEqual(gui._header_context(),'TheGamesDB'); gui.page='details'; gui.detail_title='Database Status'; self.assertEqual(gui._header_context(),'Databases')
 def test_summary_orders_errors_and_reports_hidden_count(self):
  with tempfile.TemporaryDirectory() as d:
   gui=self.gui(Path(d)); gui.summary={'OK':4,'READ_ERROR':1,'UNMATCHED':2,'MISNAMED':3,'DUPLICATE':1,'INCOMPLETE':1,'ARTWORK_PENDING':1}
   self.assertEqual(gui._summary_items()[0][0],'READ_ERROR'); gui.page='results'; image=gui.draw(); self.assertEqual(image.size,(640,480))
 def test_api_cleanup_success_is_not_an_error(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d); gui=self.gui(root); gui.page='api_status'
   with patch.object(TheGamesDBScraper,'cleanup_expired_cache',return_value=3), patch.object(TheGamesDBScraper,'cached_allowance_status',return_value={}): gui.button(Y)
   self.assertEqual(gui.api_status_message,'Expired cache removed: 3'); self.assertEqual(gui.api_status_error,'')
 def test_menu_selection_is_preserved(self):
  with tempfile.TemporaryDirectory() as d:
   gui=self.gui(Path(d)); gui.page='settings'; gui.selection=4; gui.activate(); self.assertEqual(gui.page,'advanced')
   gui.selection=2; gui.button(B); self.assertEqual(gui.page,'settings'); self.assertEqual(gui.selection,4)
 def test_status_labels_and_colours_are_semantic(self):
  self.assertEqual(GUI._status_label('BADPATH_RENAME'),'Wrong path and name'); self.assertEqual(GUI._status_label('LOOKUP_FAILED'),'Lookup failed')
  self.assertEqual(GUI._status_colour('OK'),(125,235,165)); self.assertEqual(GUI._status_colour('READ_ERROR'),(255,125,115)); self.assertEqual(GUI._status_colour('UNMATCHED'),(255,190,110))
 def test_vertical_input_does_not_toggle_horizontal_confirmation(self):
  with tempfile.TemporaryDirectory() as d:
   gui=self.gui(Path(d)); gui.page='cleanup_confirm'; gui.prompt_yes=False; gui.navigate(UP); self.assertFalse(gui.prompt_yes); gui.navigate(LEFT); self.assertTrue(gui.prompt_yes)
 def test_all_polished_pages_render(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d); gui=self.gui(root); gui.database_index={'systems':{'snes':{'action':'Validate + scrape','rom_folder':'/very/long/path/to/roms/SNES','validator':'DAT','validation_status':'available','database_status':'ready','selected_dat':'/very/long/path/Nintendo - Super Nintendo Entertainment System.dat'}}}; gui.system_keys=['snes']
   gui.page='system_details'; self.assertEqual(gui.draw().size,(640,480))
   gui.page='api_status'; gui.api_status={'total_available_allowance':10,'cache_entries':2}; self.assertEqual(gui.draw().size,(640,480))
   gui.page='report'; gui.report_paths=[]; self.assertEqual(gui.draw().size,(640,480))


class UnifiedControls139Tests(unittest.TestCase):
 def cfg(self, root):
  return {'selected_system':'snes','show_missing_systems':False,'rom_roots':[str(root/'Roms')],'bios_roots':[], 'xmame':{'executable':'/missing','rom_dir':str(root/'Roms/MAME'),'xml_path':str(root/'mame.xml'),'expected_build':'x'}, 'databases':{'url':'x','timeout':5}, 'thegamesdb':{'enabled':True,'api_key_file':str(root/'key'),'timeout':5,'cache_days':30,'download_boxart':True,'max_hash_mib':128,'reserve_requests':0,'max_retries':0,'backoff_seconds':1,'max_backoff_seconds':5}}
 def gui(self, root): return GUI(root,self.cfg(root),{'snes':{'name':'SNES'}})
 def test_details_back_regression_is_fixed(self):
  with tempfile.TemporaryDirectory() as d:
   gui=self.gui(Path(d)); gui.page='details'; gui.detail_back='advanced'; self.assertTrue(gui.button(B)); self.assertEqual(gui.page,'advanced')
   gui.page='details'; gui.detail_back='report'; self.assertTrue(gui.button(B)); self.assertEqual(gui.page,'report')
 def test_dpad_clamps_instead_of_wrapping(self):
  with tempfile.TemporaryDirectory() as d:
   gui=self.gui(Path(d)); gui.page='home'; gui.selection=0; gui.navigate(UP); self.assertEqual(gui.selection,0)
   gui.selection=len(gui.home)-1; gui.navigate(DOWN); self.assertEqual(gui.selection,len(gui.home)-1)
 def test_system_dpad_clamps(self):
  with tempfile.TemporaryDirectory() as d:
   gui=self.gui(Path(d)); gui.page='systems'; gui.system_keys=[str(i) for i in range(10)]; gui.system_selection=0; gui.navigate(UP); self.assertEqual(gui.system_selection,0)
   gui.system_selection=9; gui.navigate(DOWN); self.assertEqual(gui.system_selection,9)
 def test_shoulder_pages_use_visible_page_sizes(self):
  with tempfile.TemporaryDirectory() as d:
   gui=self.gui(Path(d)); gui.page='settings'; gui.settings=[str(i) for i in range(12)]; gui.selection=0; gui.button(R1); self.assertEqual(gui.selection,5); gui.button(L1); self.assertEqual(gui.selection,0)
   gui.page='details'; gui.detail_rows=[(str(i),i) for i in range(20)]; gui.detail_scroll=0; gui.button(R1); self.assertEqual(gui.detail_scroll,8); gui.button(L1); self.assertEqual(gui.detail_scroll,0)
   gui.page='suggestions'; gui.organisation_items=[{'name':str(i)} for i in range(20)]; gui.organisation_scroll=0; gui.button(R1); self.assertEqual(gui.organisation_scroll,7)
 def test_shoulder_system_page_preserves_relative_row(self):
  with tempfile.TemporaryDirectory() as d:
   gui=self.gui(Path(d)); gui.page='systems'; gui.system_keys=[str(i) for i in range(20)]; gui.system_scroll=0; gui.system_selection=2; gui.button(R1)
   self.assertEqual(gui.system_scroll,6); self.assertEqual(gui.system_selection,8); gui.button(L1); self.assertEqual(gui.system_scroll,0); self.assertEqual(gui.system_selection,2)
 def test_shoulder_paging_clamps(self):
  with tempfile.TemporaryDirectory() as d:
   gui=self.gui(Path(d)); gui.page='details'; gui.detail_rows=[(str(i),i) for i in range(9)]; gui.button(R1); self.assertEqual(gui.detail_scroll,1); gui.button(R1); self.assertEqual(gui.detail_scroll,1); gui.button(L1); self.assertEqual(gui.detail_scroll,0)
 def test_report_history_shoulders_and_dpad_clamp(self):
  with tempfile.TemporaryDirectory() as d:
   r=Path(d); data=r/'data'; data.mkdir()
   for n in range(3):
    path=data/('report-%d.json'%n); path.write_text(json.dumps({'type':'dat','system':'snes','results':[],'summary':{'OK':n}})); os.utime(path,(100+n,100+n))
   gui=self.gui(r); gui.show_last_report(); self.assertEqual(gui.report_index,0); gui.button(R1); self.assertEqual(gui.report_index,0); gui.button(L1); self.assertEqual(gui.report_index,1); gui.button(LEFT); self.assertEqual(gui.report_index,2); gui.button(LEFT); self.assertEqual(gui.report_index,2); gui.button(RIGHT); self.assertEqual(gui.report_index,1)
 def test_shoulders_do_nothing_on_fixed_confirmation_and_run_pages(self):
  with tempfile.TemporaryDirectory() as d:
   gui=self.gui(Path(d)); gui.page='cleanup_confirm'; gui.prompt_yes=False; gui.button(R1); self.assertFalse(gui.prompt_yes)
   gui.page='results'; gui.summary={'OK':1}; gui.button(L1); self.assertEqual(gui.page,'results')
   gui.page='run'; gui.running=True; gui.detail_scroll=3; gui.button(R1); self.assertEqual(gui.detail_scroll,3)
 def test_unified_control_footers_render(self):
  with tempfile.TemporaryDirectory() as d:
   gui=self.gui(Path(d)); gui.page='systems'; gui.system_keys=['snes']; gui.database_index={'systems':{'snes':{'capability':'Validate only'}}}; self.assertEqual(gui.draw().size,(640,480))
   source=(ROOT/'app/ui.py').read_text(); self.assertIn('[L1/R1] Page',source); self.assertIn('[Left/Right/L1/R1] History',source)
 def test_single_test_suite_remains(self):
  self.assertEqual(sorted(path.name for path in (ROOT/'tests').glob('test_*.py')),['test_retroscrape.py'])


class SDWriteDiscipline140Tests(unittest.TestCase):
 def test_durable_json_skips_identical_content(self):
  with tempfile.TemporaryDirectory() as d:
   path=Path(d)/'state.json'; self.assertTrue(durable_json(path,{'a':1})); stamp=path.stat().st_mtime_ns; self.assertFalse(durable_json(path,{'a':1})); self.assertEqual(path.stat().st_mtime_ns,stamp)
 def test_durable_json_writes_changed_content(self):
  with tempfile.TemporaryDirectory() as d:
   path=Path(d)/'state.json'; durable_json(path,{'a':1}); self.assertTrue(durable_json(path,{'a':2})); self.assertEqual(json.loads(path.read_text()),{'a':2})
 def test_byte_writer_skips_unchanged_file(self):
  with tempfile.TemporaryDirectory() as d:
   path=Path(d)/'x.xml'; self.assertTrue(write_bytes_if_changed(path,b'<x/>')); stamp=path.stat().st_mtime_ns; self.assertFalse(write_bytes_if_changed(path,b'<x/>')); self.assertEqual(path.stat().st_mtime_ns,stamp)
 def test_database_index_keeps_created_when_meaningful_data_matches(self):
  from app.database_index import build_index
  with tempfile.TemporaryDirectory() as d:
   root=Path(d); (root/'data').mkdir(); config={'rom_roots':[],'thegamesdb':{'enabled':False},'xmame':{'executable':'/missing','xml_path':'/missing','expected_build':'x'}}; systems={'snes':{'name':'SNES','folders':['SFC'],'dat':'x'}}
   first=build_index(root,config,systems); path=root/'data/database-index.json'; stamp=path.stat().st_mtime_ns; second=build_index(root,config,systems); self.assertEqual(first['created'],second['created']); self.assertEqual(path.stat().st_mtime_ns,stamp)
 def test_thegamesdb_checkpoint_is_batched(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d); scraper=TheGamesDBScraper(root,RetroScrapeTests().cfg(root),{'name':'SNES'},'snes',root/'Roms/SFC',lambda *a:None,threading.Event()); calls=[]; scraper._save_state=lambda:calls.append(True); scraper._last_checkpoint=time.monotonic()
   for _ in range(24): scraper._checkpoint_state()
   self.assertEqual(calls,[]); scraper._checkpoint_state(); self.assertEqual(calls,[True])
 def test_unchanged_gamelist_is_not_replaced(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d); rom=root/'Roms/SFC'; rom.mkdir(parents=True); scraper=TheGamesDBScraper(root,RetroScrapeTests().cfg(root),{'name':'SNES'},'snes',rom,lambda *a:None,threading.Event()); records=[{'path':'./Game.sfc','name':'Game'}]
   self.assertTrue(scraper._write_gamelist(records)); path=rom/'gamelist.xml'; stamp=path.stat().st_mtime_ns; self.assertFalse(scraper._write_gamelist(records)); self.assertEqual(path.stat().st_mtime_ns,stamp)
 def test_launcher_sync_is_conditional(self):
  source=(ROOT.parent/'RetroScrape.sh').read_text(); self.assertIn('if [ -f "$WRITE_MARKER" ]',source); self.assertNotIn('STATUS=$?\nsync\n',source)
 def test_completed_arcade_journal_is_removed(self):
  source=(ROOT/'app/xmame.py').read_text(); self.assertIn('self.journal.unlink(missing_ok=True)',source); self.assertNotIn('journal["status"] = "complete"',source)
 def test_completed_database_journal_is_removed(self):
  source=(ROOT/'app/updater.py').read_text(); self.assertIn('Installed database could not be verified',source); self.assertIn('self.journal.unlink(missing_ok=True)',source)
 def test_version_and_single_suite(self):
  self.assertEqual((ROOT/'VERSION').read_text().strip(),'1.4.1'); self.assertEqual(sorted(path.name for path in (ROOT/'tests').glob('test_*.py')),['test_retroscrape.py'])


class ActionFeedback141Tests(unittest.TestCase):
 def cfg(self, root): return RetroScrapeTests().cfg(root)
 def gui(self, root): return GUI(root,self.cfg(root),{'snes':{'name':'SNES'},'gb':{'name':'Game Boy'}})
 def test_refresh_systems_reports_no_changes(self):
  with tempfile.TemporaryDirectory() as d:
   gui=self.gui(Path(d)); gui.page='advanced'; gui.selection=1; gui.system_keys=['snes']; gui.rebuild_database_index=lambda:None; gui.activate(); self.assertEqual(gui.notice_text,'Systems checked: no changes'); self.assertEqual(gui.notice_kind,'info')
 def test_refresh_systems_reports_available_count_when_changed(self):
  with tempfile.TemporaryDirectory() as d:
   gui=self.gui(Path(d)); gui.page='advanced'; gui.selection=1; gui.system_keys=['snes']
   def refresh(): gui.system_keys=['snes','gb']
   gui.rebuild_database_index=refresh; gui.activate(); self.assertEqual(gui.notice_text,'Systems refreshed: 2 available'); self.assertEqual(gui.notice_kind,'success')
 def test_setting_toggles_have_feedback(self):
  with tempfile.TemporaryDirectory() as d:
   gui=self.gui(Path(d)); gui.page='advanced'; gui.selection=0; gui.save_config=lambda:True; gui.activate(); self.assertEqual(gui.notice_text,'Unavailable systems are now shown')
   gui.page='settings'; gui.selection=0; gui.rebuild_database_index=lambda:None; gui.activate(); self.assertEqual(gui.notice_text,'Game scraping disabled')
 def test_config_failure_uses_session_only_warning(self):
  with tempfile.TemporaryDirectory() as d:
   gui=self.gui(Path(d)); gui.page='settings'; gui.selection=0
   with patch('app.ui.durable_json',side_effect=OSError('read only')): self.assertFalse(gui.save_config())
   self.assertEqual(gui.notice_text,'Setting changed for this session only'); self.assertEqual(gui.notice_kind,'warning')
 def test_cache_cleanup_zero_has_natural_feedback(self):
  with tempfile.TemporaryDirectory() as d:
   gui=self.gui(Path(d)); gui.page='api_status'
   with patch.object(TheGamesDBScraper,'cleanup_expired_cache',return_value=0), patch.object(TheGamesDBScraper,'cached_allowance_status',return_value={}): gui.button(Y)
   self.assertEqual(gui.api_status_message,'No expired cache entries')
 def test_report_feedback_saved_and_skipped(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d); (root/'data').mkdir(); (root/'VERSION').write_text('1.4.1'); gui=self.gui(root); gui.pending_report={'type':'dat','system':'snes','results':[],'summary':{}}; gui.save_pending_report(); self.assertEqual(gui.notice_text,'Report saved')
   gui.pending_report={'x':1}; gui.page='save_report'; gui.prompt_yes=False; gui.button(0); self.assertEqual(gui.notice_text,'Report not saved'); self.assertEqual(gui.notice_kind,'neutral')
 def test_backup_cleanup_reports_missing_backup(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d); (root/'dats').mkdir(); gui=self.gui(root); gui.page='cleanup_confirm'; gui.prompt_yes=True; gui.button(0); self.assertEqual(gui.notice_text,'No database backup to remove')
 def test_completion_events_have_feedback(self):
  with tempfile.TemporaryDirectory() as d:
   gui=self.gui(Path(d)); gui.operation='database_update'; gui.validation='Database update complete'; gui.emit('done'); gui.drain(); self.assertEqual(gui.notice_text,'DAT databases updated')
   gui.operation='mame_rebuild'; gui.validation='Database rebuilt'; gui.emit('done'); gui.drain(); self.assertEqual(gui.notice_text,'MAME database rebuilt')
 def test_notice_expiry_is_in_memory(self):
  with tempfile.TemporaryDirectory() as d:
   gui=self.gui(Path(d)); gui.show_notice('Done','success',0.5); self.assertTrue(gui._notice_active()); gui.notice_until=time.monotonic()-1; self.assertFalse(gui._notice_active()); self.assertEqual(gui.notice_text,'')
 def test_notice_rendering_and_single_suite(self):
  with tempfile.TemporaryDirectory() as d:
   gui=self.gui(Path(d)); gui.page='advanced'; gui.show_notice('Systems checked: no changes','info'); self.assertEqual(gui.draw().size,(640,480))
  self.assertEqual((ROOT/'VERSION').read_text().strip(),'1.4.1'); self.assertEqual(sorted(path.name for path in (ROOT/'tests').glob('test_*.py')),['test_retroscrape.py'])

if __name__=='__main__': unittest.main()
