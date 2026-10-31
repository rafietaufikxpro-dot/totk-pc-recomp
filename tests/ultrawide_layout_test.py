"""tools/ultrawide_layout.py on a synthetic layout archive: the geometry of the wide HUD on a canvas wider than 16:9.

The real archive (UI/LayoutArchive/Common.Product.140...blarc) is game data, so the panes and animations the script
edits are rebuilt here from the script's own table: every pane field holds the value the game ships (the table read at
k = 1), and the test asks what becomes of it at 21:9 and 32:9. What is locked down is the shape of the transform, the
invariants a constant that only fits one shape would break:

- the root pane's scale_x is 16/9 over the canvas's ratio, so a 16:9 layout keeps its proportions on any canvas;
- a pane the script moves stays inside the canvas;
- the layouts that must cover the whole screen keep their root scale of 1;
- the shipped-value and missing-pane guards stop the edit instead of writing a wrong position.

Run: python tests/ultrawide_layout_test.py
"""
import re
import struct
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'tools'))
import ultrawide_layout as uw  # noqa: E402

SEC = 0x60      # a pane section: name at +12, translate x / y at +44 / +48, scale x / y at +68 / +72
HEAD = 0x20     # the BFLYT header before the first section
OFF = 10000.0   # ultrawide_layout's "move it out of sight"
HUD_CORNER = True   # what make_romfs_patches.py --aspect asks for


def pane_section(name):
    """A pane at the origin, unscaled: what a layout ships before the script touches it."""
    b = bytearray(SEC)
    b[0:4] = b'pan1'
    struct.pack_into('<I', b, 4, SEC)
    n = name.encode()
    b[12:12 + len(n)] = n
    struct.pack_into('<2f', b, uw.FIELD['scale_x'], 1.0, 1.0)
    return bytes(b)


def bflyt(names):
    """A BFLYT with a RootPane plus one section per name in `names`: every pane at the origin and unscaled until
    set_field() puts the shipped value in."""
    body = b''.join(pane_section(n) for n in ['RootPane'] + sorted(set(names)))
    out = bytearray(HEAD + len(body))
    out[0:4] = b'FLYT'
    struct.pack_into('<H', out, 6, HEAD)
    struct.pack_into('<H', out, 0x10, 1 + len(set(names)))
    out[HEAD:] = body
    return bytes(out)


def pane_at(blarc, members, layout, pane):
    lo, hi = members[f'blyt/{layout}.bflyt']
    return uw.find_pane(uw.panes(blarc, lo, hi), layout, pane)


def set_field(blarc, members, layout, pane, name, value):
    """Write one pane field; a pane the table names but the fixture lacks stops here, as it does in the script."""
    struct.pack_into('<f', blarc, pane_at(blarc, members, layout, pane) + uw.FIELD[name], value)


def get_field(blarc, members, layout, pane, name):
    return struct.unpack_from('<f', blarc, pane_at(blarc, members, layout, pane) + uw.FIELD[name])[0]


def build():
    """(bytearray, members): every layout and animation the script edits, each field at its shipped value."""
    want = {}   # layout -> {(pane, field): shipped value}
    panes = {}  # layout -> {pane}: also the ones the table only moves out of sight, which the script still looks up
    for layout, pane, name, value in uw.edits(1.0, HUD_CORNER):
        panes.setdefault(layout, set()).add(pane)
        if value != OFF:
            want.setdefault(layout, {})[(pane, name)] = value
    parts = {f'blyt/{layout}.bflyt': bflyt(panes[layout]) for layout in panes}
    for layout in uw.KEEP_ROOT:
        parts.setdefault(f'blyt/{layout}.bflyt', bflyt([]))
    anims = {}   # animation -> {offset: shipped value}
    for anim, offset, shipped, _ in uw.anim_edits(1.0, HUD_CORNER):
        anims.setdefault(anim, {})[offset] = shipped
    for anim, keys in anims.items():
        parts[f'anim/{anim}.bflan'] = bytes(max(keys) + 8)

    blarc, members, at = bytearray(), {}, 0
    for name in sorted(parts):
        blarc += parts[name]
        members[name] = (at, len(blarc))
        at = len(blarc)
    for layout, fields in want.items():
        for (pane, name), value in fields.items():
            set_field(blarc, members, layout, pane, name, value)
    for anim, keys in anims.items():
        lo = members[f'anim/{anim}.bflan'][0]
        for offset, value in keys.items():
            struct.pack_into('<f', blarc, lo + offset, value)
    return blarc, members


def apply_at(width, height):
    """The archive after the script's edits, its members, the changed list and k = 16/9 over the canvas's ratio."""
    blarc, members = build()
    changed = uw.apply(blarc, members, width, height)
    return blarc, members, changed, (16 / 9) / (width / height)


def wide_sizes_in_menu():
    """The wide sizes runtime/ui/menu.cpp offers under Game resolution (row_output_res)."""
    menu = (ROOT / 'runtime' / 'ui' / 'menu.cpp').read_text(encoding='utf-8')
    row = re.search(r'static void row_output_res\(void\) \{(.*?)opt_choice_list', menu, re.S)
    sizes = re.findall(r'"(\d+)x(\d+)"', row[1]) if row else []
    return {f'{w}x{h}' for w, h in sizes if int(w) / int(h) > 16 / 9 * 1.01}


def sizes_the_setup_prepares():
    """installer/steps.py WIDE_SIZES, read as text: importing it needs the installer's dependencies."""
    steps = (ROOT / 'installer' / 'steps.py').read_text(encoding='utf-8')
    block = re.search(r'WIDE_SIZES = \((.*?)\)', steps, re.S)
    return set(re.findall(r"'(\d+x\d+)'", block[1]))


class WideLayoutTest(unittest.TestCase):
    # The sizes the setup prepares a HUD layout for: 21:9 panels and 32:9 ones (installer/steps.py WIDE_SIZES).
    CANVASES = [(2560, 1080), (3440, 1440), (3840, 1600), (5120, 2160), (3840, 1080), (5120, 1440)]

    def test_the_root_pane_narrows_the_layout_to_16_9(self):
        for w, h in self.CANVASES:
            blarc, members, _, k = apply_at(w, h)
            self.assertLess(k, 1.0, f'{w}x{h} is not wider than 16:9')
            self.assertAlmostEqual(get_field(blarc, members, 'AppAlbum_00', 'RootPane', 'scale_x'), k, places=5)

    def test_32_9_halves_the_layout_and_21_9_widens_the_canvas_by_a_third(self):
        # k = 16/9 over the canvas ratio: exactly 0.5 on a 32:9 screen, 0.75 on 2560x1080, 0.744 on a 3440x1440 panel.
        for (w, h), want in (((5120, 1440), 0.5), ((3840, 1080), 0.5), ((2560, 1080), 0.75), ((3440, 1440), 0.74419)):
            _, _, _, k = apply_at(w, h)
            self.assertAlmostEqual(k, want, places=4, msg=f'{w}x{h}')

    def test_no_pane_the_script_moves_leaves_the_canvas(self):
        """The root's scale applies to a pane's layout coordinate, so k * x is what the screen shows and the canvas
        ends at 960 of them. A distance found on a 21:9 screen breaks here first when it does not scale."""
        for w, h in self.CANVASES:
            blarc, members, _, k = apply_at(w, h)
            for layout, pane, name, value in uw.edits(k, HUD_CORNER):
                if name != 'x' or value == OFF:
                    continue
                x = get_field(blarc, members, layout, pane, 'x')
                self.assertLessEqual(abs(k * x), 960 + 1e-3, f'{w}x{h}: {layout} {pane} at {x} (k={k:.3f})')

    def test_the_full_screen_layouts_keep_their_width(self):
        for w, h in self.CANVASES:
            blarc, members, _, _ = apply_at(w, h)
            for layout in uw.KEEP_ROOT:
                self.assertEqual(get_field(blarc, members, layout, 'RootPane', 'scale_x'), 1.0, layout)

    def test_the_backgrounds_are_widened_back_over_the_canvas(self):
        # A scale of 1/k cancels the root's k, so the pane is as wide as the canvas again.
        for w, h in self.CANVASES:
            blarc, members, _, k = apply_at(w, h)
            for layout, pane in (('GameOver_00', 'P_BG_00'), ('ShopMenu_00', 'N_BG_00'), ('StaffRoll_00', 'N_BG_00')):
                self.assertAlmostEqual(get_field(blarc, members, layout, pane, 'scale_x'), 1 / k, places=4,
                                       msg=f'{w}x{h}: {layout} {pane}')

    def test_the_edge_distances_follow_the_canvas(self):
        """w = 960 * (1/k - 1) is how far the canvas reaches past a 16:9 layout: the loading screen's logo, the
        minimap's slide-in keys and the dragon-tears map icon's zoom all land by that much further out."""
        for w, h in self.CANVASES:
            blarc, members, _, k = apply_at(w, h)
            overhang = 960 * (1 / k - 1)
            self.assertAlmostEqual(get_field(blarc, members, 'LoadingFade_00', 'N_Logo_00', 'x'), overhang, places=1)
            lo = members['anim/AppMap_00_MiniMap.bflan'][0]
            for offset in (872, 880, 1628, 1636, 3264, 3272, 3468, 3476):
                self.assertAlmostEqual(struct.unpack_from('<f', blarc, lo + offset)[0], 765 + overhang, places=1,
                                       msg=f'{w}x{h} at +{offset}')
            lo = members['anim/PaMapIconDragonTears_00_Zoom.bflan'][0]
            self.assertAlmostEqual(struct.unpack_from('<f', blarc, lo + 288)[0], 1.08 / k, places=3)

    def test_the_hud_moves_out_to_the_corners(self):
        for w, h in self.CANVASES:
            blarc, members, changed, _ = apply_at(w, h)
            self.assertIn('blyt/PlayerStatus_00.bflyt', changed)
            # The heart gauge's left anchor and the companion life leave the middle 16:9 behind, within the canvas.
            self.assertLess(get_field(blarc, members, 'PlayerStatus_00', 'N_HeartGaugeTargetLT_00', 'x'), -576)
            self.assertGreater(get_field(blarc, members, 'EnvironmentInfo_00', 'RootPane', 'x'), 0)

    def test_the_menu_offers_exactly_what_the_setup_prepares(self):
        """A size under Game resolution with no romfs-patches-<w>x<h> gives the wide camera and a stretched 16:9 HUD."""
        self.assertEqual(wide_sizes_in_menu(), set(f'{w}x{h}' for w, h in self.CANVASES))
        self.assertEqual(sizes_the_setup_prepares(), wide_sizes_in_menu())

    def test_a_canvas_that_is_not_wider_than_16_9_is_refused(self):
        for w, h in ((1920, 1080), (1600, 900), (1280, 1024), (2560, 1440)):
            blarc, members = build()
            with self.assertRaises(AssertionError, msg=f'{w}x{h}'):
                uw.apply(blarc, members, w, h)

    def test_a_field_that_does_not_hold_the_expected_value_stops_the_edit(self):
        blarc, members = build()
        set_field(blarc, members, 'GameOver_00', 'P_BG_00', 'scale_x', 3.5)   # a version that shipped another scale
        with self.assertRaises(SystemExit):
            uw.apply(blarc, members, 3440, 1440)

    def test_a_renamed_pane_stops_the_edit(self):
        blarc, members = build()
        at = pane_at(blarc, members, 'AppMap_00', 'N_MiniMap_00') + 12
        blarc[at:at + 11] = b'N_Remapped_'
        with self.assertRaises(SystemExit):
            uw.apply(blarc, members, 3440, 1440)

    def test_a_missing_animation_stops_the_edit(self):
        blarc, members = build()
        del members['anim/PaMapIconDragonTears_00_Zoom.bflan']
        with self.assertRaises(SystemExit):
            uw.apply(blarc, members, 5120, 1440)

    def test_a_missing_full_screen_layout_stops_the_edit(self):
        blarc, members = build()
        del members['blyt/PauseMenuBG_00.bflyt']   # KEEP_ROOT names a layout that is not there
        with self.assertRaises(SystemExit):
            uw.apply(blarc, members, 5120, 1440)

    def test_the_edit_reports_the_members_it_changed(self):
        blarc, members, changed, _ = apply_at(3440, 1440)
        self.assertIn('blyt/GameOver_00.bflyt', changed)
        self.assertIn('anim/AppMap_00_MiniMap.bflan', changed)
        self.assertEqual([m for m in changed if m not in members], [], 'named a member it does not hold')
        fresh, _ = build()
        for m in (m for m in members if m not in changed):
            lo, hi = members[m]
            self.assertEqual(bytes(blarc[lo:hi]), bytes(fresh[lo:hi]), f'{m} changed but was not reported')


if __name__ == '__main__':
    unittest.main()
