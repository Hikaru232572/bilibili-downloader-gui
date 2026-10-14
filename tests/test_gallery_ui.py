from pathlib import Path
from dataclasses import replace
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4

from app import BiliDownloaderApp
from bili.auth import AuthConfig
from bili.models import Candidate,Component,MediaItem,Platform,Work
from tests.test_gallery import work


class GalleryUiTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.app = BiliDownloaderApp(data_dir=Path(self.directory.name)/'state')
        self.app.withdraw()
        self.request = patch.object(self.app.media_preview,'request',side_effect=lambda *a,**k:str(uuid4()))
        self.request.start()
        self.app.output_dir_var.set(self.directory.name)

    def tearDown(self):
        self.app.destroy()
        self.request.stop()
        self.directory.cleanup()

    def parse_gallery(self, media):
        self.app.url_var.set('https://xhslink.cn/o/sample')
        self.app._parsed_input = self.app.url_var.get()
        self.app._handle_work_done('https://www.xiaohongshu.com/explore/'+media.work_id,media)
        self.app.update_idletasks()

    def test_gallery_can_select_none_or_original_third_item(self):
        self.parse_gallery(work(count=3))
        page = self.app.gallery_page
        self.assertEqual(page.selection(),('item-1','item-2','item-3'))
        page.select(False)
        self.app.download_gallery()
        self.assertEqual(len(self.app.task_store.tasks()),0)
        page.selected['item-3'].set(True)
        with patch.object(self.app.task_service,'submit') as submit:
            self.app.download_gallery()
        spec, outputs = submit.call_args.args
        self.assertEqual(spec.selected_item_ids,('item-3',))
        self.assertEqual(outputs[0].original_index,3)
        self.assertIsNotNone(spec.work_ref)
        self.assertEqual(spec.components,(Component.STILL,))

    def test_deferred_live_photo_has_only_still_outputs_and_no_motion_request(self):
        self.parse_gallery(work(live=True))
        page=self.app.gallery_page
        with patch.object(self.app.task_service,'submit') as submit:
            self.app.download_gallery()
        spec,outputs=submit.call_args.args
        self.assertEqual(spec.components,(Component.STILL,))
        self.assertEqual([output.component for output in outputs],[Component.STILL])
        page.open_preview(page.work.items[0])
        popup=next(iter(page.popups))
        self.assertEqual(popup.motion_controls.winfo_manager(),'')
        requests=self.app.media_preview.request.call_count
        popup.load_motion()
        self.assertEqual(self.app.media_preview.request.call_count,requests)
        self.assertIn('暂未支持',popup.status.get())

    def test_editing_input_removes_gallery_and_rejects_stale_work(self):
        self.parse_gallery(work())
        generation = self.app._view_generation
        self.app.url_var.set('https://v.douyin.com/new/')
        self.app.queue.put(('work_result',(generation,'https://xhslink.cn/o/old',work())))
        self.app._drain_queue()
        self.assertIsNone(self.app.current_work)
        self.assertEqual(self.app.gallery_page.winfo_manager(),'')

    def test_legacy_http_thumbnail_uses_safe_resource_instead_of_breaking_gallery(self):
        media=work()
        media=replace(media,items=(replace(media.items[0],preview_url='http://sns-img-bd.xhscdn.com/old'),))
        self.parse_gallery(media)
        self.assertEqual(self.app.gallery_page.selection(),('item-1',))
        self.assertEqual(self.app.media_preview.request.call_args.args[1].url,media.items[0].still_candidates[0].url)

    def test_multiple_links_require_choice_before_any_parse(self):
        self.app.url_var.set('https://v.douyin.com/a/ https://xhslink.cn/o/b')
        with patch.object(self.app,'_fetch_link') as fetch:
            self.app.fetch_options()
        fetch.assert_not_called()
        self.assertTrue(self.app._link_dialog.winfo_exists())
        self.assertIsNone(self.app.operation)

    def test_browsing_originals_cancels_old_result_without_changing_download_selection(self):
        self.parse_gallery(work(count=3))
        page=self.app.gallery_page
        page.open_preview(page.work.items[0])
        popup=next(iter(page.popups))
        old_requests=set(popup.requests)
        popup.navigate(1)
        self.assertEqual(popup.item.original_index,2)
        self.assertTrue(old_requests.isdisjoint(page.callbacks))
        self.assertEqual(page.selection(),('item-1','item-2','item-3'))
        popup.navigate(-1)
        self.assertEqual(popup.item.original_index,1)

    def test_douyin_video_and_audio_are_independent_with_original_audio_default(self):
        media=Work(Platform.DOUYIN,'123','video',(MediaItem('123:video',1,'video',
            video_candidates=(Candidate('https://v.douyinvod.com/test',Component.VIDEO,'test'),)),),title='video')
        self.app.url_var.set('https://www.douyin.com/video/123')
        self.app._parsed_input=self.app.url_var.get()
        self.app._handle_work_done(self.app._parsed_input,media)
        page=self.app.douyin_video_page
        self.assertEqual(page.components(),(Component.VIDEO,))
        self.assertEqual(page.audio_policy.get(),'original')
        page.audio_var.set(True)
        with patch.object(self.app.task_service,'submit') as submit:
            self.app.download_gallery()
        spec,outputs=submit.call_args.args
        self.assertEqual(spec.components,(Component.VIDEO,Component.AUDIO))
        self.assertEqual(spec.audio_format,'original')
        self.assertEqual(len(outputs),2)
        page.video_var.set(False)
        self.assertEqual(page.components(),(Component.AUDIO,))

    def test_platform_authentication_drafts_are_separate_and_cancel_is_reversible(self):
        self.app.auth_service.configure(Platform.DOUYIN,AuthConfig('browser','edge'))
        self.app.show_settings_page()
        state = self.app.settings_state
        state['platform_var'].set('douyin')
        self.assertEqual(state['auth_var'].get(),'browser')
        state['browser_var'].set('firefox')
        state['platform_var'].set('xiaohongshu')
        self.assertEqual(state['auth_var'].get(),'none')
        state['platform_var'].set('douyin')
        self.assertEqual(state['browser_var'].get(),'firefox')
        state['cancel_button'].invoke()
        self.app.show_settings_page()
        state['platform_var'].set('douyin')
        self.assertEqual(state['browser_var'].get(),'edge')

    def test_clearing_one_auth_source_preserves_other_platform_and_external_file(self):
        cookie = Path(self.directory.name)/'cookie.txt'
        cookie.write_text('# Netscape HTTP Cookie File\n',encoding='utf-8')
        self.app.auth_service.configure(Platform.DOUYIN,AuthConfig('cookie_file',cookie_path=str(cookie)))
        self.app.auth_service.configure(Platform.XIAOHONGSHU,AuthConfig('browser','firefox'))
        self.app.show_settings_page()
        state = self.app.settings_state
        state['platform_var'].set('douyin')
        state['clear_button'].invoke()
        state['save_button'].invoke()
        self.assertEqual(self.app.auth_service.config(Platform.DOUYIN).mode,'guest')
        self.assertEqual(self.app.auth_service.config(Platform.XIAOHONGSHU).browser,'firefox')
        self.assertTrue(cookie.is_file())


if __name__=='__main__':
    unittest.main()
