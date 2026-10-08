"""Mocked Windows clipboard checks ensuring focus changes cannot copy or read another window item."""

import unittest
from unittest.mock import Mock, patch

from PoE2_Data_Logger.platform import hover_copy


class ClipboardContextTests(unittest.TestCase):
    def test_focus_change_before_copy_sends_no_keys(self):
        user = Mock()
        with patch.object(hover_copy.sys, "platform", "win32"), patch(
                "ctypes.WinDLL", create=True, return_value=user), patch(
                "PoE2_Data_Logger.platform.live_watch.game_foreground", side_effect=[True, False]):
            self.assertIsNone(hover_copy.read_hovered_text())
        user.keybd_event.assert_not_called()
        user.GetClipboardSequenceNumber.assert_not_called()

    def test_focus_change_after_copy_does_not_read_other_window_clipboard(self):
        user = Mock()
        user.GetAsyncKeyState.return_value = 0
        user.GetClipboardSequenceNumber.side_effect = [1, 2]
        with patch.object(hover_copy.sys, "platform", "win32"), patch(
                "ctypes.WinDLL", create=True, return_value=user), patch(
                "PoE2_Data_Logger.platform.live_watch.game_foreground", side_effect=[True, True, False]), patch.object(
                hover_copy, "_clipboard_text", return_value="Item Class: Waystones\nOther window") as read:
            self.assertIsNone(hover_copy.read_hovered_text())
        self.assertEqual(user.keybd_event.call_count, 4)
        read.assert_not_called()


if __name__ == "__main__":
    unittest.main()
