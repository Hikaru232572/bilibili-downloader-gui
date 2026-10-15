from pathlib import Path
import tempfile
import unittest

from app import BiliDownloaderApp
from bili.models import Component, OutputSpec, Platform, TaskSpec
from tests.test_ui import metadata


class NavigationUiTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.app = BiliDownloaderApp(data_dir=Path(self.directory.name) / 'state')
        self.app.withdraw()

    def tearDown(self):
        self.app.destroy()
        self.directory.cleanup()

    def test_back_preserves_unsaved_settings_and_selected_platform(self):
        self.app.show_settings_page()
        state = self.app.settings_state
        state['profile_var'].set('Bili profile')
        state['platform_control'].invoke('douyin')
        state['browser_var'].set('firefox')
        state['auth_var'].set('browser')
        self.app.settings_back_button.invoke()
        self.app.show_settings_page()
        self.assertEqual(state['platform_var'].get(), 'douyin')
        self.assertEqual(state['browser_var'].get(), 'firefox')
        state['platform_control'].invoke('bilibili')
        self.assertEqual(state['profile_var'].get(), 'Bili profile')
        self.assertNotEqual(self.app.browser_profile_var.get(), 'Bili profile')

    def test_direct_settings_tasks_navigation_preserves_draft(self):
        self.app.show_settings_page()
        state = self.app.settings_state
        state['profile_var'].set('unsaved')
        self.app.settings_tasks_button.invoke()
        self.assertEqual(self.app.task_page.winfo_manager(), 'pack')
        self.app.task_settings_button.invoke()
        self.assertEqual(state['profile_var'].get(), 'unsaved')
        self.assertEqual(self.app.settings_page.winfo_manager(), 'pack')
        self.assertEqual(self.app.settings_button.cget('icon'), 'gear')
        self.assertEqual(self.app.task_settings_button.cget('icon'), 'gear')

    def test_save_and_revert_stay_in_settings_and_track_dirty(self):
        self.app.show_settings_page()
        state = self.app.settings_state
        original = state['output_var'].get()
        self.assertEqual(state['save_button'].cget('state'), 'disabled')
        state['output_var'].set(str(Path(self.directory.name) / 'new'))
        self.assertEqual(state['dirty_var'].get(), '未保存')
        state['cancel_button'].invoke()
        self.assertEqual(state['output_var'].get(), original)
        self.assertEqual(self.app.settings_page.winfo_manager(), 'pack')
        state['output_var'].set(str(Path(self.directory.name) / 'saved'))
        state['save_button'].invoke()
        self.assertEqual(self.app.settings_page.winfo_manager(), 'pack')
        self.assertEqual(self.app.output_dir_var.get(), state['output_var'].get())
        self.assertEqual(state['save_button'].cget('state'), 'disabled')
        self.assertEqual(state['dirty_var'].get(), '已保存')

    def test_footer_follows_queue_across_pages_and_input_changes(self):
        self.app.task_service.close()
        self.assertEqual(self.app.task_footer.winfo_manager(), '')
        spec = TaskSpec(Platform.BILIBILI, 'fixture', '测试任务', 'protected',
                        self.directory.name, ('item',), (Component.VIDEO,))
        self.app.task_store.submit(spec, (OutputSpec(spec.task_id, 'item', Component.VIDEO, 1),))
        self.app.task_page.refresh()
        self.assertEqual(self.app.task_footer.winfo_manager(), 'pack')
        self.assertIn('排队', self.app.task_summary_var.get())
        self.app.url_var.set('another input')
        self.app.show_settings_page()
        self.assertEqual(self.app.task_footer.winfo_manager(), 'pack')
        self.app.task_summary_button.invoke()
        self.assertEqual(self.app.task_page.winfo_manager(), 'pack')

    def test_minimum_window_keeps_expanded_download_reachable_above_footer(self):
        self.app.task_service.close()
        spec = TaskSpec(Platform.BILIBILI, 'fixture', '测试任务', 'protected',
                        self.directory.name, ('item',), (Component.VIDEO,))
        self.app.task_store.submit(spec, (OutputSpec(spec.task_id, 'item', Component.VIDEO, 1),))
        self.app.task_page.refresh()
        self.app.geometry('700x620')
        self.app.deiconify()
        self.app.parsed_url = 'https://www.bilibili.com/video/BVtest'
        self.app._handle_fetch_done(metadata(with_subtitles=True))
        self.app._cancel_preview_result_animation()
        self.app._toggle_more_options()
        self.app.update()
        viewport = self.app.result_canvas
        viewport.yview_moveto(1)
        self.app.update()
        button = self.app.download_button
        self.assertGreaterEqual(button.winfo_rooty(), viewport.winfo_rooty())
        self.assertLessEqual(button.winfo_rooty() + button.winfo_height(),
                             viewport.winfo_rooty() + viewport.winfo_height())
        self.assertLess(button.winfo_rooty() + button.winfo_height(),
                        self.app.task_footer.winfo_rooty())


if __name__ == '__main__':
    unittest.main()
