import unittest

from bili.adapters import douyin_work, xiaohongshu_work
from bili.models import Component, Watermark


class AdapterTests(unittest.TestCase):
    def test_xhs_middle_missing_motion_never_shifts_later_pair(self):
        raw = {'noteId':'test-note','type':'normal','imageList':[
            {'fileId':'a','urlDefault':'https://sns-img-bd.xhscdn.com/a','livePhoto':True,
             'stream':{'h264':[{'masterUrl':'https://sns-video.xhscdn.com/a.mp4'}]}},
            {'fileId':'b','urlDefault':'https://sns-img-bd.xhscdn.com/b','livePhoto':True,'stream':{}},
            {'fileId':'c','urlDefault':'https://sns-img-bd.xhscdn.com/c','livePhoto':True,
             'stream':{'h264':[{'masterUrl':'https://sns-video.xhscdn.com/c.mp4'}]}}]}
        work = xiaohongshu_work(raw,'test')
        self.assertEqual([i.original_index for i in work.items], [1,2,3])
        self.assertEqual(work.items[1].missing_components, (Component.MOTION,))
        self.assertTrue(work.items[2].motion_candidates[0].url.endswith('/c.mp4'))
        self.assertEqual(work.items[2].item_id,'c')
        self.assertIsNone(work.declared_item_count)

    def test_video_note_cover_is_not_a_gallery(self):
        work = xiaohongshu_work({'noteId':'video','type':'video',
                                'imageList':[{'urlDefault':'https://sns-img-bd.xhscdn.com/cover'}]},'test')
        self.assertEqual(work.content_type, 'unsupported')
        self.assertEqual(work.items, ())

    def test_all_live_streams_are_retained_and_watermark_not_inferred_from_url(self):
        raw = {'noteId':'note','type':'normal','imageList':[{
            'fileId':'id','urlDefault':'https://sns-img-bd.xhscdn.com/original',
            'stream':{'h264':[{'masterUrl':'https://sns-video.xhscdn.com/1.mp4',
                              'backupUrls':['https://sns-video.xhscdn.com/2.mp4']}],
                      'h265':[{'masterUrl':'https://sns-video.xhscdn.com/3.mp4'}]}}]}
        item, = xiaohongshu_work(raw,'test').items
        self.assertEqual(len(item.motion_candidates),3)
        self.assertEqual(item.still_candidates[0].watermark,Watermark.UNKNOWN)

    def test_douyin_sparse_gallery_keeps_positions_and_original_item_identity(self):
        work = douyin_work({'aweme_detail':{'aweme_id':'work','images':[
            {'uri':'a','url_list':['https://p.douyinpic.com/a']},None,
            {'uri':'c','url_list':['https://p.douyinpic.com/c'],
             'video':{'play_addr':{'url_list':['https://v.douyinvod.com/c']}}}]}},'test')
        self.assertEqual([i.original_index for i in work.items],[1,2,3])
        self.assertEqual(work.items[1].kind,'missing')
        self.assertEqual(work.items[2].kind,'live')
        self.assertEqual(work.items[2].item_id,'c')

    def test_background_music_is_not_presented_as_complete_video_audio(self):
        work = douyin_work({'aweme_detail':{'aweme_id':'video',
            'video':{'play_addr':{'url_list':['https://v.douyinvod.com/video']}},
            'music':{'play_url':{'url_list':['https://music.amemv.com/background.mp3']}}}},'test')
        self.assertEqual(work.items[0].audio_candidates, ())

    def test_duplicate_image_identity_fails_instead_of_silently_merging(self):
        with self.assertRaises(ValueError):
            xiaohongshu_work({'noteId':'note','type':'normal','imageList':[{'fileId':'a'},{'fileId':'a'}]},'test')
